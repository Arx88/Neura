import { TaskState } from '@/types/tasks';
import { backendApi, ApiResponse } from './api-client'; // Use the existing backendApi

// Define specific payload types if they differ significantly from TaskState or Partial<TaskState>
// For now, using Partial<TaskState> for updates and a specific structure for creation if needed.

export interface CreateTaskPayload {
  name: string;
  description?: string | null;
  parentId?: string | null;
  dependencies?: string[];
  assignedTools?: string[];
  metadata?: Record<string, any>;
  status?: string;
  progress?: number;
}

export interface PlanTaskPayload {
  description: string;
  context?: Record<string, any>;
}

// The backend API returns tasks matching FullTaskStateResponse, which aligns with our frontend TaskState.
// So, we can expect TaskState directly or within a wrapper like { task: TaskState } or { tasks: TaskState[] }
// Based on the FastAPI backend, list returns { tasks: [...] } and single returns the task object directly.

export const tasksApi = {
  async planTask(description: string, context?: Record<string, any>): Promise<TaskState> {
    const payload: PlanTaskPayload = { description, context };
    const response: ApiResponse<TaskState> = await backendApi.post('/tasks/plan', payload);
    if (!response.success || !response.data) {
      throw response.error || new Error('Failed to plan task');
    }
    return response.data;
  },

  async createTask(taskData: CreateTaskPayload): Promise<TaskState> {
    const response: ApiResponse<TaskState> = await backendApi.post('/tasks', taskData);
    if (!response.success || !response.data) {
      throw response.error || new Error('Failed to create task');
    }
    return response.data;
  },

  async getTasks(filters?: { parentId?: string; status?: string }): Promise<TaskState[]> {
    let endpoint = '/tasks';
    const queryParams = new URLSearchParams();
    if (filters?.parentId) {
      queryParams.append('parent_id', filters.parentId); // Ensure query param name matches backend
    }
    if (filters?.status) {
      queryParams.append('status', filters.status);
    }
    if (queryParams.toString()) {
      endpoint += `?${queryParams.toString()}`;
    }

    // Backend returns { tasks: TaskState[] } which matches FullTaskListResponse
    const response: ApiResponse<{ tasks: TaskState[] }> = await backendApi.get(endpoint);
    if (!response.success || !response.data) {
      throw response.error || new Error('Failed to get tasks');
    }
    return response.data.tasks;
  },

  async getTask(taskId: string): Promise<TaskState> {
    const response: ApiResponse<TaskState> = await backendApi.get(`/tasks/${taskId}`);
    if (!response.success || !response.data) {
      throw response.error || new Error(`Failed to get task ${taskId}`);
    }
    return response.data;
  },

  async updateTask(taskId: string, updates: Partial<TaskState>): Promise<TaskState> {
    const response: ApiResponse<TaskState> = await backendApi.put(`/tasks/${taskId}`, updates);
    if (!response.success || !response.data) {
      throw response.error || new Error(`Failed to update task ${taskId}`);
    }
    return response.data;
  },

  async deleteTask(taskId: string): Promise<void> {
    // Delete typically returns 204 No Content, so response.data might be undefined or empty.
    const response: ApiResponse<void> = await backendApi.delete(`/tasks/${taskId}`);
    if (!response.success) {
      // If response.error is not specific enough, you might want to throw a generic error
      // For 204, response.data will be undefined, so we rely on response.success
      throw response.error || new Error(`Failed to delete task ${taskId}`);
    }
    // No data to return for a successful delete
  },
};
