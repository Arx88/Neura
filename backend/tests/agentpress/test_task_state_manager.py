import pytest
import asyncio
import time
from typing import List, Optional, Dict, Any, Set, Callable, Coroutine
import uuid

from agentpress.api_models_tasks import TaskState, TaskStorage
from agentpress.task_state_manager import TaskStateManager

# --- Mock TaskStorage Implementation ---
class MockTaskStorage(TaskStorage):
    def __init__(self):
        self.tasks: Dict[str, TaskState] = {}
        self.update_calls: List[Dict[str, Any]] = [] # To track updates

    async def save_task(self, task: TaskState) -> None:
        # Deepcopy might be better if TaskState objects are mutated after saving elsewhere
        # For these tests, direct assignment should be fine.
        self.tasks[task.id] = task
        # print(f"MockTaskStorage: Saved task {task.id}, current tasks: {list(self.tasks.keys())}")


    async def load_task(self, task_id: str) -> Optional[TaskState]:
        return self.tasks.get(task_id)

    async def load_all_tasks(self) -> List[TaskState]:
        return list(self.tasks.values())

    async def delete_task(self, task_id: str) -> None:
        if task_id in self.tasks:
            del self.tasks[task_id]

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[TaskState]:
        self.update_calls.append({"task_id": task_id, "updates": updates})
        if task_id in self.tasks:
            task = self.tasks[task_id]
            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            # Simulate endTime update if status is terminal
            if updates.get("status") in ["completed", "failed", "cancelled"] and not task.endTime:
                task.endTime = updates.get("endTime", time.time())

            self.tasks[task_id] = task # Re-assign to ensure update is reflected if task was a copy
            return task
        return None

import pytest_asyncio # Import the decorator

# --- Test Fixtures ---
@pytest.fixture
def mock_storage():
    return MockTaskStorage()

@pytest_asyncio.fixture # Mark as pytest-asyncio fixture
async def task_manager(mock_storage: MockTaskStorage):
    manager = TaskStateManager(storage=mock_storage)
    # No need to explicitly call initialize if tests handle it or it's part of setup
    return manager

@pytest_asyncio.fixture # Mark as pytest-asyncio fixture
async def initialized_task_manager(mock_storage: MockTaskStorage):
    # Pre-populate storage with some tasks
    task1 = TaskState(id="task_init_1", name="Initial Task 1", status="pending")
    task2 = TaskState(id="task_init_2", name="Initial Task 2", status="running", parentId="task_init_1")
    # Explicitly link subtask in parent's list for initialization
    task1.subtasks = [task2.id]
    mock_storage.tasks = {task1.id: task1, task2.id: task2}

    manager = TaskStateManager(storage=mock_storage)
    await manager.initialize()
    return manager

# --- Listener Utilities ---
class Listener:
    def __init__(self):
        self.received_tasks: List[TaskState] = []
        self.call_count = 0

    async def callback(self, task: TaskState):
        self.received_tasks.append(task)
        self.call_count += 1

    def get_last_task(self) -> Optional[TaskState]:
        return self.received_tasks[-1] if self.received_tasks else None

# --- Test Cases ---

@pytest.mark.asyncio
async def test_initialize_loads_tasks(initialized_task_manager: TaskStateManager, mock_storage: MockTaskStorage):
    assert len(initialized_task_manager._tasks) == 2
    assert "task_init_1" in initialized_task_manager._tasks
    assert initialized_task_manager._tasks["task_init_1"].name == "Initial Task 1"

@pytest.mark.asyncio
async def test_create_task_no_parent(task_manager: TaskStateManager, mock_storage: MockTaskStorage):
    listener = Listener()
    task_manager.subscribe_to_all(listener.callback)

    created_task = await task_manager.create_task(name="Test Task A", description="A test task")

    assert created_task is not None
    assert created_task.name == "Test Task A"
    assert created_task.id in task_manager._tasks
    assert created_task.id in mock_storage.tasks
    assert mock_storage.tasks[created_task.id].description == "A test task"

    assert listener.call_count == 1
    assert listener.get_last_task().id == created_task.id

