from fastapi import APIRouter, HTTPException, Depends, Request, Body, File, UploadFile, Form
from fastapi.responses import StreamingResponse
import asyncio
import json
import traceback
from datetime import datetime, timezone
import uuid
from typing import Optional, List, Dict, Any
import jwt
from pydantic import BaseModel
import tempfile
import os

from agentpress.thread_manager import ThreadManager
from services.supabase import DBConnection
from services import redis
from agent.run import run_agent
from utils.auth_utils import get_current_user_id_from_jwt, get_user_id_from_stream_auth, verify_thread_access
from utils.logger import logger
from services.billing import check_billing_status, can_use_model
from utils.config import config
from sandbox.sandbox import create_sandbox, get_or_start_sandbox
from services.llm import make_llm_api_call
from run_agent_background import run_agent_background, _cleanup_redis_response_list, update_agent_run_status
from utils.constants import MODEL_NAME_ALIASES
# Initialize shared resources
router = APIRouter()
db = None
instance_id = None # Global instance ID for this backend instance

# TTL for Redis response lists (24 hours)
REDIS_RESPONSE_LIST_TTL = 3600 * 24


class AgentStartRequest(BaseModel):
    model_name: Optional[str] = None  # Will be set from config.MODEL_TO_USE in the endpoint
    enable_thinking: Optional[bool] = False
    reasoning_effort: Optional[str] = 'low'
    stream: Optional[bool] = True
    enable_context_manager: Optional[bool] = False

class InitiateAgentResponse(BaseModel):
    thread_id: str
    agent_run_id: Optional[str] = None

def initialize(
    _db: DBConnection,
    _instance_id: str = None
):
    """Initialize the agent API with resources from the main API."""
    global db, instance_id
    db = _db

    # Use provided instance_id or generate a new one
    if _instance_id:
        instance_id = _instance_id
    else:
        # Generate instance ID
        instance_id = str(uuid.uuid4())[:8]

    logger.info(f"Initialized agent API with instance ID: {instance_id}")

    # Note: Redis will be initialized in the lifespan function in api.py

async def cleanup():
    """Clean up resources and stop running agents on shutdown."""
    logger.info("Starting cleanup of agent API resources")

    # Use the instance_id to find and clean up this instance's keys
    try:
        if instance_id: # Ensure instance_id is set
            running_keys = await redis.keys(f"active_run:{instance_id}:*")
            logger.info(f"Found {len(running_keys)} running agent runs for instance {instance_id} to clean up")

            for key in running_keys:
                # Key format: active_run:{instance_id}:{agent_run_id}
                parts = key.split(":")
                if len(parts) == 3:
                    agent_run_id = parts[2]
                    await stop_agent_run(agent_run_id, error_message=f"Instance {instance_id} shutting down")
                else:
                    logger.warning(f"Unexpected key format found: {key}")
        else:
            logger.warning("Instance ID not set, cannot clean up instance-specific agent runs.")

    except Exception as e:
        logger.error(f"Failed to clean up running agent runs: {str(e)}")

    # Close Redis connection
    await redis.close()
    logger.info("Completed cleanup of agent API resources")

async def stop_agent_run(agent_run_id: str, error_message: Optional[str] = None):
    """Update database and publish stop signal to Redis."""
    logger.info(f"Stopping agent run: {agent_run_id}")
    client = await db.client
    final_status = "failed" if error_message else "stopped"

    # Attempt to fetch final responses from Redis
    response_list_key = f"agent_run:{agent_run_id}:responses"
    all_responses = []
    try:
        all_responses_json = await redis.lrange(response_list_key, 0, -1)
        all_responses = [json.loads(r) for r in all_responses_json]
        logger.info(f"Fetched {len(all_responses)} responses from Redis for DB update on stop/fail: {agent_run_id}")
    except Exception as e:
        logger.error(f"Failed to fetch responses from Redis for {agent_run_id} during stop/fail: {e}")
        # Try fetching from DB as a fallback? Or proceed without responses? Proceeding without for now.

    # Update the agent run status in the database
    update_success = await update_agent_run_status(
        client, agent_run_id, final_status, error=error_message, responses=all_responses
    )

    if not update_success:
        logger.error(f"Failed to update database status for stopped/failed run {agent_run_id}")

    # Send STOP signal to the global control channel
    global_control_channel = f"agent_run:{agent_run_id}:control"
    try:
        await redis.publish(global_control_channel, "STOP")
        logger.debug(f"Published STOP signal to global channel {global_control_channel}")
    except Exception as e:
        logger.error(f"Failed to publish STOP signal to global channel {global_control_channel}: {str(e)}")

    # Find all instances handling this agent run and send STOP to instance-specific channels
    try:
        instance_keys = await redis.keys(f"active_run:*:{agent_run_id}")
        logger.debug(f"Found {len(instance_keys)} active instance keys for agent run {agent_run_id}")

        for key in instance_keys:
            # Key format: active_run:{instance_id}:{agent_run_id}
            parts = key.split(":")
            if len(parts) == 3:
                instance_id_from_key = parts[1]
                instance_control_channel = f"agent_run:{agent_run_id}:control:{instance_id_from_key}"
                try:
                    await redis.publish(instance_control_channel, "STOP")
                    logger.debug(f"Published STOP signal to instance channel {instance_control_channel}")
                except Exception as e:
                    logger.warning(f"Failed to publish STOP signal to instance channel {instance_control_channel}: {str(e)}")
            else:
                 logger.warning(f"Unexpected key format found: {key}")

        # Clean up the response list immediately on stop/fail
        await _cleanup_redis_response_list(agent_run_id)

    except Exception as e:
        logger.error(f"Failed to find or signal active instances for {agent_run_id}: {str(e)}")

    logger.info(f"Successfully initiated stop process for agent run: {agent_run_id}")

