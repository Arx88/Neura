// Defines the structure for a task state on the frontend.
// This should ideally be kept in sync with backend/agentpress/task_types.py TaskState.

export interface TaskState {
  id: string;
  name: string;
  description?: string | null;
  status: string; // e.g., pending, running, completed, failed, paused, pending_planning, planning_failed, planned
  progress: number; // 0.0 to 1.0
  startTime: number; // Unix timestamp (seconds or milliseconds)
  endTime?: number | null; // Unix timestamp
  parentId?: string | null;
  subtasks: string[]; // List of subtask IDs
  dependencies: string[]; // List of prerequisite task IDs
  assignedTools: string[]; // Tools assigned or relevant to this task
  artifacts: Array<{ type: string; uri?: string; description?: string; content?: string }>; // List of artifact objects
  metadata: Record<string, any>; // For any other custom data
  error?: string | null; // Error message if the task failed
  result?: any | null; // Stores the outcome or product of the task

  // Frontend-specific or resolved fields (optional, can be populated by hooks)
  _subtaskDetails?: TaskState[]; // Populated by the hook for convenience
}
