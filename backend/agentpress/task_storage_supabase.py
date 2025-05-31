from typing import List, Optional, Dict, Any
import json
# Removed APIError import
from services.supabase import DBConnection
from agentpress.task_types import TaskState, TaskStorage
from utils.logger import logger

class SupabaseTaskStorage(TaskStorage):
    """
    Supabase implementation for TaskStorage.
    Persists tasks in a Supabase PostgreSQL database.
    """

    def __init__(self, db_connection: Optional[DBConnection] = None):
        self.db_connection = db_connection if db_connection else DBConnection()
        self._table_name = "tasks"
        logger.info("SupabaseTaskStorage initialized.")

    def _to_db_format(self, task: TaskState) -> Dict[str, Any]:
        """Converts TaskState object to a dictionary suitable for Supabase."""
        db_dict = task.__dict__.copy()
        # JSONB fields need to be dumped if they are complex objects,
        # though Supabase client might handle dicts for JSONB automatically.
        # Ensuring they are JSON strings if that's preferred or causes issues.
        for field in ["subtasks", "dependencies", "assignedTools", "artifacts", "metadata", "result"]:
            if field in db_dict and db_dict[field] is not None:
                # The client should handle Python dicts to JSONB correctly.
                # If direct dicts cause issues, uncomment json.dumps:
                # db_dict[field] = json.dumps(db_dict[field])
                pass
        return db_dict

    def _from_db_format(self, data: Dict[str, Any]) -> TaskState:
        """Converts a dictionary from Supabase to a TaskState object."""
        # Ensure all fields expected by TaskState are present, fill with defaults if not.
        # This is mostly handled by TaskState's default_factory or defaults.

        # Supabase might return JSON fields as strings; ensure they are parsed.
        # However, python-supabase typically returns dicts for JSONB.
        # for field in ["subtasks", "dependencies", "assignedTools", "artifacts", "metadata", "result"]:
        #     if field in data and isinstance(data[field], str):
        #         try:
        #             data[field] = json.loads(data[field])
        #         except json.JSONDecodeError:
        #             logger.warning(f"Failed to parse JSON string for field {field} in task {data.get('id')}")
        #             # Keep as string or set to default? For now, keep potentially malformed string.

        # Convert dict to TaskState. This will use __init__ of TaskState.
        # Ensure all keys match TaskState attributes.
        # The `id` field is `default_factory=str(uuid.uuid4())`, but when loading, we use the db `id`.
        return TaskState(**data)


    async def save_task(self, task: TaskState) -> None:
        """Saves or updates a task in Supabase."""
        client = await self.db_connection.client
        task_dict = self._to_db_format(task)

        try:
            # Upsert operation: inserts if id doesn't exist, updates if it does.
            # 'id' is the primary key and should be used for conflict resolution.
            response = await client.table(self._table_name).upsert(task_dict).execute()
            # Assuming execute() raises an exception on error, response.data should be valid if no exception.
            if response.data:
                logger.info(f"Task {task.id} saved/updated successfully.")
            else:
                # This case might indicate an issue with RLS or if upsert doesn't return data by default
                # depending on preference settings (e.g. return="minimal").
                # If data is critical, this might need specific handling or ensuring preferences demand data.
                logger.warning(f"Supabase upsert for task {task.id} returned no data. Response: {response}")
        except Exception as e: # Changed from APIError to Exception
            logger.error(f"Supabase client operation failed with error type: {type(e).__name__} - {e}", exc_info=True)
            raise # Re-raise other errors

    async def load_task(self, task_id: str) -> Optional[TaskState]:
        """Loads a specific task by its ID from Supabase."""
        client = await self.db_connection.client
        try:
            response = await client.table(self._table_name).select("*").eq("id", task_id).maybe_single().execute()
            if response.data:
                return self._from_db_format(response.data)
            # No data and no error from maybe_single() means task not found.
            return None
        except Exception as e: # Changed from APIError to Exception
            logger.error(f"Supabase client operation failed with error type: {type(e).__name__} - {e}", exc_info=True)
            raise

    async def load_all_tasks(self) -> List[TaskState]:
        """Loads all tasks from Supabase."""
        client = await self.db_connection.client
        try:
            response = await client.table(self._table_name).select("*").execute()
            if response.data:
                return [self._from_db_format(item) for item in response.data]
            # If no data and no error, it's an empty list, not an error.
            return []
        except Exception as e: # Changed from APIError to Exception
            logger.error(f"Supabase client operation failed with error type: {type(e).__name__} - {e}", exc_info=True)
            raise

    async def delete_task(self, task_id: str) -> None:
        """Deletes a task by its ID from Supabase."""
        client = await self.db_connection.client
        try:
            # Delete operation: execute() will raise an error if deletion fails.
            # Successful deletion might not return data, depending on Supabase settings (e.g. return="minimal").
            await client.table(self._table_name).delete().eq("id", task_id).execute()
            # If execute() does not raise, assume success.
            logger.info(f"Task {task_id} deleted (or did not exist).")
        except Exception as e: # Changed from APIError to Exception
            logger.error(f"Supabase client operation failed with error type: {type(e).__name__} - {e}", exc_info=True)
            raise

    async def update_task(self, task_id: str, updates: Dict[str, Any]) -> Optional[TaskState]:
        """
        Atomically updates a task in Supabase using its update method.
        Note: This assumes 'updates' contains keys that match column names.
        The TaskState dataclass field names (camelCase for some) need to match DB column names (snake_case or quoted camelCase).
        The migration used quoted camelCase for "startTime", "endTime", "parentId", "assignedTools".
        """
        client = await self.db_connection.client

        # Ensure endTime is handled correctly based on status
        if updates.get("status") in ["completed", "failed", "cancelled"] and "endTime" not in updates:
            updates["endTime"] = DBConnection.now_iso() # Use DBConnection helper for consistent time

        # The keys in 'updates' must match the database column names.
        # If TaskState uses camelCase and DB uses snake_case, mapping is needed here
        # or ensure DB columns match TaskState fields (migration uses quoted camelCase for some).
        # For this implementation, we assume `updates` keys are already correct for the DB.

        db_updates = updates.copy()

        # Ensure JSONB fields are correctly formatted if necessary, though client usually handles dicts.
        # for field in ["subtasks", "dependencies", "assignedTools", "artifacts", "metadata", "result"]:
        #     if field in db_updates and isinstance(db_updates[field], (list, dict)):
        #         db_updates[field] = json.dumps(db_updates[field])

        try:
            response = await client.table(self._table_name).update(db_updates).eq("id", task_id).execute()
            if response.data:
                logger.info(f"Task {task_id} updated via Supabase direct update.")
                return self._from_db_format(response.data[0]) # Assuming update returns the updated record
            else:
                # This could happen if the task_id doesn't exist or RLS prevents viewing.
                # Or if PostgREST preference is `return=minimal`.
                logger.warning(f"Update for task {task_id} returned no data. Task may not exist or check PostgREST preferences.")
                return None
        except Exception as e: # Changed from APIError to Exception
            logger.error(f"Supabase client operation failed with error type: {type(e).__name__} - {e}", exc_info=True)
            raise

    async def get_tasks_by_status(self, status: str) -> List[TaskState]:
        """Loads all tasks with a specific status."""
        client = await self.db_connection.client
        try:
            response = await client.table(self._table_name).select("*").eq("status", status).execute()
            if response.data:
                return [self._from_db_format(item) for item in response.data]
            return [] # No tasks found with this status
        except Exception as e: # Changed from APIError to Exception
            logger.error(f"Supabase client operation failed with error type: {type(e).__name__} - {e}", exc_info=True)
            raise

    async def get_subtasks(self, parent_id: str) -> List[TaskState]:
        """Loads all subtasks for a given parent ID."""
        client = await self.db_connection.client
        try:
            response = await client.table(self._table_name).select("*").eq("parentId", parent_id).execute()
            if response.data:
                return [self._from_db_format(item) for item in response.data]
            return [] # No subtasks found for this parent
        except Exception as e: # Changed from APIError to Exception
            logger.error(f"Supabase client operation failed with error type: {type(e).__name__} - {e}", exc_info=True)
            raise
