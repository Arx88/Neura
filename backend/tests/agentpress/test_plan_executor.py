import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch, call
from typing import List, Optional, Dict, Any
import datetime # For TaskState.created_at if needed for sorting

# Imports from the application
from backend.agentpress.plan_executor import PlanExecutor
from backend.agentpress.task_state_manager import TaskStateManager
from backend.agentpress.tool_orchestrator import ToolOrchestrator
from backend.agentpress.task_types import TaskState
from backend.agentpress.tool import ToolResult # For mocking return values

# Fixtures
@pytest.fixture
def mock_task_manager():
    manager = MagicMock(spec=TaskStateManager)
    manager.get_task = AsyncMock()
    manager.update_task = AsyncMock()
    manager.get_subtasks = AsyncMock()
    return manager

@pytest.fixture
def mock_tool_orchestrator():
    orchestrator = MagicMock(spec=ToolOrchestrator)
    orchestrator.execute_tool = AsyncMock()
    orchestrator.get_tool_schemas_for_llm = MagicMock(return_value=[
        {"name": "ToolA__method1", "description": "Description for ToolA method1"},
        {"name": "ToolB__method2", "description": "Description for ToolB method2"},
        {"name": "SystemCompleteTask__task_complete", "description": "Signals task completion"}
    ])
    return orchestrator

@pytest.fixture
def mock_user_message_callback():
    return AsyncMock()

@pytest.fixture
def plan_executor(mock_task_manager, mock_tool_orchestrator, mock_user_message_callback):
    return PlanExecutor(
        main_task_id="main_task_001",
        task_manager=mock_task_manager,
        tool_orchestrator=mock_tool_orchestrator,
        user_message_callback=mock_user_message_callback
    )

# Helper function to create mock TaskState objects
def create_mock_task(
    id: str,
    name: str,
    status: str = "pending",
    dependencies: Optional[List[str]] = None,
    assigned_tools: Optional[List[str]] = None,
    description: str = "",
    parentId: Optional[str] = None,
    output: Optional[str] = None,
    created_at: Optional[datetime.datetime] = None # For sorting
) -> TaskState:
    return TaskState(
        id=id, name=name, status=status, parentId=parentId,
        dependencies=dependencies or [],
        assignedTools=assigned_tools or [],
        description=description,
        output=output or "", # Ensure output is string
        progress=0.0,
        created_at=created_at or datetime.datetime.now(datetime.timezone.utc), # Add created_at for sorting
        updated_at=datetime.datetime.now(datetime.timezone.utc),
        subtasks=[] # Initialize subtasks list
    )

# Test Cases
@pytest.mark.asyncio
async def test_execute_plan_successful_simple(plan_executor, mock_task_manager, mock_tool_orchestrator, mock_user_message_callback):
    main_task = create_mock_task(id="main_task_001", name="Main Task Simple", status="running")
    subtask1 = create_mock_task(id="sub1", name="Subtask 1", assigned_tools=["ToolA__method1"])

    mock_task_manager.get_task.return_value = main_task
    mock_task_manager.get_subtasks.return_value = [subtask1]

    with patch('backend.agentpress.plan_executor.make_llm_api_call', AsyncMock(return_value=json.dumps({"param": "value"}))) as mock_llm_call:
        mock_tool_orchestrator.execute_tool.return_value = ToolResult(tool_id="ToolA", execution_id="eid1", status="completed", result={"data": "success"}, error=None, start_time=datetime.datetime.now(), end_time=datetime.datetime.now())

        await plan_executor.execute_plan()

        # Verify status updates
        mock_task_manager.update_task.assert_any_call("main_task_001", {"status": "running"})
        mock_task_manager.update_task.assert_any_call("sub1", {"status": "running"})
        mock_task_manager.update_task.assert_any_call("sub1", {"status": "completed", "output": json.dumps([{"tool_id": "ToolA", "execution_id": "eid1", "status": "completed", "result": {"data": "success"}, "error": None, "start_time": mock_tool_orchestrator.execute_tool.return_value.start_time.isoformat(), "end_time": mock_tool_orchestrator.execute_tool.return_value.end_time.isoformat()}])})
        mock_task_manager.update_task.assert_any_call("main_task_001", {"status": "completed", "output": json.dumps({"message": "All subtasks processed successfully without explicit agent completion signal."})})

        mock_llm_call.assert_called_once()
        mock_tool_orchestrator.execute_tool.assert_called_once_with("ToolA", "method1", {"param": "value"})
        assert mock_user_message_callback.call_count >= 3 # Start plan, start subtask, complete subtask, complete plan