@pytest.mark.asyncio
async def test_create_subtask(task_manager: TaskStateManager, mock_storage: MockTaskStorage):
    parent_task = await task_manager.create_task(name="Parent Task")
    assert parent_task is not None

    subtask_listener = Listener()
    parent_listener = Listener()
    task_manager.subscribe(parent_task.id, parent_listener.callback) # For parent update

    subtask = await task_manager.add_subtask(
        parent_id=parent_task.id,
        subtask_creation_data={"name": "Subtask 1", "description": "A subtask"}
    )

    assert subtask is not None
    assert subtask.name == "Subtask 1"
    assert subtask.parentId == parent_task.id
    assert subtask.id in task_manager._tasks
    assert subtask.id in mock_storage.tasks

    # Check if parent task in memory and storage reflects the new subtask
    updated_parent_in_memory = await task_manager.get_task(parent_task.id)
    assert updated_parent_in_memory is not None
    assert subtask.id in updated_parent_in_memory.subtasks

    updated_parent_in_storage = await mock_storage.load_task(parent_task.id)
    assert updated_parent_in_storage is not None
    assert subtask.id in updated_parent_in_storage.subtasks

    # Parent listener should be called because its subtasks list was modified by create_task
    assert parent_listener.call_count > 0
    assert parent_listener.get_last_task().id == parent_task.id
    assert subtask.id in parent_listener.get_last_task().subtasks


@pytest.mark.asyncio
async def test_get_task(initialized_task_manager: TaskStateManager):
    task = await initialized_task_manager.get_task("task_init_1")
    assert task is not None
    assert task.name == "Initial Task 1"

    non_existent_task = await initialized_task_manager.get_task("non_existent_id")
    assert non_existent_task is None

@pytest.mark.asyncio
async def test_update_task(initialized_task_manager: TaskStateManager, mock_storage: MockTaskStorage):
    task_id_to_update = "task_init_1"
    listener = Listener()
    unsubscribe = initialized_task_manager.subscribe(task_id_to_update, listener.callback)

    updates = {"name": "Updated Task Name", "status": "running", "progress": 0.5}
    updated_task = await initialized_task_manager.update_task(task_id_to_update, updates)

    assert updated_task is not None
    assert updated_task.name == "Updated Task Name"
    assert updated_task.status == "running"
    assert updated_task.progress == 0.5

    assert task_id_to_update in initialized_task_manager._tasks
    assert initialized_task_manager._tasks[task_id_to_update].name == "Updated Task Name"

    assert task_id_to_update in mock_storage.tasks
    assert mock_storage.tasks[task_id_to_update].status == "running"

    # Check that storage's update_task was called correctly
    assert len(mock_storage.update_calls) > 0
    last_update_call = mock_storage.update_calls[-1]
    assert last_update_call["task_id"] == task_id_to_update
    assert last_update_call["updates"]["name"] == "Updated Task Name"

    assert listener.call_count == 1
    assert listener.get_last_task().progress == 0.5

    unsubscribe() # Test unsubscription

@pytest.mark.asyncio
async def test_update_task_completes_sets_endtime(task_manager: TaskStateManager):
    task = await task_manager.create_task(name="Task to complete")
    assert task.endTime is None

    await task_manager.update_task(task.id, {"status": "completed"})
    updated_task = await task_manager.get_task(task.id)
    assert updated_task is not None
    assert updated_task.status == "completed"
    assert updated_task.endTime is not None
    assert isinstance(updated_task.endTime, float)

@pytest.mark.asyncio
async def test_delete_task(initialized_task_manager: TaskStateManager, mock_storage: MockTaskStorage):
    parent_id = "task_init_1"
    subtask_id = "task_init_2" # This is a subtask of task_init_1

    # Ensure parent knows about subtask
    parent_task_before = await initialized_task_manager.get_task(parent_id)
    if parent_task_before: # Add subtask if not already (though fixture should handle it)
        if subtask_id not in parent_task_before.subtasks:
             parent_task_before.subtasks.append(subtask_id)
        await initialized_task_manager.update_task(parent_id, {"subtasks": parent_task_before.subtasks})


    assert subtask_id in initialized_task_manager._tasks
    assert subtask_id in mock_storage.tasks

    await initialized_task_manager.delete_task(subtask_id)

    assert subtask_id not in initialized_task_manager._tasks
    assert subtask_id not in mock_storage.tasks

    # Check if parent task was updated
    parent_task_after = await initialized_task_manager.get_task(parent_id)
    assert parent_task_after is not None
    assert subtask_id not in parent_task_after.subtasks

    # Check storage for parent update
    parent_in_storage_after = await mock_storage.load_task(parent_id)
    assert parent_in_storage_after is not None
    assert subtask_id not in parent_in_storage_after.subtasks


