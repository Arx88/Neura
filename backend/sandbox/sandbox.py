from daytona_sdk import Daytona, DaytonaConfig, CreateSandboxParams, Sandbox, SessionExecuteRequest
from daytona_api_client.models.workspace_state import WorkspaceState
from dotenv import load_dotenv
from backend.utils.logger import logger
from backend.utils.config import config
from backend.utils.config import Configuration
from backend.sandbox.local_sandbox import local_sandbox

load_dotenv()

# Conditional Daytona Client Initialization
daytona = None # Initialize module-level 'daytona' client as None

def use_daytona():
    """Determinar si se debe usar DAYTONA o sandbox local"""
    return bool(config.DAYTONA_API_KEY and config.DAYTONA_SERVER_URL and config.DAYTONA_TARGET)

if use_daytona():
    logger.debug("Daytona mode enabled. Initializing Daytona sandbox configuration.")

    # Specific check for DAYTONA_TARGET validity before DaytonaConfig instantiation
    if config.DAYTONA_TARGET not in ['eu', 'us', 'asia']:
        logger.error(
            f"Invalid DAYTONA_TARGET: '{config.DAYTONA_TARGET}' in .env. Must be one of 'eu', 'us', or 'asia'. "
            "Daytona client will not be initialized. Ensure this is intended if local mode is active, "
            "or correct it if Daytona mode is intended."
        )
        # 'daytona' remains None
    else:
        try:
            daytona_config = DaytonaConfig(
                api_key=config.DAYTONA_API_KEY,
                server_url=config.DAYTONA_SERVER_URL,
                target=config.DAYTONA_TARGET
            )
            daytona = Daytona(daytona_config) # Assign to the module-level 'daytona'
            logger.debug("Daytona client initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Daytona client even though use_daytona() was true and target seemed valid: {e}")
            # daytona remains None
else:
    logger.debug(
        "Daytona mode disabled (DAYTONA_API_KEY, DAYTONA_SERVER_URL, or DAYTONA_TARGET not set, empty, or invalid). "
        "Skipping Daytona client initialization."
    )

async def get_or_start_sandbox(sandbox_id: str):
    """Retrieve a sandbox by ID, check its state, and start it if needed."""
    
    logger.info(f"Getting or starting sandbox with ID: {sandbox_id}")

    if use_daytona():
        logger.info("Using Daytona for sandbox operations")
        try:
            sandbox = daytona.get_current_sandbox(sandbox_id)

            # Check if sandbox needs to be started
            if sandbox.instance.state == WorkspaceState.ARCHIVED or sandbox.instance.state == WorkspaceState.STOPPED:
                logger.info(f"Daytona sandbox is in {sandbox.instance.state} state. Starting...")
                try:
                    daytona.start(sandbox)
                    # Refresh sandbox state after starting
                    sandbox = daytona.get_current_sandbox(sandbox_id)
                    # Start supervisord in a session when restarting
                    start_supervisord_session(sandbox) # Ensure this is compatible or adapted if needed
                except Exception as e:
                    logger.error(f"Error starting Daytona sandbox: {e}")
                    raise e

            logger.info(f"Daytona sandbox {sandbox_id} is ready")
            return sandbox

        except Exception as e:
            logger.error(f"Error retrieving or starting Daytona sandbox: {str(e)}")
            raise e
    else:
        logger.info("Using local sandbox for operations")
        try:
            try:
                sandbox = local_sandbox.get_current_sandbox(sandbox_id)
                # Docker container states: created, restarting, running, removing, paused, exited, dead
                if sandbox and sandbox.get('instance', {}).get('state') in ['exited', 'stopped']: # 'stopped' might not be a direct docker state, but good to check
                    logger.info(f"Local sandbox {sandbox_id} is stopped. Starting...")
                    sandbox = local_sandbox.start(sandbox)
                elif not sandbox: # Should be caught by the exception below, but as a safeguard
                    logger.info(f"Local sandbox {sandbox_id} not found. Creating...")
                    sandbox = local_sandbox.create(project_id=sandbox_id)
                
                logger.info(f"Local sandbox {sandbox_id} is ready. State: {sandbox.get('instance', {}).get('state')}")
                return sandbox
            except Exception as e: # Catches error from get_current_sandbox if not found
                logger.info(f"Local sandbox {sandbox_id} not found or error during get: {str(e)}. Creating new one...")
                sandbox = local_sandbox.create(project_id=sandbox_id)
                logger.info(f"New local sandbox {sandbox_id} created.")
                return sandbox
        except Exception as e:
            logger.error(f"Error with local sandbox operations for {sandbox_id}: {str(e)}")
            raise e

def start_supervisord_session(sandbox: Sandbox):
    """Start supervisord in a session."""
    session_id = "supervisord-session"
    try:
        logger.info(f"Creating session {session_id} for supervisord")
        sandbox.process.create_session(session_id)
        
        # Execute supervisord command
        sandbox.process.execute_session_command(session_id, SessionExecuteRequest(
            command="exec /usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf",
            var_async=True
        ))
        logger.info(f"Supervisord started in session {session_id}")
    except Exception as e:
        logger.error(f"Error starting supervisord session: {str(e)}")
        raise e

