import sentry
import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Optional

# --- INICIO DEL BLOQUE DE DIAGNÓSTICO PYTHON ---
import sys
import os
import dramatiq # Asegúrate de que dramatiq se importe antes de intentar acceder a __version__
import inspect  # Para obtener rutas de archivos de módulos

def ejecutar_diagnostico_entorno_dramatiq():
    print("PYTHON_DIAG: --- Iniciando Diagnóstico de Entorno Python y Dramatiq ---", flush=True)
    print(f"PYTHON_DIAG: Versión de Python: {sys.version}", flush=True)
    print(f"PYTHON_DIAG: sys.path: {sys.path}", flush=True)

    try:
        print(f"PYTHON_DIAG: Versión de Dramatiq detectada: {dramatiq.__version__}", flush=True)
        print(f"PYTHON_DIAG: Ubicación del módulo Dramatiq principal: {inspect.getfile(dramatiq)}", flush=True)
    except Exception as e_dramatiq_version:
        print(f"PYTHON_DIAG: No se pudo obtener la versión o ubicación de Dramatiq: {e_dramatiq_version}", flush=True)

    try:
        import dramatiq.middleware
        print(f"PYTHON_DIAG: Módulo dramatiq.middleware encontrado en: {inspect.getfile(dramatiq.middleware)}", flush=True)

        # Listar contenido de dramatiq.middleware
        middleware_contents = dir(dramatiq.middleware)
        print(f"PYTHON_DIAG: Contenido de dramatiq.middleware (dir()): {middleware_contents}", flush=True)

        # Verificar específicamente la presencia de 'Results'
        if 'Results' in middleware_contents:
            print("PYTHON_DIAG: El atributo 'Results' SÍ ESTÁ PRESENTE en dramatiq.middleware.", flush=True)
            results_attr = getattr(dramatiq.middleware, 'Results')
            print(f"PYTHON_DIAG: Tipo de dramatiq.middleware.Results: {type(results_attr)}", flush=True)
            try:
                print(f"PYTHON_DIAG: 'Results' se define en el archivo: {inspect.getfile(results_attr)}", flush=True)
            except TypeError:
                print("PYTHON_DIAG: No se pudo determinar el archivo para 'Results' (podría ser un atributo no modular/clase).", flush=True)
        else:
            print("PYTHON_DIAG: El atributo 'Results' NO ESTÁ PRESENTE en dramatiq.middleware.", flush=True)

    except ImportError as e_import_middleware:
        print(f"PYTHON_DIAG: ImportError al intentar importar o inspeccionar dramatiq.middleware: {e_import_middleware}", flush=True)
    except Exception as e_general_diag:
        print(f"PYTHON_DIAG: Error general durante el diagnóstico de dramatiq.middleware: {e_general_diag}", flush=True)

    print("PYTHON_DIAG: --- Fin del Diagnóstico de Entorno Python y Dramatiq ---", flush=True)

ejecutar_diagnostico_entorno_dramatiq()
# --- FIN DEL BLOQUE DE DIAGNÓSTICO PYTHON ---

from services import redis
from agent.run import run_agent
from utils.logger import setup_logger
import dramatiq
import uuid
from agentpress.thread_manager import ThreadManager
from services.supabase import DBConnection
from services import redis # This is the async redis used by the app
import redis as redis_sync # Synchronous redis for Dramatiq results backend
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from dramatiq.middleware import Results
from dramatiq_redis import RedisBackend
from utils.config import config # Added import
from services.langfuse import langfuse
from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.task_storage_supabase import SupabaseTaskStorage # Added import
from agentpress.task_state_manager import TaskStateManager # Added import
import os # Necesario para getenv
# Imports for sandbox stopping
from sandbox.sandbox import get_or_start_sandbox, daytona, use_daytona # Modified import
from daytona_api_client.models.workspace_state import WorkspaceState
from daytona_sdk import SessionExecuteRequest # Added for workspace cleanup

# Setup for Dramatiq Results Backend
# Assuming config has REDIS_HOST, REDIS_PORT, REDIS_PASSWORD, REDIS_SSL
# Fallback to typical defaults if specific config values are not found.
redis_host = getattr(config, 'REDIS_HOST', 'redis')
redis_port = int(getattr(config, 'REDIS_PORT', 6379))
redis_password = getattr(config, 'REDIS_PASSWORD', None)
redis_ssl_str = str(getattr(config, 'REDIS_SSL', 'False')).lower()
redis_ssl = redis_ssl_str == 'true'

sync_redis_client = redis_sync.Redis(
    host=redis_host,
    port=redis_port,
    password=redis_password,
    ssl=redis_ssl,
    decode_responses=True # Important for Dramatiq results
)

results_backend = RedisBackend(client=sync_redis_client)
results_middleware = Results(backend=results_backend)

# Configure RabbitMQ broker with AsyncIO and Results middleware
current_middlewares = [dramatiq.middleware.AsyncIO()]
all_middlewares = current_middlewares + [results_middleware]

rabbitmq_broker = RabbitmqBroker(
    host=config.RABBITMQ_HOST,
    port=config.RABBITMQ_PORT,
    middleware=all_middlewares
)
dramatiq.set_broker(rabbitmq_broker)

# En backend/run_agent_background.py
# Después de dramatiq.set_broker(rabbitmq_broker)
# y después de from agent.run import run_agent (y other relevant top-level imports)

# import asyncio # Asegúrate de que asyncio esté importado (should be from previous step)
# import logging # Asegúrate de que logging esté importado (should be from previous step)
# from typing import Optional # Already present
# from agent.run import run_agent # Already present
# from agentpress.tool_orchestrator import ToolOrchestrator # Already present
# from agentpress.task_state_manager import TaskStateManager # Already present
# from services.langfuse import initialize_langfuse # langfuse is imported
# from utils.config import config # config is imported

# Placeholder: Para este actor, podríamos necesitar pasar IDs o referencias
# y luego reconstruir/re-obtener las instancias dentro del actor si no son serializables
# o no están disponibles globalmente en el contexto del worker.

