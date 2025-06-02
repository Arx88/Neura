from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
import sentry
from contextlib import asynccontextmanager
# Remove ThreadManager if not directly used by these new endpoints
# from agentpress.thread_manager import ThreadManager
from services.supabase import DBConnection
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any # Added for type hinting
from pydantic import BaseModel, Field # Added for FrontendErrorPayload
from utils.config import config, EnvMode
import asyncio
import json # Added import for json.dumps
from utils.logger import logger, setup_logger as get_logger # Added get_logger
import uuid
import time
from collections import OrderedDict

# AgentPress specific imports
from agentpress.api_models_tasks import TaskState # For direct use if needed
from agentpress.task_storage_supabase import SupabaseTaskStorage
from agentpress.task_state_manager import TaskStateManager
from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.task_planner import TaskPlanner
from agentpress.plan_executor import PlanExecutor # Added import
from agentpress.api_models_tasks import (
    CreateTaskPayload,
    UpdateTaskPayload,
    PlanTaskPayload,
    FullTaskStateResponse, # Using this for most responses
    FullTaskListResponse,
    # PlanTaskResponse, # This is same as FullTaskStateResponse for now
)


# Import other API modules
from agent import api as agent_api
from sandbox import api as sandbox_api
from services import billing as billing_api
from services import transcription as transcription_api

# Global instances for AgentPress services
# These will be initialized in the lifespan manager
db_connection: Optional[DBConnection] = None
supabase_task_storage: Optional[SupabaseTaskStorage] = None
tool_orchestrator: Optional[ToolOrchestrator] = None
task_state_manager: Optional[TaskStateManager] = None
task_planner: Optional[TaskPlanner] = None

instance_id = "single" # TODO: Review if this is still needed or how it's used

# Rate limiter state (if still applicable, move if not global)
ip_tracker = OrderedDict()
MAX_CONCURRENT_IPS = 25

@asynccontextmanager
async def lifespan(app: FastAPI):
    global db_connection, supabase_task_storage, tool_orchestrator, task_state_manager, task_planner
    logger.info(f"Starting up FastAPI application with instance ID: {instance_id} in {config.ENV_MODE.value} mode")
    logger.info("<<<<< CODE VERSION: DIAGNOSTIC_V1_LOCAL_SANDBOX_FIXES ACTIVE >>>>>")
    
    try:
        # Initialize database
        db_connection = DBConnection()
        await db_connection.initialize()
        
        # Initialize AgentPress services
        supabase_task_storage = SupabaseTaskStorage(db_connection=db_connection)
        
        tool_orchestrator = ToolOrchestrator()
        # Assuming DEFAULT_PLUGINS_DIR is defined in ToolOrchestrator or passed here
        tool_orchestrator.load_tools_from_directory()

        task_state_manager = TaskStateManager(storage=supabase_task_storage)
        await task_state_manager.initialize() # Load existing tasks

        task_planner = TaskPlanner(task_manager=task_state_manager, tool_orchestrator=tool_orchestrator)

        logger.info("AgentPress services (TaskStateManager, ToolOrchestrator, TaskPlanner) initialized.")

        # Initialize other APIs (agent, sandbox, etc.)
        # Pass relevant initialized components if they need them
        agent_api.initialize(
            _db=db_connection,
            _tool_orchestrator=tool_orchestrator,
            _task_planner=task_planner, # Added task_planner
            _task_state_manager=task_state_manager, # Added task_state_manager
            _instance_id=instance_id
        )
        sandbox_api.initialize(db_connection)
        
        # Initialize Redis connection
        from services import redis
        try:
            await redis.initialize_async()
            logger.info("Redis connection initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Redis connection: {e}")
        
        yield
        
        # Clean up agent resources
        logger.info("Cleaning up agent resources")
        await agent_api.cleanup()
        
        # Clean up Redis connection
        try:
            logger.info("Closing Redis connection")
            if redis.redis_async_client: # Check if client was initialized
                 await redis.close()
                 logger.info("Redis connection closed successfully")
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")
        
        # Clean up database connection
        if db_connection:
            logger.info("Disconnecting from database")
            await db_connection.disconnect()
    except Exception as e:
        logger.error(f"Error during application lifespan: {e}", exc_info=True)
        raise

app = FastAPI(lifespan=lifespan)

# Dependency provider functions
async def get_task_state_manager() -> TaskStateManager:
    if not task_state_manager:
        raise HTTPException(status_code=503, detail="TaskStateManager not initialized")
    return task_state_manager

async def get_task_planner() -> TaskPlanner:
    if not task_planner:
        raise HTTPException(status_code=503, detail="TaskPlanner not initialized")
    return task_planner

async def get_tool_orchestrator() -> ToolOrchestrator:
    if not tool_orchestrator:
        raise HTTPException(status_code=503, detail="ToolOrchestrator not initialized")
    return tool_orchestrator


