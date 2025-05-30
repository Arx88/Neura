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

        # Configuración del contenedor similar a la de DAYTONA
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

        # Configurar el entorno de visualización
        self._setup_visualization_environment(container)

        # Iniciar supervisord
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
            self.logger.info("Configurando entorno de visualización para sandbox local")

            # Instalar paquetes necesarios
            exit_code, output = container.exec_run(
                cmd="pip install matplotlib pandas seaborn plotly",
                stdout=True,
                stderr=True
            )

            if exit_code == 0:
                self.logger.info("Paquetes de visualización instalados correctamente")
            else:
                self.logger.error(f"Error al instalar paquetes de visualización: {output.decode('utf-8')}")

            # Crear directorio de visualizaciones
            container.exec_run(cmd="mkdir -p /workspace/visualizations")

        except Exception as e:
            self.logger.error(f"Error al configurar entorno de visualización: {str(e)}")

    def _start_supervisord(self, container):
        """Iniciar supervisord en el contenedor"""
        try:
            self.logger.info("Iniciando supervisord en sandbox local")

            exit_code, output = container.exec_run(
                cmd="/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
                detach=True
            )

            # Note: For detached exec_run, exit_code might not be immediately useful
            # or might indicate the command was launched, not its completion status.
            # Consider logging output if available, but be aware it might be empty for detached processes.
            self.logger.info(f"Supervisord launch attempted. Exit code: {exit_code}. Output: {output.decode('utf-8') if output else 'N/A'}")
            # A more robust check might involve checking container logs or process status if critical

        except Exception as e:
            self.logger.error(f"Error al iniciar supervisord: {str(e)}")

    def _get_container_info(self, container):
        """Obtener información del contenedor"""
        container.reload()
        return {
            'state': container.status,
            'ports': container.ports
        }

# Instancia global
local_sandbox = LocalSandbox()
