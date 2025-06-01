// Archivo: task-tool-interface.ts
import { tasksApi } from './tasks-api';
import { TaskState } from '@/types/tasks';

// Interfaz para la comunicación con el ordenador
export interface TaskToolInterface {
  planTask: (description: string, context?: Record<string, any>) => Promise<TaskState>;
  getTask: (taskId: string) => Promise<TaskState>;
  updateTaskProgress: (taskId: string, progress: number, status?: string) => Promise<TaskState>;
  completeTask: (taskId: string, result?: any) => Promise<TaskState>;
  failTask: (taskId: string, error: string) => Promise<TaskState>;
}

// Implementación de la interfaz que utiliza la API de tareas existente
export const taskToolInterface: TaskToolInterface = {
  // Planificar una nueva tarea y mostrarla en el ordenador
  async planTask(description: string, context?: Record<string, any>): Promise<TaskState> {
    try {
      const plannedTask = await tasksApi.planTask(description, context);
      // Aquí es donde el ordenador podría ser notificado o la vista actualizada.
      // Por ahora, solo devolvemos la tarea planificada.
      console.log('Tarea planificada a través de la interfaz:', plannedTask);
      return plannedTask;
    } catch (error) {
      console.error('Error al planificar tarea vía interfaz:', error);
      throw error;
    }
  },

  // Obtener detalles de una tarea para mostrarla en el ordenador
  async getTask(taskId: string): Promise<TaskState> {
    try {
      const task = await tasksApi.getTask(taskId);
      console.log(`Tarea obtenida vía interfaz ${taskId}:`, task);
      return task;
    } catch (error) {
      console.error(`Error al obtener tarea ${taskId} vía interfaz:`, error);
      throw error;
    }
  },

  // Actualizar el progreso de una tarea y reflejarlo en el ordenador
  async updateTaskProgress(taskId: string, progress: number, status?: string): Promise<TaskState> {
    try {
      const updates: Partial<TaskState> = { progress };
      if (status) {
        updates.status = status;
      }
      const updatedTask = await tasksApi.updateTask(taskId, updates);
      console.log(`Progreso de tarea ${taskId} actualizado vía interfaz:`, updatedTask);
      return updatedTask;
    } catch (error) {
      console.error(`Error al actualizar progreso de tarea ${taskId} vía interfaz:`, error);
      throw error;
    }
  },

  // Marcar una tarea como completada y actualizar su visualización en el ordenador
  async completeTask(taskId: string, result?: any): Promise<TaskState> {
    try {
      const updates: Partial<TaskState> = {
        status: 'completed',
        progress: 1.0,
      };
      if (result !== undefined) {
        updates.result = result;
      }
      const completedTask = await tasksApi.updateTask(taskId, updates);
      console.log(`Tarea ${taskId} completada vía interfaz:`, completedTask);
      return completedTask;
    } catch (error) {
      console.error(`Error al completar tarea ${taskId} vía interfaz:`, error);
      throw error;
    }
  },

  // Marcar una tarea como fallida y mostrar el error en el ordenador
  async failTask(taskId: string, errorMsg: string): Promise<TaskState> { // Renamed 'error' to 'errorMsg' to avoid conflict
    try {
      const updates: Partial<TaskState> = {
        status: 'failed',
        error: errorMsg, // Use the passed error message
      };
      const failedTask = await tasksApi.updateTask(taskId, updates);
      console.log(`Tarea ${taskId} marcada como fallida vía interfaz:`, failedTask);
      return failedTask;
    } catch (err) { // Renamed to 'err' to avoid conflict with outer scope 'error' if any
      console.error(`Error al marcar tarea ${taskId} como fallida vía interfaz:`, err);
      throw err;
    }
  },
};

// Exportar la interfaz para su uso en componentes de ordenador
export default taskToolInterface;
