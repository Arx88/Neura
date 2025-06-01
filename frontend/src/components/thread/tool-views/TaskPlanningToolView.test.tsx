import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import '@testing-library/jest-dom';
import { TaskPlanningToolView } from './TaskPlanningToolView';
import { useTaskManager } from '@/hooks/use-task-manager';
import { Progress } from '@/components/ui/progress'; // For mocking if needed or checking props

// Mock lucide-react icons used directly or indirectly
jest.mock('lucide-react', () => {
  const original = jest.requireActual('lucide-react');
  return {
    ...original,
    CheckCircle: (props: any) => <svg data-testid="icon-checkcircle" {...props} />,
    Loader: (props: any) => <svg data-testid="icon-loader" {...props} />,
    AlertCircle: (props: any) => <svg data-testid="icon-alertcircle" {...props} />,
    CircleDashed: (props: any) => <svg data-testid="icon-circledashed" {...props} />,
    Clock: (props: any) => <svg data-testid="icon-clock" {...props} />,
    ChevronRight: (props: any) => <svg data-testid="icon-chevronright" {...props} />,
    // Mock icons returned by getIconForTool
    Terminal: (props: any) => <svg data-testid="icon-terminal" {...props} />,
    Search: (props: any) => <svg data-testid="icon-search" {...props} />,
    FileText: (props: any) => <svg data-testid="icon-filetext" {...props} />,
    Wrench: (props: any) => <svg data-testid="icon-wrench" {...props} />,
  };
});

// Mock the useTaskManager hook
jest.mock('@/hooks/use-task-manager');

// Mock @/components/ui/progress
jest.mock('@/components/ui/progress', () => ({
  Progress: jest.fn(({ value }) => (
    <div data-testid="progress-bar" data-value={value}>
      Progress: {value}%
    </div>
  )),
}));


const mockUseTaskManager = useTaskManager as jest.MockedFunction<typeof useTaskManager>;

const mockTask = {
  id: 'task-1',
  name: 'Main Test Task',
  description: 'This is the main task description.',
  status: 'running',
  progress: 0.5,
  startTime: Date.now() / 1000 - 3600, // an hour ago
  endTime: null,
};

const mockSubtasksBase = [
  { id: 'subtask-1', name: 'execute-command', description: 'Run the first command.', status: 'completed', progress: 1, startTime: Date.now() / 1000 - 3000, endTime: Date.now() / 1000 - 2000 },
  { id: 'subtask-2', name: 'web-search', description: 'Search for information.', status: 'running', progress: 0.5, startTime: Date.now() / 1000 - 1000, endTime: null },
  { id: 'subtask-3', name: 'read-file', description: 'Read important data.', status: 'pending', progress: 0, startTime: Date.now() / 1000, endTime: null },
  { id: 'subtask-4', name: 'unknown-tool-for-test', description: 'A step with an unknown tool.', status: 'failed', progress: 0, startTime: Date.now() / 1000, endTime: null },
];

const defaultToolViewProps = {
  name: 'TaskPlanningToolView',
  assistantContent: '',
  toolContent: JSON.stringify({ id: 'task-1' }), // Tool content should contain task ID
  isSuccess: true,
  isStreaming: false,
  assistantTimestamp: Date.now(),
  toolTimestamp: Date.now(),
  onFileClick: jest.fn(),
};

