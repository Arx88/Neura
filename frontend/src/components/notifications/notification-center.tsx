import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Bell, Info, CheckCircle, AlertTriangle, XCircle, Trash2, MailCheck, X } from 'lucide-react';
import { Notification, NotificationType } from '@/types/notifications'; // Assuming path is correct

// --- Mock useNotifications Hook ---
const useMockNotifications = () => {
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [nextId, setNextId] = useState(0);

  const addMockNotification = useCallback((notificationData: Omit<Notification, 'id' | 'timestamp' | 'read'>) => {
    setNotifications(prev => [
      {
        id: `notif-${nextId}`,
        timestamp: Date.now(),
        read: false,
        ...notificationData,
      },
      ...prev, // Add new notifications to the top
    ]);
    setNextId(prev => prev + 1);
  }, [nextId]);

  // Initialize with some sample notifications and simulate new ones
  useEffect(() => {
    addMockNotification({ title: 'Welcome!', type: 'info', message: 'Welcome to AgentPress! Explore the features.', source: 'System' });
    setTimeout(() => addMockNotification({ title: 'Task Update', type: 'success', message: 'Task "Develop UI" marked as completed.', source: 'Task Manager' }), 2000);
    setTimeout(() => addMockNotification({ type: 'warning', message: 'Low disk space. Please clean up.', details: 'Only 500MB remaining on /dev/sda1', source: 'System Monitor' }), 5000);

    const interval = setInterval(() => {
        const randomType: NotificationType[] = ['info', 'success', 'warning', 'error', 'system'];
        const randomIndex = Math.floor(Math.random() * randomType.length);
        addMockNotification({
            type: randomType[randomIndex],
            title: `Random Event: ${randomType[randomIndex].charAt(0).toUpperCase() + randomType[randomIndex].slice(1)}`,
            message: `A new ${randomType[randomIndex]} event occurred at ${new Date().toLocaleTimeString()}`,
            source: 'Event Generator'
        });
    }, 15000); // New notification every 15 seconds

    return () => clearInterval(interval);
  }, [addMockNotification]);


  const unreadCount = useMemo(() => notifications.filter(n => !n.read).length, [notifications]);

  const markAsRead = useCallback((id: string) => {
    setNotifications(prev => prev.map(n => n.id === id ? { ...n, read: true } : n));
  }, []);

  const markAllAsRead = useCallback(() => {
    setNotifications(prev => prev.map(n => ({ ...n, read: true })));
  }, []);

  const removeNotification = useCallback((id: string) => {
    setNotifications(prev => prev.filter(n => n.id !== id));
  }, []);

  const clearAll = useCallback(() => {
    setNotifications([]);
  }, []);

  return {
    notifications,
    unreadCount,
    markAsRead,
    markAllAsRead,
    removeNotification,
    clearAll,
    addMockNotification // Expose for testing if needed from outside
  };
};
// --- End Mock useNotifications Hook ---


// Helper function for relative time
const formatRelativeTime = (timestamp: number): string => {
  const now = Date.now();
  const seconds = Math.round((now - timestamp) / 1000);

  if (seconds < 5) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;

  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;

  const days = Math.round(hours / 24);
  return `${days}d ago`;
};

const NotificationIcon: React.FC<{ type: NotificationType, className?: string }> = ({ type, className = "w-5 h-5" }) => {
  switch (type) {
    case 'success': return <CheckCircle className={`${className} text-green-500`} />;
    case 'warning': return <AlertTriangle className={`${className} text-yellow-500`} />;
    case 'error': return <XCircle className={`${className} text-red-500`} />;
    case 'system': return <Info className={`${className} text-purple-500`} />; // Example for system
    case 'info':
    default: return <Info className={`${className} text-blue-500`} />;
  }
};

