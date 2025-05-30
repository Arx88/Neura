import docker
import uuid
import os
from utils.logger import logger
from utils.config import config

class LocalSandbox:
    def __init__(self):
        self.client = docker.from_env()
        self.logger = logger

    def create(self, project_id=None, password=None):
        """Crear un nuevo sandbox local usando Docker"""
        sandbox_id = project_id or str(uuid.uuid4())
        self.logger.info(f"Creando sandbox local con ID: {sandbox_id}")

        try:
            # ... (existing info log about creating sandbox) ...
            self.logger.info(f"Attempting to run Docker container for sandbox: {sandbox_id} with image: {config.SANDBOX_IMAGE_NAME}")
            container = self.client.containers.run(
                image=config.SANDBOX_IMAGE_NAME,
                detach=True,
                environment={
                    "CHROME_PERSISTENT_SESSION": "true",
                    "RESOLUTION": "1024x768x24",
                    "RESOLUTION_WIDTH": "1024",
                    "RESOLUTION_HEIGHT": "768",
                    "VNC_PASSWORD": password or "suna",
                    "ANONYMIZED_TELEMETRY": "false",
                    "CHROME_DEBUGGING_PORT": "9222",
                    "CHROME_DEBUGGING_HOST": "localhost",
                },
                ports={
                    '5900/tcp': None,  # VNC
                    '9222/tcp': None,  # Chrome debugging
                },
                name=f"suna-sandbox-{sandbox_id}",
                labels={
                    'id': sandbox_id,
                    'type': 'suna-sandbox'
                }
            )
            self.logger.info(f"Successfully ran Docker container: {container.id} for sandbox: {sandbox_id}")

            self._setup_visualization_environment(container)
            self._start_supervisord(container)

            return {
                'id': sandbox_id,
                'container': container,
                'info': lambda: self._get_container_info(container),
                'process': {
                    'create_session': lambda session_id: None,
                    'execute_session_command': lambda session_id, command: self._execute_command(container, command),
                    'delete_session': lambda session_id: None,
                    'get_session_command_logs': lambda session_id, cmd_id: ""
                }
            }
        except docker.errors.ImageNotFound as img_err:
            self.logger.critical(f"DOCKER IMAGE NOT FOUND for sandbox {sandbox_id}: {str(img_err)}. Explanation: {getattr(img_err, 'explanation', 'N/A')}. Ensure image '{config.SANDBOX_IMAGE_NAME}' is available.", exc_info=True)
            raise
        except docker.errors.APIError as api_err:
            self.logger.critical(f"DOCKER API ERROR during container run for sandbox {sandbox_id}: {str(api_err)}. Explanation: {getattr(api_err, 'explanation', 'N/A')}", exc_info=True)
            raise
        except Exception as e:
            # This will catch errors from _setup_visualization_environment or _start_supervisord if they are not docker.errors.APIError
            self.logger.error(f"Error in LocalSandbox.create for {sandbox_id} after container run attempt or during setup: {str(e)}", exc_info=True)
            raise

    def get_current_sandbox(self, sandbox_id):
        """Obtener un sandbox existente por ID"""
        try:
            container = self.client.containers.get(f"suna-sandbox-{sandbox_id}")
            return {
                'id': sandbox_id,
                'container': container,
                'instance': {
                    'state': container.status
                },
                'info': lambda: self._get_container_info(container),
                'process': {
                    'create_session': lambda session_id: None,
                    'execute_session_command': lambda session_id, command: self._execute_command(container, command),
                    'delete_session': lambda session_id: None,
                    'get_session_command_logs': lambda session_id, cmd_id: ""
                }
            }
        except Exception as e:
            self.logger.error(f"Error al obtener sandbox local: {str(e)}")
            raise e

    def start(self, sandbox):
        """Iniciar un sandbox detenido"""
        try:
            container = sandbox['container']
            container.start()
            self.logger.info(f"Sandbox local {sandbox['id']} iniciado")

            # Iniciar supervisord
            self._start_supervisord(container)

            return sandbox
        except Exception as e:
            self.logger.error(f"Error al iniciar sandbox local: {str(e)}")
            raise e

    def stop(self, sandbox):
        """Detener un sandbox en ejecución"""
        try:
            container = sandbox['container']
            container.stop()
            self.logger.info(f"Sandbox local {sandbox['id']} detenido")
        except Exception as e:
            self.logger.error(f"Error al detener sandbox local: {str(e)}")
            raise e

    def _execute_command(self, container, command_request):
        """Ejecutar un comando en el contenedor"""
        try:
            cmd = command_request.command if hasattr(command_request, 'command') else command_request
            cwd = command_request.cwd if hasattr(command_request, 'cwd') else "/workspace"

            # Ejecutar el comando
            exit_code, output = container.exec_run(
                cmd=f"cd {cwd} && {cmd}",
                stdout=True,
                stderr=True
            )

            return {
                'cmd_id': str(uuid.uuid4()),
                'exit_code': exit_code,
                'output': output.decode('utf-8', errors='replace')
            }
        except Exception as e:
            self.logger.error(f"Error al ejecutar comando en sandbox local: {str(e)}")
            raise e

    def _setup_visualization_environment(self, container):
        """Configurar el entorno de visualización en el sandbox"""
        try:
            self.logger.info(f"Configurando entorno de visualización para sandbox local (Container: {container.short_id})")
            self.logger.info(f"Attempting pip install in {container.short_id}: matplotlib pandas seaborn plotly")
            # Instalar paquetes necesarios
            exit_code, output_bytes = container.exec_run(
                cmd="pip install matplotlib pandas seaborn plotly",
                stdout=True,
                stderr=True
            )

            output_str = ""
            try:
                output_str = output_bytes.decode('utf-8') if output_bytes else ""
            except UnicodeDecodeError:
                output_str = output_bytes.decode('latin-1', errors='replace') if output_bytes else "" # Fallback decoding

            if exit_code == 0:
                self.logger.info(f"Paquetes de visualización instalados correctamente in {container.short_id}.")
            else:
                error_message = f"PIP INSTALL FAILED in {container.short_id} with exit code {exit_code}. Output: {output_str}"
                self.logger.error(error_message)
                raise Exception(error_message)

            # Crear directorio de visualizaciones
            self.logger.info(f"Creating /workspace/visualizations in {container.short_id}")
            exit_code_mkdir, output_mkdir_bytes = container.exec_run(cmd="mkdir -p /workspace/visualizations")

            output_mkdir_str = ""
            try:
                output_mkdir_str = output_mkdir_bytes.decode('utf-8') if output_mkdir_bytes else ""
            except UnicodeDecodeError:
                output_mkdir_str = output_mkdir_bytes.decode('latin-1', errors='replace') if output_mkdir_bytes else "" # Fallback decoding

            if exit_code_mkdir != 0:
                error_message_mkdir = f"MKDIR FAILED for /workspace/visualizations in {container.short_id} with exit code {exit_code_mkdir}. Output: {output_mkdir_str}"
                self.logger.error(error_message_mkdir)
                raise Exception(error_message_mkdir)
            self.logger.info(f"/workspace/visualizations directory ensured in {container.short_id}")

        except Exception as e:
            self.logger.error(f"Error al configurar entorno de visualización in {container.short_id}: {str(e)}", exc_info=True)
            # Re-raise the exception to be caught by the caller (LocalSandbox.create)
            # This ensures that sandbox creation fails if visualization setup fails.
            raise

    def _start_supervisord(self, container):
        """Iniciar supervisord en el contenedor"""
        try:
            self.logger.info(f"Iniciando supervisord en sandbox local (Container: {container.short_id})")

            # The command is detached, so we don't rely heavily on its immediate output for success.
            # We log the attempt and any immediate output. If supervisord fails to start correctly,
            # subsequent operations in the sandbox that depend on it would likely fail.
            _exit_code, _output_bytes = container.exec_run(
                cmd="/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
                detach=True
            )
            # For detached, output might be minimal or non-existent.
            # _output_str = _output_bytes.decode('utf-8', errors='replace') if _output_bytes else "N/A (detached)"
            # self.logger.info(f"Supervisord launch command issued in {container.short_id}. Exit Code: {_exit_code}, Output: {_output_str}")
            self.logger.info(f"Supervisord launch command issued in {container.short_id}.")

        except Exception as e:
            self.logger.error(f"Error al iniciar supervisord in {container.short_id}: {str(e)}", exc_info=True)
            # Re-raise the exception to be caught by the caller (LocalSandbox.create)
            # This ensures that sandbox creation fails if supervisord setup fails.
            raise

    def _get_container_info(self, container):
        """Obtener información del contenedor"""
        container.reload()
        return {
            'state': container.status,
            'ports': container.ports
        }

# Instancia global
local_sandbox = LocalSandbox()