# --- Task API Router ---
task_router = APIRouter(prefix="/tasks", tags=["Tasks"])

@task_router.post("/plan", response_model=FullTaskStateResponse)
async def plan_new_task(
    payload: PlanTaskPayload,
    planner: TaskPlanner = Depends(get_task_planner),
    # Add dependencies for TaskStateManager and ToolOrchestrator for PlanExecutor
    tsm: TaskStateManager = Depends(get_task_state_manager),
    tor_orch: ToolOrchestrator = Depends(get_tool_orchestrator)
):
    try:
        main_task = await planner.plan_task(payload.description, payload.context)
        if not main_task:
            raise HTTPException(status_code=500, detail="Task planning failed to produce a main task.")

        # After successful planning, initiate execution
        if main_task.status == "planned": # Check if planning was successful
            # Send initial "Plan Generated!" message via Redis
            initial_message_content = "¡Plan generado! Empezando a trabajar en tu tarea..."
            message_data = {
                "type": "status", # Or "assistant_message_update"
                "status": "starting",
                "message": initial_message_content,
                "content": {
                    "role": "assistant",
                    "content": initial_message_content
                },
                "metadata": {
                    "thread_run_id": main_task.id
                }
            }

            response_list_key = f"agent_run:{main_task.id}:responses"
            response_channel = f"agent_run:{main_task.id}:new_response"
            message_json_str = json.dumps(message_data) # Renamed to avoid conflict

            try:
                # Import redis here if not globally available or prefer scoped import
                from services import redis
                await asyncio.gather(
                    redis.rpush(response_list_key, message_json_str),
                    redis.publish(response_channel, "new")
                )
                logger.info(f"API: Sent initial 'Plan Generated' message for task {main_task.id} to Redis.")
            except Exception as e_redis: # Renamed exception variable
                logger.error(f"API: Failed to send initial 'Plan Generated' message for task {main_task.id} to Redis: {e_redis}", exc_info=True)

            logger.info(f"API: Plan created for task {main_task.id}, initiating execution.")

            plan_executor = PlanExecutor(
                main_task_id=main_task.id,
                task_manager=tsm,
                tool_orchestrator=tor_orch
            )
            asyncio.create_task(plan_executor.execute_plan())
            logger.debug(f"API: Background execution started for task {main_task.id}")

        elif main_task:
            logger.warning(f"API: Plan created for task {main_task.id} but status is '{main_task.status}'. Initial message not sent, execution not started automatically.")
        else: # If main_task is None - this case is technically covered by the check above main_task
            logger.error(f"API: Task planning failed to produce a main task. No initial message sent, no execution started.")

        return main_task
    except Exception as e:
        logger.error(f"Error during /plan endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to plan task: {str(e)}")

@task_router.post("", response_model=FullTaskStateResponse)
async def create_new_task(
    payload: CreateTaskPayload,
    tsm: TaskStateManager = Depends(get_task_state_manager)
):
    try:
        # Convert Pydantic payload to dict for create_task, filtering out None values explicitly if needed
        task_data = payload.model_dump(exclude_unset=True)

        # create_task expects individual arguments, not a dict.
        # We need to map payload fields to create_task parameters.
        new_task = await tsm.create_task(
            name=task_data["name"], # Name is mandatory in payload
            description=task_data.get("description"),
            parent_id=task_data.get("parentId"),
            dependencies=task_data.get("dependencies"),
            assigned_tools=task_data.get("assignedTools"),
            metadata=task_data.get("metadata"),
            status=task_data.get("status", "pending"),
            progress=task_data.get("progress", 0.0)
        )
        return new_task
    except Exception as e:
        logger.error(f"Error creating task: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to create task: {str(e)}")

@task_router.get("", response_model=FullTaskListResponse)
async def list_all_tasks(
    parent_id: Optional[str] = None,
    status: Optional[str] = None,
    tsm: TaskStateManager = Depends(get_task_state_manager)
):
    tasks: List[TaskState]
    if parent_id:
        tasks = await tsm.get_subtasks(parent_id)
    elif status:
        tasks = await tsm.get_tasks_by_status(status)
    else:
        tasks = await tsm.get_all_tasks()
    return {"tasks": tasks}

@task_router.get("/{task_id}", response_model=FullTaskStateResponse)
async def get_single_task(
    task_id: str,
    tsm: TaskStateManager = Depends(get_task_state_manager)
):
    task = await tsm.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@task_router.put("/{task_id}", response_model=FullTaskStateResponse)
async def update_existing_task(
    task_id: str,
    payload: UpdateTaskPayload,
    tsm: TaskStateManager = Depends(get_task_state_manager)
):
    updates = payload.model_dump(exclude_unset=True) # Get only fields that were set
    if not updates:
        raise HTTPException(status_code=400, detail="No update fields provided")

    updated_task = await tsm.update_task(task_id, updates)
    if not updated_task:
        raise HTTPException(status_code=404, detail="Task not found or update failed")
    return updated_task