@dramatiq.actor(queue_name="default", store_results=True) # Forzar uso de cola "default"
def execute_run_agent_task(thread_id: str, project_id: str, stream: bool, 
                           initial_prompt_text: Optional[str] = None, # Argumentos que necesita run_agent
                           native_max_auto_continues: int = 25,
                           max_iterations: int = 100,
                           model_name: str = "anthropic/claude-3-7-sonnet-latest", # O tomar de config
                           enable_thinking: Optional[bool] = False,
                           reasoning_effort: Optional[str] = 'low',
                           enable_context_manager: bool = True
                           # No pases objetos complejos como tool_orchestrator o task_state_manager directamente
                           # si no son serializables o si su estado no se comparte entre API y worker.
                           # Pasa IDs o datos necesarios para recrearlos/obtenerlos en el worker.
                           ):
    try:
        diag_logger_actor = logging.getLogger("dramatiq_actor_diag")
        if not diag_logger_actor.handlers: # Evitar duplicar handlers si el actor se llama múltiples veces
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - ACTOR - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            diag_logger_actor.addHandler(handler)
            diag_logger_actor.setLevel(logging.INFO)

        diag_logger_actor.info(f"ACTOR 'execute_run_agent_task' INVOCADO para thread_id: {thread_id}")

        # Reconstruir/obtener instancias necesarias dentro del worker si no se pasan:
        # Esto es CRÍTICO y depende de la arquitectura de tu aplicación.
        # El siguiente es un EJEMPLO y probablemente necesite un ajuste significativo.

        # Supongamos que task_state_manager y tool_orchestrator se inicializan globalmente
        # en el contexto del worker o se pueden crear bajo demanda.
        # Si ya tienes 'task_state_manager_singleton' y 'tool_orchestrator_singleton'
        # inicializados en run_agent_background.py (fuera de esta función de actor), podrías usarlos.
        # Pero deben ser seguros para usar entre diferentes ejecuciones de tareas.

        # ESTA PARTE ES LA MÁS COMPLEJA Y REQUIERE CONOCIMIENTO DE TU APLICACIÓN:
        # Cómo obtener/crear 'tool_orchestrator' y 'task_state_manager' válidos aquí.
        # Por ahora, solo loguearemos y no llamaremos a run_agent para evitar errores si no están disponibles.

        diag_logger_actor.info(f"Placeholder: Aquí se llamaría a run_agent para thread_id: {thread_id}")
        diag_logger_actor.info(f"Argumentos recibidos: project_id={project_id}, stream={stream}, initial_prompt_text_len={len(initial_prompt_text) if initial_prompt_text else 0}")

        # EJEMPLO de cómo podrías intentar llamar a run_agent (NECESITARÁ ADAPTACIÓN):
        # trace_client = initialize_langfuse(config, user_id="dramatiq_worker", session_id=thread_id) # O alguna forma de obtenerlo
        # tool_orchestrator_instance = ToolOrchestrator(project_id, trace_client) # Esto es una suposición
        # task_state_manager_instance = TaskStateManager(project_id, supabase_client_override=None) # Esto es una suposición
        # 
        # asyncio.run(run_agent(
        #     thread_id=thread_id,
        #     project_id=project_id,
        #     stream=stream, # run_agent es async, stream=True podría no tener sentido aquí si el actor es síncrono y devuelve resultado
        #     tool_orchestrator=tool_orchestrator_instance,
        #     task_state_manager=task_state_manager_instance,
        #     initial_prompt_text=initial_prompt_text, # Pasar el prompt
        #     native_max_auto_continues=native_max_auto_continues,
        #     max_iterations=max_iterations,
        #     model_name=model_name,
        #     enable_thinking=enable_thinking,
        #     reasoning_effort=reasoning_effort,
        #     enable_context_manager=enable_context_manager,
        #     trace=trace_client
        # ))
        # diag_logger_actor.info(f"LLAMADA ASÍNCRONA A run_agent COMPLETADA para thread_id: {thread_id}")

    except Exception as e:
        diag_logger_actor.error(f"ACTOR 'execute_run_agent_task' FALLÓ para thread_id: {thread_id}. Error: {str(e)}", exc_info=True)
        raise # Re-lanzar la excepción para que Dramatiq la maneje (ej. reintentos)

_initialized = False
db = DBConnection()
instance_id = "single"

worker_logger = setup_logger('WORKER')

async def initialize():
    """Initialize the agent API with resources from the main API."""
    global db, instance_id, _initialized
    if _initialized:
        return

    # Use provided instance_id or generate a new one
    if not instance_id:
        # Generate instance ID
        instance_id = str(uuid.uuid4())[:8]
    await redis.initialize_async()
    await db.initialize()

    _initialized = True
    worker_logger.info(f"Initialized agent API with instance ID: {instance_id}")