describe('TaskPlanningToolView', () => {
  beforeEach(() => {
    mockUseTaskManager.mockReturnValue({
      useTask: jest.fn().mockReturnValue({
        data: mockTask,
        isLoading: false,
        error: null,
      }),
      useSubtasks: jest.fn().mockReturnValue({
        data: mockSubtasksBase,
        isLoading: false,
        error: null,
      }),
    });
     // Reset mock component calls if necessary
     (Progress as jest.Mock).mockClear();
  });

  it('should render the main task details', () => {
    render(<TaskPlanningToolView {...defaultToolViewProps} />);
    expect(screen.getByText('Main Test Task')).toBeInTheDocument();
    expect(screen.getByText('This is the main task description.')).toBeInTheDocument();
    // Check for main task status and progress (simplified)
    expect(screen.getByText(/running/i)).toBeInTheDocument(); // Main task status
  });

  describe('Timeline Rendering', () => {
    it('should render the timeline with the correct number of steps', () => {
      render(<TaskPlanningToolView {...defaultToolViewProps} />);
      const timelineItems = screen.getAllByRole('listitem'); // Each <li> is a timeline item
      expect(timelineItems).toHaveLength(mockSubtasksBase.length);
    });

    it('should display "No hay pasos definidos" when there are no subtasks', () => {
      mockUseTaskManager.mockReturnValue({
        useTask: jest.fn().mockReturnValue({ data: mockTask, isLoading: false, error: null }),
        useSubtasks: jest.fn().mockReturnValue({ data: [], isLoading: false, error: null }),
      });
      render(<TaskPlanningToolView {...defaultToolViewProps} />);
      expect(screen.getByText('No hay pasos definidos para esta tarea.')).toBeInTheDocument();
    });
  });

  describe('Step Details', () => {
    it('renders step status icon, tool label, and tool icon correctly', () => {
      render(<TaskPlanningToolView {...defaultToolViewProps} />);
      // Step 1 (completed, execute-command)
      expect(screen.getByText('Execute Command')).toBeInTheDocument(); // Label from getLabelForTool
      // Check for icons within the context of the first list item
      const firstStepListItem = screen.getAllByRole('listitem')[0];
      expect(firstStepListItem.querySelector('[data-testid="icon-checkcircle"]')).toBeInTheDocument(); // Status icon
      expect(firstStepListItem.querySelector('[data-testid="icon-terminal"]')).toBeInTheDocument(); // Tool icon

      // Step 2 (running, web-search)
      expect(screen.getByText('Web Search')).toBeInTheDocument();
      const secondStepListItem = screen.getAllByRole('listitem')[1];
      expect(secondStepListItem.querySelector('[data-testid="icon-loader"]')).toBeInTheDocument(); // Status icon
      expect(secondStepListItem.querySelector('[data-testid="icon-search"]')).toBeInTheDocument(); // Tool icon

      // Step 4 (failed, unknown-tool-for-test -> Wrench icon)
      expect(screen.getByText('Unknown Tool For Test')).toBeInTheDocument(); // Label
      const fourthStepListItem = screen.getAllByRole('listitem')[3];
      expect(fourthStepListItem.querySelector('[data-testid="icon-alertcircle"]')).toBeInTheDocument(); // Status icon
      expect(fourthStepListItem.querySelector('[data-testid="icon-wrench"]')).toBeInTheDocument(); // Default Tool icon
    });

    it('renders step description with prominence classes', () => {
      render(<TaskPlanningToolView {...defaultToolViewProps} />);
      const descriptionElement = screen.getByText('Run the first command.');
      expect(descriptionElement).toHaveClass('text-base text-zinc-700 dark:text-zinc-300 mt-1');
    });
  });

  describe('Collapsible Sections', () => {
    it('should render collapsible trigger and content for each step', () => {
      render(<TaskPlanningToolView {...defaultToolViewProps} />);
      const triggers = screen.getAllByText('Ver detalles técnicos');
      expect(triggers.length).toBe(mockSubtasksBase.length);
      // Content is initially not visible by default in Radix Collapsible,
      // so we can't easily query for it without interacting.
      // We can check if the trigger has the correct aria-expanded attribute.
      triggers.forEach(trigger => {
        expect(trigger.closest('button')).toHaveAttribute('aria-expanded', 'false');
      });
    });

    it('should show content on trigger click and display stringified step', () => {
      render(<TaskPlanningToolView {...defaultToolViewProps} />);
      const firstTrigger = screen.getAllByText('Ver detalles técnicos')[0];
      fireEvent.click(firstTrigger.closest('button')!);

      expect(firstTrigger.closest('button')).toHaveAttribute('aria-expanded', 'true');
      // Technical details for the first subtask
      const expectedJson = JSON.stringify(mockSubtasksBase[0], null, 2);
      // The <pre> tag will contain the JSON.
      // We need to find the <pre> tag associated with the first subtask.
      // This assumes the content is rendered immediately after the trigger's parent collapsible.
      const preElement = firstTrigger.closest('li')?.querySelector('pre');
      expect(preElement).toHaveTextContent(expectedJson);
    });
  });

  describe('General Progress Bar', () => {
    it('should render the progress bar with correct values', () => {
      render(<TaskPlanningToolView {...defaultToolViewProps} />);
      // completedSteps = 1, totalSteps = 4. progressPercentage = 25
      expect(screen.getByText('Progreso de la Tarea')).toBeInTheDocument();

      const progressBar = screen.getByTestId('progress-bar');
      expect(progressBar).toBeInTheDocument();
      expect(progressBar).toHaveAttribute('data-value', '25'); // 1 out of 4 completed

      expect(screen.getByText('1 de 4 pasos completados (25%)')).toBeInTheDocument();
    });

    it('should handle zero total steps for progress bar', () => {
       mockUseTaskManager.mockReturnValue({
        useTask: jest.fn().mockReturnValue({ data: mockTask, isLoading: false, error: null }),
        useSubtasks: jest.fn().mockReturnValue({ data: [], isLoading: false, error: null }),
      });
      render(<TaskPlanningToolView {...defaultToolViewProps} />);
      // The progress bar section should not render if there are no subtasks
      expect(screen.queryByText('Progreso de la Tarea')).not.toBeInTheDocument();
      expect(screen.queryByTestId('progress-bar')).not.toBeInTheDocument();
    });

     it('should show 100% progress if all tasks completed', () => {
      const allCompletedSubtasks = mockSubtasksBase.map(st => ({ ...st, status: 'completed', progress: 1 }));
      mockUseTaskManager.mockReturnValue({
        useTask: jest.fn().mockReturnValue({ data: mockTask, isLoading: false, error: null }),
        useSubtasks: jest.fn().mockReturnValue({ data: allCompletedSubtasks, isLoading: false, error: null }),
      });
      render(<TaskPlanningToolView {...defaultToolViewProps} />);

      const progressBar = screen.getByTestId('progress-bar');
      expect(progressBar).toHaveAttribute('data-value', '100');
      expect(screen.getByText('4 de 4 pasos completados (100%)')).toBeInTheDocument();
    });
  });

  // Test for streaming state (optional, based on current implementation)
  it('should show streaming state if isStreaming is true', () => {
    render(<TaskPlanningToolView {...defaultToolViewProps} isStreaming={true} />);
    expect(screen.getByText('Planificando tareas... Por favor, espera.')).toBeInTheDocument();
  });

  // Test for error state
   it('should show error message if task loading fails', () => {
    mockUseTaskManager.mockReturnValue({
      useTask: jest.fn().mockReturnValue({ data: null, isLoading: false, error: new Error("Failed to load task") }),
      useSubtasks: jest.fn().mockReturnValue({ data: [], isLoading: false, error: null }),
    });
    render(<TaskPlanningToolView {...defaultToolViewProps} toolContent={JSON.stringify({ id: 'task-error' })} />);
    expect(screen.getByText(/Error al cargar la tarea: Failed to load task/i)).toBeInTheDocument();
  });

});
