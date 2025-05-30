export type ThemePreference = 'light' | 'dark' | 'system';

export type TaskDensity = 'compact' | 'comfortable';

export interface Shortcut {
  action: string; // e.g., "saveTask", "openSearch"
  label: string; // e.g., "Save Task", "Open Search"
  keys: string;  // e.g., "Ctrl+S", "Cmd+K"
}

export interface Preferences {
  theme: ThemePreference;
  notificationsEnabled: boolean;
  taskDensity: TaskDensity;
  // Using a list of Shortcut objects for better structure and display
  keyboardShortcuts: Shortcut[];
  // Example of a more specific notification preference
  notificationSoundEnabled: boolean;
  // Example of a feature flag preference
  showExperimentalFeatures: boolean;
  // Language preference
  language: string; // e.g., 'en', 'es', 'fr'
}

// Default values for preferences
export const defaultPreferences: Preferences = {
  theme: 'system',
  notificationsEnabled: true,
  taskDensity: 'comfortable',
  keyboardShortcuts: [
    { action: 'saveItem', label: 'Save Current Item', keys: 'Ctrl+S' },
    { action: 'openSearch', label: 'Open Search', keys: 'Cmd+K' },
    { action: 'toggleSidebar', label: 'Toggle Sidebar', keys: 'Ctrl+B' },
    { action: 'newTask', label: 'Create New Task', keys: 'N' },
  ],
  notificationSoundEnabled: true,
  showExperimentalFeatures: false,
  language: 'en',
};
