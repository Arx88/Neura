import unittest
from unittest.mock import AsyncMock, MagicMock, call

# The function to test
from sandbox.sandbox import setup_visualization_environment, SessionExecuteRequest
# We need to mock the Sandbox object that setup_visualization_environment expects
from daytona_sdk.sandbox import Sandbox 
from utils.logger import logger # Assuming logger is used

# Disable logging for tests to keep output clean
logger.disabled = True

class TestSetupVisualizationEnvironment(unittest.IsolatedAsyncioTestCase):

    async def test_setup_visualization_environment_success(self):
        sandbox_mock = MagicMock(spec=Sandbox)
        sandbox_mock.process = AsyncMock()
        
        # Mock responses for session commands
        sandbox_mock.process.create_session = AsyncMock()
        sandbox_mock.process.execute_session_command = AsyncMock()
        sandbox_mock.process.delete_session = AsyncMock()

        await setup_visualization_environment(sandbox_mock)

        # Verify session creation
        sandbox_mock.process.create_session.assert_called_once_with("viz_setup_session")
        
        # Verify command executions
        expected_pip_command = "pip install matplotlib pandas seaborn plotly"
        expected_mkdir_command = "mkdir -p /workspace/visualizations"
        
        calls = [
            call("viz_setup_session", SessionExecuteRequest(command=expected_pip_command)),
            call("viz_setup_session", SessionExecuteRequest(command=expected_mkdir_command))
        ]
        sandbox_mock.process.execute_session_command.assert_has_calls(calls, any_order=False)
        
        # Verify session deletion
        sandbox_mock.process.delete_session.assert_called_once_with("viz_setup_session")

    async def test_setup_visualization_environment_pip_install_fails(self):
        sandbox_mock = MagicMock(spec=Sandbox)
        sandbox_mock.process = AsyncMock()

        sandbox_mock.process.create_session = AsyncMock()
        # Simulate pip install failing
        sandbox_mock.process.execute_session_command = AsyncMock(
            side_effect=[
                Exception("Pip install error"), # First call (pip) throws error
                AsyncMock() # Second call (mkdir) would be normal if not for the error
            ]
        )
        sandbox_mock.process.delete_session = AsyncMock()

        # Expect the function to catch the exception and log it, but still delete session
        # Depending on implementation, it might re-raise or just log.
        # The current implementation in sandbox.py logs the error and continues to finally.
        await setup_visualization_environment(sandbox_mock)
            
        # Verify session creation
        sandbox_mock.process.create_session.assert_called_once_with("viz_setup_session")
        
        # Verify pip command was attempted
        expected_pip_command = "pip install matplotlib pandas seaborn plotly"
        sandbox_mock.process.execute_session_command.assert_any_call(
            "viz_setup_session", SessionExecuteRequest(command=expected_pip_command)
        )
        # mkdir might not be called if pip install fails and the error is re-raised or not caught properly.
        # Given current implementation, it proceeds. Let's check if it was called.
        expected_mkdir_command = "mkdir -p /workspace/visualizations"
        # This assertion will fail if the first execute_session_command raises and is not handled inside the try block
        # In the actual code, the exception is caught and logged.

        # So, the second command should NOT have been called if the first one failed and propagated
        # However, the provided code catches the exception from execute_session_command.
        # Let's verify execute_session_command was called for pip, and then for mkdir.
        self.assertEqual(sandbox_mock.process.execute_session_command.call_count, 1)


        # Verify session deletion (most important in finally block)
        sandbox_mock.process.delete_session.assert_called_once_with("viz_setup_session")

    async def test_setup_visualization_environment_session_delete_fails(self):
        sandbox_mock = MagicMock(spec=Sandbox)
        sandbox_mock.process = AsyncMock()

        sandbox_mock.process.create_session = AsyncMock()
        sandbox_mock.process.execute_session_command = AsyncMock() # All commands succeed
        sandbox_mock.process.delete_session = AsyncMock(side_effect=Exception("Session delete error"))

        # The function should catch and log the error from delete_session
        await setup_visualization_environment(sandbox_mock)
            
        sandbox_mock.process.create_session.assert_called_once()
        self.assertEqual(sandbox_mock.process.execute_session_command.call_count, 2) # pip and mkdir
        sandbox_mock.process.delete_session.assert_called_once_with("viz_setup_session")
        # Error during deletion is caught and logged, not re-raised.

if __name__ == '__main__':
    unittest.main()
