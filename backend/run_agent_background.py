import sentry
import asyncio
import json
import traceback
from datetime import datetime, timezone
from typing import Optional
from services import redis
from agent.run import run_agent
from utils.logger import logger
import dramatiq
import uuid
from agentpress.thread_manager import ThreadManager
from services.supabase import DBConnection
from services import redis
from dramatiq.brokers.rabbitmq import RabbitmqBroker
from utils.config import config # Added import
from services.langfuse import langfuse
from agentpress.tool_orchestrator import ToolOrchestrator
# Imports for sandbox stopping
from sandbox.sandbox import get_or_start_sandbox, daytona, use_daytona # Modified import
from daytona_api_client.models.workspace_state import WorkspaceState
from daytona_sdk import SessionExecuteRequest # Added for workspace cleanup


rabbitmq_broker = RabbitmqBroker(host=config.RABBITMQ_HOST, port=config.RABBITMQ_PORT, middleware=[dramatiq.middleware.AsyncIO()]) # Use config
dramatiq.set_broker(rabbitmq_broker)

_initialized = False
db = DBConnection()
instance_id = "single"

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
    logger.info(f"Initialized agent API with instance ID: {instance_id}")


@dramatiq.actor
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
    """Run the agent in the background using Redis for state."""
    logger.info(f"Entering run_agent_background task for agent_run_id: {agent_run_id}, thread_id: {thread_id}, project_id: {project_id}, model: {model_name}")
    await initialize()

    sentry.sentry.set_tag("thread_id", thread_id)

    logger.info(f"Starting background agent run: {agent_run_id} for thread: {thread_id} (Instance: {instance_id})")
    logger.info(f"🚀 Using model: {model_name} (thinking: {enable_thinking}, reasoning_effort: {reasoning_effort})")

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
                        logger.info(f"Received STOP signal for agent run {agent_run_id} (Instance: {instance_id})")
                        stop_signal_received = True
                        break
                # Periodically refresh the active run key TTL
                if total_responses % 50 == 0: # Refresh every 50 responses or so
                    try: await redis.expire(instance_active_key, redis.REDIS_KEY_TTL)
                    except Exception as ttl_err: logger.warning(f"Failed to refresh TTL for {instance_active_key}: {ttl_err}")
                await asyncio.sleep(0.1) # Short sleep to prevent tight loop
        except asyncio.CancelledError:
            logger.info(f"Stop signal checker cancelled for {agent_run_id} (Instance: {instance_id})")
        except Exception as e:
            logger.error(f"Error in stop signal checker for {agent_run_id}: {e}", exc_info=True)
            stop_signal_received = True # Stop the run if the checker fails

    trace = langfuse.trace(name="agent_run", id=agent_run_id, session_id=thread_id, metadata={"project_id": project_id, "instance_id": instance_id})
    try:
        # Setup Pub/Sub listener for control signals
        pubsub = await redis.create_pubsub()
        await pubsub.subscribe(instance_control_channel, global_control_channel)
        logger.debug(f"Subscribed to control channels: {instance_control_channel}, {global_control_channel}")
        stop_checker = asyncio.create_task(check_for_stop_signal())

        # Ensure active run key exists and has TTL
        await redis.set(instance_active_key, "running", ex=redis.REDIS_KEY_TTL)

        final_status = "running" # Initialize final_status
        error_message = None # Initialize error_message
        agent_gen = None # Initialize agent_gen to None

        try:
            # Initialize ToolOrchestrator locally for this worker context
            logger.info("RUN_AGENT_BACKGROUND: Initializing ToolOrchestrator for worker...")
            local_tool_orchestrator = ToolOrchestrator()
            local_tool_orchestrator.load_tools_from_directory() # This uses the corrected absolute path
            logger.info(f"RUN_AGENT_BACKGROUND: ToolOrchestrator for worker initialized. {len(local_tool_orchestrator.get_tool_schemas_for_llm())} tools loaded.")

            # Initialize agent generator
            agent_gen = run_agent(
                thread_id=thread_id, project_id=project_id, stream=stream,
                model_name=model_name,
                enable_thinking=enable_thinking, reasoning_effort=reasoning_effort,
                enable_context_manager=enable_context_manager,
                tool_orchestrator=local_tool_orchestrator, # Use the new local instance
                trace=trace
            )
        except Exception as e_agent_init:
            init_error_message = f"Failed to initialize agent generator: {str(e_agent_init)}"
            traceback_str = traceback.format_exc()
            logger.error(f"{init_error_message}\n{traceback_str} (AgentRunID: {agent_run_id}, Instance: {instance_id})")
            final_status = "failed"
            error_message = init_error_message # This will be used by the main except block for DB update
            # Push specific error to Redis for frontend
            error_response_init = {"type": "status", "status": "error", "message": init_error_message}
            try:
                await redis.rpush(response_list_key, json.dumps(error_response_init))
                await redis.publish(response_channel, "new")
            except Exception as redis_err_init:
                 logger.error(f"Failed to push agent initialization error to Redis for {agent_run_id}: {redis_err_init}")
            # Raise the exception to be caught by the main try-except block for consistent error handling
            raise e_agent_init


        if agent_gen: # Proceed only if agent_gen was successfully initialized
            async for response in agent_gen:
                if stop_signal_received:
                    logger.info(f"Agent run {agent_run_id} stopped by signal.")
                    final_status = "stopped"
                    # It's better to create a status message for Redis here if we want immediate feedback on stop
                    stop_message_obj = {"type": "status", "status": "stopped", "message": "Agent run stopped by signal."}
                    try:
                        await redis.rpush(response_list_key, json.dumps(stop_message_obj))
                        await redis.publish(response_channel, "new")
                    except Exception as e_redis_stop:
                        logger.warning(f"Failed to push stop signal message to Redis for {agent_run_id}: {e_redis_stop}")
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
                             logger.info(f"Agent run {agent_run_id} finished via status message: {status_val}")
                             final_status = status_val
                             if status_val == 'failed' or status_val == 'stopped':
                                 # Ensure error_message is a string. If response['message'] is complex, serialize or simplify.
                                 raw_msg = response.get('message', f"Run ended with status: {status_val}")
                                 error_message = raw_msg if isinstance(raw_msg, str) else json.dumps(raw_msg)
                             break
                except Exception as e_loop_redis:
                    loop_error_message = f"Error processing/pushing agent response to Redis: {str(e_loop_redis)}"
                    traceback_str_loop = traceback.format_exc()
                    logger.error(f"{loop_error_message}\n{traceback_str_loop} (AgentRunID: {agent_run_id}, Response: {response})")
                    final_status = "failed"
                    error_message = loop_error_message # This will be used by the main except block for DB update
                    # Push specific error to Redis for frontend
                    error_response_loop = {"type": "status", "status": "error", "message": loop_error_message}
                    try:
                        await redis.rpush(response_list_key, json.dumps(error_response_loop))
                        await redis.publish(response_channel, "new")
                    except Exception as redis_err_loop:
                         logger.error(f"Failed to push loop processing error to Redis for {agent_run_id}: {redis_err_loop}")
                    # Break from the loop as we can't reliably process further responses
                    break

            # If loop finished without explicit completion/error/stop signal, mark as completed
            if final_status == "running":
                 final_status = "completed"
                 duration = (datetime.now(timezone.utc) - start_time).total_seconds()
                 logger.info(f"Agent run {agent_run_id} completed normally (duration: {duration:.2f}s, responses: {total_responses})")
                 completion_message = {"type": "status", "status": "completed", "message": "Agent run completed successfully"}
                 trace.span(name="agent_run_completed").end(status_message="agent_run_completed")
                 try:
                     await redis.rpush(response_list_key, json.dumps(completion_message))
                     await redis.publish(response_channel, "new") # Notify about the completion message
                 except Exception as e_redis_complete:
                     logger.error(f"Failed to push completion message to Redis for {agent_run_id}: {e_redis_complete}")
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
            logger.debug(f"Published final control signal '{control_signal}' to {global_control_channel}")
        except Exception as e:
            logger.warning(f"Failed to publish final control signal {control_signal}: {str(e)}")

    except Exception as e:
        error_message = str(e)
        traceback_str = traceback.format_exc()
        duration = (datetime.now(timezone.utc) - start_time).total_seconds()
        logger.error(f"Error in agent run {agent_run_id} after {duration:.2f}s: {error_message}\n{traceback_str} (Instance: {instance_id})")
        final_status = "failed"
        trace.span(name="agent_run_failed").end(status_message=error_message, level="ERROR")

        # Push error message to Redis list
        error_response = {"type": "status", "status": "error", "message": error_message}
        try:
            await redis.rpush(response_list_key, json.dumps(error_response))
            await redis.publish(response_channel, "new")
        except Exception as redis_err:
             logger.error(f"Failed to push error response to Redis for {agent_run_id}: {redis_err}")

        # Fetch final responses (including the error)
        all_responses = []
        try:
             all_responses_json = await redis.lrange(response_list_key, 0, -1)
             all_responses = [json.loads(r) for r in all_responses_json]
        except Exception as fetch_err:
             logger.error(f"Failed to fetch responses from Redis after error for {agent_run_id}: {fetch_err}")
             all_responses = [error_response] # Use the error message we tried to push

        # Update DB status
        await update_agent_run_status(client, agent_run_id, "failed", error=f"{error_message}\n{traceback_str}", responses=all_responses)

        # Publish ERROR signal
        try:
            await redis.publish(global_control_channel, "ERROR")
            logger.debug(f"Published ERROR signal to {global_control_channel}")
        except Exception as e:
            logger.warning(f"Failed to publish ERROR signal: {str(e)}")

    finally:
        # Cleanup stop checker task
        if stop_checker and not stop_checker.done():
            stop_checker.cancel()
            try: await stop_checker
            except asyncio.CancelledError: pass
            except Exception as e: logger.warning(f"Error during stop_checker cancellation: {e}")

        # Close pubsub connection
        if pubsub:
            try:
                await pubsub.unsubscribe()
                await pubsub.close()
                logger.debug(f"Closed pubsub connection for {agent_run_id}")
            except Exception as e:
                logger.warning(f"Error closing pubsub for {agent_run_id}: {str(e)}")

        # Set TTL on the response list in Redis
        await _cleanup_redis_response_list(agent_run_id)

        # Remove the instance-specific active run key
        await _cleanup_redis_instance_key(agent_run_id)
        
        # --- Workspace Cleanup and Sandbox Stopping ---
        try:
            logger.info(f"Starting workspace cleanup and sandbox stop for project: {project_id} in run {agent_run_id}")
            client = await db.client
            project_result = await client.table('projects').select('sandbox').eq('project_id', project_id).maybe_single().execute()
            sandbox_id_for_cleanup_and_stop = None

            if project_result and project_result.data: # Check project_result.data
                sandbox_info = project_result.data.get('sandbox')
                if sandbox_info and isinstance(sandbox_info, dict): # Ensure sandbox_info is a dict
                    sandbox_id_for_cleanup_and_stop = sandbox_info.get('id')
                else:
                    logger.warning(f"Sandbox info for project {project_id} is not in the expected format or missing: {sandbox_info}")
            else:
                logger.warning(f"No project data found for project_id {project_id} when attempting sandbox cleanup.")

            if sandbox_id_for_cleanup_and_stop:
                sandbox_instance = None # Define here to ensure it's in scope for stopping if cleanup fails partially
                try:
                    logger.info(f"Fetching sandbox instance for ID: {sandbox_id_for_cleanup_and_stop}")
                    sandbox_instance = await get_or_start_sandbox(sandbox_id_for_cleanup_and_stop)

                    if sandbox_instance:
                        # 1. Perform Workspace Cleanup
                        logger.info(f"Attempting workspace cleanup for sandbox: {sandbox_id_for_cleanup_and_stop}")
                        cleanup_session_id = f"cleanup_ws_{uuid.uuid4().hex[:8]}"
                        try:
                            if use_daytona():
                                sandbox_instance.process.create_session(cleanup_session_id)
                            else:
                                sandbox_instance['process']['create_session'](cleanup_session_id)
                            logger.debug(f"Created session {cleanup_session_id} for workspace cleanup.")

                            cleanup_commands = [
                                "find /workspace -type f -name '*.tmp' -print -delete",
                                "find /workspace -type f -name 'temp_*' -print -delete",
                                "find /workspace -type f -name '*_temp.*' -print -delete",
                                "find /workspace -depth -type d -empty -print -delete"
                            ]

                            for cmd in cleanup_commands:
                                logger.debug(f"Executing cleanup command in session {cleanup_session_id}: {cmd}")
                                exec_req = SessionExecuteRequest(command=cmd, var_async=False, cwd="/workspace")
                                if use_daytona():
                                    response = await sandbox_instance.process.execute_session_command(cleanup_session_id, exec_req, timeout=60)
                                else:
                                    # LocalSandbox's execute_session_command is synchronous and does not accept timeout
                                    response = sandbox_instance['process']['execute_session_command'](cleanup_session_id, exec_req)

                                if response['exit_code'] == 0:
                                    logger.info(f"Cleanup command '{cmd}' successful.")
                                else:
                                    logs_output = "Could not retrieve logs." # Default if log retrieval fails
                                    try:
                                        if use_daytona():
                                            logs = await sandbox_instance.process.get_session_command_logs(cleanup_session_id, response['cmd_id'])
                                            logs_output = logs.stdout if logs and logs.stdout else (logs.stderr if logs and logs.stderr else "No output captured")
                                        else:
                                            # LocalSandbox's get_session_command_logs is synchronous
                                            logs = sandbox_instance['process']['get_session_command_logs'](cleanup_session_id, response['cmd_id'])
                                            logs_output = logs['stdout'] if logs and logs['stdout'] else (logs['stderr'] if logs and logs['stderr'] else "No output captured")
                                    except Exception as e_logs:
                                        logger.error(f"Error retrieving logs for failed cleanup command '{cmd}': {e_logs}")
                                    logger.warning(f"Cleanup command '{cmd}' failed. Exit: {response['exit_code']}. Logs: {logs_output}")
                        except Exception as e_cleanup_ws:
                            logger.error(f"Error during workspace cleanup for sandbox {sandbox_id_for_cleanup_and_stop}: {e_cleanup_ws}", exc_info=True)
                        finally:
                            # Ensure sandbox_instance is still valid before trying to delete session
                            if sandbox_instance:
                                try:
                                    logger.debug(f"Deleting cleanup session {cleanup_session_id} for sandbox {sandbox_id_for_cleanup_and_stop}.")
                                    if use_daytona():
                                        sandbox_instance.process.delete_session(cleanup_session_id)
                                    else:
                                        sandbox_instance['process']['delete_session'](cleanup_session_id)
                                except Exception as e_del_session:
                                    logger.error(f"Error deleting cleanup session {cleanup_session_id}: {e_del_session}", exc_info=True)
                            else:
                                logger.warning(f"Skipping cleanup session deletion as sandbox_instance is None for {sandbox_id_for_cleanup_and_stop}")
                        # 2. Stop the Sandbox
                        logger.info(f"Attempting to stop sandbox: {sandbox_id_for_cleanup_and_stop} after cleanup.")
                        # Ensure sandbox_instance is still valid before stopping
                        if sandbox_instance:
                            if use_daytona():
                                logger.info(f"Using Daytona to stop sandbox: {sandbox_id_for_cleanup_and_stop}")
                                current_state = sandbox_instance.info().state
                                logger.info(f"Daytona sandbox {sandbox_id_for_cleanup_and_stop} current state before stop attempt: {current_state}")
                                if current_state not in [WorkspaceState.STOPPED, WorkspaceState.ARCHIVED, WorkspaceState.STOPPING, WorkspaceState.ARCHIVING]:
                                    await daytona.stop(sandbox_instance)
                                    logger.info(f"Successfully sent stop command to Daytona sandbox {sandbox_id_for_cleanup_and_stop}")
                                else:
                                    logger.info(f"Daytona sandbox {sandbox_id_for_cleanup_and_stop} is already in state '{current_state}', no stop action needed.")
                            else: # Local sandbox
                                logger.info(f"Using local_sandbox to stop sandbox: {sandbox_id_for_cleanup_and_stop}")
                                from sandbox.local_sandbox import local_sandbox # Ensure import
                                current_state = sandbox_instance['info']()['state']
                                logger.info(f"Local sandbox {sandbox_id_for_cleanup_and_stop} current state before stop attempt: {current_state}")
                                if current_state not in ['exited', 'stopped', 'stopping']:
                                    local_sandbox.stop(sandbox_instance)
                                    logger.info(f"Successfully called stop for local sandbox {sandbox_id_for_cleanup_and_stop}")
                                else:
                                    logger.info(f"Local sandbox {sandbox_id_for_cleanup_and_stop} is already in state '{current_state}', no stop action needed.")
                        else:
                             logger.warning(f"Skipping sandbox stop as sandbox_instance is None for {sandbox_id_for_cleanup_and_stop}")
                    else:
                        logger.warning(f"Could not retrieve valid sandbox instance for ID: {sandbox_id_for_cleanup_and_stop} (it was None). Skipping cleanup and stop.")

                except Exception as e_get_stop_sandbox:
                    logger.error(f"Error during getting, cleaning, or stopping sandbox {sandbox_id_for_cleanup_and_stop}: {e_get_stop_sandbox}", exc_info=True)
            else:
                logger.info(f"No valid sandbox_id found for project {project_id}; skipping workspace cleanup and stop.") # Changed to info from warning

        except Exception as e_outer_finally:
            logger.error(f"Outer error in finally block for workspace cleanup/stop for project {project_id}: {e_outer_finally}", exc_info=True)
        # --- End Workspace Cleanup and Sandbox Stopping ---

        logger.info(f"Agent run background task fully completed for: {agent_run_id} (Instance: {instance_id}) with final status: {final_status}")

