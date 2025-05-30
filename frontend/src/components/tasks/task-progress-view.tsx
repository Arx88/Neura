import React, { useState, useEffect } from 'react'; // Removed useCallback as it's not directly used by component itself after hook change
import { TaskState } from '@/types/tasks';
import { useTaskManager } from '@/hooks/use-task-manager'; // Import the real hook

// --- Mock TaskManager Hook (No longer needed here, moved to use-task-manager.ts) ---

export interface TaskProgressViewProps {
  taskId: string;
}

const TaskProgressView: React.FC<TaskProgressViewProps> = ({ taskId }) => {
  const [activeTab, setActiveTab] = useState<string>('overview');

  // Use the real useTaskManager hook
  const {
    useTask,
    useSubtasks,
    // We might need mutation hooks later if this component allows actions
    // updateTaskMutation, deleteTaskMutation
  } = useTaskManager();

  // Fetch the main task
  // The polling for updates is handled by refetchInterval in useTask
  const {
    data: task,
    isLoading: isLoadingTask,
    error: taskError,
    // refetch: refetchTask // Can be used to manually trigger a refresh
  } = useTask(taskId, { refetchInterval: 5000 }); // Poll every 5 seconds

  // Fetch subtasks for the main task
  // This will only run if task.id is available and will refetch if task.id changes (though taskId prop is stable here)
  // It won't poll by default unless configured similarly to useTask.
  // For now, subtasks will refresh when the parent task object changes due to polling,
  // if the subtask list on the parent is what drives re-render or if we explicitly refetch.
  const {
    data: subtasks,
    isLoading: isLoadingSubtasks,
    error: subtasksError
  } = useSubtasks(task?.id, { enabled: !!task?.id }); // Fetch subtasks only if task exists

  // Combined loading and error states
  const isLoading = isLoadingTask || (task && isLoadingSubtasks); // Loading if main task or its subtasks are loading
  const error = taskError || subtasksError;


  // No explicit useEffect for subscription needed here, as useTask handles polling.
  // The component will re-render when `task` or `subtasks` data changes due to react-query's state management.

  if (isLoading && !task) { // Show initial loading state
    return <div className="p-4 text-center text-gray-500 dark:text-gray-300">Loading task details...</div>;
  }

  if (error) {
    return <div className="p-4 text-center text-red-500 dark:text-red-300">Error: {error.message}</div>;
  }

  if (!task) {
    return <div className="p-4 text-center text-gray-500 dark:text-gray-300">Task not found.</div>;
  }

  const renderProgressBar = (progress: number, status: string) => {
    let bgColor = 'bg-blue-500 dark:bg-blue-600'; // Default for 'running' or 'pending'
    if (status === 'completed') bgColor = 'bg-green-500 dark:bg-green-600';
    if (status === 'failed') bgColor = 'bg-red-500 dark:bg-red-600';
    if (status === 'paused') bgColor = 'bg-yellow-500 dark:bg-yellow-600';

    return (
      <div className="w-full bg-gray-200 rounded-full h-2.5 dark:bg-gray-700 my-1">
        <div
          className={`h-2.5 rounded-full ${bgColor} transition-all duration-300 ease-out`}
          style={{ width: `${progress * 100}%` }}
        ></div>
      </div>
    );
  };

  const tabButtonClasses = (tabName: string) =>
    `px-3 sm:px-4 py-2 font-medium text-sm rounded-md focus:outline-none transition-colors duration-150 ` +
    (activeTab === tabName
      ? 'bg-primary text-primary-foreground shadow-sm'
      : 'text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-700');

  return (
    <div className="p-4 sm:p-6 bg-white dark:bg-gray-800 shadow-lg rounded-xl border border-gray-200 dark:border-gray-700 max-w-4xl mx-auto my-4">
      <header className="mb-6">
        <h1 className="text-2xl sm:text-3xl font-bold text-gray-800 dark:text-white mb-1">{task.name}</h1>
        <p className="text-sm text-gray-500 dark:text-gray-400">{task.description || 'No description available.'}</p>
      </header>

      <div className="mb-6 border-b border-gray-200 dark:border-gray-600">
        <nav className="-mb-px flex space-x-1 sm:space-x-2" aria-label="Tabs">
          <button onClick={() => setActiveTab('overview')} className={tabButtonClasses('overview')}>
            Overview
          </button>
          <button onClick={() => setActiveTab('subtasks')} className={tabButtonClasses('subtasks')}>
            Subtasks ({(subtasks || []).length})
          </button>
          <button onClick={() => setActiveTab('gantt')} className={tabButtonClasses('gantt')}>
            Gantt
          </button>
           <button onClick={() => setActiveTab('artifacts')} className={tabButtonClasses('artifacts')}>
            Artifacts ({(task.artifacts || []).length})
          </button>
        </nav>
      </div>

      <div>
        {activeTab === 'overview' && (
          <div className="space-y-4 animate-fadeIn">
            <h2 className="text-xl font-semibold text-gray-700 dark:text-gray-200">Task Overview</h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <span className="font-medium text-gray-600 dark:text-gray-400">Status:</span>
                    <span className={`ml-2 px-2.5 py-1 text-xs font-semibold rounded-full ${
                        task.status === 'completed' ? 'bg-green-100 text-green-800 dark:bg-green-700 dark:text-green-200' :
                        task.status === 'running' ? 'bg-blue-100 text-blue-800 dark:bg-blue-700 dark:text-blue-200' :
                        task.status === 'failed' ? 'bg-red-100 text-red-800 dark:bg-red-700 dark:text-red-200' :
                        task.status === 'pending' || task.status === 'pending_planning' ? 'bg-yellow-100 text-yellow-800 dark:bg-yellow-700 dark:text-yellow-200' :
                        'bg-gray-100 text-gray-800 dark:bg-gray-700 dark:text-gray-200'
                    }`}>
                        {task.status.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                    </span>
                </div>
                <div>
                    <span className="font-medium text-gray-600 dark:text-gray-400">Progress:</span>
                    {renderProgressBar(task.progress, task.status)}
                    <span className="text-sm text-gray-500 dark:text-gray-400 ml-2">{(task.progress * 100).toFixed(0)}%</span>
                </div>
                <div>
                    <span className="font-medium text-gray-600 dark:text-gray-400">Start Time:</span>
                    <span className="ml-2 text-gray-700 dark:text-gray-300">{new Date(task.startTime * 1000).toLocaleString()}</span>
                </div>
                {task.endTime && (
                <div>
                    <span className="font-medium text-gray-600 dark:text-gray-400">End Time:</span>
                    <span className="ml-2 text-gray-700 dark:text-gray-300">{new Date(task.endTime * 1000).toLocaleString()}</span>
                </div>
                )}
                {task.parentId && (
                <div>
                    <span className="font-medium text-gray-600 dark:text-gray-400">Parent Task ID:</span>
                    {/* TODO: Make this a link if navigation is implemented */}
                    <span className="ml-2 text-blue-600 dark:text-blue-400 hover:underline cursor-pointer">
                        {task.parentId}
                    </span>
                </div>
                )}
            </div>
            {task.assignedTools && task.assignedTools.length > 0 && (
                <div className="mt-3">
                    <span className="font-medium text-gray-600 dark:text-gray-400">Assigned Tools:</span>
                    <div className="flex flex-wrap gap-2 mt-1">
                        {task.assignedTools.map(tool => (
                            <span key={tool} className="px-2.5 py-1 text-xs bg-slate-200 text-slate-700 rounded-full dark:bg-slate-600 dark:text-slate-200">
                                {tool}
                            </span>
                        ))}
                    </div>
                </div>
            )}
             {task.error && (
              <div className="mt-3 p-3 bg-red-50 dark:bg-red-800/30 border border-red-200 dark:border-red-700/50 rounded-md">
                <h3 className="text-sm font-medium text-red-700 dark:text-red-300">Error Information</h3>
                <p className="text-xs text-red-600 dark:text-red-400 mt-1 whitespace-pre-wrap">{task.error}</p>
              </div>
            )}
            {task.result && (
                 <div className="mt-3">
                    <span className="font-medium text-gray-600 dark:text-gray-400">Result:</span>
                    <pre className="mt-1 p-3 text-xs bg-gray-100 dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700 rounded-md overflow-x-auto whitespace-pre-wrap">
                        {typeof task.result === 'object' ? JSON.stringify(task.result, null, 2) : String(task.result)}
                    </pre>
                 </div>
            )}
          </div>
        )}

        {activeTab === 'subtasks' && (
          <div className="animate-fadeIn">
            <h2 className="text-xl font-semibold text-gray-700 dark:text-gray-200 mb-4">Subtasks</h2>

            {/* Loading indicator for subtasks, shown only if no subtasks are currently displayed */}
            {isLoadingSubtasks && (!Array.isArray(subtasks) || subtasks.length === 0) && (
              <p className="text-gray-500 dark:text-gray-400">Loading subtasks...</p>
            )}

            {/* Display subtask list if loading is complete and subtasks exist */}
            {!isLoadingSubtasks && Array.isArray(subtasks) && subtasks.length > 0 && (
              <ul className="space-y-3">
                {subtasks.map(sub => (
                  <li key={sub.id} className="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700">
                    <h3 className="font-medium text-gray-800 dark:text-gray-100">{sub.name}</h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mb-1">
                        Status: {sub.status.replace(/_/g, ' ')} | Progress: {(sub.progress * 100).toFixed(0)}%
                    </p>
                    {renderProgressBar(sub.progress, sub.status)}
                  </li>
                ))}
              </ul>
            )}

            {/* Display "No subtasks" message if loading is complete and no subtasks are found */}
            {!isLoadingSubtasks && (!Array.isArray(subtasks) || subtasks.length === 0) && (
              <p className="text-gray-500 dark:text-gray-400">No subtasks for this task.</p>
            )}
          </div>
        )}

        {activeTab === 'gantt' && (
          <div className="animate-fadeIn">
            <h2 className="text-xl font-semibold text-gray-700 dark:text-gray-200">Gantt Chart</h2>
            <p className="text-gray-500 dark:text-gray-400 mt-2">Gantt Chart View (Coming Soon)</p>
          </div>
        )}

        {activeTab === 'artifacts' && (
          <div className="animate-fadeIn">
            <h2 className="text-xl font-semibold text-gray-700 dark:text-gray-200 mb-4">Artifacts</h2>
            {task.artifacts && task.artifacts.length > 0 ? (
              <ul className="space-y-3">
                {task.artifacts.map((artifact, index) => (
                  <li key={index} className="p-3 bg-gray-50 dark:bg-gray-700/50 rounded-lg shadow-sm border border-gray-100 dark:border-gray-700">
                    <h3 className="font-medium text-gray-800 dark:text-gray-100">{artifact.description || `Artifact ${index + 1}`}</h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400">Type: {artifact.type}</p>
                    {artifact.uri && (
                        <a href={artifact.uri} target="_blank" rel="noopener noreferrer" className="text-xs text-blue-600 dark:text-blue-400 hover:underline break-all">
                            {artifact.uri}
                        </a>
                    )}
                     {artifact.content && (
                        <pre className="mt-1 p-2 text-xs bg-gray-100 dark:bg-gray-900/50 border border-gray-200 dark:border-gray-700 rounded-md overflow-x-auto whitespace-pre-wrap">
                            {artifact.content}
                        </pre>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-gray-500 dark:text-gray-400">No artifacts associated with this task.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

// Dummy logger for browser environment if not globally available
const logger = {
    debug: (...args: any[]) => console.debug(...args),
    info: (...args: any[]) => console.info(...args),
    warn: (...args: any[]) => console.warn(...args),
    error: (...args: any[]) => console.error(...args),
};

export default TaskProgressView;