@dramatiq.actor(queue_name="default")
async def run_agent_background(
    agent_run_id: str,
    thread_id: str,
    instance_id: str, # Use the global instance ID passed during initialization
    project_id: str,
    model_name: str,
    enable_thinking: Optional[bool],
    reasoning_effort: Optional[str],
    stream: bool,
    enable_context_manager: bool
):
    worker_logger.error("TEST_WORKER_ERROR_LOG: This is a test error from the worker startup.")
    """Run the agent in the background using Redis for state."""
    worker_logger.info(f"Entering run_agent_background task for agent_run_id: {agent_run_id}, thread_id: {thread_id}, project_id: {project_id}, model: {model_name}")
    await initialize()

    sentry.sentry.set_tag("thread_id", thread_id)

    worker_logger.info(f"Starting background agent run: {agent_run_id} for thread: {thread_id} (Instance: {instance_id})")
    worker_logger.info(f"🚀 Using model: {model_name} (thinking: {enable_thinking}, reasoning_effort: {reasoning_effort})")

    client = await db.client
    start_time = datetime.now(timezone.utc)
    total_responses = 0
    pubsub = None
    stop_checker = None
    stop_signal_received = False

    # Define Redis keys and channels
    response_list_key = f"agent_run:{agent_run_id}:responses"
    response_channel = f"agent_run:{agent_run_id}:new_response"
    instance_control_channel = f"agent_run:{agent_run_id}:control:{instance_id}"
    global_control_channel = f"agent_run:{agent_run_id}:control"
    instance_active_key = f"active_run:{instance_id}:{agent_run_id}"

    async def check_for_stop_signal():
        nonlocal stop_signal_received
        if not pubsub: return
        try:
            while not stop_signal_received:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                if message and message.get("type") == "message":
                    data = message.get("data")
                    if isinstance(data, bytes): data = data.decode('utf-8')
                    if data == "STOP":
                        worker_logger.info(f"Received STOP signal for agent run {agent_run_id} (Instance: {instance_id})")
                        stop_signal_received = True
                        break
                # Periodically refresh the active run key TTL
                if total_responses % 50 == 0: # Refresh every 50 responses or so
                    try: await redis.expire(instance_active_key, redis.REDIS_KEY_TTL)
                    except Exception as ttl_err: worker_logger.warning(f"Failed to refresh TTL for {instance_active_key}: {ttl_err}")
                await asyncio.sleep(0.1) # Short sleep to prevent tight loop
        except asyncio.CancelledError:
            worker_logger.info(f"Stop signal checker cancelled for {agent_run_id} (Instance: {instance_id})")
        except Exception as e:
            worker_logger.error(f"Error in stop signal checker for {agent_run_id}: {e}", exc_info=True)
            stop_signal_received = True # Stop the run if the checker fails

    trace = langfuse.trace(name="agent_run", id=agent_run_id, session_id=thread_id, metadata={"project_id": project_id, "instance_id": instance_id})
    try:
        # Setup Pub/Sub listener for control signals
        pubsub = await redis.create_pubsub()
        await pubsub.subscribe(instance_control_channel, global_control_channel)
        worker_logger.debug(f"Subscribed to control channels: {instance_control_channel}, {global_control_channel}")
        stop_checker = asyncio.create_task(check_for_stop_signal())

        # Ensure active run key exists and has TTL
        await redis.set(instance_active_key, "running", ex=redis.REDIS_KEY_TTL)

        final_status = "running" # Initialize final_status
        error_message = None # Initialize error_message
        agent_gen = None # Initialize agent_gen to None

        try:
            # Initialize ToolOrchestrator locally for this worker context
            worker_logger.info("RUN_AGENT_BACKGROUND: Initializing ToolOrchestrator for worker...")
            local_tool_orchestrator = ToolOrchestrator()
            local_tool_orchestrator.load_tools_from_directory() # This uses the corrected absolute path
            worker_logger.info(f"RUN_AGENT_BACKGROUND: ToolOrchestrator for worker initialized. {len(local_tool_orchestrator.get_tool_schemas_for_llm())} tools loaded.")

            # Initialize TaskStateManager for this run
            worker_logger.info(f"RUN_AGENT_BACKGROUND: Initializing TaskStateManager for agent_run_id: {agent_run_id}...")
            # db.client is the initialized Supabase client from `await db.initialize()`
            # Corrected to use db_connection=db
            task_storage = SupabaseTaskStorage(db_connection=db)
            local_task_state_manager = TaskStateManager(storage=task_storage)
            # Assuming initialize method exists and is async, as per prompt context
            # If TaskStateManager's initialize is not async, remove await
            await local_task_state_manager.initialize()
            worker_logger.info("RUN_AGENT_BACKGROUND: TaskStateManager initialized.")

            # Initialize agent generator
            agent_gen = run_agent(
                thread_id=thread_id, project_id=project_id, stream=stream,
                model_name=model_name,
                enable_thinking=enable_thinking, reasoning_effort=reasoning_effort,
                enable_context_manager=enable_context_manager,
                tool_orchestrator=local_tool_orchestrator,
                task_state_manager=local_task_state_manager, # Pass the new instance
                trace=trace
            )
        except Exception as e_agent_init:
            init_error_message = f"Failed to initialize agent generator: {str(e_agent_init)}"
            traceback_str = traceback.format_exc()
            worker_logger.error(f"{init_error_message}\n{traceback_str} (AgentRunID: {agent_run_id}, Instance: {instance_id})")
            final_status = "failed"
            error_message = init_error_message # This will be used by the main except block for DB update
            # Push specific error to Redis for frontend
            error_response_init = {"type": "status", "status": "error", "message": init_error_message}
            try:
                await redis.rpush(response_list_key, json.dumps(error_response_init))
                await redis.publish(response_channel, "new")
            except Exception as redis_err_init:
                 worker_logger.error(f"Failed to push agent initialization error to Redis for {agent_run_id}: {redis_err_init}")
            # Raise the exception to be caught by the main try-except block for consistent error handling
            raise e_agent_init


        if agent_gen: # Proceed only if agent_gen was successfully initialized
            async for response in agent_gen:
                if stop_signal_received:
                    worker_logger.info(f"Agent run {agent_run_id} stopped by signal.")
                    final_status = "stopped"
                    # It's better to create a status message for Redis here if we want immediate feedback on stop
                    stop_message_obj = {"type": "status", "status": "stopped", "message": "Agent run stopped by signal."}
                    try:
                        await redis.rpush(response_list_key, json.dumps(stop_message_obj))
                        await redis.publish(response_channel, "new")
                    except Exception as e_redis_stop:
                        worker_logger.warning(f"Failed to push stop signal message to Redis for {agent_run_id}: {e_redis_stop}")
                    trace.span(name="agent_run_stopped").end(status_message="agent_run_stopped", level="WARNING")
                    break

                try:
                    # Store response in Redis list and publish notification
                    response_json = json.dumps(response) # response is already a dict from run_agent
                    asyncio.create_task(redis.rpush(response_list_key, response_json))
                    asyncio.create_task(redis.publish(response_channel, "new"))
                    total_responses += 1

                    # Check for agent-signaled completion or error
                    if response.get('type') == 'status':
                         status_val = response.get('status')
                         if status_val in ['completed', 'failed', 'stopped']:
                             worker_logger.info(f"Agent run {agent_run_id} finished via status message: {status_val}")
                             final_status = status_val
                             if status_val == 'failed' or status_val == 'stopped':
                                 # Ensure error_message is a string. If response['message'] is complex, serialize or simplify.
                                 raw_msg = response.get('message', f"Run ended with status: {status_val}")
                                 error_message = raw_msg if isinstance(raw_msg, str) else json.dumps(raw_msg)
                             break
                except Exception as e_loop_redis:
                    loop_error_message = f"Error processing/pushing agent response to Redis: {str(e_loop_redis)}"
                    traceback_str_loop = traceback.format_exc()
                    worker_logger.error(f"{loop_error_message}\n{traceback_str_loop} (AgentRunID: {agent_run_id}, Response: {response})")
                    final_status = "failed"
                    error_message = loop_error_message # This will be used by the main except block for DB update
                    # Push specific error to Redis for frontend
                    error_response_loop = {"type": "status", "status": "error", "message": loop_error_message}
                    try:
                        await redis.rpush(response_list_key, json.dumps(error_response_loop))
                        await redis.publish(response_channel, "new")
                    except Exception as redis_err_loop:
                         worker_logger.error(f"Failed to push loop processing error to Redis for {agent_run_id}: {redis_err_loop}")
                    # Break from the loop as we can't reliably process further responses
                    break

            # If loop finished without explicit completion/error/stop signal, mark as completed
            if final_status == "running":
                 final_status = "completed"
                 duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                 worker_logger.info(f"Agent run {agent_run_id} completed normally (duration: {duration:.2f}s, responses: {total_responses})")
                 completion_message = {"type": "status", "status": "completed", "message": "Agent run completed successfully"}
                 trace.span(name="agent_run_completed").end(status_message="agent_run_completed")
                 try:
                     await redis.rpush(response_list_key, json.dumps(completion_message))
                     await redis.publish(response_channel, "new") # Notify about the completion message
                 except Exception as e_redis_complete:
                     worker_logger.error(f"Failed to push completion message to Redis for {agent_run_id}: {e_redis_complete}")
                     # The run is still considered complete, but frontend might not get the last message.
                     # The overall status will be updated in DB.

        # Fetch final responses from Redis for DB update (ensuring this is always done)
        all_responses_json = await redis.lrange(response_list_key, 0, -1)
        all_responses = [json.loads(r) for r in all_responses_json]

        # Update DB status
        await update_agent_run_status(client, agent_run_id, final_status, error=error_message, responses=all_responses)

        # Publish final control signal (END_STREAM or ERROR)
        control_signal = "END_STREAM" if final_status == "completed" else "ERROR" if final_status == "failed" else "STOP"
        try:
            await redis.publish(global_control_channel, control_signal)
            # No need to publish to instance channel as the run is ending on this instance
            worker_logger.debug(f"Published final control signal '{control_signal}' to {global_control_channel}")
        except Exception as e:
            worker_logger.warning(f"Failed to publish final control signal {control_signal}: {str(e)}")

    except Exception as e:
        error_message = str(e)
        traceback_str = traceback.format_exc()
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        worker_logger.error(f"Error in agent run {agent_run_id} after {duration:.2f}s: {error_message}\n{traceback_str} (Instance: {instance_id})")
        final_status = "failed"
        trace.span(name="agent_run_failed").end(status_message=error_message, level="ERROR")

        # Push error message to Redis list
        error_response = {"type": "status", "status": "error", "message": error_message}
        try:
            await redis.rpush(response_list_key, json.dumps(error_response))
            await redis.publish(response_channel, "new")
        except Exception as redis_err:
             worker_logger.error(f"Failed to push error response to Redis for {agent_run_id}: {redis_err}")

        # Fetch final responses (including the error)
        all_responses = []
        try:
             all_responses_json = await redis.lrange(response_list_key, 0, -1)
             all_responses = [json.loads(r) for r in all_responses_json]
        except Exception as fetch_err:
             worker_logger.error(f"Failed to fetch responses from Redis after error for {agent_run_id}: {fetch_err}")
             all_responses = [error_response] # Use the error message we tried to push

        # Update DB status
        await update_agent_run_status(client, agent_run_id, "failed", error=f"{error_message}\n{traceback_str}", responses=all_responses)

        # Publish ERROR signal
        try:
            await redis.publish(global_control_channel, "ERROR")
            worker_logger.debug(f"Published ERROR signal to {global_control_channel}")
        except Exception as e:
            worker_logger.warning(f"Failed to publish ERROR signal: {str(e)}")

    finally:
        # Cleanup stop checker task
        if stop_checker and not stop_checker.done():
            stop_checker.cancel()
            try: await stop_checker
            except asyncio.CancelledError: pass
            except Exception as e: worker_logger.warning(f"Error during stop_checker cancellation: {e}")

        # Close pubsub connection
        if pubsub:
            try:
                await pubsub.unsubscribe()
                await pubsub.close()
                worker_logger.debug(f"Closed pubsub connection for {agent_run_id}")
            except Exception as e:
                worker_logger.warning(f"Error closing pubsub for {agent_run_id}: {str(e)}")

        # Set TTL on the response list in Redis
        await _cleanup_redis_response_list(agent_run_id)

        # Remove the instance-specific active run key
        await _cleanup_redis_instance_key(agent_run_id)
        
        # --- Workspace Cleanup and Sandbox Stopping ---
        try:
            worker_logger.info(f"Starting workspace cleanup and sandbox stop for project: {project_id} in run {agent_run_id}")
            client = await db.client
            project_result = await client.table('projects').select('sandbox').eq('project_id', project_id).maybe_single().execute()
            sandbox_id_for_cleanup_and_stop = None

            if project_result and project_result.data: # Check project_result.data
                sandbox_info = project_result.data.get('sandbox')
                if sandbox_info and isinstance(sandbox_info, dict): # Ensure sandbox_info is a dict
                    sandbox_id_for_cleanup_and_stop = sandbox_info.get('id')
                else:
                    worker_logger.warning(f"Sandbox info for project {project_id} is not in the expected format or missing: {sandbox_info}")
            else:
                worker_logger.warning(f"No project data found for project_id {project_id} when attempting sandbox cleanup.")

            if sandbox_id_for_cleanup_and_stop:
                sandbox_instance = None # Define here to ensure it's in scope for stopping if cleanup fails partially
                try:
                    worker_logger.info(f"Fetching sandbox instance for ID: {sandbox_id_for_cleanup_and_stop}")
                    sandbox_instance = await get_or_start_sandbox(sandbox_id_for_cleanup_and_stop)

                    if sandbox_instance:
                        # 1. Perform Workspace Cleanup
                        worker_logger.info(f"Attempting workspace cleanup for sandbox: {sandbox_id_for_cleanup_and_stop}")
                        cleanup_session_id = f"cleanup_ws_{uuid.uuid4().hex[:8]}"
                        try:
                            if use_daytona():
                                sandbox_instance.process.create_session(cleanup_session_id)
                            else:
                                sandbox_instance['process']['create_session'](cleanup_session_id)
                            worker_logger.debug(f"Created session {cleanup_session_id} for workspace cleanup.")

                            cleanup_script_str = """
print("Python cleanup script started successfully.", flush=True)
import os
import shutil # Keep for rmtree just in case, though os.rmdir is preferred for empty.

def robust_remove(path):
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
            print(f"Deleted file/link: {path}", flush=True)
        elif os.path.isdir(path):
            # For safety, only remove if it's truly empty or use shutil.rmtree if non-empty is intended for some patterns
            # The original find commands were specific about deleting empty dirs or files by pattern.
            # Let's stick to os.remove for files and os.rmdir for empty dirs.
            pass # Handled by directory walk
    except OSError as e:
        print(f"Error deleting {path}: {e}", flush=True)

workspace_root = "/workspace"
files_deleted_count = 0
dirs_deleted_count = 0

# Delete specific patterned files
patterns_to_delete = ["*.tmp", "temp_*", "*_temp.*"]
print(f"Starting file deletion pass for patterns: {patterns_to_delete}", flush=True)
for dirpath, dirnames, filenames in os.walk(workspace_root):
    for filename in filenames:
        full_path = os.path.join(dirpath, filename)
        if filename.endswith(".tmp"):
            robust_remove(full_path)
            files_deleted_count += 1
        elif filename.startswith("temp_"):
            robust_remove(full_path)
            files_deleted_count += 1
        elif "_temp." in filename: # Simplified from '*_temp.*' to catch 'name_temp.ext'
            robust_remove(full_path)
            files_deleted_count += 1
print(f"Completed file deletion pass. Files deleted: {files_deleted_count}", flush=True)

# Delete empty directories (bottom-up)
print("Starting empty directory deletion pass...", flush=True)
for dirpath, dirnames, filenames in os.walk(workspace_root, topdown=False):
    if not os.listdir(dirpath): # Check if directory is empty
        try:
            os.rmdir(dirpath)
            print(f"Deleted empty directory: {dirpath}", flush=True)
            dirs_deleted_count += 1
        except OSError as e:
            print(f"Error deleting directory {dirpath}: {e}", flush=True)
print(f"Completed empty directory deletion pass. Directories deleted: {dirs_deleted_count}", flush=True)

print(f"Python cleanup script finished. Total files deleted: {files_deleted_count}, Total empty dirs deleted: {dirs_deleted_count}", flush=True)
"""
                            # Python interpreter probing
                            python_executables = ["/usr/bin/python3", "/usr/local/bin/python3", "python3", "python"]
                            found_python_executable = None

                            # Log current PATH
                            try:
                                path_cmd_req = SessionExecuteRequest(command="echo $PATH", var_async=False, cwd="/workspace")
                                if use_daytona():
                                    path_cmd_resp = await sandbox_instance.process.execute_session_command(cleanup_session_id, path_cmd_req, timeout=30)
                                else:
                                    path_cmd_resp = sandbox_instance['process']['execute_session_command'](cleanup_session_id, path_cmd_req)

                                path_logs_resp_str = "Could not retrieve PATH logs."
                                if path_cmd_resp and path_cmd_resp.get('exit_code') == 0:
                                    if use_daytona():
                                        # Daytona-specific log retrieval (assuming it works if Daytona is used)
                                        path_logs = await sandbox_instance.process.get_session_command_logs(cleanup_session_id, path_cmd_resp['cmd_id'])
                                        path_logs_resp_str = path_logs.stdout if path_logs and path_logs.stdout else (path_logs.stderr if path_logs and path_logs.stderr else "No output from echo $PATH")
                                    else: # local_sandbox
                                        # For local_sandbox, 'output' contains combined stdout/stderr
                                        path_logs_resp_str = path_cmd_resp.get('output', "No output from echo $PATH for local sandbox").strip()
                                elif path_cmd_resp:
                                     worker_logger.warning(f"echo $PATH failed with exit code {path_cmd_resp.get('exit_code')}. Output: {path_cmd_resp.get('output', '').strip()}")
                                worker_logger.info(f"Sandbox PATH environment variable for cleanup: {path_logs_resp_str}") # Removed "(attempted with forced PATH)"
                            except Exception as e_path:
                                worker_logger.warning(f"Could not determine sandbox PATH: {e_path}")

                            # Attempt 1: Use 'command -v' to find python3 or python
                            find_python_cmd = "sh -c 'command -v python3 || command -v python'" # Reverted
                            worker_logger.info(f"Attempting to find Python using 'command -v'...") # Removed "with PATH..."
                            cmd_v_req = SessionExecuteRequest(command=find_python_cmd, var_async=False, cwd="/workspace")
                            response_cmd_v = None
                            try:
                                if use_daytona():
                                    response_cmd_v = await sandbox_instance.process.execute_session_command(cleanup_session_id, cmd_v_req, timeout=10)
                                else:
                                    response_cmd_v = sandbox_instance['process']['execute_session_command'](cleanup_session_id, cmd_v_req)

                                if response_cmd_v and response_cmd_v.get('exit_code') == 0:
                                    cmd_v_logs_output = ""
                                    if use_daytona():
                                        cmd_v_logs = await sandbox_instance.process.get_session_command_logs(cleanup_session_id, response_cmd_v['cmd_id'])
                                        cmd_v_logs_output = cmd_v_logs.stdout if cmd_v_logs and cmd_v_logs.stdout else ""
                                    else: # local_sandbox
                                        # For local_sandbox, 'output' contains combined stdout/stderr
                                        # If exit_code is 0, this should be the path.
                                        cmd_v_logs_output = response_cmd_v.get('output', "").strip()

                                    python_path_from_cmd_v = cmd_v_logs_output.strip()
                                    python_path_from_cmd_v = cmd_v_logs_output.strip()
                                    if python_path_from_cmd_v: # Check if not empty
                                        worker_logger.info(f"'command -v' found Python at: {python_path_from_cmd_v}")
                                        # Verify this path works with a simple command
                                        verify_cmd = f"{python_path_from_cmd_v} -c \"print('Python probe success via command -v')\"" # Corrected: Unescaped single quotes
                                        verify_req = SessionExecuteRequest(command=verify_cmd, var_async=False, cwd="/workspace")
                                        response_verify = None
                                        if use_daytona():
                                            response_verify = await sandbox_instance.process.execute_session_command(cleanup_session_id, verify_req, timeout=10)
                                        else:
                                            response_verify = sandbox_instance['process']['execute_session_command'](cleanup_session_id, verify_req)

                                        if response_verify and response_verify.get('exit_code') == 0:
                                            found_python_executable = python_path_from_cmd_v
                                            worker_logger.info(f"Successfully verified Python at {found_python_executable} (found by 'command -v').")
                                        else:
                                            verify_output = response_verify.get('output', '') if not use_daytona() else "N/A (Daytona: check separate logs)"
                                            worker_logger.warning(f"Python path '{python_path_from_cmd_v}' from 'command -v' failed verification. Exit code: {response_verify.get('exit_code') if response_verify else 'N/A'}. Output: {verify_output}")
                                    else:
                                        worker_logger.info("'command -v' did not return a path.")
                                else:
                                    cmd_v_output = response_cmd_v.get('output', '') if response_cmd_v and not use_daytona() else "N/A (Daytona: check separate logs or no response_cmd_v)"
                                    worker_logger.info(f"'command -v' failed or returned non-zero exit. Exit code: {response_cmd_v.get('exit_code') if response_cmd_v else 'N/A'}. Output: {cmd_v_output}")
                            except Exception as e_cmd_v:
                                worker_logger.warning(f"Exception during 'command -v' Python probe: {e_cmd_v}")

                            # Attempt 2: Fallback to predefined list if 'command -v' failed
                            if not found_python_executable:
                                worker_logger.info("Python not found via 'command -v', falling back to predefined list.") # Removed "with PATH"
                                for exe_path in python_executables:
                                    worker_logger.info(f"Probing for Python interpreter at: {exe_path}") # Removed "with PATH..."
                                    test_cmd = f"{exe_path} -c \"print('Python probe success')\"" # Corrected: Unescaped single quotes
                                    probe_req = SessionExecuteRequest(command=test_cmd, var_async=False, cwd="/workspace")
                                    try:
                                        if use_daytona():
                                            response_probe = await sandbox_instance.process.execute_session_command(cleanup_session_id, probe_req, timeout=10)
                                        else:
                                            response_probe = sandbox_instance['process']['execute_session_command'](cleanup_session_id, probe_req)

                                        if response_probe and response_probe.get('exit_code') == 0:
                                            found_python_executable = exe_path
                                            worker_logger.info(f"Found working Python interpreter: {found_python_executable}")
                                            probe_logs_output = "Could not retrieve probe logs."
                                            if use_daytona():
                                                probe_logs = await sandbox_instance.process.get_session_command_logs(cleanup_session_id, response_probe['cmd_id'])
                                                probe_logs_output = probe_logs.stdout if probe_logs and probe_logs.stdout else (probe_logs.stderr if probe_logs and probe_logs.stderr else "No output from probe")
                                            else: # local_sandbox
                                                probe_logs_output = response_probe.get('output', "No output from probe for local_sandbox").strip()
                                            worker_logger.debug(f"Probe success output for {exe_path}: {probe_logs_output.strip()}")
                                            break
                                        else:
                                            probe_output_fallback = "N/A"
                                            if response_probe:
                                                probe_output_fallback = response_probe.get('output', '') if not use_daytona() else "N/A (Daytona: check separate logs)"
                                            worker_logger.warning(f"Probe failed for {exe_path}. Exit code: {response_probe.get('exit_code') if response_probe else 'N/A'}. Output: {probe_output_fallback.strip()}")
                                    except Exception as e_probe:
                                        worker_logger.warning(f"Exception during probe for {exe_path}: {e_probe}")

                            if found_python_executable:
                                worker_logger.debug(f"Attempting to execute Python cleanup script (first 200 chars): {cleanup_script_str[:200]}...")
                                # Escape the script for shell command line execution (for python -c "...")
                                escaped_python_script = cleanup_script_str.replace('\\', '\\\\').replace('"', '\\"') # Reverted
                                python_exec_command = f"{found_python_executable} -c \"{escaped_python_script}\"" # Reverted

                                worker_logger.debug(f"Executing Python cleanup script in session {cleanup_session_id} using {found_python_executable}") # Removed "with PATH..."
                                exec_req_python = SessionExecuteRequest(command=python_exec_command, var_async=False, cwd="/workspace")

                                if use_daytona():
                                    response_python_clean = await sandbox_instance.process.execute_session_command(cleanup_session_id, exec_req_python, timeout=120)
                                else:
                                    response_python_clean = sandbox_instance['process']['execute_session_command'](cleanup_session_id, exec_req_python)

                                if response_python_clean and response_python_clean.get('exit_code') == 0:
                                    worker_logger.info(f"Python-based sandbox cleanup script using '{found_python_executable}' executed successfully.")
                                else:
                                    worker_logger.warning(f"Python-based sandbox cleanup script using '{found_python_executable}' failed. Exit: {response_python_clean.get('exit_code') if response_python_clean else 'N/A'}.")

                                try:
                                    logs_output_python = "Could not retrieve logs from Python cleanup."
                                    if response_python_clean: # Check if response_python_clean is not None
                                        if use_daytona():
                                            logs_python = await sandbox_instance.process.get_session_command_logs(cleanup_session_id, response_python_clean['cmd_id'])
                                            logs_output_python = logs_python.stdout if logs_python and logs_python.stdout else (logs_python.stderr if logs_python and logs_python.stderr else "No output captured from Python script")
                                        else: # local_sandbox
                                            # For local_sandbox, 'output' contains combined stdout/stderr
                                            logs_output_python = response_python_clean.get('output', "No output from Python script for local_sandbox").strip()
                                    worker_logger.info(f"Python cleanup script output (using {found_python_executable}):\n{logs_output_python}")
                                except Exception as e_logs_python:
                                    worker_logger.error(f"Error retrieving logs for Python cleanup script (using {found_python_executable}): {e_logs_python}")
                            else:
                                worker_logger.error("No working Python interpreter found in sandbox after probing. Python cleanup script will not run.")

                        except Exception as e_cleanup_ws:
                            worker_logger.error(f"Error during Python-based workspace cleanup for sandbox {sandbox_id_for_cleanup_and_stop}: {e_cleanup_ws}", exc_info=True)
                        finally:
                            # Ensure sandbox_instance is still valid before trying to delete session
                            if sandbox_instance:
                                try:
                                    worker_logger.debug(f"Deleting cleanup session {cleanup_session_id} for sandbox {sandbox_id_for_cleanup_and_stop}.")
                                    if use_daytona():
                                        sandbox_instance.process.delete_session(cleanup_session_id)
                                    else:
                                        sandbox_instance['process']['delete_session'](cleanup_session_id)
                                except Exception as e_del_session:
                                    worker_logger.error(f"Error deleting cleanup session {cleanup_session_id}: {e_del_session}", exc_info=True)
                            else:
                                worker_logger.warning(f"Skipping cleanup session deletion as sandbox_instance is None for {sandbox_id_for_cleanup_and_stop}")
                        # 2. Stop the Sandbox
                        worker_logger.info(f"Attempting to stop sandbox: {sandbox_id_for_cleanup_and_stop} after cleanup.")
                        # Ensure sandbox_instance is still valid before stopping
                        if sandbox_instance:
                            if use_daytona():
                                worker_logger.info(f"Using Daytona to stop sandbox: {sandbox_id_for_cleanup_and_stop}")
                                current_state = sandbox_instance.info().state
                                worker_logger.info(f"Daytona sandbox {sandbox_id_for_cleanup_and_stop} current state before stop attempt: {current_state}")
                                if current_state not in [WorkspaceState.STOPPED, WorkspaceState.ARCHIVED, WorkspaceState.STOPPING, WorkspaceState.ARCHIVING]:
                                    await daytona.stop(sandbox_instance)
                                    worker_logger.info(f"Successfully sent stop command to Daytona sandbox {sandbox_id_for_cleanup_and_stop}")
                                else:
                                    worker_logger.info(f"Daytona sandbox {sandbox_id_for_cleanup_and_stop} is already in state '{current_state}', no stop action needed.")
                            else: # Local sandbox
                                worker_logger.info(f"Using local_sandbox to stop sandbox: {sandbox_id_for_cleanup_and_stop}")
                                from sandbox.local_sandbox import local_sandbox # Ensure import
                                current_state = sandbox_instance['info']()['state']
                                worker_logger.info(f"Local sandbox {sandbox_id_for_cleanup_and_stop} current state before stop attempt: {current_state}")
                                if current_state not in ['exited', 'stopped', 'stopping']:
                                    local_sandbox.stop(sandbox_instance)
                                    worker_logger.info(f"Successfully called stop for local sandbox {sandbox_id_for_cleanup_and_stop}")
                                else:
                                    worker_logger.info(f"Local sandbox {sandbox_id_for_cleanup_and_stop} is already in state '{current_state}', no stop action needed.")
                        else:
                             worker_logger.warning(f"Skipping sandbox stop as sandbox_instance is None for {sandbox_id_for_cleanup_and_stop}")
                    else:
                        worker_logger.warning(f"Could not retrieve valid sandbox instance for ID: {sandbox_id_for_cleanup_and_stop} (it was None). Skipping cleanup and stop.")

                except Exception as e_get_stop_sandbox:
                    worker_logger.error(f"Error during getting, cleaning, or stopping sandbox {sandbox_id_for_cleanup_and_stop}: {e_get_stop_sandbox}", exc_info=True)
            else:
                worker_logger.info(f"No valid sandbox_id found for project {project_id}; skipping workspace cleanup and stop.") # Changed to info from warning

        except Exception as e_outer_finally:
            worker_logger.error(f"Outer error in finally block for workspace cleanup/stop for project {project_id}: {e_outer_finally}", exc_info=True)
        # --- End Workspace Cleanup and Sandbox Stopping ---

        worker_logger.info(f"Agent run background task fully completed for: {agent_run_id} (Instance: {instance_id}) with final status: {final_status}")

