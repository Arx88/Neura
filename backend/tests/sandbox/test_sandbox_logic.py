import unittest
from unittest.mock import patch, MagicMock, ANY, call, AsyncMock
import asyncio

# Import the items to be tested
from backend.sandbox.sandbox import use_daytona, get_or_start_sandbox, create_sandbox

# Attempt to import WorkspaceState for Daytona tests
try:
    from daytona_api_client.models.workspace_state import WorkspaceState
except ImportError:
    # Create a dummy WorkspaceState if daytona_api_client is not available in test env
    class DummyWorkspaceState:
        STOPPED = "STOPPED"
        ARCHIVED = "ARCHIVED"
        RUNNING = "RUNNING" # Add other states if needed by tests
    WorkspaceState = DummyWorkspaceState()


class TestSandboxLogic(unittest.IsolatedAsyncioTestCase):

    # --- Tests for use_daytona ---
    @patch('backend.sandbox.sandbox.config')
    def test_use_daytona_true_when_all_set(self, mock_config):
        mock_config.DAYTONA_API_KEY = "test_key"
        mock_config.DAYTONA_SERVER_URL = "http://test.server"
        mock_config.DAYTONA_TARGET = "test_target"
        self.assertTrue(use_daytona())

    @patch('backend.sandbox.sandbox.config')
    def test_use_daytona_false_if_api_key_missing(self, mock_config):
        mock_config.DAYTONA_API_KEY = None
        mock_config.DAYTONA_SERVER_URL = "http://test.server"
        mock_config.DAYTONA_TARGET = "test_target"
        self.assertFalse(use_daytona())

    @patch('backend.sandbox.sandbox.config')
    def test_use_daytona_false_if_server_url_missing(self, mock_config):
        mock_config.DAYTONA_API_KEY = "test_key"
        mock_config.DAYTONA_SERVER_URL = "" # Empty string
        mock_config.DAYTONA_TARGET = "test_target"
        self.assertFalse(use_daytona())

    @patch('backend.sandbox.sandbox.config')
    def test_use_daytona_false_if_target_missing(self, mock_config):
        mock_config.DAYTONA_API_KEY = "test_key"
        mock_config.DAYTONA_SERVER_URL = "http://test.server"
        mock_config.DAYTONA_TARGET = None
        self.assertFalse(use_daytona())

    # --- Tests for get_or_start_sandbox ---

    @patch('backend.sandbox.sandbox.start_supervisord_session') # Mock helper
    @patch('backend.sandbox.sandbox.logger')
    @patch('backend.sandbox.sandbox.daytona') # Mock daytona client instance
    @patch('backend.sandbox.sandbox.use_daytona') # Mock the function itself
    async def test_get_or_start_sandbox_daytona_running(self, mock_use_daytona_func, mock_daytona_client, mock_logger, mock_start_supervisord):
        mock_use_daytona_func.return_value = True
        
        mock_daytona_sandbox_instance = MagicMock()
        mock_daytona_sandbox_instance.instance.state = WorkspaceState.RUNNING
        mock_daytona_client.get_current_sandbox.return_value = mock_daytona_sandbox_instance

        sandbox_id = "daytona-test-id"
        result = await get_or_start_sandbox(sandbox_id)

        mock_daytona_client.get_current_sandbox.assert_called_once_with(sandbox_id)
        mock_daytona_client.start.assert_not_called()
        mock_start_supervisord.assert_not_called()
        self.assertEqual(result, mock_daytona_sandbox_instance)
        mock_logger.info.assert_any_call(f"Getting or starting sandbox with ID: {sandbox_id}")
        mock_logger.info.assert_any_call("Using Daytona for sandbox operations")

    @patch('backend.sandbox.sandbox.start_supervisord_session')
    @patch('backend.sandbox.sandbox.logger')
    @patch('backend.sandbox.sandbox.daytona')
    @patch('backend.sandbox.sandbox.use_daytona')
    async def test_get_or_start_sandbox_daytona_stopped_starts_it(self, mock_use_daytona_func, mock_daytona_client, mock_logger, mock_start_supervisord):
        mock_use_daytona_func.return_value = True
        
        initial_mock_sandbox = MagicMock()
        initial_mock_sandbox.instance.state = WorkspaceState.STOPPED
        
        started_mock_sandbox = MagicMock() # Simulate state refresh after start
        started_mock_sandbox.instance.state = WorkspaceState.RUNNING

        # get_current_sandbox will be called twice: once initially, once after start
        mock_daytona_client.get_current_sandbox.side_effect = [initial_mock_sandbox, started_mock_sandbox]
        mock_daytona_client.start = AsyncMock() # daytona.start is likely async if get_or_start is

        sandbox_id = "daytona-stopped-id"
        result = await get_or_start_sandbox(sandbox_id)

        mock_daytona_client.get_current_sandbox.assert_has_calls([call(sandbox_id), call(sandbox_id)])
        mock_daytona_client.start.assert_called_once_with(initial_mock_sandbox)
        mock_start_supervisord.assert_called_once_with(started_mock_sandbox)
        self.assertEqual(result, started_mock_sandbox)

    @patch('backend.sandbox.sandbox.logger')
    @patch('backend.sandbox.sandbox.local_sandbox') # Mock local_sandbox object
    @patch('backend.sandbox.sandbox.use_daytona')
    async def test_get_or_start_sandbox_local_exists_running(self, mock_use_daytona_func, mock_ls_object, mock_logger):
        mock_use_daytona_func.return_value = False # Use local sandbox
        
        mock_local_sandbox_instance = {
            'id': 'local-id-running',
            'container': MagicMock(),
            'instance': {'state': 'running'}, # local_sandbox uses dicts
            'info': MagicMock(return_value={'state': 'running'})
        }
        mock_ls_object.get_current_sandbox.return_value = mock_local_sandbox_instance

        sandbox_id = "local-id-running"
        result = await get_or_start_sandbox(sandbox_id)

        mock_ls_object.get_current_sandbox.assert_called_once_with(sandbox_id)
        mock_ls_object.start.assert_not_called()
        self.assertEqual(result, mock_local_sandbox_instance)
        mock_logger.info.assert_any_call("Using local sandbox for operations")

    @patch('backend.sandbox.sandbox.logger')
    @patch('backend.sandbox.sandbox.local_sandbox')
    @patch('backend.sandbox.sandbox.use_daytona')
    async def test_get_or_start_sandbox_local_exists_exited_starts_it(self, mock_use_daytona_func, mock_ls_object, mock_logger):
        mock_use_daytona_func.return_value = False
        
        initial_local_sandbox = {
            'id': 'local-id-exited',
            'container': MagicMock(),
            'instance': {'state': 'exited'},
            'info': MagicMock(return_value={'state': 'exited'})
        }
        started_local_sandbox = {**initial_local_sandbox, 'instance': {'state': 'running'}}

        mock_ls_object.get_current_sandbox.return_value = initial_local_sandbox
        mock_ls_object.start.return_value = started_local_sandbox # start returns the started sandbox

        sandbox_id = "local-id-exited"
        result = await get_or_start_sandbox(sandbox_id)

        mock_ls_object.get_current_sandbox.assert_called_once_with(sandbox_id)
        mock_ls_object.start.assert_called_once_with(initial_local_sandbox)
        self.assertEqual(result, started_local_sandbox)

    @patch('backend.sandbox.sandbox.logger')
    @patch('backend.sandbox.sandbox.local_sandbox')
    @patch('backend.sandbox.sandbox.use_daytona')
    async def test_get_or_start_sandbox_local_not_found_creates_new(self, mock_use_daytona_func, mock_ls_object, mock_logger):
        mock_use_daytona_func.return_value = False
        # Simulate local_sandbox.get_current_sandbox raising an error (e.g., docker.errors.NotFound)
        mock_ls_object.get_current_sandbox.side_effect = Exception("Simulated Docker Not Found")
        
        created_local_sandbox = {
            'id': 'new-local-id',
            'container': MagicMock(),
            'instance': {'state': 'running'},
            'info': MagicMock(return_value={'state': 'running'})
        }
        mock_ls_object.create.return_value = created_local_sandbox

        sandbox_id = "new-local-id"
        result = await get_or_start_sandbox(sandbox_id)

        mock_ls_object.get_current_sandbox.assert_called_once_with(sandbox_id)
        mock_ls_object.create.assert_called_once_with(project_id=sandbox_id)
        self.assertEqual(result, created_local_sandbox)

    # --- Tests for create_sandbox ---

    @patch('backend.sandbox.sandbox.setup_visualization_environment')
    @patch('backend.sandbox.sandbox.start_supervisord_session')
    @patch('backend.sandbox.sandbox.logger')
    @patch('backend.sandbox.sandbox.daytona')
    @patch('backend.sandbox.sandbox.use_daytona')
    @patch('backend.sandbox.sandbox.Configuration') # For Configuration.SANDBOX_IMAGE_NAME
    async def test_create_sandbox_daytona_path(self, mock_configuration, mock_use_daytona_func, mock_daytona_client, mock_logger, mock_start_supervisord, mock_setup_viz):
        mock_use_daytona_func.return_value = True
        mock_configuration.SANDBOX_IMAGE_NAME = "daytona/image:latest"
        
        mock_created_daytona_sandbox = MagicMock()
        mock_daytona_client.create.return_value = mock_created_daytona_sandbox

        project_id = "daytona-proj-id"
        password = "secure_password"
        
        result = create_sandbox(password=password, project_id=project_id) # create_sandbox is sync

        mock_daytona_client.create.assert_called_once_with(ANY) # ANY for CreateSandboxParams
        args, kwargs = mock_daytona_client.create.call_args
        created_params = args[0] # CreateSandboxParams is the first arg
        
        self.assertEqual(created_params.image, "daytona/image:latest")
        self.assertEqual(created_params.labels, {'id': project_id})
        self.assertEqual(created_params.name, f"suna-sandbox-{project_id}")
        self.assertEqual(created_params.env_vars["VNC_PASSWORD"], password)

        mock_setup_viz.assert_called_once_with(mock_created_daytona_sandbox)
        mock_start_supervisord.assert_called_once_with(mock_created_daytona_sandbox)
        self.assertEqual(result, mock_created_daytona_sandbox)

    @patch('backend.sandbox.sandbox.logger')
    @patch('backend.sandbox.sandbox.local_sandbox')
    @patch('backend.sandbox.sandbox.use_daytona')
    async def test_create_sandbox_local_path(self, mock_use_daytona_func, mock_ls_object, mock_logger):
        mock_use_daytona_func.return_value = False

        mock_created_local_sandbox = {
            'id': 'local-proj-id',
            'container': MagicMock(),
            'info': MagicMock(return_value={'state': 'running'})
        }
        mock_ls_object.create.return_value = mock_created_local_sandbox
        
        project_id = "local-proj-id"
        password = "local_password"

        result = create_sandbox(password=password, project_id=project_id) # create_sandbox is sync

        mock_ls_object.create.assert_called_once_with(project_id=project_id, password=password)
        self.assertEqual(result, mock_created_local_sandbox)
        mock_logger.info.assert_any_call(f"Creating new local sandbox with project_id: {project_id}")

if __name__ == '__main__':
    unittest.main()