@task_router.delete("/{task_id}", status_code=204) # No content for successful delete
async def delete_existing_task(
    task_id: str,
    tsm: TaskStateManager = Depends(get_task_state_manager)
):
    try:
        await tsm.delete_task(task_id)
        return # Returns 204 No Content by default
    except Exception as e: # Catch if delete_task might raise error (e.g. if task not found, though current impl doesn't)
        logger.error(f"Error deleting task {task_id}: {e}", exc_info=True)
        # Depending on desired behavior, could return 404 if task not found for deletion.
        # Current TaskStateManager.delete_task logs a warning if not found but doesn't raise.
        # If it did raise a custom "NotFound" error, we could map that to 404.
        raise HTTPException(status_code=500, detail=f"Failed to delete task: {str(e)}")


# --- End Task API Router ---


@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.time()
    client_ip = request.client.host if request.client else "unknown_client"
    method = request.method
    url = str(request.url)
    path = request.url.path
    query_params = str(request.query_params)
    
    logger.info(f"Request started: {method} {path} from {client_ip} | Query: {query_params}")
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.debug(f"Request completed: {method} {path} | Status: {response.status_code} | Time: {process_time:.2f}s")
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"Request failed: {method} {path} | Error: {str(e)} | Time: {process_time:.2f}s", exc_info=True)
        # Return a generic error response to avoid leaking details, Sentry will capture it.
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# CORS Middleware (ensure this is correctly placed if you have multiple app instances or modify it)
# Define allowed origins based on environment
allowed_origins_list = ["https://www.suna.so", "https://suna.so", "http://localhost:3000"]
allow_origin_regex_str: Optional[str] = None # Renamed to avoid conflict with imported var if any

if config.ENV_MODE == EnvMode.STAGING:
    allowed_origins_list.append("https://staging.suna.so")
    allow_origin_regex_str = r"https://suna-.*-prjcts\.vercel\.app"
elif config.ENV_MODE == EnvMode.DEV: # Example for local dev with different ports or setups
    allowed_origins_list.extend(["http://localhost:3001", "http://127.0.0.1:3000"])

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins_list,
    allow_origin_regex=allow_origin_regex_str,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Request-ID"], # Added X-Request-ID example
)

# Include existing routers
app.include_router(agent_api.router, prefix="/api")
app.include_router(sandbox_api.router, prefix="/api")
app.include_router(billing_api.router, prefix="/api")
app.include_router(transcription_api.router, prefix="/api")

# Include the new task router
app.include_router(task_router, prefix="/api") # All task routes will be under /api/tasks

# --- Frontend Error Logging ---
class FrontendErrorPayload(BaseModel):
    message: str
    source: Optional[str] = None # e.g., component name, file name
    stack_trace: Optional[str] = None
    url: Optional[str] = None # The URL where the error occurred
    user_agent: Optional[str] = None
    context: Optional[Dict[str, Any]] = None # For any other contextual data

frontend_error_logger = get_logger('FRONTEND')
log_router = APIRouter(prefix="/api", tags=["Logging"])

@log_router.post("/log_frontend_error")
async def log_frontend_error_endpoint(payload: FrontendErrorPayload, request: Request): # Renamed function
    try:
        client_ip = request.client.host if request.client else "unknown"

        log_message = (
            f"Frontend Error from {client_ip} at {payload.url or 'unknown URL'}: "
            f"{payload.message}"
        )

        extra_info = {
            "source": payload.source,
            "stack_trace": payload.stack_trace,
            "user_agent": payload.user_agent,
            "additional_context": payload.context,
            "client_ip": client_ip # Also adding client_ip to extra
        }

        extra_info_filtered = {k: v for k, v in extra_info.items() if v is not None}

        frontend_error_logger.error(log_message, extra=extra_info_filtered)

        return {"status": "logged"}
    except Exception as e:
        # Use the main backend logger to log issues with the logging endpoint itself
        main_logger = get_logger('BACKEND') # Or the default 'logger' instance
        main_logger.error(f"Error in /log_frontend_error endpoint: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to log frontend error.")

app.include_router(log_router, prefix="") # Add the new router, prefix is already in APIRouter

# --- End Frontend Error Logging ---


@app.get("/api/health")
async def health_check():
    """Health check endpoint to verify API is working."""
    logger.info("Health check endpoint called")
    return {
        "status": "ok", 
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "instance_id": instance_id
    }

if __name__ == "__main__":
    import uvicorn
    
    workers = 2
    
    logger.info(f"Starting server on 0.0.0.0:8000 with {workers} workers")
    uvicorn.run(
        "api:app", 
        host="0.0.0.0", 
        port=8000,
        workers=workers,
        # reload=True
    )