@pytest.mark.asyncio
async def test_execute_plan_tool_execution_fails(plan_executor, mock_task_manager, mock_tool_orchestrator):
    main_task = create_mock_task(id="main_task_001", name="Main Task Tool Fail", status="running")
    subtask1 = create_mock_task(id="sub1_fail", name="Subtask Tool Fail", assigned_tools=["ToolA__method1"])

    mock_task_manager.get_task.return_value = main_task
    mock_task_manager.get_subtasks.return_value = [subtask1]

    with patch('backend.agentpress.plan_executor.make_llm_api_call', AsyncMock(return_value=json.dumps({"param": "value"}))) as mock_llm_call, \
         patch('backend.agentpress.plan_executor.logger.error') as mock_logger_error:

        failed_tool_result_dict = {"tool_id": "ToolA", "execution_id": "eid_fail", "status": "failed", "result": None, "error": "Tool error details", "start_time": datetime.datetime.now().isoformat(), "end_time": datetime.datetime.now().isoformat()}
        mock_tool_orchestrator.execute_tool.return_value = ToolResult(tool_id="ToolA", execution_id="eid_fail", status="failed", result=None, error="Tool error details", start_time=datetime.datetime.fromisoformat(failed_tool_result_dict["start_time"]), end_time=datetime.datetime.fromisoformat(failed_tool_result_dict["end_time"]))

        await plan_executor.execute_plan()

        mock_task_manager.update_task.assert_any_call("sub1_fail", {"status": "failed", "output": json.dumps([failed_tool_result_dict])})
        mock_task_manager.update_task.assert_any_call("main_task_001", {"status": "failed", "output": json.dumps({"message": "Plan execution failed due to one or more subtask failures or deadlock."})})
        mock_logger_error.assert_any_call(f"PLAN_EXECUTOR: Subtask sub1_fail - Tool execution failed for 'ToolA__method1'. Error: Tool error details")


@pytest.mark.asyncio
async def test_execute_plan_llm_param_generation_fails(plan_executor, mock_task_manager, mock_tool_orchestrator):
    main_task = create_mock_task(id="main_task_001", name="Main Task LLM Fail", status="running")
    subtask1 = create_mock_task(id="sub1_llm_fail", name="Subtask LLM Fail", assigned_tools=["ToolA__method1"])

    mock_task_manager.get_task.return_value = main_task
    mock_task_manager.get_subtasks.return_value = [subtask1]

    with patch('backend.agentpress.plan_executor.make_llm_api_call', AsyncMock(side_effect=json.JSONDecodeError("Simulated LLM Error", "doc", 0))) as mock_llm_call, \
         patch('backend.agentpress.plan_executor.logger.error') as mock_logger_error:

        await plan_executor.execute_plan()

        assert mock_llm_call.call_count == 3 # Initial + 2 retries

        # Check that the subtask was marked as failed
        # The exact error message in output depends on the retry logic's final state
        update_calls = mock_task_manager.update_task.call_args_list
        subtask_fail_call = next((c for c in update_calls if c[0][0] == "sub1_llm_fail" and c[0][1].get("status") == "failed"), None)
        assert subtask_fail_call is not None
        assert "LLM failed to generate valid JSON parameters after retries" in subtask_fail_call[0][1]["output"]

        mock_task_manager.update_task.assert_any_call("main_task_001", {"status": "failed", "output": json.dumps({"message": "Plan execution failed due to one or more subtask failures or deadlock."})})
        mock_logger_error.assert_any_call(f"PLAN_EXECUTOR: Subtask sub1_llm_fail - LLM failed to generate valid JSON parameters for tool ToolA__method1 after 3 attempts. Raw LLM output: Error during LLM call: Simulated LLM Error")