async def _cleanup_redis_instance_key(agent_run_id: str):
    """Clean up the instance-specific Redis key for an agent run."""
    if not instance_id:
        logger.warning("Instance ID not set, cannot clean up instance key.")
        return
    key = f"active_run:{instance_id}:{agent_run_id}"
    logger.debug(f"Cleaning up Redis instance key: {key}")
    try:
        await redis.delete(key)
        logger.debug(f"Successfully cleaned up Redis key: {key}")
    except Exception as e:
        logger.warning(f"Failed to clean up Redis key {key}: {str(e)}")

# TTL for Redis response lists (24 hours)
REDIS_RESPONSE_LIST_TTL = 3600 * 24

async def _cleanup_redis_response_list(agent_run_id: str):
    """Set TTL on the Redis response list."""
    response_list_key = f"agent_run:{agent_run_id}:responses"
    try:
        await redis.expire(response_list_key, REDIS_RESPONSE_LIST_TTL)
        logger.debug(f"Set TTL ({REDIS_RESPONSE_LIST_TTL}s) on response list: {response_list_key}")
    except Exception as e:
        logger.warning(f"Failed to set TTL on response list {response_list_key}: {str(e)}")

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
                    logger.info(f"Successfully updated agent run {agent_run_id} status to '{status}' (retry {retry})")

                    # Verify the update
                    verify_result = await client.table('agent_runs').select('status', 'completed_at').eq("id", agent_run_id).execute()
                    if verify_result.data:
                        actual_status = verify_result.data[0].get('status')
                        completed_at = verify_result.data[0].get('completed_at')
                        logger.info(f"Verified agent run update: status={actual_status}, completed_at={completed_at}")
                    return True
                else:
                    logger.warning(f"Database update returned no data for agent run {agent_run_id} on retry {retry}: {update_result}")
                    if retry == 2:  # Last retry
                        logger.error(f"Failed to update agent run status after all retries: {agent_run_id}")
                        return False
            except Exception as db_error:
                logger.error(f"Database error on retry {retry} updating status for {agent_run_id}: {str(db_error)}")
                if retry < 2:  # Not the last retry yet
                    await asyncio.sleep(0.5 * (2 ** retry))  # Exponential backoff
                else:
                    logger.error(f"Failed to update agent run status after all retries: {agent_run_id}", exc_info=True)
                    return False
    except Exception as e:
        logger.error(f"Unexpected error updating agent run status for {agent_run_id}: {str(e)}", exc_info=True)
        return False

    return False