async def _cleanup_redis_instance_key(agent_run_id: str):
    """Clean up the instance-specific Redis key for an agent run."""
    if not instance_id:
        worker_logger.warning("Instance ID not set, cannot clean up instance key.")
        return
    key = f"active_run:{instance_id}:{agent_run_id}"
    worker_logger.debug(f"Cleaning up Redis instance key: {key}")
    try:
        await redis.delete(key)
        worker_logger.debug(f"Successfully cleaned up Redis key: {key}")
    except Exception as e:
        worker_logger.warning(f"Failed to clean up Redis key {key}: {str(e)}")

# TTL for Redis response lists (24 hours)
REDIS_RESPONSE_LIST_TTL = 3600 * 24

async def _cleanup_redis_response_list(agent_run_id: str):
    """Set TTL on the Redis response list."""
    response_list_key = f"agent_run:{agent_run_id}:responses"
    try:
        await redis.expire(response_list_key, REDIS_RESPONSE_LIST_TTL)
        worker_logger.debug(f"Set TTL ({REDIS_RESPONSE_LIST_TTL}s) on response list: {response_list_key}")
    except Exception as e:
        worker_logger.warning(f"Failed to set TTL on response list {response_list_key}: {str(e)}")

async def update_agent_run_status(
    client,
    agent_run_id: str,
    status: str,
    error: Optional[str] = None,
    responses: Optional[list[any]] = None # Expects parsed list of dicts
) -> bool:
    """
    Centralized function to update agent run status.
    Returns True if update was successful.
    """
    try:
        update_data = {
            "status": status,
            "completed_at": datetime.now(timezone.utc).isoformat()
        }

        if error:
            update_data["error"] = error

        if responses:
            # Ensure responses are stored correctly as JSONB
            update_data["responses"] = responses

        # Retry up to 3 times
        for retry in range(3):
            try:
                update_result = await client.table('agent_runs').update(update_data).eq("id", agent_run_id).execute()

                if hasattr(update_result, 'data') and update_result.data:
                    worker_logger.info(f"Successfully updated agent run {agent_run_id} status to '{status}' (retry {retry})")

                    # Verify the update
                    verify_result = await client.table('agent_runs').select('status', 'completed_at').eq("id", agent_run_id).execute()
                    if verify_result.data:
                        actual_status = verify_result.data[0].get('status')
                        completed_at = verify_result.data[0].get('completed_at')
                        worker_logger.info(f"Verified agent run update: status={actual_status}, completed_at={completed_at}")
                    return True
                else:
                    worker_logger.warning(f"Database update returned no data for agent run {agent_run_id} on retry {retry}: {update_result}")
                    if retry == 2:  # Last retry
                        worker_logger.error(f"Failed to update agent run status after all retries: {agent_run_id}")
                        return False
            except Exception as db_error:
                worker_logger.error(f"Database error on retry {retry} updating status for {agent_run_id}: {str(db_error)}")
                if retry < 2:  # Not the last retry yet
                    await asyncio.sleep(0.5 * (2 ** retry))  # Exponential backoff
                else:
                    worker_logger.error(f"Failed to update agent run status after all retries: {agent_run_id}", exc_info=True)
                    return False
    except Exception as e:
        worker_logger.error(f"Unexpected error updating agent run status for {agent_run_id}: {str(e)}", exc_info=True)
        return False

    return False

