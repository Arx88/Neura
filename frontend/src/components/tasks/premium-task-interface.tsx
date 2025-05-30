import React, { useState, useEffect } from 'react'; // Removed useCallback
import { motion, AnimatePresence } from 'framer-motion';
import { TaskState } from '@/types/tasks';
import { useTaskManager } from '@/hooks/use-task-manager'; // Import the real hook
import { ChevronDown, Edit3, Save, XCircle, Loader2 } from 'lucide-react'; // Added Loader2

// --- Mock TaskManager Hook (No longer needed here) ---

export interface PremiumTaskInterfaceProps {
  taskId: string;
}

const PremiumTaskInterface: React.FC<PremiumTaskInterfaceProps> = ({ taskId }) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(true);
  // For inline editing
  const [isEditing, setIsEditing] = useState<boolean>(false);
  const [editableName, setEditableName] = useState<string>('');
  const [editableDescription, setEditableDescription] = useState<string>('');

  const { useTask, updateTaskMutation } = useTaskManager();

  const {
    data: task,
    isLoading: isLoadingTask,
    error: taskQueryError,
    // refetch: refetchTask
  } = useTask(taskId, { refetchInterval: 5000 }); // Polling for real-time updates

  // Effect to initialize editable fields when task data loads or changes, but only if not editing
  useEffect(() => {
    if (task && !isEditing) {
      setEditableName(task.name);
      setEditableDescription(task.description || '');
    }
  }, [task, isEditing]);

  const handleToggleExpand = () => setIsExpanded(!isExpanded);

  const handleEdit = () => {
    if (task) {
      setEditableName(task.name);
      setEditableDescription(task.description || '');
      setIsEditing(true);
    }
  };

  const handleCancelEdit = () => {
    setIsEditing(false);
    // Reset editable fields to current task state if task exists
    if (task) {
      setEditableName(task.name);
      setEditableDescription(task.description || '');
    }
  };

  const handleSaveChanges = async () => {
    if (!task) return;

    updateTaskMutation.mutate(
      { taskId: task.id, updates: { name: editableName, description: editableDescription } },
      {
        onSuccess: (updatedTask) => {
          setIsEditing(false);
          // The cache will be updated by react-query's onSuccess in the hook,
          // and polling will eventually reflect it too.
          // If immediate reflection is needed and not handled by setQueryData in hook:
          // setTask(updatedTask); // This would require setTask from useState if not relying solely on RQ cache for display
        },
        onError: (error) => {
          // Error is already logged by the hook. Optionally show a toast here.
          logger.error("Save failed from component:", error.message);
        },
      }
    );
  };

  const renderProgressBar = (progress: number, status: string) => {
    let bgColor = 'bg-blue-600 dark:bg-blue-500';
    if (status === 'completed') bgColor = 'bg-green-500 dark:bg-green-400';
    if (status === 'failed') bgColor = 'bg-red-500 dark:bg-red-400';
    if (status === 'paused') bgColor = 'bg-yellow-500 dark:bg-yellow-400';
    return (
      <div className="w-full bg-slate-200 dark:bg-slate-700 rounded-full h-2 my-1 shadow-inner">
        <motion.div
          className={`h-2 rounded-full ${bgColor}`}
          initial={{ width: 0 }}
          animate={{ width: `${progress * 100}%` }}
          transition={{ duration: 0.5, ease: "circOut" }} // Changed ease
        />
      </div>
    );
  };

  if (isLoadingTask && !task) {
    return (
      <div className="p-6 text-center text-slate-500 dark:text-slate-400 flex flex-col items-center justify-center h-32">
        <Loader2 className="h-8 w-8 animate-spin text-primary mb-2" />
        <span>Loading task...</span>
      </div>
    );
  }

  if (taskQueryError) {
    return <div className="p-6 text-center text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/30 rounded-lg">Error: {taskQueryError.message}</div>;
  }

  if (!task) {
    return <div className="p-6 text-center text-slate-500 dark:text-slate-400">Task not found or no longer available.</div>;
  }

  const cardVariants = {
    expanded: { height: 'auto', opacity: 1, marginTop: 16 }, // Added marginTop for spacing
    collapsed: { height: 0, opacity: 0, marginTop: 0, overflow: 'hidden' }
  };

  return (
    <div className="bg-white dark:bg-slate-800 shadow-xl rounded-xl border border-slate-200 dark:border-slate-700 max-w-2xl mx-auto my-8 transition-shadow duration-300 hover:shadow-2xl">
      {/* Header */}
      <div
        className="flex items-center justify-between p-4 sm:p-5 cursor-pointer" // Slightly reduced padding
        onClick={handleToggleExpand}
      >
        {isEditing ? (
          <input
            type="text"
            value={editableName}
            onChange={(e) => setEditableName(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            className="text-lg sm:text-xl font-semibold text-slate-800 dark:text-slate-100 bg-transparent border-b-2 border-primary focus:outline-none focus:border-primary-focus w-full mr-3"
            aria-label="Task name"
          />
        ) : (
          <h2 className="text-lg sm:text-xl font-semibold text-slate-800 dark:text-slate-100 truncate pr-3">
            {task.name}
          </h2>
        )}
        <div className="flex items-center space-x-1 sm:space-x-2">
          {!isEditing && (
             <button
                onClick={(e) => { e.stopPropagation(); handleEdit(); }}
                className="p-1.5 sm:p-2 rounded-full text-slate-500 dark:text-slate-400 hover:text-primary dark:hover:text-primary-light hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
                aria-label="Edit task"
             >
                <Edit3 size={16} />
            </button>
          )}
          <motion.button
            onClick={(e) => { e.stopPropagation(); handleToggleExpand();}}
            className="p-1.5 sm:p-2 rounded-full text-slate-500 dark:text-slate-400 hover:text-primary dark:hover:text-primary-light hover:bg-slate-100 dark:hover:bg-slate-700 transition-colors"
            aria-label={isExpanded ? "Collapse task" : "Expand task"}
            animate={{ rotate: isExpanded ? 0 : -180 }}
            transition={{ duration: 0.3 }}
          >
            <ChevronDown size={18} />
          </motion.button>
        </div>
      </div>

      {/* Collapsible Content */}
      <AnimatePresence initial={false}>
        {isExpanded && (
          <motion.div
            key="content"
            initial="collapsed"
            animate="expanded"
            exit="collapsed"
            variants={cardVariants}
            transition={{ duration: 0.35, ease: "easeInOut" }} // Slightly faster
            className="px-4 sm:px-5 pb-5 border-t border-slate-200 dark:border-slate-700" // Removed pt from here, added to variants
          >
            <div className="space-y-3 pt-1"> {/* Reduced general spacing, increased pt for content from border */}
              {isEditing ? (
                <textarea
                  value={editableDescription}
                  onChange={(e) => setEditableDescription(e.target.value)}
                  rows={3}
                  className="w-full p-2 text-sm text-slate-600 dark:text-slate-300 bg-slate-50 dark:bg-slate-700/60 border border-slate-300 dark:border-slate-600 rounded-md focus:ring-1 focus:ring-primary focus:border-primary"
                  aria-label="Task description"
                />
              ) : (
                <p className="text-sm text-slate-600 dark:text-slate-300 whitespace-pre-wrap leading-relaxed">
                  {task.description || 'No description provided.'}
                </p>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-2 text-sm">
                <div>
                  <span className="font-medium text-slate-500 dark:text-slate-400">Status:</span>
                  <span className={`ml-1.5 px-2 py-0.5 text-xs font-semibold rounded-full ${
                    task.status === 'completed' ? 'bg-green-100 text-green-700 dark:bg-green-600/30 dark:text-green-300' :
                    task.status === 'running' ? 'bg-blue-100 text-blue-700 dark:bg-blue-600/30 dark:text-blue-300' :
                    task.status === 'failed' ? 'bg-red-100 text-red-700 dark:bg-red-600/30 dark:text-red-300' :
                    task.status === 'pending' || task.status === 'pending_planning' ? 'bg-yellow-100 text-yellow-700 dark:bg-yellow-600/30 dark:text-yellow-300' :
                    'bg-slate-100 text-slate-700 dark:bg-slate-600/40 dark:text-slate-300'
                  }`}>
                    {task.status.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase())}
                  </span>
                </div>
                <div className="flex items-center">
                  <span className="font-medium text-slate-500 dark:text-slate-400 mr-1.5">Progress:</span>
                  <div className="flex-grow">
                    {renderProgressBar(task.progress, task.status)}
                  </div>
                  <span className="text-xs text-slate-500 dark:text-slate-400 ml-2">{(task.progress * 100).toFixed(0)}%</span>
                </div>
                <div>
                    <span className="font-medium text-slate-500 dark:text-slate-400">Start:</span>
                    <span className="ml-1.5 text-slate-600 dark:text-slate-300">{new Date(task.startTime * 1000).toLocaleDateString()}</span>
                </div>
                {task.endTime && (
                    <div>
                        <span className="font-medium text-slate-500 dark:text-slate-400">End:</span>
                        <span className="ml-1.5 text-slate-600 dark:text-slate-300">{new Date(task.endTime * 1000).toLocaleDateString()}</span>
                    </div>
                )}
              </div>

              {isEditing && (
                <div className="flex justify-end space-x-2 pt-2">
                  <button
                    onClick={handleCancelEdit}
                    className="px-3 py-1.5 text-xs font-medium text-slate-700 dark:text-slate-200 bg-slate-100 hover:bg-slate-200 dark:bg-slate-600 dark:hover:bg-slate-500 rounded-md transition-colors flex items-center"
                    aria-label="Cancel editing"
                  >
                    <XCircle size={14} className="mr-1.5" /> Cancel
                  </button>
                  <button
                    onClick={handleSaveChanges}
                    disabled={updateTaskMutation.isPending}
                    className="px-3 py-1.5 text-xs font-medium text-white bg-primary hover:bg-primary-focus rounded-md transition-colors flex items-center disabled:opacity-70"
                    aria-label="Save changes"
                  >
                    {updateTaskMutation.isPending ? (
                        <Loader2 size={14} className="mr-1.5 animate-spin" />
                    ) : (
                        <Save size={14} className="mr-1.5" />
                    )}
                    {updateTaskMutation.isPending ? 'Saving...' : 'Save Changes'}
                  </button>
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
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

export default PremiumTaskInterface;
