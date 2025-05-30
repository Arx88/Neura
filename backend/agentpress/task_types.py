from typing import List, Optional, Dict, Any, Set, Callable, Coroutine, TypedDict
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
import uuid
import time

# Using TypedDict for more precise dictionary structure if needed,
# but dataclass is often easier to work with for state objects.
# For now, let's use dataclass.

@dataclass
class TaskState:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: Optional[str] = None
    status: str = "pending"  # e.g., pending, running, completed, failed, paused
    progress: float = 0.0  # 0.0 to 1.0
    startTime: float = field(default_factory=time.time)
    endTime: Optional[float] = None
    parentId: Optional[str] = None
    subtasks: List[str] = field(default_factory=list)  # List of subtask IDs
    dependencies: List[str] = field(default_factory=list)  # List of prerequisite task IDs
    assignedTools: List[str] = field(default_factory=list) # Tools assigned or relevant to this task
    artifacts: List[Dict[str, Any]] = field(default_factory=list)  # e.g., {"type": "file", "uri": "path/to/file.txt"}
    metadata: Dict[str, Any] = field(default_factory=dict) # For any other custom data
    error: Optional[str] = None # Error message if the task failed
    result: Optional[Any] = None # Stores the outcome or product of the task

class TaskStorage(ABC):
    """
    Abstract Base Class for task persistence.
    Defines the interface for saving, loading, and deleting tasks.
    """

    @abstractmethod
    async def save_task(self, task: TaskState) -> None:
        """Persists a task state."""
        pass

    @abstractmethod
    async def load_task(self, task_id: str) -> Optional[TaskState]:
        """Loads a specific task state by its ID."""
        pass

    @abstractmethod
    async def load_all_tasks(self) -> List[TaskState]:
        """Loads all task states."""
        pass

    @abstractmethod
    async def delete_task(self, task_id: str) -> None:
        """Deletes a task state by its ID."""
        pass

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[TaskState]:
        """
        Atomically updates a task.
        Default implementation loads, updates, and saves.
        Subclasses can override for more efficient atomic updates if supported by the backend.
        """
        task = await self.load_task(task_id)
        if task:
            for key, value in updates.items():
                if hasattr(task, key):
                    setattr(task, key, value)
                else:
                    # Potentially handle updates to metadata or other dict fields more granularly
                    # For now, direct attribute setting or error.
                    # Consider adding to metadata if key not a direct attribute.
                    # task.metadata[key] = value
                    pass # Or raise an error for unknown fields
            if "endTime" in updates and updates["endTime"] is None and task.status not in ["running", "pending", "paused"]:
                 # If endTime is being cleared, it implies task is restarting or being reset.
                 # However, endTime is usually set when a task concludes.
                 pass
            elif updates.get("status") in ["completed", "failed", "cancelled"] and not task.endTime:
                task.endTime = updates.get("endTime", time.time())

            await self.save_task(task)
            return task
        return None

# Example of an artifact structure
# class Artifact(TypedDict):
#     type: str # e.g., 'file', 'url', 'text_snippet'
#     uri: Optional[str] # path or url
#     description: Optional[str]
#     content: Optional[str] # for small text snippets

# Example of how TaskState.subtasks and TaskState.dependencies would work:
# If TaskA depends on TaskB, TaskA.dependencies would contain TaskB.id.
# If TaskC is a subtask of TaskA, TaskA.subtasks would contain TaskC.id, and TaskC.parentId would be TaskA.id.
