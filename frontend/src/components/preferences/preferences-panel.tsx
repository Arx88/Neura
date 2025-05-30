import React, { useState, useCallback, useEffect } from 'react';
import { Preferences, ThemePreference, TaskDensity, Shortcut, defaultPreferences } from '@/types/preferences';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';
import { Separator } from '@/components/ui/separator';
import { RotateCcw, Palette, BellRing, Settings2, ListTree, Keyboard } from 'lucide-react'; // Example icons

// --- Mock usePreferences Hook ---
const useMockPreferences = () => {
  const [preferences, setPreferences] = useState<Preferences>(() => {
    // Try to load from localStorage or use defaults
    if (typeof window !== 'undefined') {
        const savedPrefs = localStorage.getItem('app-preferences');
        if (savedPrefs) {
            try {
                const parsed = JSON.parse(savedPrefs);
                // Basic validation to ensure it's not completely malformed
                if (parsed && typeof parsed.theme === 'string') {
                     // Merge with defaults to ensure all keys are present if structure changed
                    return { ...defaultPreferences, ...parsed };
                }
            } catch (e) {
                console.error("Failed to parse preferences from localStorage", e);
            }
        }
    }
    return defaultPreferences;
  });

  useEffect(() => {
    // Save to localStorage whenever preferences change
     if (typeof window !== 'undefined') {
        localStorage.setItem('app-preferences', JSON.stringify(preferences));
     }
  }, [preferences]);

  const updatePreference = useCallback(<K extends keyof Preferences>(key: K, value: Preferences[K]) => {
    setPreferences(prev => ({ ...prev, [key]: value }));
  }, []);

  const resetPreferences = useCallback(() => {
    setPreferences(defaultPreferences);
  }, []);

  return { preferences, updatePreference, resetPreferences };
};
// --- End Mock usePreferences Hook ---


