from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import time
from typing import List, Optional, Dict, Any, Set, Callable, Coroutine, TypedDict
import uuid

from pydantic import BaseModel, Field

# --- Content from backend/agentpress/task_types.py (excluding its original imports) ---

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

# --- Original content from backend/agentpress/api_models_tasks.py (after its imports and removed Enum) ---

# Re-using TaskState structure from backend/agentpress/task_types.py as a base
# Pydantic models are often used for API validation and serialization.

class TaskStateBase(BaseModel):
    name: str
    description: Optional[str] = None
    status: Optional[str] = "pending" # This will now refer to the string status from the dataclass TaskState
    progress: Optional[float] = 0.0
    parentId: Optional[str] = None
    # For creation, subtasks are usually not provided directly, but populated via add_subtask
    # dependencies can be provided if IDs are known
    dependencies: Optional[List[str]] = Field(default_factory=list)
    assignedTools: Optional[List[str]] = Field(default_factory=list)
    artifacts: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None
    result: Optional[Any] = None

class CreateTaskPayload(TaskStateBase):
    # Specific fields for creation, if any differ from base or need to be enforced
    name: str # Make name mandatory for creation

class UpdateTaskPayload(BaseModel):
    # All fields are optional for update
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    progress: Optional[float] = None
    # parentId is usually not changed after creation, but could be supported
    # subtasks: Handled by add_subtask or by direct parent update if needed
    dependencies: Optional[List[str]] = None
    assignedTools: Optional[List[str]] = None
    artifacts: Optional[List[Dict[str, Any]]] = None
    metadata: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    result: Optional[Any] = None
    endTime: Optional[float] = None # Allow setting endTime explicitly on update

class TaskResponse(TaskStateBase):
    id: str
    startTime: float
    endTime: Optional[float] = None
    subtasks: List[str] = Field(default_factory=list) # IDs of subtasks

    class Config:
        from_attributes = True # For compatibility if creating from ORM objects (like TaskState dataclass)

# Payload for the /plan endpoint
class PlanTaskPayload(BaseModel):
    description: str
    context: Optional[Dict[str, Any]] = None

# Response for the /plan endpoint (could be the main task or a list of all created tasks)
class PlanTaskResponse(TaskResponse): # The main task created by the planner
    pass

# To represent a list of tasks in response
class TaskListResponse(BaseModel):
    tasks: List[TaskResponse]

# If we want to exactly mirror the TaskState dataclass for responses:
class FullTaskStateResponse(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: str # This should align with the string status from the TaskState dataclass
    progress: float
    startTime: float
    endTime: Optional[float] = None
    parentId: Optional[str] = None
    subtasks: List[str]
    dependencies: List[str]
    assignedTools: List[str]
    artifacts: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    error: Optional[str] = None
    result: Optional[Any] = None

    class Config:
        from_attributes = True # Pydantic v2

class FullTaskListResponse(BaseModel):
    tasks: List[FullTaskStateResponse]