# async def restore_running_agent_runs():
#     """Mark agent runs that were still 'running' in the database as failed and clean up Redis resources."""
#     logger.info("Restoring running agent runs after server restart")
#     client = await db.client
#     running_agent_runs = await client.table('agent_runs').select('id').eq("status", "running").execute()

#     for run in running_agent_runs.data:
#         agent_run_id = run['id']
#         logger.warning(f"Found running agent run {agent_run_id} from before server restart")

#         # Clean up Redis resources for this run
#         try:
#             # Clean up active run key
#             active_run_key = f"active_run:{instance_id}:{agent_run_id}"
#             await redis.delete(active_run_key)

#             # Clean up response list
#             response_list_key = f"agent_run:{agent_run_id}:responses"
#             await redis.delete(response_list_key)

#             # Clean up control channels
#             control_channel = f"agent_run:{agent_run_id}:control"
#             instance_control_channel = f"agent_run:{agent_run_id}:control:{instance_id}"
#             await redis.delete(control_channel)
#             await redis.delete(instance_control_channel)

#             logger.info(f"Cleaned up Redis resources for agent run {agent_run_id}")
#         except Exception as e:
#             logger.error(f"Error cleaning up Redis resources for agent run {agent_run_id}: {e}")

#         # Call stop_agent_run to handle status update and cleanup
#         await stop_agent_run(agent_run_id, error_message="Server restarted while agent was running")

async def check_for_active_project_agent_run(client, project_id: str):
    """
    Check if there is an active agent run for any thread in the given project.
    If found, returns the ID of the active run, otherwise returns None.
    """
    project_threads = await client.table('threads').select('thread_id').eq('project_id', project_id).execute()
    project_thread_ids = [t['thread_id'] for t in project_threads.data]

    if project_thread_ids:
        active_runs = await client.table('agent_runs').select('id').in_('thread_id', project_thread_ids).eq('status', 'running').execute()
        if active_runs.data and len(active_runs.data) > 0:
            return active_runs.data[0]['id']
    return None

async def get_agent_run_with_access_check(client, agent_run_id: str, user_id: str):
    """Get agent run data after verifying user access."""
    agent_run = await client.table('agent_runs').select('*').eq('id', agent_run_id).execute()
    if not agent_run.data:
        raise HTTPException(status_code=404, detail="Agent run not found")

    agent_run_data = agent_run.data[0]
    thread_id = agent_run_data['thread_id']
    await verify_thread_access(client, thread_id, user_id)
    return agent_run_data

