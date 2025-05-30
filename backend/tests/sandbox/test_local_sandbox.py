import unittest
from unittest.mock import patch, MagicMock, call, ANY
import uuid # For checking command IDs or sandbox IDs if needed

# Attempt to import docker errors for more specific exception testing
try:
    from docker import errors as docker_errors
except ImportError:
    # If docker is not installed in the test environment, create a dummy class for the test
    class DockerErrors:
        class NotFound(Exception):
            pass
    docker_errors = DockerErrors()

# Assuming utils.logger and utils.config are importable or can be mocked.
# For the purpose of these tests, we will mock them where they are imported
# in local_sandbox.py. The LocalSandbox class itself receives logger and config
# through its imports, so we patch at 'backend.sandbox.local_sandbox.logger' etc.

# The class to test
from backend.sandbox.local_sandbox import LocalSandbox

class TestLocalSandbox(unittest.TestCase):

    @patch('backend.sandbox.local_sandbox.config')
    @patch('backend.sandbox.local_sandbox.logger')
    @patch('backend.sandbox.local_sandbox.docker')
    def setUp(self, mock_docker_module, mock_logger_module, mock_config_module):
        # Mock the Docker client returned by docker.from_env()
        self.mock_docker_client = MagicMock()
        mock_docker_module.from_env.return_value = self.mock_docker_client

        # Set up default mock config values (as accessed by LocalSandbox)
        # The LocalSandbox class imports 'config' directly, so we patch 'backend.sandbox.local_sandbox.config'
        mock_config_module.SANDBOX_IMAGE_NAME = "test/image:latest"

        # Instantiate the LocalSandbox
        self.sandbox_manager = LocalSandbox()

        # Replace the instance's logger with the mock_logger_module if desired,
        # or trust the patcher to handle it if LocalSandbox uses 'logger.info' etc.
        # For direct assertion on the logger instance used by sandbox_manager:
        self.sandbox_manager.logger = mock_logger_module
        self.mock_logger = mock_logger_module # Keep a reference for assertions

    def test_init(self):
        """Test that docker.from_env() is called upon instantiation."""
        # setUp already creates an instance, so we just need to assert
        # that docker.from_env was called during its __init__.
        # The patch for docker is on 'backend.sandbox.local_sandbox.docker'
        # So, we access its from_env attribute.
        # We need to ensure the patch is active when LocalSandbox is instantiated.
        # This is implicitly handled by setUp running before each test.

        # To explicitly test __init__ behavior if it were more complex:
        with patch('backend.sandbox.local_sandbox.docker') as new_mock_docker:
            new_client = MagicMock()
            new_mock_docker.from_env.return_value = new_client
            LocalSandbox()
            new_mock_docker.from_env.assert_called_once()

    @patch('backend.sandbox.local_sandbox.uuid.uuid4')
    def test_create_sandbox_default_id_and_password(self, mock_uuid_module):
        mock_uuid_module.return_value = "test-uuid"

        mock_container_instance = MagicMock()
        mock_container_instance.name = "suna-sandbox-test-uuid"
        # Simulate return values for exec_run for various setup steps
        # (pip install, mkdir, supervisord)
        mock_container_instance.exec_run.side_effect = [
            (0, b"pip install successful"), # For _setup_visualization_environment
            (0, b"mkdir successful"),       # For _setup_visualization_environment
            (0, b"supervisord started")     # For _start_supervisord
        ]
        self.mock_docker_client.containers.run.return_value = mock_container_instance

        sandbox = self.sandbox_manager.create()

        self.mock_docker_client.containers.run.assert_called_once_with(
            image="test/image:latest", # From mock_config_module.SANDBOX_IMAGE_NAME
            detach=True,
            environment={
                "CHROME_PERSISTENT_SESSION": "true",
                "RESOLUTION": "1024x768x24",
                "RESOLUTION_WIDTH": "1024",
                "RESOLUTION_HEIGHT": "768",
                "VNC_PASSWORD": "suna", # Default password
                "ANONYMIZED_TELEMETRY": "false",
                "CHROME_DEBUGGING_PORT": "9222",
                "CHROME_DEBUGGING_HOST": "localhost",
            },
            ports={'5900/tcp': None, '9222/tcp': None},
            name="suna-sandbox-test-uuid",
            labels={'id': 'test-uuid', 'type': 'suna-sandbox'}
        )
        self.assertEqual(sandbox['id'], 'test-uuid')
        self.assertEqual(sandbox['container'], mock_container_instance)

        # Verify calls for setup methods
        calls = [
            call(cmd="pip install matplotlib pandas seaborn plotly", stdout=True, stderr=True),
            call(cmd="mkdir -p /workspace/visualizations"),
            call(cmd="/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf", detach=True)
        ]
        mock_container_instance.exec_run.assert_has_calls(calls, any_order=False) # Order matters here

        # Test the info lambda
        mock_container_instance.reload.return_value = None # Mock reload
        mock_container_instance.status = "running"
        mock_container_instance.ports = {'5900/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '32768'}]}
        info_result = sandbox['info']()
        self.assertEqual(info_result['state'], "running")
        self.assertEqual(info_result['ports'], {'5900/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '32768'}]})
        mock_container_instance.reload.assert_called_once()

        # Test the process lambda (execute_session_command)
        mock_container_instance.exec_run.reset_mock() # Reset from setup calls
        mock_exec_output = (0, b"command output")
        mock_container_instance.exec_run.return_value = mock_exec_output

        with patch('backend.sandbox.local_sandbox.uuid.uuid4') as mock_cmd_uuid:
            mock_cmd_uuid.return_value = "cmd-uuid"
            exec_result = sandbox['process']['execute_session_command']("session1", "echo hello")
            self.assertEqual(exec_result['cmd_id'], "cmd-uuid")
            self.assertEqual(exec_result['exit_code'], 0)
            self.assertEqual(exec_result['output'], "command output")
            mock_container_instance.exec_run.assert_called_once_with(
                cmd="cd /workspace && echo hello", stdout=True, stderr=True
            )

    @patch('backend.sandbox.local_sandbox.uuid.uuid4')
    def test_create_sandbox_with_project_id_and_password(self, mock_uuid_module):
        # This ensures that uuid.uuid4 is not called if project_id is provided
        # mock_uuid_module should not be called.

        mock_container_instance = MagicMock()
        mock_container_instance.name = "suna-sandbox-custom-project"
        mock_container_instance.exec_run.side_effect = [(0, b""), (0, b""), (0, b"")]
        self.mock_docker_client.containers.run.return_value = mock_container_instance

        sandbox = self.sandbox_manager.create(project_id="custom-project", password="custom_password")

        mock_uuid_module.assert_not_called()
        self.mock_docker_client.containers.run.assert_called_once_with(
            image=ANY, detach=ANY, environment=ANY, ports=ANY, # Check specific if needed
            name="suna-sandbox-custom-project",
            labels={'id': 'custom-project', 'type': 'suna-sandbox'}
        )
        # Check if custom_password was used in environment
        args, kwargs = self.mock_docker_client.containers.run.call_args
        self.assertEqual(kwargs['environment']['VNC_PASSWORD'], 'custom_password')
        self.assertEqual(sandbox['id'], 'custom-project')


    def test_get_current_sandbox_success(self):
        mock_container_instance = MagicMock()
        mock_container_instance.status = "running"
        self.mock_docker_client.containers.get.return_value = mock_container_instance

        sandbox = self.sandbox_manager.get_current_sandbox("existing-id")

        self.mock_docker_client.containers.get.assert_called_once_with("suna-sandbox-existing-id")
        self.assertEqual(sandbox['id'], "existing-id")
        self.assertEqual(sandbox['container'], mock_container_instance)
        self.assertEqual(sandbox['instance']['state'], "running")

    def test_get_current_sandbox_not_found(self):
        self.mock_docker_client.containers.get.side_effect = docker_errors.NotFound("Container not found")

        with self.assertRaises(docker_errors.NotFound):
            self.sandbox_manager.get_current_sandbox("non-existent-id")

        self.mock_docker_client.containers.get.assert_called_once_with("suna-sandbox-non-existent-id")
        self.mock_logger.error.assert_called_once() # Check that an error was logged

    def test_start_sandbox(self):
        mock_container_instance = MagicMock()
        # Mock for _start_supervisord call
        mock_container_instance.exec_run.return_value = (0, b"supervisord output")

        sandbox_dict = {
            'id': 'test-id',
            'container': mock_container_instance
        }

        returned_sandbox = self.sandbox_manager.start(sandbox_dict)

        mock_container_instance.start.assert_called_once()
        # Check _start_supervisord call
        mock_container_instance.exec_run.assert_called_once_with(
            cmd="/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
            detach=True
        )
        self.assertEqual(returned_sandbox, sandbox_dict)
        self.mock_logger.info.assert_any_call("Sandbox local test-id iniciado")


    def test_stop_sandbox(self):
        mock_container_instance = MagicMock()
        sandbox_dict = {
            'id': 'test-id',
            'container': mock_container_instance
        }
        self.sandbox_manager.stop(sandbox_dict)
        mock_container_instance.stop.assert_called_once()
        self.mock_logger.info.assert_called_once_with("Sandbox local test-id detenido")

    @patch('backend.sandbox.local_sandbox.uuid.uuid4')
    def test_execute_command_as_string(self, mock_cmd_uuid):
        mock_cmd_uuid.return_value = "cmd-test-uuid"
        mock_container_instance = MagicMock()
        mock_container_instance.exec_run.return_value = (0, b"output string")

        command_request = "ls -l"
        result = self.sandbox_manager._execute_command(mock_container_instance, command_request)

        mock_container_instance.exec_run.assert_called_once_with(
            cmd=f"cd /workspace && {command_request}", # Default cwd
            stdout=True, stderr=True
        )
        self.assertEqual(result['cmd_id'], "cmd-test-uuid")
        self.assertEqual(result['exit_code'], 0)
        self.assertEqual(result['output'], "output string")

    @patch('backend.sandbox.local_sandbox.uuid.uuid4')
    def test_execute_command_as_object(self, mock_cmd_uuid):
        mock_cmd_uuid.return_value = "cmd-obj-uuid"
        mock_container_instance = MagicMock()
        mock_container_instance.exec_run.return_value = (1, b"error output")

        command_request_obj = MagicMock()
        command_request_obj.command = "python script.py"
        command_request_obj.cwd = "/app"

        result = self.sandbox_manager._execute_command(mock_container_instance, command_request_obj)

        mock_container_instance.exec_run.assert_called_once_with(
            cmd=f"cd /app && python script.py",
            stdout=True, stderr=True
        )
        self.assertEqual(result['cmd_id'], "cmd-obj-uuid")
        self.assertEqual(result['exit_code'], 1)
        self.assertEqual(result['output'], "error output")

    def test_setup_visualization_environment_success(self):
        mock_container_instance = MagicMock()
        # Simulate successful pip install and mkdir
        mock_container_instance.exec_run.side_effect = [
            (0, b"pip install successful"),
            (0, b"mkdir successful")
        ]

        self.sandbox_manager._setup_visualization_environment(mock_container_instance)

        calls = [
            call(cmd="pip install matplotlib pandas seaborn plotly", stdout=True, stderr=True),
            call(cmd="mkdir -p /workspace/visualizations")
        ]
        mock_container_instance.exec_run.assert_has_calls(calls, any_order=False)
        self.mock_logger.info.assert_any_call("Paquetes de visualización instalados correctamente")

    def test_setup_visualization_environment_pip_fail(self):
        mock_container_instance = MagicMock()
        # Simulate failed pip install
        mock_container_instance.exec_run.side_effect = [
            (1, b"pip install failed error message"),
            # mkdir might still be called or not depending on error handling, let's assume it is
            (0, b"mkdir successful")
        ]

        self.sandbox_manager._setup_visualization_environment(mock_container_instance)

        mock_container_instance.exec_run.assert_any_call(cmd="pip install matplotlib pandas seaborn plotly", stdout=True, stderr=True)
        self.mock_logger.error.assert_any_call("Error al instalar paquetes de visualización: pip install failed error message")
        # Check if mkdir was still called (good to know the behavior)
        mock_container_instance.exec_run.assert_any_call(cmd="mkdir -p /workspace/visualizations")


    def test_start_supervisord(self):
        mock_container_instance = MagicMock()
        mock_container_instance.exec_run.return_value = (0, b"supervisord output") # Though detached, mock a typical successful launch tuple

        self.sandbox_manager._start_supervisord(mock_container_instance)

        mock_container_instance.exec_run.assert_called_once_with(
            cmd="/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
            detach=True
        )
        self.mock_logger.info.assert_any_call("Iniciando supervisord en sandbox local")
        self.mock_logger.info.assert_any_call(f"Supervisord launch attempted. Exit code: 0. Output: supervisord output")


    def test_get_container_info(self):
        mock_container_instance = MagicMock()
        mock_container_instance.status = "paused"
        mock_container_instance.ports = {'1234/tcp': None}

        # reload() is called on the container, doesn't return anything itself
        mock_container_instance.reload.return_value = None

        info = self.sandbox_manager._get_container_info(mock_container_instance)

        mock_container_instance.reload.assert_called_once()
        self.assertEqual(info['state'], "paused")
        self.assertEqual(info['ports'], {'1234/tcp': None})

if __name__ == '__main__':
    unittest.main()
