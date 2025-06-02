import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch, call

from agentpress.task_planner import TaskPlanner, SubtaskDecompositionItem
from agentpress.task_state_manager import TaskStateManager
from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.task_types import TaskState # Assuming TaskState can be instantiated for mocking

# Fixtures
@pytest.fixture
def mock_task_manager():
    manager = MagicMock(spec=TaskStateManager)
    manager.create_task = AsyncMock()
    manager.update_task = AsyncMock()
    manager.get_task = AsyncMock()
    return manager

@pytest.fixture
def mock_tool_orchestrator():
    orchestrator = MagicMock(spec=ToolOrchestrator)
    orchestrator.get_tool_schemas_for_llm = MagicMock(return_value=[
        {"name": "Tool1__action1", "description": "Description for Tool1"},
        {"name": "Tool2__action2", "description": "Description for Tool2"}
    ])
    return orchestrator

@pytest.fixture
def planner(mock_task_manager, mock_tool_orchestrator):
    return TaskPlanner(task_manager=mock_task_manager, tool_orchestrator=mock_tool_orchestrator)

# Test Cases
@pytest.mark.asyncio
async def test_plan_task_successful_decomposition(planner, mock_task_manager, mock_tool_orchestrator):
    main_task_id = "main_task_123"
    subtask1_id = "sub_task_001"
    subtask2_id = "sub_task_002"

    mock_main_task_initial = TaskState(id=main_task_id, name="Main plan for: Test task", description="Overall task: Test task", status="pending_planning", subtasks=[])
    mock_subtask1 = TaskState(id=subtask1_id, name="Sub1", description="D1", parentId=main_task_id, dependencies=[], assignedTools=["T1"], status="pending")
    mock_subtask2 = TaskState(id=subtask2_id, name="Sub2", description="D2", parentId=main_task_id, dependencies=[subtask1_id], assignedTools=["T2"], status="pending")

    # Updated main task after subtasks are notionally linked by TaskManager
    mock_main_task_final = TaskState(id=main_task_id, name="Main plan for: Test task", description="Overall task: Test task", status="planned", subtasks=[subtask1_id, subtask2_id], progress=0.1)

    mock_task_manager.create_task.side_effect = [
        mock_main_task_initial, # First call for main task
        mock_subtask1,          # Second call for subtask1
        mock_subtask2           # Third call for subtask2
    ]
    mock_task_manager.get_task.return_value = mock_main_task_final # When main task is refreshed

    llm_response_json = [
        {"name": "Sub1", "description": "D1", "dependencies": [], "assigned_tools": ["T1"]},
        {"name": "Sub2", "description": "D2", "dependencies": [0], "assigned_tools": ["T2"]}
    ]

    with patch('agentpress.task_planner.make_llm_api_call', AsyncMock(return_value=json.dumps(llm_response_json))) as mock_llm_call:
        result_main_task = await planner.plan_task("Test task")

        mock_llm_call.assert_called_once()

        # Check main task creation
        mock_task_manager.create_task.assert_any_call(
            name="Main plan for: Test task",
            description="Overall task: Test task",
            status="pending_planning"
        )
        # Check subtask1 creation
        mock_task_manager.create_task.assert_any_call(
            name="Sub1", description="D1", parentId=main_task_id,
            dependencies=[], assignedTools=["T1"], status="pending"
        )
        # Check subtask2 creation (dependency resolved to actual ID)
        mock_task_manager.create_task.assert_any_call(
            name="Sub2", description="D2", parentId=main_task_id,
            dependencies=[subtask1_id], assignedTools=["T2"], status="pending"
        )

        assert mock_task_manager.create_task.call_count == 3 # Main task + 2 subtasks

        mock_task_manager.update_task.assert_called_with(
            main_task_id,
            {"status": "planned", "progress": 0.1}
        )

        assert result_main_task is not None
        assert result_main_task.id == main_task_id
        # The subtasks list in the returned main_task comes from the refreshed_main_task mock
        assert len(result_main_task.subtasks) == 2
        assert subtask1_id in result_main_task.subtasks
        assert subtask2_id in result_main_task.subtasks

@pytest.mark.asyncio
async def test_plan_task_llm_fails_json_parsing_after_retries(planner, mock_task_manager):
    main_task_id = "main_task_json_fail"
    mock_main_task = TaskState(id=main_task_id, name="Main task", description="Test", status="pending_planning", subtasks=[])
    mock_task_manager.create_task.return_value = mock_main_task

    with patch('agentpress.task_planner.make_llm_api_call', AsyncMock(return_value="invalid json string")) as mock_llm_call, \
         patch('agentpress.task_planner.logger.error') as mock_logger_error:

        result = await planner.plan_task("Test task json fail")

        assert mock_llm_call.call_count == 3 # Initial attempt + 2 retries
        mock_task_manager.update_task.assert_called_with(
            main_task_id,
            {"status": "planning_failed", "error": "No subtasks generated."} # Error from _decompose_task returning []
        )
        assert result is not None # plan_task returns the main task even on planning failure
        assert result.status == "planning_failed"
        mock_logger_error.assert_any_call(f"TASK_PLANNER: Max retries reached. Final JSON parsing failed. LLM Raw Response: 'invalid json string'")