# --- INICIO DEL BLOQUE DE DIAGNÓSTICO ---
# (Asegúrate de que 'import dramatiq', 'import os' estén al principio del archivo)
# (Y que 'logger' esté disponible o usar print como se muestra)
import dramatiq
import os
import logging # Logger básico para asegurar la salida

# Configuración de un logger simple si el logger global no está disponible aquí
diag_logger = logging.getLogger("dramatiq_diag_special")
if not diag_logger.handlers:
    diag_handler = logging.StreamHandler()
    diag_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    diag_handler.setFormatter(diag_formatter)
    diag_logger.addHandler(diag_handler)
    diag_logger.setLevel(logging.INFO)

if __name__ == "__main__":
    try:
        diag_logger.info("DRAMATIQ_DIAG_START: Iniciando diagnóstico de Dramatiq en el worker.")
        
        diag_logger.info("DRAMATIQ_DIAG_ENV: Listando variables de entorno relevantes:")
        for key, value in os.environ.items():
            if "DRAMATIQ" in key.upper() or "RABBITMQ" in key.upper() or "QUEUE" in key.upper() or key == "ENV_MODE":
                diag_logger.info(f"  DRAMATIQ_DIAG_ENV_VAR: {key}={value}")

        diag_logger.info("DRAMATIQ_DIAG_BROKER: Verificando broker actual...")
        current_broker = dramatiq.get_broker()
        diag_logger.info(f"  DRAMATIQ_DIAG_BROKER_INSTANCE: {current_broker}")
        if hasattr(current_broker, 'options'):
            diag_logger.info(f"  DRAMATIQ_DIAG_BROKER_OPTIONS: {current_broker.options}")

        diag_logger.info("DRAMATIQ_DIAG_ACTORS: Listando actores registrados y sus colas (desde broker.actors):")
        if hasattr(current_broker, 'actors') and current_broker.actors:
            if current_broker.actors:
                for actor_name_key, actor_instance_val in current_broker.actors.items():
                    diag_logger.info(f"  DRAMATIQ_DIAG_ACTOR_DETAIL: Name='{actor_name_key}', Queue='{actor_instance_val.queue_name}', Options='{actor_instance_val.options}', Func='{actor_instance_val.fn.__module__}.{actor_instance_val.fn.__name__}'")
            else:
                diag_logger.info("  DRAMATIQ_DIAG_ACTOR_DETAIL: No actors found registered via current_broker.actors.")
        else:
            diag_logger.info("  DRAMATIQ_DIAG_ACTOR_DETAIL: Atributo current_broker.actors no disponible o vacío.")
        
        diag_logger.info("DRAMATIQ_DIAG_REGISTRY: Verificando registro global de Dramatiq (dramatiq._REGISTRY):")
        if hasattr(dramatiq, '_REGISTRY') and hasattr(dramatiq._REGISTRY, 'get_actors'):
            actors_in_registry = list(dramatiq._REGISTRY.get_actors())
            if actors_in_registry:
                diag_logger.info(f"  DRAMATIQ_DIAG_REGISTRY: Encontrados {len(actors_in_registry)} actores en el registro global:")
                for act_instance in actors_in_registry:
                    diag_logger.info(f"  DRAMATIQ_DIAG_REGISTRY_ACTOR_DETAIL: Name='{act_instance.actor_name}', Queue='{act_instance.queue_name}', Options='{act_instance.options}', Func='{act_instance.fn.__module__}.{act_instance.fn.__name__}'")
            else:
                diag_logger.info("  DRAMATIQ_DIAG_REGISTRY: Registro global de actores vacío.")
        else:
            diag_logger.info("  DRAMATIQ_DIAG_REGISTRY: No se pudo acceder al registro global de actores de Dramatiq (_REGISTRY o get_actors).")

        diag_logger.info("DRAMATIQ_DIAG_END: Fin del diagnóstico de Dramatiq en el worker.")

    except Exception as e:
        # Usar print como último recurso si el logger falla
        print(f"DRAMATIQ_DIAG_ERROR_FALLBACK_PRINT: No se pudo completar el diagnóstico de Dramatiq: {str(e)}", flush=True)
        if 'diag_logger' in locals() and diag_logger:
             diag_logger.error(f"DRAMATIQ_DIAG_ERROR: No se pudo completar el diagnóstico de Dramatiq: {e}", exc_info=True)
# --- FIN DEL BLOQUE DE DIAGNÓSTICO ---
