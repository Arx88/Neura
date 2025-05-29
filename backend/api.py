from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import sentry
from contextlib import asynccontextmanager
from agentpress.thread_manager import ThreadManager
from services.supabase import DBConnection
from datetime import datetime, timezone
from dotenv import load_dotenv
from utils.config import config, EnvMode
import asyncio
from utils.logger import logger
import uuid
import time
from collections import OrderedDict
from fastapi import APIRouter, Depends, HTTPException # Added APIRouter, Depends, HTTPException
from typing import List, Dict, Optional # Added List, Dict, Optional
from pydantic import BaseModel # Added BaseModel
import litellm # Added litellm

# Import the agent API module
from agent import api as agent_api
from sandbox import api as sandbox_api
from services import billing as billing_api
from services import transcription as transcription_api

# Load environment variables (these will be available through config)
load_dotenv()

# Initialize managers
db = DBConnection()
instance_id = "single"

# Rate limiter state
ip_tracker = OrderedDict()
MAX_CONCURRENT_IPS = 25

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info(f"Starting up FastAPI application with instance ID: {instance_id} in {config.ENV_MODE.value} mode")
    
    try:
        # Initialize database
        await db.initialize()
        
        # Initialize the agent API with shared resources
        agent_api.initialize(
            db,
            instance_id
        )
        
        # Initialize the sandbox API with shared resources
        sandbox_api.initialize(db)
        
        # Initialize Redis connection
        from services import redis
        try:
            await redis.initialize_async()
            logger.info("Redis connection initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Redis connection: {e}")
            # Continue without Redis - the application will handle Redis failures gracefully
        
        # Start background tasks
        # asyncio.create_task(agent_api.restore_running_agent_runs())
        
        yield
        
        # Clean up agent resources
        logger.info("Cleaning up agent resources")
        await agent_api.cleanup()
        
        # Clean up Redis connection
        try:
            logger.info("Closing Redis connection")
            await redis.close()
            logger.info("Redis connection closed successfully")
        except Exception as e:
            logger.error(f"Error closing Redis connection: {e}")
        
        # Clean up database connection
        logger.info("Disconnecting from database")
        await db.disconnect()
    except Exception as e:
        logger.error(f"Error during application startup: {e}")
        raise

app = FastAPI(lifespan=lifespan)

@app.middleware("http")
async def log_requests_middleware(request: Request, call_next):
    start_time = time.time()
    client_ip = request.client.host
    method = request.method
    url = str(request.url)
    path = request.url.path
    query_params = str(request.query_params)
    
    # Log the incoming request
    logger.info(f"Request started: {method} {path} from {client_ip} | Query: {query_params}")
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        logger.debug(f"Request completed: {method} {path} | Status: {response.status_code} | Time: {process_time:.2f}s")
        return response
    except Exception as e:
        process_time = time.time() - start_time
        logger.error(f"Request failed: {method} {path} | Error: {str(e)} | Time: {process_time:.2f}s")
        raise

# Define allowed origins based on environment
allowed_origins = ["https://www.suna.so", "https://suna.so", "http://localhost:3000"]
allow_origin_regex = None

# Add staging-specific origins
if config.ENV_MODE == EnvMode.STAGING:
    allowed_origins.append("https://staging.suna.so")
    allow_origin_regex = r"https://suna-.*-prjcts\.vercel\.app"

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allow_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Include the agent router with a prefix
app.include_router(agent_api.router, prefix="/api")

# Include the sandbox router with a prefix
app.include_router(sandbox_api.router, prefix="/api")

# Include the billing router with a prefix
app.include_router(billing_api.router, prefix="/api")

# Include the transcription router with a prefix
app.include_router(transcription_api.router, prefix="/api")

# --- LLM Models API ---

class ModelInfo(BaseModel):
    id: str
    name: str

class ProviderModels(BaseModel):
    configured: bool
    models: List[ModelInfo]

class AllModelsResponse(BaseModel):
    ollama: Optional[ProviderModels] = None
    openai: ProviderModels
    anthropic: ProviderModels
    openrouter: ProviderModels
    groq: ProviderModels
    bedrock: ProviderModels

llm_api_router = APIRouter()