def setup_visualization_environment(sandbox: Sandbox):
    """Set up the visualization environment in the sandbox."""
    session_id = "viz_setup_session"
    try:
        logger.info(f"Setting up visualization environment for sandbox {sandbox.id}")
        sandbox.process.create_session(session_id)
        
        # Install required packages
        logger.info(f"Installing visualization packages in session {session_id}...")
        install_req = SessionExecuteRequest(
            command="pip install matplotlib pandas seaborn plotly",
            var_async=False,
            cwd="/workspace" # Typically, pip install is not cwd sensitive for global site-packages
        )
        install_response = sandbox.process.execute_session_command(session_id, install_req, timeout=300) # Increased timeout for pip install
        
        install_logs = sandbox.process.get_session_command_logs(session_id, install_response.cmd_id)
        if install_response.exit_code == 0:
            logger.info(f"Visualization packages installed successfully. Logs:\n{install_logs}")
        else:
            logger.error(f"Failed to install visualization packages. Exit code: {install_response.exit_code}. Logs:\n{install_logs}")
            # Optionally, raise an exception or handle error more specifically
            # For now, just logging the error.

        # Create visualizations directory
        logger.info(f"Creating visualizations directory in session {session_id}...")
        mkdir_req = SessionExecuteRequest(
            command="mkdir -p /workspace/visualizations",
            var_async=False,
            cwd="/workspace"
        )
        mkdir_response = sandbox.process.execute_session_command(session_id, mkdir_req)
        mkdir_logs = sandbox.process.get_session_command_logs(session_id, mkdir_response.cmd_id)

        if mkdir_response.exit_code == 0:
            logger.info(f"Visualizations directory created successfully. Logs:\n{mkdir_logs}")
        else:
            logger.error(f"Failed to create visualizations directory. Exit code: {mkdir_response.exit_code}. Logs:\n{mkdir_logs}")
            # Optionally, raise an exception

    except Exception as e:
        logger.error(f"Error setting up visualization environment: {str(e)}", exc_info=True)
        # It might be useful to re-raise the exception if setup is critical
        # raise e 
    finally:
        try:
            logger.info(f"Deleting session {session_id} for visualization setup.")
            sandbox.process.delete_session(session_id)
            logger.info(f"Session {session_id} deleted successfully.")
        except Exception as e:
            logger.error(f"Error deleting session {session_id}: {str(e)}", exc_info=True)
            pass # Avoid shadowing original exception if any

def create_sandbox(password: str, project_id: str = None):
    """Create a new sandbox with all required services configured and running."""

    if use_daytona():
        logger.info("Creating new Daytona sandbox environment")
        logger.debug("Configuring Daytona sandbox with browser-use image and environment variables")
        
        labels = None
        if project_id:
            logger.debug(f"Using project_id as label for Daytona sandbox: {project_id}")
            labels = {'id': project_id}

        params = CreateSandboxParams(
            image=Configuration.SANDBOX_IMAGE_NAME, # Assuming Configuration.SANDBOX_IMAGE_NAME is also relevant for Daytona
            public=True,
            labels=labels,
            name=f"suna-sandbox-{project_id}" if project_id else None, # Daytona might use name differently or via labels
            env_vars={
                "CHROME_PERSISTENT_SESSION": "true",
                "RESOLUTION": "1024x768x24",
                "RESOLUTION_WIDTH": "1024",
                "RESOLUTION_HEIGHT": "768",
                "VNC_PASSWORD": password,
                "ANONYMIZED_TELEMETRY": "false",
                "CHROME_PATH": "",
                "CHROME_USER_DATA": "",
                "CHROME_DEBUGGING_PORT": "9222",
                "CHROME_DEBUGGING_HOST": "localhost",
                "CHROME_CDP": ""
            },
            resources={ # These might be specific to Daytona's way of defining resources
                "cpu": 2,
                "memory": 4,
                "disk": 5,
            }
        )

        # Create the Daytona sandbox
        sandbox = daytona.create(params)
        logger.debug(f"Daytona sandbox created with ID: {sandbox.id}")

        # Setup visualization environment (ensure this is Daytona compatible)
        setup_visualization_environment(sandbox)

        # Start supervisord in a session for new Daytona sandbox
        start_supervisord_session(sandbox)

        logger.debug(f"Daytona sandbox environment successfully initialized")
        return sandbox
    else:
        logger.info(f"Creating new local sandbox with project_id: {project_id}")
        try:
            sandbox = local_sandbox.create(project_id=project_id, password=password)
            logger.info(f"Local sandbox created with ID: {sandbox['id']}")
            # Note: setup_visualization_environment and start_supervisord are called within local_sandbox.create()
            return sandbox
        except Exception as e:
            logger.error(f"Error creating local sandbox: {str(e)}")
            raise e