@pytest.mark.asyncio
async def test_execute_plan_with_dependencies(plan_executor, mock_task_manager, mock_tool_orchestrator):
    main_task = create_mock_task(id="main_task_001", name="Main Task Deps", status="running")
    # Ensure created_at is distinct for deterministic sorting if not already handled by mock IDs
    subtask1 = create_mock_task(id="sub_dep1", name="Subtask Dep1", assigned_tools=["ToolA__method1"], created_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1))
    subtask2 = create_mock_task(id="sub_dep2", name="Subtask Dep2", dependencies=["sub_dep1"], assigned_tools=["ToolB__method2"], created_at=datetime.datetime.now(datetime.timezone.utc))

    mock_task_manager.get_task.return_value = main_task
    mock_task_manager.get_subtasks.return_value = [subtask1, subtask2] # Sorted by creation time

    with patch('backend.agentpress.plan_executor.make_llm_api_call', AsyncMock(return_value=json.dumps({"param": "value"}))) as mock_llm_call:
        mock_tool_orchestrator.execute_tool.side_effect = [
            ToolResult(tool_id="ToolA", execution_id="eid_dep1", status="completed", result={"data": "s1"}, error=None, start_time=datetime.datetime.now(), end_time=datetime.datetime.now()),
            ToolResult(tool_id="ToolB", execution_id="eid_dep2", status="completed", result={"data": "s2"}, error=None, start_time=datetime.datetime.now(), end_time=datetime.datetime.now())
        ]

        await plan_executor.execute_plan()

        # Assert order of tool execution
        assert mock_tool_orchestrator.execute_tool.call_args_list[0][0] == ("ToolA", "method1", {"param": "value"})
        assert mock_tool_orchestrator.execute_tool.call_args_list[1][0] == ("ToolB", "method2", {"param": "value"})

        mock_task_manager.update_task.assert_any_call("sub_dep1", {"status": "completed", "output": json.dumps([mock_tool_orchestrator.execute_tool.side_effect[0].to_dict()])})
        mock_task_manager.update_task.assert_any_call("sub_dep2", {"status": "completed", "output": json.dumps([mock_tool_orchestrator.execute_tool.side_effect[1].to_dict()])})
        mock_task_manager.update_task.assert_any_call("main_task_001", {"status": "completed", "output": json.dumps({"message": "All subtasks processed successfully without explicit agent completion signal."})})


@pytest.mark.asyncio
async def test_execute_plan_dependency_fails(plan_executor, mock_task_manager, mock_tool_orchestrator):
    main_task = create_mock_task(id="main_task_001", name="Main Task Dep Fail", status="running")
    subtask1 = create_mock_task(id="sub_dep_fail1", name="Subtask Dep Fail1", assigned_tools=["ToolA__method1"], created_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1))
    subtask2 = create_mock_task(id="sub_dep_fail2", name="Subtask Dep Fail2", dependencies=["sub_dep_fail1"], assigned_tools=["ToolB__method2"], created_at=datetime.datetime.now(datetime.timezone.utc))

    mock_task_manager.get_task.return_value = main_task
    mock_task_manager.get_subtasks.return_value = [subtask1, subtask2]

    with patch('backend.agentpress.plan_executor.make_llm_api_call', AsyncMock(return_value=json.dumps({"param": "value"}))):
        failed_tool_result = ToolResult(tool_id="ToolA", execution_id="eid_dep_fail1", status="failed", result=None, error="Failure in Dep1", start_time=datetime.datetime.now(), end_time=datetime.datetime.now())
        mock_tool_orchestrator.execute_tool.return_value = failed_tool_result # Subtask1 tool fails

        await plan_executor.execute_plan()

        mock_task_manager.update_task.assert_any_call("sub_dep_fail1", {"status": "failed", "output": json.dumps([failed_tool_result.to_dict()])})

        # Check that Subtask2 was not set to "running" or "completed"
        subtask2_update_calls = [c for c in mock_task_manager.update_task.call_args_list if c[0][0] == "sub_dep_fail2"]
        assert not any(call_args[0][1].get("status") in ["running", "completed", "failed"] for call_args in subtask2_update_calls)

        mock_task_manager.update_task.assert_any_call("main_task_001", {"status": "failed", "output": json.dumps({"message": "Plan execution failed due to one or more subtask failures or deadlock."})})