PREDEFINED_MODELS = {
    "openai": {
        "config_key_env": "OPENAI_API_KEY", # Store env var name
        "models": [
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
            {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"},
            {"id": "gpt-3.5-turbo", "name": "GPT-3.5 Turbo"},
        ]
    },
    "anthropic": {
        "config_key_env": "ANTHROPIC_API_KEY",
        "models": [
            {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus"},
            {"id": "claude-3-sonnet-20240229", "name": "Claude 3 Sonnet"},
            {"id": "claude-3-haiku-20240307", "name": "Claude 3 Haiku"},
        ]
    },
    "openrouter": {
        "config_key_env": "OPENROUTER_API_KEY",
        "models": [
            {"id": "openrouter/anthropic/claude-3-opus", "name": "Claude 3 Opus (OpenRouter)"},
            {"id": "openrouter/google/gemini-pro-1.5", "name": "Gemini Pro 1.5 (OpenRouter)"},
            {"id": "openrouter/openai/gpt-4o", "name": "GPT-4o (OpenRouter)"},
            {"id": "openrouter/mistralai/mistral-large", "name": "Mistral Large (OpenRouter)"},
            {"id": "openrouter/meta-llama/llama-3-70b-instruct", "name": "Llama 3 70B (OpenRouter)"}
        ]
    },
    "groq": {
        "config_key_env": "GROQ_API_KEY",
        "models": [
            {"id": "llama3-8b-8192", "name": "Llama3 8B (Groq)"},
            {"id": "mixtral-8x7b-32768", "name": "Mixtral 8x7B (Groq)"},
            {"id": "gemma-7b-it", "name": "Gemma 7B (Groq)"}
        ]
    },
    "bedrock": {
        # For Bedrock, configuration depends on multiple AWS keys
        "is_configured_check": lambda: bool(config.AWS_ACCESS_KEY_ID and config.AWS_SECRET_ACCESS_KEY and config.AWS_REGION_NAME),
        "models": [
            {"id": "bedrock/anthropic.claude-3-opus-v1:0", "name": "Claude 3 Opus (Bedrock)"},
            {"id": "bedrock/anthropic.claude-3-sonnet-v1:0", "name": "Claude 3 Sonnet (Bedrock)"},
            {"id": "bedrock/amazon.titan-text-express-v1", "name": "Titan Text Express (Bedrock)"}
        ]
    }
}

@llm_api_router.get("/llm/all-models", response_model=AllModelsResponse)
async def get_all_llm_models():
    logger.debug("Fetching all LLM models")
    
    # Ollama
    ollama_provider: Optional[ProviderModels] = None
    if config.OLLAMA_API_BASE:
        ollama_models_list = []
        try:
            logger.info(f"Attempting to fetch Ollama models from {config.OLLAMA_API_BASE}")
            ollama_raw_models = await litellm.aget_model_list(base_url=config.OLLAMA_API_BASE, api_key="ollama") # Added await and api_key
            if ollama_raw_models:
                for model_data in ollama_raw_models:
                    if isinstance(model_data, dict):
                        model_id = model_data.get("id") or model_data.get("name") or model_data.get("model") # More robust id fetching
                        model_name = model_data.get("name") or model_id # Fallback name
                    elif isinstance(model_data, str):
                        model_id = model_data
                        model_name = model_data
                    else:
                        logger.warning(f"Unexpected model data format from Ollama: {model_data}")
                        continue
                    
                    if model_id:
                        # Ensure "ollama/" prefix, handling cases where it might already exist (though unlikely from get_model_list)
                        full_id = f"ollama/{model_id}" if not model_id.startswith("ollama/") else model_id
                        # Derive a user-friendly name if not sufficiently descriptive
                        simple_name = model_id.split('/')[-1] # Get the part after "ollama/" if present
                        display_name = f"{simple_name.replace('-', ' ').title()} (Local)" if model_name == model_id else f"{model_name} (Local)"

                        ollama_models_list.append(ModelInfo(id=full_id, name=display_name))
                logger.info(f"Successfully fetched {len(ollama_models_list)} models from Ollama.")
            else:
                logger.info("No models returned from Ollama.")
            ollama_provider = ProviderModels(configured=True, models=ollama_models_list)
        except Exception as e:
            logger.error(f"Error fetching Ollama models from {config.OLLAMA_API_BASE}: {e}")
            # OLLAMA_API_BASE is configured, but fetching failed
            ollama_provider = ProviderModels(configured=True, models=[]) 
    else:
        logger.info("Ollama API base URL not configured.")
        ollama_provider = ProviderModels(configured=False, models=[])

    # Cloud Providers
    provider_responses: Dict[str, ProviderModels] = {}
    for provider_key, details in PREDEFINED_MODELS.items():
        is_configured = False
        if "is_configured_check" in details: # For Bedrock
            is_configured = details["is_configured_check"]()
        elif "config_key_env" in details: # For others
            # Check if the attribute exists on config and is not None/empty
            config_val = getattr(config, details["config_key_env"], None)
            is_configured = bool(config_val) 
        
        models = [ModelInfo(**m) for m in details["models"]]
        provider_responses[provider_key] = ProviderModels(configured=is_configured, models=models)
        logger.debug(f"Provider {provider_key} configured: {is_configured}, Models: {len(models)}")

    return AllModelsResponse(
        ollama=ollama_provider,
        openai=provider_responses["openai"],
        anthropic=provider_responses["anthropic"],
        openrouter=provider_responses["openrouter"],
        groq=provider_responses["groq"],
        bedrock=provider_responses["bedrock"],
    )

# Include the LLM API router
app.include_router(llm_api_router, prefix="/api")


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