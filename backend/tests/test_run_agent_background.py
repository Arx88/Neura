import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
import uuid
import json

from backend.run_agent_background import run_agent_background, _cleanup_redis_instance_key, _cleanup_redis_response_list, update_agent_run_status # Import the target function
from daytona_api_client.models.workspace_state import WorkspaceState
from daytona_sdk import SessionExecuteRequest

# Mock logger at the module level where run_agent_background would find it
logger_mock = MagicMock()

# Mock redis at the module level
redis_mock = MagicMock()
redis_mock.create_pubsub = AsyncMock()
redis_mock.rpush = AsyncMock()
redis_mock.publish = AsyncMock()
redis_mock.expire = AsyncMock()
redis_mock.delete = AsyncMock()
redis_mock.lrange = AsyncMock(return_value=[]) # Default to empty list for responses
redis_mock.set = AsyncMock()


# Mock DBConnection and its client
db_mock = MagicMock()
db_mock.client = AsyncMock()

# Mock Langfuse
langfuse_mock = MagicMock()
langfuse_mock.trace = MagicMock(return_value=MagicMock(span=MagicMock(return_value=MagicMock(end=MagicMock()))))


@patch('backend.run_agent_background.logger', logger_mock)
@patch('backend.run_agent_background.redis', redis_mock)
@patch('backend.run_agent_background.db', db_mock) # Patch the global db instance used by the actor
@patch('backend.run_agent_background.langfuse', langfuse_mock)
class TestRunAgentBackgroundCleanup(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        logger_mock.reset_mock()
        redis_mock.reset_mock()
        db_mock.client.reset_mock() # Reset the supabase client mock part of db_mock
        langfuse_mock.reset_mock()

        self.mock_sandbox_instance = AsyncMock()
        self.mock_sandbox_instance.process = AsyncMock()
        self.mock_sandbox_instance.process.create_session = AsyncMock()
        self.mock_sandbox_instance.process.execute_session_command = AsyncMock()
        self.mock_sandbox_instance.process.get_session_command_logs = AsyncMock(return_value="Mocked logs")
        self.mock_sandbox_instance.process.delete_session = AsyncMock()
        self.mock_sandbox_instance.info = MagicMock(return_value=MagicMock(state=WorkspaceState.RUNNING))

        self.mock_daytona_stop = AsyncMock()

        # Patch the specific functions/modules used by run_agent_background's finally block
        self.patch_get_or_start_sandbox = patch('backend.sandbox.sandbox.get_or_start_sandbox', AsyncMock(return_value=self.mock_sandbox_instance))
        self.patch_daytona = patch('backend.sandbox.sandbox.daytona', MagicMock(stop=self.mock_daytona_stop))
        
        self.mock_get_or_start_sandbox = self.patch_get_or_start_sandbox.start()
        self.mock_daytona_client_for_stop = self.patch_daytona.start() # This mocks the 'daytona' object from sandbox.sandbox

        # Mock uuid to control session_id predictability if needed, though not strictly necessary for these tests
        self.patch_uuid = patch('uuid.uuid4', MagicMock(return_value=MagicMock(hex='testhex')))
        self.mock_uuid = self.patch_uuid.start()
        
        # Default successful project and sandbox ID lookup
        self.mock_project_result = MagicMock()
        self.mock_project_result.data = {'sandbox': {'id': 'test_sandbox_id_123'}}
        db_mock.client.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(return_value=self.mock_project_result)

    def tearDown(self):
        self.patch_get_or_start_sandbox.stop()
        self.patch_daytona.stop()
        self.patch_uuid.stop()

    async def run_agent_to_finally(self, mock_run_agent_gen):
        """Helper to run the agent background task until the finally block is executed."""
        with patch('backend.run_agent_background.run_agent', mock_run_agent_gen):
            await run_agent_background(
                agent_run_id="test_agent_run_id",
                thread_id="test_thread_id",
                instance_id="test_instance_id",
                project_id="test_project_id",
                model_name="test_model",
                enable_thinking=False,
                reasoning_effort='low',
                stream=False,
                enable_context_manager=False
            )

    async def test_successful_cleanup_and_stop(self):
        # Simulate run_agent generator finishing normally (e.g., yielding one status message)
        async def mock_successful_agent_gen(*args, **kwargs):
            yield {"type": "status", "status": "completed", "message": "Run completed"}
            # The generator must actually finish for the main loop in run_agent_background to exit
            # and proceed to the finally block.

        self.mock_sandbox_instance.process.execute_session_command.return_value = MagicMock(exit_code=0, cmd_id="cmd1")
        
        await self.run_agent_to_finally(mock_successful_agent_gen)

        # Verify sandbox ID fetch
        db_mock.client.table.assert_called_with('projects')
        db_mock.client.table.return_value.select.assert_called_with('sandbox')
        db_mock.client.table.return_value.select.return_value.eq.assert_called_with('project_id', 'test_project_id')

        # Verify get_or_start_sandbox called
        self.mock_get_or_start_sandbox.assert_called_once_with('test_sandbox_id_123')

        # Verify cleanup session creation
        self.mock_sandbox_instance.process.create_session.assert_called_once_with(f"cleanup_ws_testhex")
        
        # Verify cleanup commands
        expected_cleanup_commands = [
            "find /workspace -type f -name '*.tmp' -print -delete",
            "find /workspace -type f -name 'temp_*' -print -delete",
            "find /workspace -type f -name '*_temp.*' -print -delete",
            "find /workspace -depth -type d -empty -print -delete"
        ]
        
        execute_calls = self.mock_sandbox_instance.process.execute_session_command.call_args_list
        self.assertEqual(len(execute_calls), len(expected_cleanup_commands))
        
        for i, cmd_text in enumerate(expected_cleanup_commands):
            # call_args is a tuple (args, kwargs)
            actual_session_id, actual_request_obj = execute_calls[i][0] # First arg is session_id, second is request_obj
            self.assertEqual(actual_session_id, f"cleanup_ws_testhex")
            self.assertIsInstance(actual_request_obj, SessionExecuteRequest)
            self.assertEqual(actual_request_obj.command, cmd_text)

        # Verify cleanup session deletion
        self.mock_sandbox_instance.process.delete_session.assert_called_once_with(f"cleanup_ws_testhex")

        # Verify sandbox stop
        self.mock_sandbox_instance.info.assert_called_once() # To get current_state
        self.mock_daytona_stop.assert_called_once_with(self.mock_sandbox_instance)
        logger_mock.info.assert_any_call("Successfully sent stop command to sandbox test_sandbox_id_123")


    async def test_cleanup_command_fails_then_stop(self):
        async def mock_agent_gen(*args, **kwargs):
            yield {"type": "status", "status": "completed"}
        
        # First command success, second fails, rest success
        self.mock_sandbox_instance.process.execute_session_command.side_effect = [
            MagicMock(exit_code=0, cmd_id="cmd1"),
            MagicMock(exit_code=1, cmd_id="cmd2_fail"), # This one fails
            MagicMock(exit_code=0, cmd_id="cmd3"),
            MagicMock(exit_code=0, cmd_id="cmd4"),
        ]

        await self.run_agent_to_finally(mock_agent_gen)

        self.assertEqual(self.mock_sandbox_instance.process.execute_session_command.call_count, 4)
        logger_mock.warning.assert_any_call(unittest.mock.ANY, # Check for the warning log for the failed command
            "Cleanup command 'find /workspace -type f -name 'temp_*' -print -delete' failed. Exit: 1. Logs: Mocked logs"
        )
        self.mock_sandbox_instance.process.delete_session.assert_called_once_with(f"cleanup_ws_testhex")
        self.mock_daytona_stop.assert_called_once_with(self.mock_sandbox_instance)


    async def test_error_creating_cleanup_session_then_stop(self):
        async def mock_agent_gen(*args, **kwargs):
            yield {"type": "status", "status": "completed"}

        self.mock_sandbox_instance.process.create_session.side_effect = Exception("Session creation error")
        
        await self.run_agent_to_finally(mock_agent_gen)

        logger_mock.error.assert_any_call(
            "Error during workspace cleanup for sandbox test_sandbox_id_123: Session creation error",
            exc_info=True
        )
        # execute_session_command should not be called if session creation fails
        self.mock_sandbox_instance.process.execute_session_command.assert_not_called()
        # delete_session for cleanup might still be called in its own finally, depending on how it's structured
        # The current code calls delete_session with cleanup_session_id which might not be defined if create_session failed.
        # The provided snippet has `sandbox_instance_for_cleanup.process.delete_session(cleanup_session_id)`
        # which would raise NameError if cleanup_session_id is not defined.
        # This needs to be robust in the main code.
        # For now, assuming the structure from the prompt which has its own try/except for delete_session.
        # The prompt's finally for delete_session has `except Exception: pass`.

        # Crucially, sandbox stop should still be attempted
        self.mock_daytona_stop.assert_called_once_with(self.mock_sandbox_instance)


    async def test_no_sandbox_id_found_skips_cleanup_and_stop(self):
        async def mock_agent_gen(*args, **kwargs):
            yield {"type": "status", "status": "completed"}

        # Simulate no sandbox ID found
        self.mock_project_result.data = None # Or {'sandbox': None} or {}
        db_mock.client.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute = AsyncMock(return_value=self.mock_project_result)
        
        await self.run_agent_to_finally(mock_agent_gen)

        logger_mock.warning.assert_any_call("No sandbox_id found for project test_project_id; skipping workspace cleanup and stop.")
        self.mock_get_or_start_sandbox.assert_not_called()
        self.mock_sandbox_instance.process.create_session.assert_not_called()
        self.mock_daytona_stop.assert_not_called()


if __name__ == "__main__":
    unittest.main()