@pytest.mark.asyncio
async def test_execute_plan_no_tools_assigned_to_subtask(plan_executor, mock_task_manager, mock_tool_orchestrator):
    main_task = create_mock_task(id="main_task_001", name="Main Task No Tool", status="running")
    subtask_no_tool = create_mock_task(id="sub_no_tool", name="Subtask No Tool", assigned_tools=[])

    mock_task_manager.get_task.return_value = main_task
    mock_task_manager.get_subtasks.return_value = [subtask_no_tool]

    with patch('backend.agentpress.plan_executor.make_llm_api_call', new_callable=AsyncMock) as mock_llm_call:
        await plan_executor.execute_plan()

        expected_output = {"message": "No tools assigned, subtask auto-completed."}
        mock_task_manager.update_task.assert_any_call("sub_no_tool", {"status": "completed", "output": json.dumps(expected_output)})

        mock_llm_call.assert_not_called()
        mock_tool_orchestrator.execute_tool.assert_not_called()

        mock_task_manager.update_task.assert_any_call("main_task_001", {"status": "completed", "output": json.dumps({"message": "All subtasks processed successfully without explicit agent completion signal."})})


@pytest.mark.asyncio
async def test_execute_plan_main_task_not_found(plan_executor, mock_task_manager):
    mock_task_manager.get_task.return_value = None # Main task not found

    await plan_executor.execute_plan()

    mock_task_manager.update_task.assert_called_once_with("main_task_001", {"status": "failed", "output": "Main task not found during execution."})
    mock_task_manager.get_subtasks.assert_not_called()


@pytest.mark.asyncio
async def test_execute_plan_agent_signals_completion_with_systemcompletetask(plan_executor, mock_task_manager, mock_tool_orchestrator):
    main_task = create_mock_task(id="main_task_001", name="Main Task Agent Complete", status="running")
    subtask_complete = create_mock_task(id="sub_complete", name="Complete Task Sub", assigned_tools=["SystemCompleteTask__task_complete"], created_at=datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=1))
    subtask_after = create_mock_task(id="sub_after", name="Subtask After Complete", assigned_tools=["ToolA__method1"], created_at=datetime.datetime.now(datetime.timezone.utc))

    mock_task_manager.get_task.return_value = main_task
    mock_task_manager.get_subtasks.return_value = [subtask_complete, subtask_after]

    completion_summary = "Agent done with this mission!"
    complete_tool_params = {"summary": completion_summary}

    # Mock LLM call for SystemCompleteTask parameters
    with patch('backend.agentpress.plan_executor.make_llm_api_call', AsyncMock(return_value=json.dumps(complete_tool_params))) as mock_llm_call, \
         patch('backend.agentpress.plan_executor.logger.info') as mock_logger_info:

        # Mock execute_tool for SystemCompleteTask
        complete_tool_result_data = {"status": "success", "message": "Task marked as complete by agent.", "summary": completion_summary}
        mock_tool_orchestrator.execute_tool.return_value = ToolResult(
            tool_id="SystemCompleteTask",
            execution_id="eid_complete",
            status="completed",
            result=complete_tool_result_data,
            error=None,
            start_time=datetime.datetime.now(),
            end_time=datetime.datetime.now()
        )

        await plan_executor.execute_plan()

        mock_llm_call.assert_called_once_with(
            messages=unittest.mock.ANY, # Check messages more specifically if needed
            llm_model=unittest.mock.ANY,
            temperature=unittest.mock.ANY,
            json_mode=True
        )
        mock_tool_orchestrator.execute_tool.assert_called_once_with("SystemCompleteTask", "task_complete", complete_tool_params)

        mock_task_manager.update_task.assert_any_call("sub_complete", {"status": "completed", "output": json.dumps([mock_tool_orchestrator.execute_tool.return_value.to_dict()])})

        # Main task should be marked completed with the agent's summary
        mock_task_manager.update_task.assert_any_call("main_task_001", {"status": "completed", "output": json.dumps({"message": completion_summary})})

        # Ensure the second subtask was NOT processed
        subtask_after_update_calls = [c for c in mock_task_manager.update_task.call_args_list if c[0][0] == "sub_after"]
        assert not any(call_args[0][1].get("status") in ["running", "completed", "failed"] for call_args in subtask_after_update_calls)

        mock_logger_info.assert_any_call(f"PLAN_EXECUTOR: Agent signaled task completion via SystemCompleteTask. Main task main_task_001 will be marked as completed.")
        mock_logger_info.assert_any_call(f"PLAN_EXECUTOR: Plan execution for main_task_id: main_task_001 completed by agent signal.")