@pytest.mark.asyncio
async def test_plan_task_llm_fails_pydantic_validation_after_retries(planner, mock_task_manager):
    main_task_id = "main_task_pydantic_fail"
    mock_main_task = TaskState(id=main_task_id, name="Main task", description="Test", status="pending_planning", subtasks=[])
    mock_task_manager.create_task.return_value = mock_main_task

    invalid_pydantic_data = [{"description": "Missing name field"}] # name is required

    with patch('agentpress.task_planner.make_llm_api_call', AsyncMock(return_value=json.dumps(invalid_pydantic_data))) as mock_llm_call, \
         patch('agentpress.task_planner.logger.error') as mock_logger_error:

        result = await planner.plan_task("Test task pydantic fail")

        assert mock_llm_call.call_count == 3 # Initial attempt + 2 retries
        mock_task_manager.update_task.assert_called_with(
            main_task_id,
            {"status": "planning_failed", "error": "No subtasks generated."}
        )
        assert result is not None
        assert result.status == "planning_failed"
        # Check that the specific Pydantic validation error was logged eventually
        mock_logger_error.assert_any_call(f"TASK_PLANNER: Max retries reached. Final Pydantic validation failed. LLM Parsed Data: '{invalid_pydantic_data}'")


@pytest.mark.asyncio
async def test_plan_task_llm_returns_empty_list(planner, mock_task_manager):
    main_task_id = "main_task_empty_list"
    mock_main_task = TaskState(id=main_task_id, name="Main task", description="Test", status="pending_planning", subtasks=[])
    mock_task_manager.create_task.return_value = mock_main_task

    with patch('agentpress.task_planner.make_llm_api_call', AsyncMock(return_value=json.dumps([]))) as mock_llm_call, \
         patch('agentpress.task_planner.logger.warning') as mock_logger_warning:

        result = await planner.plan_task("Test task empty list")

        mock_llm_call.assert_called_once() # Should not retry if JSON is valid but empty
        mock_logger_warning.assert_any_call(f"TASK_PLANNER: LLM failed to decompose task or returned no subtasks for: Test task empty list")
        mock_task_manager.update_task.assert_called_with(
            main_task_id,
            {"status": "planning_failed", "error": "No subtasks generated."}
        )
        assert result is not None
        assert result.status == "planning_failed"


@pytest.mark.asyncio
async def test_plan_task_main_task_creation_fails(planner, mock_task_manager):
    mock_task_manager.create_task.return_value = None # Simulate main task creation failure

    with patch('agentpress.task_planner.make_llm_api_call', new_callable=AsyncMock) as mock_llm_call:
        result = await planner.plan_task("Test task main fail")

        assert result is None
        mock_llm_call.assert_not_called()
        mock_task_manager.create_task.assert_called_once() # Attempted to create main task


@pytest.mark.asyncio
async def test_plan_task_subtask_creation_fails_partially(planner, mock_task_manager):
    main_task_id = "main_task_partial_fail"
    subtask1_id = "sub_task_ok"

    mock_main_task_initial = TaskState(id=main_task_id, name="Main plan for: Test partial", description="Overall task: Test partial", status="pending_planning", subtasks=[])
    mock_subtask1 = TaskState(id=subtask1_id, name="Sub1", description="D1", parentId=main_task_id, dependencies=[], assignedTools=["T1"], status="pending")

    # Main task after first subtask created, before second one fails
    mock_main_task_after_sub1 = TaskState(id=main_task_id, name="Main plan for: Test partial", description="Overall task: Test partial", status="pending_planning", subtasks=[subtask1_id])

    mock_task_manager.create_task.side_effect = [
        mock_main_task_initial, # Main task
        mock_subtask1,          # Subtask 1 (success)
        None                    # Subtask 2 (failure)
    ]
    # get_task will be called at the end to refresh the main task
    mock_task_manager.get_task.return_value = mock_main_task_after_sub1


    llm_response_json = [
        {"name": "Sub1", "description": "D1", "dependencies": [], "assigned_tools": ["T1"]},
        {"name": "Sub2_fails", "description": "D2_fails", "dependencies": [0], "assigned_tools": ["T2"]}
    ]

    with patch('agentpress.task_planner.make_llm_api_call', AsyncMock(return_value=json.dumps(llm_response_json))) as mock_llm_call, \
         patch('agentpress.task_planner.logger.error') as mock_logger_error:

        result_main_task = await planner.plan_task("Test partial")

        mock_llm_call.assert_called_once()
        assert mock_task_manager.create_task.call_count == 3 # Main + Sub1 (ok) + Sub2 (fail)

        mock_logger_error.assert_any_call(f"TASK_PLANNER: Failed to create subtask 'Sub2_fails' for main task {main_task_id}")

        # Main task should still be marked as planned, as some subtasks might have been created
        mock_task_manager.update_task.assert_called_with(
            main_task_id,
            {"status": "planned", "progress": 0.1}
        )
        assert result_main_task is not None
        assert result_main_task.id == main_task_id
        # The subtasks list in the returned main_task comes from the mock_main_task_after_sub1
        assert len(result_main_task.subtasks) == 1
        assert result_main_task.subtasks[0] == subtask1_id