export const PreferencesPanel: React.FC = () => {
  const { preferences, updatePreference, resetPreferences } = useMockPreferences();

  // Handler for theme, as next-themes might be used elsewhere
  // For this mock, we'll just update our local state.
  // In a real app, this would interact with the theme provider (e.g., next-themes `setTheme`)
  const handleThemeChange = (value: ThemePreference) => {
    updatePreference('theme', value);
    if (typeof document !== 'undefined' && value !== 'system') {
        document.documentElement.classList.remove('light', 'dark');
        document.documentElement.classList.add(value);
    } else if (typeof document !== 'undefined' && value === 'system') {
        // Remove explicit theme to let system preference take over
        document.documentElement.classList.remove('light', 'dark');
        // This requires system detection logic, often handled by next-themes itself.
        // For mock, just removing is fine.
    }
  };

  const SectionTitle: React.FC<{ icon?: React.ElementType, title: string }> = ({ icon: Icon, title }) => (
    <div className="flex items-center space-x-2 mb-3">
      {Icon && <Icon className="w-5 h-5 text-primary" />}
      <h3 className="text-lg font-semibold text-slate-700 dark:text-slate-200">{title}</h3>
    </div>
  );

  return (
    <div className="max-w-2xl mx-auto p-4 sm:p-6 bg-white dark:bg-slate-800 shadow-lg rounded-xl border border-slate-200 dark:border-slate-700">
      <header className="mb-6 flex items-center justify-between">
        <div className='flex items-center space-x-2'>
            <Settings2 className="w-7 h-7 text-primary" />
            <h2 className="text-2xl font-bold text-slate-800 dark:text-slate-100">Preferences</h2>
        </div>
        <Button variant="outline" size="sm" onClick={resetPreferences} className="flex items-center space-x-2">
          <RotateCcw size={14} />
          <span>Reset Defaults</span>
        </Button>
      </header>

      {/* Appearance Section */}
      <section className="mb-8 p-4 border border-slate-200 dark:border-slate-700 rounded-lg">
        <SectionTitle icon={Palette} title="Appearance" />
        <div className="space-y-6">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between">
            <Label htmlFor="theme-select" className="mb-1 sm:mb-0 text-sm font-medium text-slate-600 dark:text-slate-300">
              Theme
            </Label>
            <Select
              value={preferences.theme}
              onValueChange={(value: string) => handleThemeChange(value as ThemePreference)}
            >
              <SelectTrigger id="theme-select" className="w-full sm:w-[180px] bg-slate-50 dark:bg-slate-700">
                <SelectValue placeholder="Select theme" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="light">Light</SelectItem>
                <SelectItem value="dark">Dark</SelectItem>
                <SelectItem value="system">System</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between">
            <Label htmlFor="task-density-select" className="mb-1 sm:mb-0 text-sm font-medium text-slate-600 dark:text-slate-300">
              Task View Density
            </Label>
            <Select
              value={preferences.taskDensity}
              onValueChange={(value: string) => updatePreference('taskDensity', value as TaskDensity)}
            >
              <SelectTrigger id="task-density-select" className="w-full sm:w-[180px] bg-slate-50 dark:bg-slate-700">
                <SelectValue placeholder="Select density" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="compact">Compact</SelectItem>
                <SelectItem value="comfortable">Comfortable</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </section>

      <Separator className="my-6 dark:bg-slate-700" />

      {/* Notifications Section */}
      <section className="mb-8 p-4 border border-slate-200 dark:border-slate-700 rounded-lg">
        <SectionTitle icon={BellRing} title="Notifications" />
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <Label htmlFor="notifications-enabled" className="text-sm font-medium text-slate-600 dark:text-slate-300 cursor-pointer">
                Enable All Notifications
                </Label>
                <Switch
                id="notifications-enabled"
                checked={preferences.notificationsEnabled}
                onCheckedChange={(checked) => updatePreference('notificationsEnabled', checked)}
                />
            </div>
             <div className="flex items-center justify-between">
                <Label htmlFor="notification-sound" className="text-sm font-medium text-slate-600 dark:text-slate-300 cursor-pointer">
                Notification Sounds
                </Label>
                <Switch
                id="notification-sound"
                checked={preferences.notificationSoundEnabled}
                onCheckedChange={(checked) => updatePreference('notificationSoundEnabled', checked)}
                />
            </div>
        </div>
      </section>

      <Separator className="my-6 dark:bg-slate-700" />

      {/* Advanced Section (Example) */}
      <section className="mb-8 p-4 border border-slate-200 dark:border-slate-700 rounded-lg">
        <SectionTitle icon={ListTree} title="Advanced" />
         <div className="flex items-center justify-between">
            <Label htmlFor="experimental-features" className="text-sm font-medium text-slate-600 dark:text-slate-300 cursor-pointer">
              Show Experimental Features
            </Label>
            <Switch
              id="experimental-features"
              checked={preferences.showExperimentalFeatures}
              onCheckedChange={(checked) => updatePreference('showExperimentalFeatures', checked)}
            />
          </div>
           <div className="mt-4 flex flex-col sm:flex-row sm:items-center sm:justify-between">
            <Label htmlFor="language-select" className="mb-1 sm:mb-0 text-sm font-medium text-slate-600 dark:text-slate-300">
              Language
            </Label>
            <Select
              value={preferences.language}
              onValueChange={(value: string) => updatePreference('language', value)}
            >
              <SelectTrigger id="language-select" className="w-full sm:w-[180px] bg-slate-50 dark:bg-slate-700">
                <SelectValue placeholder="Select language" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="en">English</SelectItem>
                <SelectItem value="es">Español</SelectItem>
                <SelectItem value="fr">Français</SelectItem>
                <SelectItem value="de">Deutsch</SelectItem> {/* German */}
              </SelectContent>
            </Select>
          </div>
      </section>

      <Separator className="my-6 dark:bg-slate-700" />

      {/* Keyboard Shortcuts Section */}
      <section className="p-4 border border-slate-200 dark:border-slate-700 rounded-lg">
        <SectionTitle icon={Keyboard} title="Keyboard Shortcuts" />
        {preferences.keyboardShortcuts.length > 0 ? (
          <ul className="space-y-2 text-sm">
            {preferences.keyboardShortcuts.map((shortcut) => (
              <li key={shortcut.action} className="flex justify-between p-2 bg-slate-50 dark:bg-slate-700/50 rounded-md">
                <span className="text-slate-600 dark:text-slate-300">{shortcut.label}</span>
                <kbd className="px-2 py-1 text-xs font-semibold text-slate-500 dark:text-slate-400 bg-slate-200 dark:bg-slate-600 border border-slate-300 dark:border-slate-500 rounded-md">
                  {shortcut.keys}
                </kbd>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-slate-500 dark:text-slate-400">No keyboard shortcuts defined.</p>
        )}
        <p className="mt-3 text-xs text-center text-slate-400 dark:text-slate-500">
          Shortcut customization coming soon.
        </p>
      </section>

    </div>
  );
};

export default PreferencesPanel;

// Dummy logger for browser environment if not globally available
// const logger = {
//     debug: console.debug,
//     info: console.info,
//     warn: console.warn,
//     error: console.error,
// };