@pytest.mark.asyncio
async def test_get_subtasks(initialized_task_manager: TaskStateManager):
    parent_id = "task_init_1"
    subtasks = await initialized_task_manager.get_subtasks(parent_id)
    assert len(subtasks) == 1
    assert subtasks[0].id == "task_init_2"
    assert subtasks[0].parentId == parent_id

    non_parent_subtasks = await initialized_task_manager.get_subtasks("non_existent_parent")
    assert len(non_parent_subtasks) == 0

@pytest.mark.asyncio
async def test_listener_notification_specific_and_global(task_manager: TaskStateManager):
    specific_listener = Listener()
    global_listener = Listener()

    task1 = await task_manager.create_task(name="Listener Test Task 1")

    unsubscribe_specific = task_manager.subscribe(task1.id, specific_listener.callback)
    unsubscribe_global = task_manager.subscribe_to_all(global_listener.callback)

    # Update task1
    await task_manager.update_task(task1.id, {"status": "running"})
    assert specific_listener.call_count == 1
    assert global_listener.call_count == 1 # Create + Update
    assert specific_listener.get_last_task().status == "running"
    assert global_listener.get_last_task().status == "running"

    # Create task2
    task2 = await task_manager.create_task(name="Listener Test Task 2")
    assert specific_listener.call_count == 1 # Should not be called for task2
    assert global_listener.call_count == 2 # Called for task2 creation
    assert global_listener.get_last_task().id == task2.id

    unsubscribe_specific()
    unsubscribe_global()

    # Update task1 again, no listeners should be called
    await task_manager.update_task(task1.id, {"status": "completed"})
    assert specific_listener.call_count == 1
    assert global_listener.call_count == 2


@pytest.mark.asyncio
async def test_create_task_storage_failure(task_manager: TaskStateManager, mock_storage: MockTaskStorage):
    original_save_task = mock_storage.save_task
    async def failing_save_task(task: TaskState):
        raise IOError("Disk full")
    mock_storage.save_task = failing_save_task

    with pytest.raises(IOError, match="Disk full"):
        await task_manager.create_task(name="Fail Save Task")

    assert not task_manager._tasks # Should be rolled back from memory
    mock_storage.save_task = original_save_task # Restore original method

@pytest.mark.asyncio
async def test_utility_status_methods(task_manager: TaskStateManager):
    task = await task_manager.create_task(name="Utility Test")

    await task_manager.set_task_status(task.id, "running", progress=0.25)
    updated = await task_manager.get_task(task.id)
    assert updated.status == "running"
    assert updated.progress == 0.25

    await task_manager.complete_task(task.id, result={"data": "done"}, progress=0.99) # progress can be overridden
    updated = await task_manager.get_task(task.id)
    assert updated.status == "completed"
    assert updated.progress == 0.99 # Not 1.0, as specified
    assert updated.result == {"data": "done"}
    assert updated.endTime is not None

    task2 = await task_manager.create_task(name="Utility Test 2")
    await task_manager.fail_task(task2.id, error="Something went wrong")
    updated2 = await task_manager.get_task(task2.id)
    assert updated2.status == "failed"
    assert updated2.error == "Something went wrong"
    assert updated2.endTime is not None
    # Progress should remain as it was unless specified
    assert updated2.progress == 0.0

    await task_manager.fail_task(task2.id, error="New error", progress=0.5)
    updated3 = await task_manager.get_task(task2.id)
    assert updated3.progress == 0.5
    assert updated3.error == "New error"
