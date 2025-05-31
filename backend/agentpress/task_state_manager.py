from typing import List, Optional, Dict, Any, Set, Callable, Coroutine, Union, Type
from collections import defaultdict
import uuid
import time
import asyncio

from agentpress.task_types import TaskState, TaskStorage
from utils.logger import logger # Changed import

# For Partial[TaskState] equivalent if needed for subtask_data without Pydantic/TypedDict features
# from typing import TypedDict
# class PartialTaskState(TypedDict, total=False):
#     name: str
#     description: Optional[str]
#     # ... other fields as needed for creation
# Using Dict[str, Any] for subtask_data for simplicity for now.


class TaskStateManager:
    """
    Manages the state of tasks, including their creation, updates, deletion,
    and relationships (subtasks, dependencies). Interfaces with a TaskStorage
    implementation for persistence.
    """

    def __init__(self, storage: TaskStorage):
        self.storage = storage
        self._tasks: Dict[str, TaskState] = {} # In-memory cache of tasks
        self._listeners: Dict[str, Set[Callable[[TaskState], Coroutine[Any, Any, None]]]] = defaultdict(set)
        self._global_listeners: Set[Callable[[TaskState], Coroutine[Any, Any, None]]] = set()
        self._lock = asyncio.Lock() # For thread-safe operations on _tasks
        logger.info("TaskStateManager initialized.")

    async def initialize(self):
        """Loads existing tasks from storage into memory."""
        async with self._lock:
            try:
                all_tasks = await self.storage.load_all_tasks()
                self._tasks = {task.id: task for task in all_tasks}
                logger.info(f"Initialized TaskStateManager with {len(self._tasks)} tasks from storage.")
            except Exception as e:
                logger.error(f"Failed to initialize TaskStateManager from storage: {e}", exc_info=True)
                # Depending on requirements, might re-raise or start with empty state.
                self._tasks = {}

    async def _notify_listeners(self, task_id: str, task: Optional[TaskState] = None):
        """Notifies listeners registered for a specific task and global listeners."""
        if task is None:
            task = self._tasks.get(task_id)

        if task:
            # Specific listeners for this task_id
            if task_id in self._listeners:
                # Create a list of tasks to avoid issues if a listener modifies the set
                callbacks_to_run = list(self._listeners[task_id])
                logger.debug(f"Notifying {len(callbacks_to_run)} listeners for task {task_id}")
                for callback in callbacks_to_run:
                    try:
                        await callback(task)
                    except Exception as e:
                        logger.error(f"Error in listener for task {task_id}: {e}", exc_info=True)

            # Global listeners (interested in any task change)
            global_callbacks_to_run = list(self._global_listeners)
            logger.debug(f"Notifying {len(global_callbacks_to_run)} global listeners for task {task_id}")
            for g_callback in global_callbacks_to_run:
                try:
                    await g_callback(task)
                except Exception as e:
                    logger.error(f"Error in global listener for task {task_id}: {e}", exc_info=True)


    def subscribe(self, task_id: str, callback: Callable[[TaskState], Coroutine[Any, Any, None]]) -> Callable[[], None]:
        """Subscribes a callback to updates for a specific task. Returns an unsubscribe function."""
        self._listeners[task_id].add(callback)
        logger.debug(f"Listener subscribed to task {task_id}. Total listeners for task: {len(self._listeners[task_id])}")
        def unsubscribe():
            self._listeners[task_id].remove(callback)
            if not self._listeners[task_id]: # Clean up if no listeners left
                del self._listeners[task_id]
            logger.debug(f"Listener unsubscribed from task {task_id}.")
        return unsubscribe

    def subscribe_to_all(self, callback: Callable[[TaskState], Coroutine[Any, Any, None]]) -> Callable[[], None]:
        """Subscribes a callback to updates for any task. Returns an unsubscribe function."""
        self._global_listeners.add(callback)
        logger.debug(f"Global listener subscribed. Total global listeners: {len(self._global_listeners)}")
        def unsubscribe_global():
            self._global_listeners.remove(callback)
            logger.debug("Global listener unsubscribed.")
        return unsubscribe_global

    async def create_task(
        self,
        name: str,
        description: Optional[str] = None,
        parent_id: Optional[str] = None,
        dependencies: Optional[List[str]] = None,
        assigned_tools: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        progress: float = 0.0
    ) -> TaskState:
        """Creates a new task, saves it, and notifies listeners."""
        async with self._lock:
            task_id = str(uuid.uuid4())
            new_task = TaskState(
                id=task_id,
                name=name,
                description=description,
                status=status,
                progress=progress,
                startTime=time.time(),
                parentId=parent_id,
                dependencies=dependencies or [],
                assignedTools=assigned_tools or [],
                metadata=metadata or {}
            )

            if parent_id:
                parent_task = self._tasks.get(parent_id)
                if parent_task:
                    parent_task.subtasks.append(task_id)
                    # Save parent task to persist the new subtask ID in its list
                    await self.storage.save_task(parent_task)
                    logger.debug(f"Updated parent task {parent_id} with new subtask {task_id}.")
                    # Notify listeners for the parent task as it has changed
                    await self._notify_listeners(parent_id, parent_task)
                else:
                    logger.warning(f"Parent task {parent_id} not found for new task {task_id}.")
                    # Decide on behavior: raise error, or create orphan task?
                    # For now, parent_id will be set but parent might not know about it.

            self._tasks[task_id] = new_task
            try:
                await self.storage.save_task(new_task)
                logger.info(f"Task {task_id} ('{name}') created and saved.")
                await self._notify_listeners(task_id, new_task)
                return new_task
            except Exception as e:
                logger.error(f"Failed to save new task {task_id}: {e}", exc_info=True)
                # Rollback in-memory addition if save fails
                del self._tasks[task_id]
                if parent_id and parent_task and task_id in parent_task.subtasks:
                    parent_task.subtasks.remove(task_id)
                    # Attempt to save parent again if rollback needed, or handle consistency differently
                raise # Re-raise the storage error

    async def get_task(self, task_id: str) -> Optional[TaskState]:
        """Retrieves a task by its ID from the in-memory cache."""
        async with self._lock: # Lock not strictly needed for read if TaskState is immutable after creation
                              # but good if we expect live updates to TaskState objects directly.
            return self._tasks.get(task_id)

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[TaskState]:
        """Updates an existing task in memory and storage, then notifies listeners."""
        async with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                logger.warning(f"Task {task_id} not found for update.")
                return None

            # Apply updates to the in-memory task object
            changed_fields = False
            for key, value in updates.items():
                if hasattr(task, key):
                    if getattr(task, key) != value:
                        setattr(task, key, value)
                        changed_fields = True
                elif key in task.metadata: # Allow updating metadata sub-keys directly
                    if task.metadata[key] != value:
                         task.metadata[key] = value
                         changed_fields = True
                else: # New key, add to metadata by default or log warning
                    logger.debug(f"Adding new key '{key}' to metadata for task {task_id}")
                    task.metadata[key] = value
                    changed_fields = True

            if updates.get("status") in ["completed", "failed", "cancelled"] and not task.endTime:
                task.endTime = updates.get("endTime", time.time()) # Set endTime if task is finishing
                changed_fields = True

            if not changed_fields:
                logger.debug(f"No actual changes for task {task_id}, update skipped.")
                # Still notify, as an "update" call was made, could be a refresh signal
                await self._notify_listeners(task_id, task)
                return task

            try:
                # Use the storage's update_task method for potentially atomic DB updates
                updated_task_from_storage = await self.storage.update_task(task_id, updates)
                if updated_task_from_storage:
                    # Replace in-memory task with the one returned from storage to ensure consistency
                    self._tasks[task_id] = updated_task_from_storage
                    logger.info(f"Task {task_id} updated successfully in storage and memory.")
                    await self._notify_listeners(task_id, updated_task_from_storage)
                    return updated_task_from_storage
                else:
                    # This case should ideally not happen if task was found in memory,
                    # unless DB update failed in a way that update_task returned None without error.
                    logger.error(f"Task {task_id} found in memory but failed to update in storage (no data returned).")
                    # Potentially revert in-memory changes or mark task as desynced.
                    # For now, we keep the in-memory change and log error.
                    return task # Return the in-memory (potentially inconsistent) task
            except Exception as e:
                logger.error(f"Failed to update task {task_id} in storage: {e}", exc_info=True)
                # Here, task object in memory was modified. Consider reverting if storage fails.
                # For simplicity, current state is that memory might be ahead if storage fails.
                raise


    async def delete_task(self, task_id: str) -> None:
        """Deletes a task from memory and storage."""
        async with self._lock:
            task_to_delete = self._tasks.get(task_id)
            if not task_to_delete:
                logger.warning(f"Task {task_id} not found for deletion.")
                return

            # Handle parent's subtask list
            if task_to_delete.parentId:
                parent_task = self._tasks.get(task_to_delete.parentId)
                if parent_task and task_id in parent_task.subtasks:
                    parent_task.subtasks.remove(task_id)
                    try:
                        # Update parent in storage
                        await self.storage.update_task(parent_task.id, {"subtasks": parent_task.subtasks})
                        logger.info(f"Removed subtask {task_id} from parent {parent_task.id}.")
                        await self._notify_listeners(parent_task.id, parent_task)
                    except Exception as e:
                        logger.error(f"Failed to update parent task {parent_task.id} after removing subtask {task_id}: {e}", exc_info=True)
                        # Continue with deleting the task, but parent might be inconsistent in storage.

            # TODO: Handle dependencies: what happens to tasks that depend on this one?
            # This might involve setting them to a "blocked" or "failed" state, or re-evaluating.
            # For now, just deleting the task.

            try:
                await self.storage.delete_task(task_id)
                del self._tasks[task_id]
                if task_id in self._listeners: # Clean up listeners for deleted task
                    del self._listeners[task_id]
                logger.info(f"Task {task_id} deleted successfully from storage and memory.")
                # Notify with a "deleted" state or special marker? For now, no notification on delete.
                # Or notify with the task object just before deletion.
            except Exception as e:
                logger.error(f"Failed to delete task {task_id} from storage: {e}", exc_info=True)
                # Task remains in memory if storage deletion fails. Consider consistency strategy.
                raise

    async def add_subtask(self, parent_id: str, subtask_creation_data: Dict[str, Any]) -> Optional[TaskState]:
        """
        Creates a new task and adds it as a subtask to the specified parent.
        `subtask_creation_data` should be a dictionary with fields for TaskState,
        e.g., {"name": "Subtask Name", "description": "..."}.
        """
        if parent_id not in self._tasks:
            logger.warning(f"Parent task {parent_id} not found. Cannot add subtask.")
            return None

        # Ensure parent_id is set in the subtask data
        # subtask_creation_data["parentId"] = parent_id # This would cause duplicate if parent_id is also a named arg in create_task

        # Default name if not provided
        name = subtask_creation_data.pop("name", f"Subtask of {self._tasks[parent_id].name}")
        description = subtask_creation_data.pop("description", None)
        # Pass other fields from subtask_creation_data explicitly or ensure create_task handles them well in **kwargs
        # For now, assuming create_task's signature is mainly name, description, parent_id, dependencies, etc.
        # and other arbitrary data goes into metadata.
        # Let's simplify: remove known args from subtask_creation_data and pass the rest as metadata
        # if create_task is structured to accept **kwargs for metadata or similar.
        # Current create_task signature: name, description, parent_id, dependencies, assigned_tools, metadata, status, progress

        # Extract known fields for create_task signature
        dependencies = subtask_creation_data.pop("dependencies", None)
        assigned_tools = subtask_creation_data.pop("assignedTools", None)
        status = subtask_creation_data.pop("status", "pending")
        progress = subtask_creation_data.pop("progress", 0.0)
        # Any remaining items in subtask_creation_data can be passed as metadata
        metadata = subtask_creation_data # Remaining items are metadata

        try:
            # Use the main create_task method which handles saving and locking
            subtask = await self.create_task(
                name=name,
                description=description,
                parent_id=parent_id, # Pass explicitly
                dependencies=dependencies,
                assigned_tools=assigned_tools,
                metadata=metadata,
                status=status,
                progress=progress
            )
            # create_task already handles adding to parent's subtasks list if parent_id is provided.
            return subtask
        except Exception as e:
            logger.error(f"Failed to create subtask for parent {parent_id}: {e}", exc_info=True)
            return None

    async def get_subtasks(self, parent_id: str) -> List[TaskState]:
        """Retrieves all subtasks for a given parent ID from in-memory cache."""
        async with self._lock: # Lock for consistent read of parent and subtasks
            parent_task = self._tasks.get(parent_id)
            if not parent_task:
                return []
            return [self._tasks[sub_id] for sub_id in parent_task.subtasks if sub_id in self._tasks]

    async def get_all_tasks(self) -> List[TaskState]:
        """Returns a list of all tasks currently in memory."""
        async with self._lock:
            return list(self._tasks.values())

    async def get_tasks_by_status(self, status: str) -> List[TaskState]:
        """Returns tasks filtered by status from in-memory cache."""
        async with self._lock:
            return [task for task in self._tasks.values() if task.status == status]

    async def set_task_status(self, task_id: str, status: str, progress: Optional[float] = None) -> Optional[TaskState]:
        """Helper to quickly update task status and optionally progress."""
        updates = {"status": status}
        if progress is not None:
            updates["progress"] = progress
        return await self.update_task(task_id, updates)

    async def complete_task(self, task_id: str, result: Optional[Any] = None, progress: float = 1.0) -> Optional[TaskState]:
        """Marks a task as completed."""
        updates = {"status": "completed", "progress": progress, "endTime": time.time()}
        if result is not None:
            updates["result"] = result
        return await self.update_task(task_id, updates)

    async def fail_task(self, task_id: str, error: str, progress: Optional[float] = None) -> Optional[TaskState]:
        """Marks a task as failed."""
        updates = {"status": "failed", "error": error, "endTime": time.time()}
        if progress is not None: # Use current progress if not specified
            updates["progress"] = progress
        return await self.update_task(task_id, updates)