@router.post("/thread/{thread_id}/agent/start")
async def start_agent(
    thread_id: str,
    body: AgentStartRequest = Body(...),
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    """Start an agent for a specific thread in the background."""
    try:
        global instance_id # Ensure instance_id is accessible
        if not instance_id:
            # This specific check should probably remain as it's a precondition for the API itself
            raise HTTPException(status_code=500, detail="Agent API not initialized with instance ID")

        # Nueva lógica de determinación de model_name
        server_configured_model = config.MODEL_TO_USE
        model_name_from_request = body.model_name
        final_model_name_to_use = None

        if config.OLLAMA_API_BASE and config.OLLAMA_API_BASE.strip() and server_configured_model and server_configured_model.strip():
            logger.info(f"OLLAMA_API_BASE ('{config.OLLAMA_API_BASE}') and MODEL_TO_USE ('{server_configured_model}') are set in server config. Prioritizing server config for main agent model.")
            final_model_name_to_use = server_configured_model
            if model_name_from_request:
                logger.info(f"Ignoring 'model_name: {model_name_from_request}' from request body due to server-side OLLAMA configuration priority.")
        elif model_name_from_request:
            logger.info(f"Using 'model_name: {model_name_from_request}' from request body.")
            final_model_name_to_use = model_name_from_request
        else:
            logger.info(f"No model_name in request body, using MODEL_TO_USE ('{server_configured_model}') from server config.")
            final_model_name_to_use = server_configured_model
        
        logger.info(f"Effective model name before alias resolution: {final_model_name_to_use}")

        # Log the model name after alias resolution
        resolved_model = MODEL_NAME_ALIASES.get(final_model_name_to_use, final_model_name_to_use)
        model_name = resolved_model # Esta es la variable que se usará en el resto de la función
        logger.info(f"Model name after alias resolution: {model_name}")

        # New logic to insert for Ollama model prefixing
        if config.OLLAMA_API_BASE and config.OLLAMA_API_BASE.strip():
            has_known_provider_prefix = any(model_name.startswith(p) for p in ["openrouter/", "openai/", "anthropic/", "bedrock/", "ollama/"])
            if not has_known_provider_prefix:
                logger.info(f"OLLAMA_API_BASE is set ('{config.OLLAMA_API_BASE}') and resolved model '{model_name}' has no explicit provider prefix. Defaulting to 'ollama/' prefix.")
                model_name = f"ollama/{model_name}"
                # logger.info(f"Final model name for LLM call: {model_name}") # Log duplicado, se loguea más abajo
            elif model_name.startswith("ollama/"):
                logger.info(f"Model '{model_name}' is already specified as an Ollama model. Using with OLLAMA_API_BASE: '{config.OLLAMA_API_BASE}'.")
            else:
                logger.info(f"Model '{model_name}' has a prefix for a different provider. Not prepending 'ollama/'.") # Ajustado el log
        else:
            logger.info(f"OLLAMA_API_BASE is not configured. Not attempting to default to Ollama for model '{model_name}'.")
        
        logger.info(f"Final model name for LLM call (after ollama prefixing if any): {model_name}")

        logger.info(f"Starting new agent for thread: {thread_id} with config: model={model_name}, thinking={body.enable_thinking}, effort={body.reasoning_effort}, stream={body.stream}, context_manager={body.enable_context_manager} (Instance: {instance_id})")
        client = await db.client

        await verify_thread_access(client, thread_id, user_id)
        thread_result = await client.table('threads').select('project_id', 'account_id').eq('thread_id', thread_id).execute()
        if not thread_result.data:
            raise HTTPException(status_code=404, detail="Thread not found")
        thread_data = thread_result.data[0]
        project_id = thread_data.get('project_id')
        account_id = thread_data.get('account_id')

        can_use, model_message, allowed_models = await can_use_model(client, account_id, model_name)
        if not can_use:
            raise HTTPException(status_code=403, detail={"message": model_message, "allowed_models": allowed_models})

        can_run, message, subscription = await check_billing_status(client, account_id)
        if not can_run:
            raise HTTPException(status_code=402, detail={"message": message, "subscription": subscription})

        active_run_id = await check_for_active_project_agent_run(client, project_id)
        if active_run_id:
            logger.info(f"Stopping existing agent run {active_run_id} for project {project_id}")
            await stop_agent_run(active_run_id)

        try:
            # Get project data to find sandbox ID
            project_result = await client.table('projects').select('*').eq('project_id', project_id).execute()
            if not project_result.data:
                raise HTTPException(status_code=404, detail="Project not found")
            
            project_data = project_result.data[0]
            sandbox_info = project_data.get('sandbox', {})
            if not sandbox_info.get('id'):
                raise HTTPException(status_code=404, detail="No sandbox found for this project")
                
            sandbox_id = sandbox_info['id']
            sandbox = await get_or_start_sandbox(sandbox_id)
            logger.info(f"Successfully started sandbox {sandbox_id} for project {project_id}")
        except Exception as e_sandbox: # Keep existing specific exception handling for sandbox
            logger.error(f"Failed to start sandbox for project {project_id}: {str(e_sandbox)}") # Existing log
            raise HTTPException(status_code=500, detail=f"Failed to initialize sandbox: {str(e_sandbox)}") # Existing raise

        agent_run = await client.table('agent_runs').insert({
            "thread_id": thread_id, "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        agent_run_id = agent_run.data[0]['id']
        logger.info(f"Created new agent run: {agent_run_id}")

        # Register this run in Redis with TTL using instance ID
        instance_key = f"active_run:{instance_id}:{agent_run_id}"
        try:
            await redis.set(instance_key, "running", ex=redis.REDIS_KEY_TTL)
        except Exception as e_redis: # More specific logging for Redis error
            logger.warning(f"Failed to register agent run in Redis ({instance_key}): {str(e_redis)}")
            # Depending on policy, you might want to raise here or allow continuing without Redis registration

        # Run the agent in the background
        run_agent_background.send(
            agent_run_id=agent_run_id, thread_id=thread_id, instance_id=instance_id,
            project_id=project_id,
            model_name=model_name,  # Already resolved above
            enable_thinking=body.enable_thinking, reasoning_effort=body.reasoning_effort,
            stream=body.stream, enable_context_manager=body.enable_context_manager
        )

        return {"agent_run_id": agent_run_id, "status": "running"}

    except Exception as e: # New outer catch-all
        logger.error(f"Error in start_agent for thread {thread_id}: {str(e)}", exc_info=True)
        # Potentially add cleanup logic here if needed, similar to initiate_agent_with_files
        raise HTTPException(status_code=500, detail=f"Failed to start agent: {str(e)}")

@router.post("/agent-run/{agent_run_id}/stop")
async def stop_agent(agent_run_id: str, user_id: str = Depends(get_current_user_id_from_jwt)):
    """Stop a running agent."""
    logger.info(f"Received request to stop agent run: {agent_run_id}")
    client = await db.client
    await get_agent_run_with_access_check(client, agent_run_id, user_id)
    await stop_agent_run(agent_run_id)
    return {"status": "stopped"}

@router.get("/thread/{thread_id}/agent-runs")
async def get_agent_runs(thread_id: str, user_id: str = Depends(get_current_user_id_from_jwt)):
    """Get all agent runs for a thread."""
    logger.info(f"Fetching agent runs for thread: {thread_id}")
    client = await db.client
    await verify_thread_access(client, thread_id, user_id)
    agent_runs = await client.table('agent_runs').select('*').eq("thread_id", thread_id).order('created_at', desc=True).execute()
    logger.debug(f"Found {len(agent_runs.data)} agent runs for thread: {thread_id}")
    return {"agent_runs": agent_runs.data}

@router.get("/agent-run/{agent_run_id}")
async def get_agent_run(agent_run_id: str, user_id: str = Depends(get_current_user_id_from_jwt)):
    """Get agent run status and responses."""
    logger.info(f"Fetching agent run details: {agent_run_id}")
    client = await db.client
    agent_run_data = await get_agent_run_with_access_check(client, agent_run_id, user_id)
    # Note: Responses are not included here by default, they are in the stream or DB
    return {
        "id": agent_run_data['id'],
        "threadId": agent_run_data['thread_id'],
        "status": agent_run_data['status'],
        "startedAt": agent_run_data['started_at'],
        "completedAt": agent_run_data['completed_at'],
        "error": agent_run_data['error']
    }

@router.get("/agent-run/{agent_run_id}/stream")
async def stream_agent_run(
    agent_run_id: str,
    token: Optional[str] = None,
    request: Request = None
):
    """Stream the responses of an agent run using Redis Lists and Pub/Sub."""
    logger.info(f"Starting stream for agent run: {agent_run_id}")
    client = await db.client

    user_id = await get_user_id_from_stream_auth(request, token)
    agent_run_data = await get_agent_run_with_access_check(client, agent_run_id, user_id)

    response_list_key = f"agent_run:{agent_run_id}:responses"
    response_channel = f"agent_run:{agent_run_id}:new_response"
    control_channel = f"agent_run:{agent_run_id}:control" # Global control channel

    async def stream_generator():
        logger.debug(f"Streaming responses for {agent_run_id} using Redis list {response_list_key} and channel {response_channel}")
        last_processed_index = -1
        pubsub_response = None
        pubsub_control = None
        listener_task = None
        terminate_stream = False
        initial_yield_complete = False

        try:
            # 1. Fetch and yield initial responses from Redis list
            initial_responses_json = await redis.lrange(response_list_key, 0, -1)
            initial_responses = []
            if initial_responses_json:
                initial_responses = [json.loads(r) for r in initial_responses_json]
                logger.debug(f"Sending {len(initial_responses)} initial responses for {agent_run_id}")
                for response in initial_responses:
                    yield f"data: {json.dumps(response)}\n\n"
                last_processed_index = len(initial_responses) - 1
            initial_yield_complete = True

            # 2. Check run status *after* yielding initial data
            run_status = await client.table('agent_runs').select('status').eq("id", agent_run_id).maybe_single().execute()
            current_status = run_status.data.get('status') if run_status.data else None

            if current_status != 'running':
                logger.info(f"Agent run {agent_run_id} is not running (status: {current_status}). Ending stream.")
                yield f"data: {json.dumps({'type': 'status', 'status': 'completed'})}\n\n"
                return

            # 3. Set up Pub/Sub listeners for new responses and control signals
            pubsub_response = await redis.create_pubsub()
            await pubsub_response.subscribe(response_channel)
            logger.debug(f"Subscribed to response channel: {response_channel}")

            pubsub_control = await redis.create_pubsub()
            await pubsub_control.subscribe(control_channel)
            logger.debug(f"Subscribed to control channel: {control_channel}")

            # Queue to communicate between listeners and the main generator loop
            message_queue = asyncio.Queue()

            async def listen_messages():
                response_reader = pubsub_response.listen()
                control_reader = pubsub_control.listen()
                tasks = [asyncio.create_task(response_reader.__anext__()), asyncio.create_task(control_reader.__anext__())]

                while not terminate_stream:
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for task in done:
                        try:
                            message = task.result()
                            if message and isinstance(message, dict) and message.get("type") == "message":
                                channel = message.get("channel")
                                data = message.get("data")
                                if isinstance(data, bytes): data = data.decode('utf-8')

                                if channel == response_channel and data == "new":
                                    await message_queue.put({"type": "new_response"})
                                elif channel == control_channel and data in ["STOP", "END_STREAM", "ERROR"]:
                                    logger.info(f"Received control signal '{data}' for {agent_run_id}")
                                    await message_queue.put({"type": "control", "data": data})
                                    return # Stop listening on control signal

                        except StopAsyncIteration:
                            logger.warning(f"Listener {task} stopped.")
                            # Decide how to handle listener stopping, maybe terminate?
                            await message_queue.put({"type": "error", "data": "Listener stopped unexpectedly"})
                            return
                        except Exception as e:
                            logger.error(f"Error in listener for {agent_run_id}: {e}")
                            await message_queue.put({"type": "error", "data": "Listener failed"})
                            return
                        finally:
                            # Reschedule the completed listener task
                            if task in tasks:
                                tasks.remove(task)
                                if message and isinstance(message, dict) and message.get("channel") == response_channel:
                                     tasks.append(asyncio.create_task(response_reader.__anext__()))
                                elif message and isinstance(message, dict) and message.get("channel") == control_channel:
                                     tasks.append(asyncio.create_task(control_reader.__anext__()))

                # Cancel pending listener tasks on exit
                for p_task in pending: p_task.cancel()
                for task in tasks: task.cancel()


            listener_task = asyncio.create_task(listen_messages())

            # 4. Main loop to process messages from the queue
            while not terminate_stream:
                try:
                    queue_item = await message_queue.get()

                    if queue_item["type"] == "new_response":
                        # Fetch new responses from Redis list starting after the last processed index
                        new_start_index = last_processed_index + 1
                        new_responses_json = await redis.lrange(response_list_key, new_start_index, -1)

                        if new_responses_json:
                            new_responses = [json.loads(r) for r in new_responses_json]
                            num_new = len(new_responses)
                            # logger.debug(f"Received {num_new} new responses for {agent_run_id} (index {new_start_index} onwards)")
                            for response in new_responses:
                                yield f"data: {json.dumps(response)}\n\n"
                                # Check if this response signals completion
                                if response.get('type') == 'status' and response.get('status') in ['completed', 'failed', 'stopped']:
                                    logger.info(f"Detected run completion via status message in stream: {response.get('status')}")
                                    terminate_stream = True
                                    break # Stop processing further new responses
                            last_processed_index += num_new
                        if terminate_stream: break

                    elif queue_item["type"] == "control":
                        control_signal = queue_item["data"]
                        terminate_stream = True # Stop the stream on any control signal
                        yield f"data: {json.dumps({'type': 'status', 'status': control_signal})}\n\n"
                        break

                    elif queue_item["type"] == "error":
                        logger.error(f"Listener error for {agent_run_id}: {queue_item['data']}")
                        terminate_stream = True
                        yield f"data: {json.dumps({'type': 'status', 'status': 'error'})}\n\n"
                        break

                except asyncio.CancelledError:
                     logger.info(f"Stream generator main loop cancelled for {agent_run_id}")
                     terminate_stream = True
                     break
                except Exception as loop_err:
                    logger.error(f"Error in stream generator main loop for {agent_run_id}: {loop_err}", exc_info=True)
                    terminate_stream = True
                    yield f"data: {json.dumps({'type': 'status', 'status': 'error', 'message': f'Stream failed: {loop_err}'})}\n\n"
                    break

        except Exception as e:
            logger.error(f"Error setting up stream for agent run {agent_run_id}: {e}", exc_info=True)
            # Only yield error if initial yield didn't happen
            if not initial_yield_complete:
                 yield f"data: {json.dumps({'type': 'status', 'status': 'error', 'message': f'Failed to start stream: {e}'})}\n\n"
        finally:
            terminate_stream = True
            # Graceful shutdown order: unsubscribe → close → cancel
            if pubsub_response: await pubsub_response.unsubscribe(response_channel)
            if pubsub_control: await pubsub_control.unsubscribe(control_channel)
            if pubsub_response: await pubsub_response.close()
            if pubsub_control: await pubsub_control.close()

            if listener_task:
                listener_task.cancel()
                try:
                    await listener_task  # Reap inner tasks & swallow their errors
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.debug(f"listener_task ended with: {e}")
            # Wait briefly for tasks to cancel
            await asyncio.sleep(0.1)
            logger.debug(f"Streaming cleanup complete for agent run: {agent_run_id}")

    return StreamingResponse(stream_generator(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "Connection": "keep-alive",
        "X-Accel-Buffering": "no", "Content-Type": "text/event-stream",
        "Access-Control-Allow-Origin": "*"
    })

async def generate_and_update_project_name(project_id: str, prompt: str):
    """Generates a project name using an LLM and updates the database."""
    logger.info(f"Starting background task to generate name for project: {project_id}")
    try:
        db_conn = DBConnection()
        client = await db_conn.client

        # Determine model for project naming
        model_for_naming = config.MODEL_TO_USE
        logger.info(f"Project naming: Initial model from config.MODEL_TO_USE: '{model_for_naming}'")

        project_naming_model = "ollama/llama2" # Default fallback

        if model_for_naming:
            # Resolver alias
            aliased_model_for_naming = MODEL_NAME_ALIASES.get(model_for_naming, model_for_naming)
            if aliased_model_for_naming != model_for_naming:
                logger.info(f"Project naming: Model after alias resolution: '{aliased_model_for_naming}'")
            model_for_naming = aliased_model_for_naming

            # Verificar si es un modelo de un proveedor específico o si se puede asumir Ollama
            if config.OLLAMA_API_BASE and config.OLLAMA_API_BASE.strip():
                is_explicit_ollama = model_for_naming.startswith("ollama/")
                is_other_provider = any(model_for_naming.startswith(p) for p in ["openrouter/", "openai/", "anthropic/", "bedrock/"])

                if is_explicit_ollama:
                    project_naming_model = model_for_naming
                    logger.info(f"Project naming: Using explicitly configured Ollama model: '{project_naming_model}'")
                elif not is_other_provider: # No es de otro proveedor y no tiene prefijo ollama/, asumir ollama
                    project_naming_model = f"ollama/{model_for_naming}"
                    logger.info(f"Project naming: Assuming Ollama for unprefixed model from config.MODEL_TO_USE. Using: '{project_naming_model}'")
                else: # Es de otro proveedor (e.g., openrouter/)
                    logger.warning(f"Project naming: config.MODEL_TO_USE ('{model_for_naming}') is configured for a non-Ollama provider. Falling back to 'ollama/llama2' for project naming.")
                    # project_naming_model remains "ollama/llama2" (default fallback)
            elif model_for_naming.startswith("ollama/"): # OLLAMA_API_BASE no está, pero el modelo es ollama/
                 project_naming_model = model_for_naming
                 logger.info(f"Project naming: Using Ollama model '{project_naming_model}' but OLLAMA_API_BASE is not configured. This might fail if not default http://localhost:11434")
            else: # No OLLAMA_API_BASE y no es un modelo ollama/ explícito
                logger.warning(f"Project naming: OLLAMA_API_BASE not configured and config.MODEL_TO_USE ('{model_for_naming}') is not an explicit Ollama model. Falling back to 'ollama/llama2'.")
                # project_naming_model remains "ollama/llama2" (default fallback)
        else: # config.MODEL_TO_USE no está definido
            logger.warning("Project naming: config.MODEL_TO_USE is not defined. Falling back to 'ollama/llama2'.")
            # project_naming_model remains "ollama/llama2" (default fallback)
        
        # `project_naming_model` es el que se usará para make_llm_api_call
        system_prompt = "You are a helpful assistant that generates extremely concise titles (2-4 words maximum) for chat threads based on the user's message. Respond with only the title, no other text or punctuation."
        user_message = f"Generate an extremely brief title (2-4 words only) for a chat thread that starts with this message: \"{prompt}\""
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_message}]

        logger.debug(f"Calling LLM ({project_naming_model}) for project {project_id} naming.")
        response = await make_llm_api_call(messages=messages, model_name=project_naming_model, max_tokens=20, temperature=0.7)
        
        generated_name = None
        if response and response.get('choices') and response['choices'][0].get('message'):
            raw_name = response['choices'][0]['message'].get('content', '').strip()
            cleaned_name = raw_name.strip('\'" \n\t')
            if cleaned_name:
                generated_name = cleaned_name
                logger.info(f"LLM generated name for project {project_id}: '{generated_name}'")
            else:
                logger.warning(f"LLM returned an empty name for project {project_id}.")
        else:
            logger.warning(f"Failed to get valid response from LLM for project {project_id} naming. Response: {response}")

        if generated_name:
            update_result = await client.table('projects').update({"name": generated_name}).eq("project_id", project_id).execute()
            if hasattr(update_result, 'data') and update_result.data:
                logger.info(f"Successfully updated project {project_id} name to '{generated_name}'")
            else:
                logger.error(f"Failed to update project {project_id} name in database. Update result: {update_result}")
        else:
            logger.warning(f"No generated name, skipping database update for project {project_id}.")

    except Exception as e:
        logger.error(f"Error in background naming task for project {project_id}: {str(e)}\n{traceback.format_exc()}")
    finally:
        # No need to disconnect DBConnection singleton instance here
        logger.info(f"Finished background naming task for project: {project_id}")

@router.post("/agent/initiate", response_model=InitiateAgentResponse)
async def initiate_agent_with_files(
    prompt: str = Form(...),
    model_name: Optional[str] = Form(None),  # Default to None to use config.MODEL_TO_USE
    enable_thinking: Optional[bool] = Form(False),
    reasoning_effort: Optional[str] = Form("low"),
    stream: Optional[bool] = Form(True),
    enable_context_manager: Optional[bool] = Form(False),
    files: List[UploadFile] = File(default=[]),
    user_id: str = Depends(get_current_user_id_from_jwt)
):
    """Initiate a new agent session with optional file attachments."""
    global instance_id # Ensure instance_id is accessible
    if not instance_id:
        raise HTTPException(status_code=500, detail="Agent API not initialized with instance ID")

    # Nueva lógica de determinación de model_name
    server_configured_model = config.MODEL_TO_USE
    # model_name_from_request se obtiene del parámetro de la función 'model_name'
    model_name_from_request = model_name 
    final_model_name_to_use = None

    if config.OLLAMA_API_BASE and config.OLLAMA_API_BASE.strip() and server_configured_model and server_configured_model.strip():
        logger.info(f"OLLAMA_API_BASE ('{config.OLLAMA_API_BASE}') and MODEL_TO_USE ('{server_configured_model}') are set in server config. Prioritizing server config for main agent model.")
        final_model_name_to_use = server_configured_model
        if model_name_from_request:
            logger.info(f"Ignoring 'model_name: {model_name_from_request}' from request (Form value) due to server-side OLLAMA configuration priority.")
    elif model_name_from_request:
        logger.info(f"Using 'model_name: {model_name_from_request}' from request (Form value).")
        final_model_name_to_use = model_name_from_request
    else:
        logger.info(f"No model_name in request (Form value), using MODEL_TO_USE ('{server_configured_model}') from server config.")
        final_model_name_to_use = server_configured_model

    logger.info(f"Effective model name before alias resolution: {final_model_name_to_use}")
    
    # Log the model name after alias resolution
    resolved_model = MODEL_NAME_ALIASES.get(final_model_name_to_use, final_model_name_to_use)
    model_name = resolved_model # Esta es la variable que se usará en el resto de la función
    logger.info(f"Model name after alias resolution: {model_name}")
    
    # New logic to insert for Ollama model prefixing
    if config.OLLAMA_API_BASE and config.OLLAMA_API_BASE.strip():
        has_known_provider_prefix = any(model_name.startswith(p) for p in ["openrouter/", "openai/", "anthropic/", "bedrock/", "ollama/"])
        if not has_known_provider_prefix:
            logger.info(f"OLLAMA_API_BASE is set ('{config.OLLAMA_API_BASE}') and resolved model '{model_name}' has no explicit provider prefix. Defaulting to 'ollama/' prefix.")
            model_name = f"ollama/{model_name}"
            # logger.info(f"Final model name for LLM call: {model_name}") # Log duplicado, se loguea más abajo
        elif model_name.startswith("ollama/"):
            logger.info(f"Model '{model_name}' is already specified as an Ollama model. Using with OLLAMA_API_BASE: '{config.OLLAMA_API_BASE}'.")
        else:
            logger.info(f"Model '{model_name}' has a prefix for a different provider. Not prepending 'ollama/'.") # Ajustado el log
    else:
        logger.info(f"OLLAMA_API_BASE is not configured. Not attempting to default to Ollama for model '{model_name}'.")

    logger.info(f"Final model name for LLM call (after ollama prefixing if any): {model_name}")

    logger.info(f"[\033[91mDEBUG\033[0m] Initiating new agent with prompt and {len(files)} files (Instance: {instance_id}), model: {model_name}, enable_thinking: {enable_thinking}")
    client = await db.client
    account_id = user_id # In Basejump, personal account_id is the same as user_id
    
    can_use, model_message, allowed_models = await can_use_model(client, account_id, model_name)
    if not can_use:
        raise HTTPException(status_code=403, detail={"message": model_message, "allowed_models": allowed_models})

    can_run, message, subscription = await check_billing_status(client, account_id)
    if not can_run:
        raise HTTPException(status_code=402, detail={"message": message, "subscription": subscription})

    try:
        # 1. Create Project
        placeholder_name = f"{prompt[:30]}..." if len(prompt) > 30 else prompt
        project = await client.table('projects').insert({
            "project_id": str(uuid.uuid4()), "account_id": account_id, "name": placeholder_name,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        project_id = project.data[0]['project_id']
        logger.info(f"Created new project: {project_id}")

        # 2. Create Thread
        thread = await client.table('threads').insert({
            "thread_id": str(uuid.uuid4()), "project_id": project_id, "account_id": account_id,
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        thread_id = thread.data[0]['thread_id']
        logger.info(f"Created new thread: {thread_id}")

        # Trigger Background Naming Task
        asyncio.create_task(generate_and_update_project_name(project_id=project_id, prompt=prompt))

        # 3. Create Sandbox
        sandbox_pass = str(uuid.uuid4())
        sandbox_obj = create_sandbox(sandbox_pass, project_id) # Renamed to sandbox_obj

        is_daytona_sandbox = not isinstance(sandbox_obj, dict)
        sandbox_id = None
        vnc_url = "N/A"
        website_url = "N/A"
        token = None

        if is_daytona_sandbox:
            sandbox_id = sandbox_obj.id
            logger.info(f"Created new Daytona sandbox {sandbox_id} for project {project_id}")
            try:
                vnc_link = sandbox_obj.get_preview_link(6080)
                website_link = sandbox_obj.get_preview_link(8080)
                vnc_url = vnc_link.url if hasattr(vnc_link, 'url') else str(vnc_link).split("url='")[1].split("'")[0]
                website_url = website_link.url if hasattr(website_link, 'url') else str(website_link).split("url='")[1].split("'")[0]
                if hasattr(vnc_link, 'token'):
                    token = vnc_link.token
                elif "token='" in str(vnc_link):
                    token = str(vnc_link).split("token='")[1].split("'")[0]
            except Exception as e_preview:
                logger.error(f"Error getting preview links for Daytona sandbox {sandbox_id}: {str(e_preview)}", exc_info=True)
                # vnc_url and website_url will remain "N/A"
        else: # Local sandbox (dict)
            sandbox_id = sandbox_obj.get('id')
            logger.info(f"Created new Local sandbox {sandbox_id} for project {project_id}")
            container = sandbox_obj.get('container')
            if container:
                try:
                    container.reload() # Ensure ports are up-to-date
                    ports = container.ports
                    # Example: {'5900/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '32789'}], ...}
                    vnc_host_port_list = ports.get('5900/tcp')
                    if vnc_host_port_list and vnc_host_port_list[0] and vnc_host_port_list[0].get('HostPort'):
                        vnc_host_port = vnc_host_port_list[0]['HostPort']
                        vnc_url = f"localhost:{vnc_host_port}" # Assuming running on localhost
                        logger.info(f"Local sandbox VNC URL determined: {vnc_url}")
                    else:
                        logger.warning(f"Could not determine VNC host port for local sandbox {sandbox_id}. Ports: {ports}")
                    # For local sandbox, website_url might not be directly equivalent to Daytona's 8080 preview.
                    # We'll set it to N/A or a placeholder.
                    website_url = "N/A (local sandbox, direct website preview not applicable)"
                    logger.info(f"Local sandbox website_url set to: {website_url}")
                    token = None # No token concept for local_sandbox in this way
                except Exception as e_local_ports:
                    logger.error(f"Error getting port info for local sandbox {sandbox_id}: {str(e_local_ports)}", exc_info=True)
            else:
                logger.warning(f"Local sandbox object for {sandbox_id} does not contain a 'container' key.")

        if not sandbox_id:
             logger.error(f"Failed to obtain sandbox_id for project {project_id}")
             raise Exception("Sandbox ID could not be determined.")

        # Update project with sandbox info
        update_result = await client.table('projects').update({
            'sandbox': {
                'id': sandbox_id, 'pass': sandbox_pass, 'vnc_preview': vnc_url,
                'sandbox_url': website_url, 'token': token,
                'is_local': not is_daytona_sandbox # Add flag to indicate local
            }
        }).eq('project_id', project_id).execute()

        if not update_result.data:
            logger.error(f"Failed to update project {project_id} with new sandbox {sandbox_id}")
            raise Exception("Database update failed for project sandbox info")

        # 4. Upload Files to Sandbox (if any)
        message_content = prompt
        if files:
            successful_uploads = []
            failed_uploads = []
            for file in files:
                if file.filename:
                    upload_successful = False # Reset for each file
                    try:
                        safe_filename = file.filename.replace('/', '_').replace('\\', '_')
                        target_path = f"/workspace/{safe_filename}"

                        if is_daytona_sandbox:
                            logger.info(f"Attempting to upload {safe_filename} to {target_path} in Daytona sandbox {sandbox_id}")
                            content = await file.read()
                            try:
                                if hasattr(sandbox_obj, 'fs') and hasattr(sandbox_obj.fs, 'upload_file'):
                                    import inspect
                                    if inspect.iscoroutinefunction(sandbox_obj.fs.upload_file):
                                        await sandbox_obj.fs.upload_file(target_path, content)
                                    else:
                                        sandbox_obj.fs.upload_file(target_path, content)
                                    logger.debug(f"Called sandbox_obj.fs.upload_file for {target_path}")
                                    upload_successful = True
                                else:
                                    logger.error(f"Daytona sandbox object for {sandbox_id} does not have 'fs.upload_file' method.")
                                    # raise NotImplementedError("Suitable upload method not found on Daytona sandbox object.")
                            except Exception as upload_error:
                                logger.error(f"Error during Daytona sandbox upload call for {safe_filename}: {str(upload_error)}", exc_info=True)
                        else: # Local sandbox
                            logger.warning(f"Local sandbox file upload for {safe_filename} via this path is not fully implemented. Skipping file.")
                            upload_successful = False # Explicitly false, though it's default

                        if upload_successful and is_daytona_sandbox: # Verification only for Daytona for now
                            try:
                                await asyncio.sleep(0.2) # Give a moment for fs to sync
                                parent_dir = os.path.dirname(target_path)
                                files_in_dir = sandbox_obj.fs.list_files(parent_dir)
                                file_names_in_dir = [f.name for f in files_in_dir]
                                if safe_filename in file_names_in_dir:
                                    successful_uploads.append(target_path)
                                    logger.info(f"Successfully uploaded and verified file {safe_filename} to Daytona sandbox path {target_path}")
                                else:
                                    logger.error(f"Verification failed for {safe_filename} in Daytona sandbox: File not found in {parent_dir} after upload attempt.")
                                    failed_uploads.append(safe_filename)
                            except Exception as verify_error:
                                logger.error(f"Error verifying file {safe_filename} after Daytona upload: {str(verify_error)}", exc_info=True)
                                failed_uploads.append(safe_filename)
                        elif upload_successful: # Should not happen for local if logic is correct
                             successful_uploads.append(target_path) # Should ideally be verified too
                        else: # upload_successful is False
                            if not is_daytona_sandbox: # Already logged for local
                                pass
                            else: # Failed for Daytona
                                failed_uploads.append(safe_filename)
                    except Exception as file_error:
                        logger.error(f"Error processing file {file.filename}: {str(file_error)}", exc_info=True)
                        failed_uploads.append(file.filename)
                    finally:
                        await file.close()

            if successful_uploads:
                message_content += "\n\n" if message_content else ""
                for file_path in successful_uploads: message_content += f"[Uploaded File: {file_path}]\n"
            if failed_uploads:
                message_content += "\n\nThe following files failed to upload:\n"
                for failed_file in failed_uploads: message_content += f"- {failed_file}\n"

        # 5. Add initial user message to thread
        message_id = str(uuid.uuid4())
        message_payload = {"role": "user", "content": message_content}
        await client.table('messages').insert({
            "message_id": message_id, "thread_id": thread_id, "type": "user",
            "is_llm_message": True, "content": json.dumps(message_payload),
            "created_at": datetime.now(timezone.utc).isoformat()
        }).execute()

        # 6. Start Agent Run
        agent_run = await client.table('agent_runs').insert({
            "thread_id": thread_id, "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat()
        }).execute()
        agent_run_id = agent_run.data[0]['id']
        logger.info(f"Created new agent run: {agent_run_id}")

        # Register run in Redis
        instance_key = f"active_run:{instance_id}:{agent_run_id}"
        try:
            await redis.set(instance_key, "running", ex=redis.REDIS_KEY_TTL)
        except Exception as e:
            logger.warning(f"Failed to register agent run in Redis ({instance_key}): {str(e)}")

        # Run agent in background
        run_agent_background.send(
            agent_run_id=agent_run_id, thread_id=thread_id, instance_id=instance_id,
            project_id=project_id,
            model_name=model_name,  # Already resolved above
            enable_thinking=enable_thinking, reasoning_effort=reasoning_effort,
            stream=stream, enable_context_manager=enable_context_manager
        )

        return {"thread_id": thread_id, "agent_run_id": agent_run_id}

    except Exception as e:
        logger.error(f"Error in agent initiation: {str(e)}\n{traceback.format_exc()}")
        # TODO: Clean up created project/thread if initiation fails mid-way
        raise HTTPException(status_code=500, detail=f"Failed to initiate agent session: {str(e)}")
