import unittest
from unittest.mock import patch, MagicMock, call, ANY
import sys
import os

# Add the directory containing setup.py to sys.path to allow importing from it
# Assuming test_setup_script_docker_logic.py is in the same directory as setup.py
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

# Import the specific function to test and any helper print functions from setup.py
# This relies on setup.py being importable.
try:
    from setup import install_dependencies, print_info, print_success, print_warning, print_error
except ImportError as e:
    print(f"Failed to import from setup.py: {e}")
    # Fallback if direct import fails (e.g. if setup.py has top-level code that runs on import)
    # In a real scenario, refactoring setup.py to be more import-friendly would be better.
    # For this test, we'll assume it can be imported or proceed with caution.
    install_dependencies = None # Placeholder to allow tests to be defined

# Attempt to import docker errors for more specific exception testing
try:
    from docker import errors as docker_errors
except ImportError:
    # If docker is not installed in the test environment, create a dummy class for the test
    class DockerErrors:
        class DockerException(Exception): pass
        class ImageNotFound(DockerException): pass
        class NotFound(ImageNotFound): pass # Often NotFound is used for registry non-existence
        class APIError(DockerException): pass
    docker_errors = DockerErrors() # type: ignore


@unittest.skipIf(install_dependencies is None, "setup.py could not be imported for testing")
class TestSetupDockerLogic(unittest.TestCase):

    def setUp(self):
        # Basic state for tests, will be customized per test
        self.mock_state = {
            'execution_mode': 'local',
            'env_vars': {
                'llm': {
                    'sandbox_image_name': 'test/suna-sandbox:latest'
                }
            }
        }
        # Mock sys.executable for pip install part of install_dependencies
        self.patch_sys_executable = patch('setup.sys.executable', 'mock_python')
        self.mock_sys_executable = self.patch_sys_executable.start()

    def tearDown(self):
        self.patch_sys_executable.stop()

    @patch('setup.docker') # Mocks 'import docker' in setup.py
    @patch('setup.print_info')
    @patch('setup.print_success')
    @patch('setup.print_warning')
    @patch('setup.print_error')
    @patch('setup.subprocess.run') # Mock subprocess for pip install part
    def test_image_exists_locally(self, mock_subprocess_run, mock_print_error, mock_print_warning, mock_print_success, mock_print_info, mock_docker_module):
        mock_docker_client = MagicMock()
        mock_docker_module.from_env.return_value = mock_docker_client
        mock_docker_client.ping.return_value = True # Docker daemon is responsive
        # client.images.get() succeeds, meaning image is found
        mock_docker_client.images.get.return_value = MagicMock()

        # Simulate pip install docker already done or successful
        mock_subprocess_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        # Act
        install_dependencies(state=self.mock_state, dependencies_installed=False)

        # Assert
        mock_docker_module.from_env.assert_called_once()
        mock_docker_client.ping.assert_called_once()
        image_name = self.mock_state['env_vars']['llm']['sandbox_image_name']
        mock_docker_client.images.get.assert_called_once_with(image_name)
        mock_docker_client.api.pull.assert_not_called() # Or client.images.pull if that's used
        mock_print_success.assert_any_call(f"Image '{image_name}' found locally.")

    @patch('setup.docker')
    @patch('setup.print_info')
    @patch('setup.print_success')
    @patch('setup.print_warning')
    @patch('setup.print_error')
    @patch('setup.subprocess.run')
    def test_image_not_local_pull_succeeds(self, mock_subprocess_run, mock_print_error, mock_print_warning, mock_print_success, mock_print_info, mock_docker_module):
        mock_docker_client = MagicMock()
        mock_docker_module.from_env.return_value = mock_docker_client
        mock_docker_client.ping.return_value = True

        image_name = self.mock_state['env_vars']['llm']['sandbox_image_name']
        # First call to images.get (check) raises ImageNotFound
        # Second call to images.get (verify after pull) succeeds
        mock_docker_client.images.get.side_effect = [
            docker_errors.ImageNotFound("Image not found locally"),
            MagicMock() # Simulates image found after pull
        ]
        mock_docker_client.api.pull.return_value = [] # Simulate successful pull (empty log stream for simplicity)

        mock_subprocess_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        install_dependencies(state=self.mock_state, dependencies_installed=False)

        mock_docker_client.ping.assert_called_once()
        self.assertEqual(mock_docker_client.images.get.call_count, 2)
        mock_docker_client.images.get.assert_any_call(image_name)
        mock_docker_client.api.pull.assert_called_once_with(image_name, stream=True, decode=True)
        mock_print_warning.assert_any_call(f"Image '{image_name}' not found locally.")
        mock_print_info.assert_any_call(f"Attempting to pull '{image_name}'. This may take a few minutes...")
        mock_print_success.assert_any_call(f"Successfully pulled image '{image_name}'.")

    @patch('setup.docker')
    @patch('setup.print_info')
    @patch('setup.print_success')
    @patch('setup.print_warning')
    @patch('setup.print_error')
    @patch('setup.subprocess.run')
    def test_image_not_local_pull_fails_not_in_registry(self, mock_subprocess_run, mock_print_error, mock_print_warning, mock_print_success, mock_print_info, mock_docker_module):
        mock_docker_client = MagicMock()
        mock_docker_module.from_env.return_value = mock_docker_client
        mock_docker_client.ping.return_value = True
        image_name = self.mock_state['env_vars']['llm']['sandbox_image_name']

        mock_docker_client.images.get.side_effect = docker_errors.ImageNotFound("Image not found locally")
        mock_docker_client.api.pull.side_effect = docker_errors.NotFound("Image not found in registry") # docker.errors.NotFound for registry

        mock_subprocess_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        install_dependencies(state=self.mock_state, dependencies_installed=False)

        mock_docker_client.api.pull.assert_called_once_with(image_name, stream=True, decode=True)
        mock_print_error.assert_any_call(f"Failed to pull image '{image_name}': Image not found in the registry.")

    @patch('setup.docker')
    @patch('setup.print_info')
    @patch('setup.print_success')
    @patch('setup.print_warning')
    @patch('setup.print_error')
    @patch('setup.subprocess.run')
    def test_image_not_local_pull_fails_apierror(self, mock_subprocess_run, mock_print_error, mock_print_warning, mock_print_success, mock_print_info, mock_docker_module):
        mock_docker_client = MagicMock()
        mock_docker_module.from_env.return_value = mock_docker_client
        mock_docker_client.ping.return_value = True
        image_name = self.mock_state['env_vars']['llm']['sandbox_image_name']

        mock_docker_client.images.get.side_effect = docker_errors.ImageNotFound("Image not found locally")
        mock_docker_client.api.pull.side_effect = docker_errors.APIError("Docker API error during pull")

        mock_subprocess_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        install_dependencies(state=self.mock_state, dependencies_installed=False)

        mock_docker_client.api.pull.assert_called_once_with(image_name, stream=True, decode=True)
        mock_print_error.assert_any_call(f"Failed to pull image '{image_name}': Docker API error: Docker API error during pull")

    @patch('setup.docker')
    @patch('setup.print_info')
    @patch('setup.print_warning')
    @patch('setup.print_error')
    @patch('setup.subprocess.run')
    def test_docker_daemon_not_responsive(self, mock_subprocess_run, mock_print_error, mock_print_warning, mock_print_info, mock_docker_module):
        mock_docker_client = MagicMock()
        mock_docker_module.from_env.return_value = mock_docker_client
        mock_docker_client.ping.side_effect = docker_errors.DockerException("Cannot connect to Docker daemon")

        mock_subprocess_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        install_dependencies(state=self.mock_state, dependencies_installed=False)

        mock_docker_client.ping.assert_called_once()
        mock_docker_client.images.get.assert_not_called()
        mock_docker_client.api.pull.assert_not_called()
        mock_print_error.assert_any_call("Could not connect to Docker daemon: Cannot connect to Docker daemon")

    @patch('setup.docker')
    @patch('setup.print_info')
    @patch('setup.subprocess.run')
    def test_execution_mode_not_local(self, mock_subprocess_run, mock_print_info, mock_docker_module):
        self.mock_state['execution_mode'] = 'daytona'

        mock_docker_client = MagicMock()
        mock_docker_module.from_env.return_value = mock_docker_client

        mock_subprocess_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        install_dependencies(state=self.mock_state, dependencies_installed=False)

        # Assert that Docker related calls are not made
        mock_docker_client.ping.assert_not_called()
        mock_docker_client.images.get.assert_not_called()
        mock_docker_client.api.pull.assert_not_called()
        # Check that the general "Execution mode is 'local'..." message is NOT printed
        # This requires checking calls to print_info
        local_mode_message_found = False
        for call_args in mock_print_info.call_args_list:
            if "Execution mode is 'local'" in call_args[0][0]:
                local_mode_message_found = True
                break
        self.assertFalse(local_mode_message_found, "Docker logic should not run if mode is not 'local'")

    @patch('setup.docker')
    @patch('setup.print_info')
    @patch('setup.print_success')
    @patch('setup.print_warning')
    @patch('setup.print_error')
    @patch('setup.subprocess.run') # Mock subprocess for pip install part
    def test_image_name_default_used_if_not_in_state(self, mock_subprocess_run, mock_print_error, mock_print_warning, mock_print_success, mock_print_info, mock_docker_module):
        mock_docker_client = MagicMock()
        mock_docker_module.from_env.return_value = mock_docker_client
        mock_docker_client.ping.return_value = True
        mock_docker_client.images.get.return_value = MagicMock()

        # Modify state to not include sandbox_image_name
        current_state = {
            'execution_mode': 'local',
            'env_vars': {
                'llm': {} # No sandbox_image_name
            }
        }
        DEFAULT_IMAGE_IN_SETUP = 'kortix/suna:0.1.2.8' # Should match the default in install_dependencies

        mock_subprocess_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        install_dependencies(state=current_state, dependencies_installed=False)

        mock_docker_client.images.get.assert_called_once_with(DEFAULT_IMAGE_IN_SETUP)
        mock_print_warning.assert_any_call(f"Sandbox image name was empty in config, defaulting to {DEFAULT_IMAGE_IN_SETUP}")
        mock_print_success.assert_any_call(f"Image '{DEFAULT_IMAGE_IN_SETUP}' found locally.")


if __name__ == '__main__':
    # This setup allows running the tests directly from the file if needed,
    # though typically a test runner like 'python -m unittest discover' would be used.
    # Ensure setup.py is in a location where it can be imported, or adjust sys.path.
    if install_dependencies is None:
        print("Skipping tests as setup.py could not be imported.")
    else:
        unittest.main()
