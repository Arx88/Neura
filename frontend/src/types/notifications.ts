export type NotificationType = 'info' | 'success' | 'warning' | 'error' | 'system'; // Added 'system' for general app notifications

export interface Notification {
  id: string;
  type: NotificationType;
  message: string;
  timestamp: number; // Unix timestamp in milliseconds
  read: boolean;
  details?: string; // Optional additional information or a link
  title?: string; // Optional title for the notification
  source?: string; // Optional: where the notification originated, e.g., "Task Manager", "System Update"
}
