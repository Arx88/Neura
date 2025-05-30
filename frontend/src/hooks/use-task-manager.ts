import { useQuery, useMutation, useQueryClient, QueryKey } from '@tanstack/react-query';
import { tasksApi, CreateTaskPayload } from '@/lib/tasks-api'; // Assuming tasks-api.ts is in lib
import { TaskState } from '@/types/tasks';

// Query Keys
export const taskKeys = {
  all: ['tasks'] as const,
  lists: () => [...taskKeys.all, 'list'] as const,
  list: (filters?: { parentId?: string; status?: string }) => {
    const filterKey = filters ? JSON.stringify(filters) : 'all';
    return [...taskKeys.lists(), filterKey] as const;
  },
  details: () => [...taskKeys.all, 'detail'] as const,
  detail: (id: string | undefined) => [...taskKeys.details(), id] as const,
};

// Hook
export const useTaskManager = () => {
  const queryClient = useQueryClient();

  // --- Queries ---

  // Get a single task with polling for pseudo-real-time updates
  const useTask = (taskId: string | undefined, options?: { refetchInterval?: number | false }) => {
    return useQuery<TaskState, Error, TaskState, QueryKey>({
      queryKey: taskKeys.detail(taskId),
      queryFn: async () => {
        if (!taskId) throw new Error("Task ID is required");
        return tasksApi.getTask(taskId);
      },
      enabled: !!taskId, // Only run if taskId is provided
      refetchInterval: options?.refetchInterval !== undefined ? options.refetchInterval : 5000, // Default polling interval 5s
      // Consider adding staleTime if you don't want polling when window is not focused, etc.
    });
  };

  // Get a list of tasks (e.g., all tasks, or filtered by parentId for subtasks)
  const useTasks = (filters?: { parentId?: string; status?: string }) => {
    return useQuery<TaskState[], Error, TaskState[], QueryKey>({
      queryKey: taskKeys.list(filters),
      queryFn: () => tasksApi.getTasks(filters),
      // staleTime: 5 * 60 * 1000, // 5 minutes, example
    });
  };

  // Specific hook for subtasks of a parent
  const useSubtasks = (parentId: string | undefined, options?: { enabled?: boolean }) => {
    return useQuery<TaskState[], Error, TaskState[], QueryKey>({
      queryKey: taskKeys.list({ parentId }),
      queryFn: async () => {
        if (!parentId) return []; // Or throw error, depending on desired behavior
        return tasksApi.getTasks({ parentId });
      },
      enabled: options?.enabled !== undefined ? options.enabled && !!parentId : !!parentId,
    });
  };


  // --- Mutations ---

  const usePlanTask = ()_=> {
    return useMutation<TaskState, Error, { description: string; context?: Record<string, any> }>({
      mutationFn: ({ description, context }) => tasksApi.planTask(description, context),
      onSuccess: (data) => {
        // When a plan is created, a main task and potentially subtasks are created.
        // Invalidate all task lists and the detail for the new main task.
        queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
        queryClient.invalidateQueries({ queryKey: taskKeys.detail(data.id) });
        // If planTask returns all created tasks, you could prime the cache for them.
        // For now, just invalidating.
        logger.info(`Task plan created successfully for: ${data.name}`);
      },
      onError: (error) => {
        logger.error("Failed to plan task:", error.message);
        // Potentially show a toast notification here
      },
    });
  };

  const useCreateTask = () => {
    return useMutation<TaskState, Error, CreateTaskPayload>({
      mutationFn: (newTaskData) => tasksApi.createTask(newTaskData),
      onSuccess: (data) => {
        // Invalidate general task lists and if it's a subtask, invalidate its parent's subtask list
        queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
        if (data.parentId) {
          queryClient.invalidateQueries({ queryKey: taskKeys.list({ parentId: data.parentId }) });
           // Also invalidate the parent task detail as its `subtasks` array might have changed
          queryClient.invalidateQueries({ queryKey: taskKeys.detail(data.parentId) });
        }
        // Optionally, prime the cache for the new task
        queryClient.setQueryData(taskKeys.detail(data.id), data);
        logger.info(`Task created successfully: ${data.name}`);
      },
      onError: (error) => {
        logger.error("Failed to create task:", error.message);
      },
    });
  };

  const useUpdateTask = () => {
    return useMutation<TaskState, Error, { taskId: string; updates: Partial<TaskState> }>({
      mutationFn: ({ taskId, updates }) => tasksApi.updateTask(taskId, updates),
      onSuccess: (data, variables) => {
        // Optimistically update the specific task detail
        queryClient.setQueryData(taskKeys.detail(variables.taskId), data);

        // Invalidate relevant lists
        queryClient.invalidateQueries({ queryKey: taskKeys.lists() });
        if (data.parentId) {
          queryClient.invalidateQueries({ queryKey: taskKeys.list({ parentId: data.parentId }) });
        }
        // If status changed, could invalidate status-specific lists if you have them
        // queryClient.invalidateQueries(taskKeys.list({ status: data.status }));
        logger.info(`Task updated successfully: ${data.name}`);
      },
      // Example of optimistic update (more advanced):
      // onMutate: async ({ taskId, updates }) => {
      //   await queryClient.cancelQueries(taskKeys.detail(taskId));
      //   const previousTask = queryClient.getQueryData<TaskState>(taskKeys.detail(taskId));
      //   if (previousTask) {
      //     queryClient.setQueryData<TaskState>(taskKeys.detail(taskId), {
      //       ...previousTask,
      //       ...updates,
      //     });
      //   }
      //   return { previousTask };
      // },
      // onError: (err, variables, context) => {
      //   if (context?.previousTask) {
      //     queryClient.setQueryData(taskKeys.detail(variables.taskId), context.previousTask);
      //   }
      //   logger.error("Failed to update task:", err.message);
      // },
      // onSettled: (data, error, variables) => {
      //   queryClient.invalidateQueries(taskKeys.detail(variables.taskId));
      //   // Invalidate lists as well
      //   queryClient.invalidateQueries(taskKeys.lists());
      //   if (data?.parentId) {
      //      queryClient.invalidateQueries(taskKeys.list({ parentId: data.parentId }));
      //   }
      // },
    });
  };

  const useDeleteTask = () => {
    return useMutation<void, Error, string>({ // string is taskId
      mutationFn: (taskId) => tasksApi.deleteTask(taskId),
      onSuccess: (data, taskId) => { // data is void here
        logger.info(`Task deleted successfully: ${taskId}`);
        // Remove from cache if it exists
        queryClient.removeQueries({ queryKey: taskKeys.detail(taskId) });

        // Invalidate all lists as any list could have contained this task
        queryClient.invalidateQueries({ queryKey: taskKeys.lists() });

        // TODO: If the deleted task had a parent, we should invalidate that parent's subtask list
        // This requires knowing the parentId. One way is to fetch the task before deleting
        // or pass parentId to the mutation if available. For now, broad invalidation.
      },
      onError: (error, taskId) => {
        logger.error(`Failed to delete task ${taskId}:`, error.message);
      },
    });
  };

  return {
    useTask,
    useTasks,
    useSubtasks, // Expose useSubtasks
    planTaskMutation: usePlanTask(),
    createTaskMutation: useCreateTask(),
    updateTaskMutation: useUpdateTask(),
    deleteTaskMutation: useDeleteTask(),
  };
};


// Dummy logger for browser environment if not globally available
const logger = {
    debug: (...args: any[]) => console.debug(...args),
    info: (...args: any[]) => console.info(...args),
    warn: (...args: any[]) => console.warn(...args),
    error: (...args: any[]) => console.error(...args),
};
