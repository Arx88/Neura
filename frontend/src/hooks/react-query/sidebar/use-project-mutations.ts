'use client';

import { useMutation, useQueryClient } from '@tanstack/react-query';
import { createMutationHook } from '@/hooks/use-query';
import { 
  createProject, 
  updateProject, 
  deleteProject,
  Project 
} from '@/lib/api';
import { toast } from 'sonner';
import { projectKeys } from './keys';
import { handleApiError } from '@/lib/error-handler';

export const useCreateProject = createMutationHook(
  (data: { name: string; description: string; accountId?: string }) => 
    createProject(data, data.accountId),
  {
    onSuccess: () => {
      toast.success('Project created successfully');
    },
    errorContext: {
      operation: 'create project',
      resource: 'project'
    }
  }
);

export const useUpdateProject = createMutationHook(
  ({ projectId, data }: { projectId: string; data: Partial<Project> }) => 
    updateProject(projectId, data),
  {
    onSuccess: () => {
    //   toast.success('Project updated successfully');
    },
    errorContext: {
      operation: 'update project',
      resource: 'project'
    }
  }
);

export const useDeleteProject = () => {
  const queryClient = useQueryClient();
  
  return useMutation({
    mutationFn: ({ projectId }: { projectId: string }) => deleteProject(projectId),
    onSuccess: (data: unknown, variables: { projectId: string }, context: unknown) => {
      toast.success('Project deleted successfully');
      queryClient.invalidateQueries({ queryKey: projectKeys.all });
    },
    onError: (error: Error, variables: { projectId: string }, context: unknown) => {
      const errorContext = { 
        operation: 'delete project', 
        resource: `project (ID: ${variables.projectId})` // Resource ID included for better error tracking
      };
      handleApiError(error, errorContext);
    }
  });
};