export const NotificationCenter: React.FC = () => {
  const {
    notifications,
    unreadCount,
    markAsRead,
    markAllAsRead,
    removeNotification,
    clearAll
  } = useMockNotifications();

  const [isOpen, setIsOpen] = useState(false);

  const togglePanel = () => setIsOpen(!isOpen);

  return (
    <div className="relative">
      {/* Trigger Button */}
      <button
        onClick={togglePanel}
        className="p-2 rounded-full text-slate-600 dark:text-slate-300 hover:bg-slate-200 dark:hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-primary focus:ring-opacity-50 transition-colors"
        aria-label="Open notifications"
      >
        <Bell size={22} />
        {unreadCount > 0 && (
          <span className="absolute -top-1 -right-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-500 text-xs font-bold text-white">
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {/* Notification Panel */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: -10, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.95, transition: { duration: 0.15 } }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            className="absolute right-0 mt-2 w-80 sm:w-96 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl z-50 overflow-hidden"
          >
            <header className="flex items-center justify-between p-3 border-b border-slate-200 dark:border-slate-700 sticky top-0 bg-white/80 dark:bg-slate-800/80 backdrop-blur-sm">
              <h3 className="text-md font-semibold text-slate-800 dark:text-slate-100">Notifications</h3>
              <div className="flex items-center space-x-2">
                {notifications.length > 0 && unreadCount > 0 && (
                     <button
                        onClick={markAllAsRead}
                        className="text-xs text-primary hover:underline disabled:text-slate-400"
                        disabled={unreadCount === 0}
                        title="Mark all as read"
                    >
                        <MailCheck size={16} />
                    </button>
                )}
                {notifications.length > 0 && (
                     <button
                        onClick={clearAll}
                        className="text-xs text-red-500 hover:underline disabled:text-slate-400"
                        disabled={notifications.length === 0}
                        title="Clear all notifications"
                    >
                        <Trash2 size={16} />
                    </button>
                )}
              </div>
            </header>

            {notifications.length === 0 ? (
              <p className="p-6 text-center text-sm text-slate-500 dark:text-slate-400">
                No new notifications.
              </p>
            ) : (
              <div className="max-h-96 overflow-y-auto divide-y divide-slate-100 dark:divide-slate-700/50">
                {notifications.map((notification) => (
                  <div
                    key={notification.id}
                    className={`p-3 hover:bg-slate-50 dark:hover:bg-slate-700/50 transition-colors ${!notification.read ? 'bg-blue-50 dark:bg-blue-500/10' : ''}`}
                  >
                    <div className="flex items-start space-x-3">
                      <div className="flex-shrink-0 pt-1">
                        <NotificationIcon type={notification.type} />
                      </div>
                      <div className="flex-1 min-w-0">
                        {notification.title && <h4 className="text-sm font-medium text-slate-800 dark:text-slate-100">{notification.title}</h4>}
                        <p className={`text-sm ${!notification.read ? 'font-semibold text-slate-700 dark:text-slate-200' : 'text-slate-600 dark:text-slate-300'}`}>
                          {notification.message}
                        </p>
                        {notification.details && (
                          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{notification.details}</p>
                        )}
                        <p className="mt-1 text-xs text-slate-400 dark:text-slate-500">
                          {formatRelativeTime(notification.timestamp)}
                          {notification.source && ` · ${notification.source}`}
                        </p>
                      </div>
                      <div className="flex-shrink-0 flex flex-col items-center space-y-1">
                        {!notification.read && (
                          <button
                            onClick={() => markAsRead(notification.id)}
                            className="p-1 text-xs text-blue-500 hover:text-blue-700 dark:hover:text-blue-300"
                            title="Mark as read"
                          >
                            <CheckCircle size={14}/>
                          </button>
                        )}
                        <button
                          onClick={() => removeNotification(notification.id)}
                          className="p-1 text-xs text-slate-400 hover:text-red-500 dark:hover:text-red-400"
                          title="Remove notification"
                        >
                          <X size={14} />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
             <footer className="p-2 border-t border-slate-200 dark:border-slate-700 text-center">
                <button
                    onClick={() => alert("View all notifications (not implemented)")}
                    className="text-xs text-primary hover:underline"
                >
                    View all notifications
                </button>
            </footer>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default NotificationCenter;

// Dummy logger for browser environment if not globally available (e.g. if used in Storybook without context)
// const logger = {
//     debug: console.debug,
//     info: console.info,
//     warn: console.warn,
//     error: console.error,
// };
