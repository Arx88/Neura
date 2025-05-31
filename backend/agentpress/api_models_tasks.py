from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import uuid
import time

# Re-using TaskState structure from backend/agentpress/task_types.py as a base
# Pydantic models are often used for API validation and serialization.

class TaskStateBase(BaseModel):
    name: str
    description: Optional[str] = None
    status: Optional[str] = "pending"
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
    status: str
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
