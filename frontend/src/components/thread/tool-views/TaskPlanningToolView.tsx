// Archivo: TaskPlanningToolView.tsx
import React, { useEffect } from 'react';
import { ToolViewProps } from './types';
import { ToolViewWrapper } from './wrapper/ToolViewWrapper';
import { useTaskManager } from '@/hooks/use-task-manager';
import { CircleDashed, CheckCircle, AlertTriangle, Clock } from 'lucide-react';

export function TaskPlanningToolView({
  name,
  assistantContent,
  toolContent,
  isSuccess = true,
  isStreaming = false,
  assistantTimestamp,
  toolTimestamp,
  onFileClick,
}: ToolViewProps) {
  // Parse the task data from toolContent if available
  const taskData = React.useMemo(() => {
    if (!toolContent || toolContent === 'STREAMING') return null;
    try {
      return JSON.parse(toolContent);
    } catch (e) {
      console.error('Failed to parse task data:', e);
      return null;
    }
  }, [toolContent]);

  // Get the task ID from the parsed data
  const taskId = taskData?.id;

  // Use the task manager hook to fetch task details and subtasks
  const { useTask, useSubtasks } = useTaskManager();

  // Fetch the main task with polling for updates
  const { data: task, isLoading: isLoadingTask, error: taskError, } = useTask(taskId, { refetchInterval: 3000 }); // Poll every 3 seconds

  // Fetch subtasks for the main task
  const { data: subtasks, isLoading: isLoadingSubtasks, error: subtasksError } = useSubtasks(task?.id, { enabled: !!task?.id });

  // Combined loading and error states
  const isLoading = isLoadingTask || (task && isLoadingSubtasks);
  const error = taskError || subtasksError;

  // Format timestamp to readable format
  const formatTimestamp = (timestamp: number) => {
    return new Date(timestamp * 1000).toLocaleString();
  };

  // Render progress bar for tasks
  const renderProgressBar = (progress: number, status: string) => {
    let bgColor = 'bg-blue-500';
    if (status === 'completed') bgColor = 'bg-green-500';
    if (status === 'failed') bgColor = 'bg-red-500';
    if (status === 'paused') bgColor = 'bg-yellow-500';

    return (
      <div className="w-full bg-zinc-200 dark:bg-zinc-700 rounded-full h-1.5 my-1">
        <div
          className={`h-1.5 rounded-full ${bgColor}`}
          style={{ width: `${progress * 100}%` }}
        />
      </div>
    );
  };

  // Get status icon based on task status
  const getStatusIcon = (status: string) => {
    switch (status) {
      case 'completed':
        return <CheckCircle className="h-4 w-4 text-green-500" />;
      case 'running':
      case 'pending_planning':
        return <CircleDashed className="h-4 w-4 text-blue-500 animate-spin" />;
      case 'failed':
      case 'planning_failed':
        return <AlertTriangle className="h-4 w-4 text-red-500" />;
      case 'paused':
        return <Clock className="h-4 w-4 text-yellow-500" />;
      default:
        return <CircleDashed className="h-4 w-4 text-zinc-500" />;
    }
  };

  return (
    <ToolViewWrapper
      name={name}
      isSuccess={isSuccess}
      isStreaming={isStreaming}
      assistantTimestamp={assistantTimestamp}
      toolTimestamp={toolTimestamp}
      customStatus={{
        success: "Planificación de tareas completada",
        failure: "Error en la planificación de tareas",
        streaming: "Planificando tareas..."
      }}
    >
      <div className="p-4 space-y-4">
        {isStreaming && (
          <div className="flex items-center space-x-2 text-sm text-zinc-500 dark:text-zinc-400">
            <CircleDashed className="h-4 w-4 animate-spin" />
            <span>Planificando tareas... Por favor, espera.</span>
          </div>
        )}

        {!isStreaming && !task && !isLoading && (
           <div className="text-sm text-zinc-500 dark:text-zinc-400">
            {error ? (
              <span className="text-red-500 dark:text-red-400">Error al cargar la tarea: {error.message}</span>
            ) : (
              <span>No se encontró información de la tarea.</span>
            )}
          </div>
        )}

        {(task || isLoading) && !isStreaming && (
          <>
            {/* Main task section */}
            <div className="bg-zinc-50 dark:bg-zinc-800 rounded-lg p-4 border border-zinc-200 dark:border-zinc-700">
              <div className="flex justify-between items-center mb-2">
                <h3 className="text-lg font-medium text-zinc-900 dark:text-zinc-100">
                  {isLoading && !task ? "Cargando tarea..." : task?.name}
                </h3>
                {task && (
                  <div className="flex items-center gap-1.5">
                    {getStatusIcon(task.status)}
                    <span className={`text-xs px-2 py-0.5 rounded-full ${
                      task.status === 'completed' ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-300' :
                      task.status === 'running' ? 'bg-blue-100 text-blue-800 dark:bg-blue-900/30 dark:text-blue-300' :
                      task.status === 'failed' ? 'bg-red-100 text-red-800 dark:bg-red-900/30 dark:text-red-300' :
                      'bg-zinc-100 text-zinc-800 dark:bg-zinc-800 dark:text-zinc-300'
                    }`}>
                      {task.status.replace(/_/g, ' ').replace(/\w/g, l => l.toUpperCase())}
                    </span>
                  </div>
                )}
              </div>

              {task && (
                <>
                  <p className="text-sm text-zinc-600 dark:text-zinc-400 mb-3">
                    {task.description || "Sin descripción"}
                  </p>

                  <div className="space-y-2">
                    <div>
                      <div className="flex justify-between text-xs text-zinc-500 dark:text-zinc-400 mb-1">
                        <span>Progreso</span>
                        <span>{Math.round(task.progress * 100)}%</span>
                      </div>
                      {renderProgressBar(task.progress, task.status)}
                    </div>

                    <div className="grid grid-cols-2 gap-2 text-xs mt-3">
                      <div>
                        <span className="text-zinc-500 dark:text-zinc-400">Inicio:</span>
                        <span className="ml-1 text-zinc-700 dark:text-zinc-300">{formatTimestamp(task.startTime)}</span>
                      </div>
                      {task.endTime && (
                        <div>
                          <span className="text-zinc-500 dark:text-zinc-400">Fin:</span>
                          <span className="ml-1 text-zinc-700 dark:text-zinc-300">{formatTimestamp(task.endTime)}</span>
                        </div>
                      )}
                    </div>
                  </div>
                </>
              )}

              {isLoading && !task && (
                <div className="animate-pulse space-y-3">
                  <div className="h-4 bg-zinc-200 dark:bg-zinc-700 rounded w-3/4"></div>
                  <div className="h-2 bg-zinc-200 dark:bg-zinc-700 rounded w-full"></div>
                  <div className="h-2 bg-zinc-200 dark:bg-zinc-700 rounded w-5/6"></div>
                </div>
              )}
            </div>

            {/* Subtasks section */}
            <div>
              <h4 className="text-sm font-medium text-zinc-900 dark:text-zinc-100 mb-2 flex items-center">
                Subtareas
                {isLoadingSubtasks && <CircleDashed className="ml-2 h-3 w-3 text-zinc-400 animate-spin" />}
              </h4>

              {subtasks && subtasks.length > 0 ? (
                <div className="space-y-2">
                  {subtasks.map(subtask => (
                    <div key={subtask.id} className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 rounded-md p-3">
                      <div className="flex justify-between items-center mb-1">
                        <span className="text-sm font-medium text-zinc-800 dark:text-zinc-200">{subtask.name}</span>
                        <div className="flex items-center">
                          {getStatusIcon(subtask.status)}
                          <span className="ml-1 text-xs text-zinc-500 dark:text-zinc-400">
                            {Math.round(subtask.progress * 100)}%
                          </span>
                        </div>
                      </div>
                      {renderProgressBar(subtask.progress, subtask.status)}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-sm text-zinc-500 dark:text-zinc-400 italic">
                  {isLoadingSubtasks ? "Cargando subtareas..." : "No hay subtareas disponibles"}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </ToolViewWrapper>
  );
}
