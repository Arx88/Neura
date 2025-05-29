"""
LLM API interface for making calls to various language models.

This module provides a unified interface for making API calls to different LLM providers
(OpenAI, Anthropic, Groq, etc.) using LiteLLM. It includes support for:
- Streaming responses
- Tool calls and function calling
- Retry logic with exponential backoff
- Model-specific configurations
- Comprehensive error handling and logging
"""

from typing import Union, Dict, Any, Optional, AsyncGenerator, List
import os
import json
import asyncio
from unittest.mock import patch
from openai import OpenAIError
import litellm
from utils.logger import logger
from utils.config import config

# litellm.set_verbose=True
litellm.modify_params=True

# Constants
MAX_RETRIES = 2
RATE_LIMIT_DELAY = 30
RETRY_DELAY = 0.1

class LLMError(Exception):
    """Base exception for LLM-related errors."""
    pass

class LLMRetryError(LLMError):
    """Exception raised when retries are exhausted."""
    pass

def setup_api_keys() -> None:
    """Set up API keys from environment variables."""
    providers = ['OPENAI', 'ANTHROPIC', 'GROQ', 'OPENROUTER', 'OLLAMA']
    for provider in providers:
        key = getattr(config, f'{provider}_API_KEY')
        if key:
            logger.debug(f"API key set for provider: {provider}")
        else:
            logger.warning(f"No API key found for provider: {provider}")

    # Set up OpenRouter API base if not already set
    if config.OPENROUTER_API_KEY and config.OPENROUTER_API_BASE:
        os.environ['OPENROUTER_API_BASE'] = config.OPENROUTER_API_BASE
        logger.debug(f"Set OPENROUTER_API_BASE to {config.OPENROUTER_API_BASE}")

    # Set up AWS Bedrock credentials
    aws_access_key = config.AWS_ACCESS_KEY_ID
    aws_secret_key = config.AWS_SECRET_ACCESS_KEY
    aws_region = config.AWS_REGION_NAME

    if aws_access_key and aws_secret_key and aws_region:
        logger.debug(f"AWS credentials set for Bedrock in region: {aws_region}")
        # Configure LiteLLM to use AWS credentials
        os.environ['AWS_ACCESS_KEY_ID'] = aws_access_key
        os.environ['AWS_SECRET_ACCESS_KEY'] = aws_secret_key
        os.environ['AWS_REGION_NAME'] = aws_region
    else:
        logger.warning(f"Missing AWS credentials for Bedrock integration - access_key: {bool(aws_access_key)}, secret_key: {bool(aws_secret_key)}, region: {aws_region}")

async def handle_error(error: Exception, attempt: int, max_attempts: int) -> None:
    """Handle API errors with appropriate delays and logging."""
    delay = RATE_LIMIT_DELAY if isinstance(error, litellm.exceptions.RateLimitError) else RETRY_DELAY
    logger.warning(f"Error on attempt {attempt + 1}/{max_attempts}: {str(error)}")
    logger.debug(f"Waiting {delay} seconds before retry...")
    await asyncio.sleep(delay)

def prepare_params(
    messages: List[Dict[str, Any]],
    model_name: str,
    temperature: float = 0,
    max_tokens: Optional[int] = None,
    response_format: Optional[Any] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    stream: bool = False,
    top_p: Optional[float] = None,
    model_id: Optional[str] = None,
    enable_thinking: Optional[bool] = False,
    reasoning_effort: Optional[str] = 'low'
) -> Dict[str, Any]:
    """Prepare parameters for the API call."""
    params = {
        "model": model_name,
        "messages": messages,
        "temperature": temperature,
        "response_format": response_format,
        "top_p": top_p,
        "stream": stream,
    }

    if api_key:
        params["api_key"] = api_key
    if api_base:
        params["api_base"] = api_base
    if model_id:
        params["model_id"] = model_id

    # Handle token limits
    if max_tokens is not None:
        # For Claude 3.7 in Bedrock, do not set max_tokens or max_tokens_to_sample
        # as it causes errors with inference profiles
        if model_name.startswith("bedrock/") and "claude-3-7" in model_name:
            logger.debug(f"Skipping max_tokens for Claude 3.7 model: {model_name}")
            # Do not add any max_tokens parameter for Claude 3.7
        else:
            param_name = "max_completion_tokens" if 'o1' in model_name else "max_tokens"
            params[param_name] = max_tokens

    # Add tools if provided
    if tools:
        params.update({
            "tools": tools,
            "tool_choice": tool_choice
        })
        logger.debug(f"Added {len(tools)} tools to API parameters")

    # # Add Claude-specific headers
    if "claude" in model_name.lower() or "anthropic" in model_name.lower():
        params["extra_headers"] = {
            # "anthropic-beta": "max-tokens-3-5-sonnet-2024-07-15"
            "anthropic-beta": "output-128k-2025-02-19"
        }
        logger.debug("Added Claude-specific headers")

    # Add OpenRouter-specific parameters
    if model_name.startswith("openrouter/"):
        logger.debug(f"Preparing OpenRouter parameters for model: {model_name}")

        # Add optional site URL and app name from config
        site_url = config.OR_SITE_URL
        app_name = config.OR_APP_NAME
        if site_url or app_name:
            extra_headers = params.get("extra_headers", {})
            if site_url:
                extra_headers["HTTP-Referer"] = site_url
            if app_name:
                extra_headers["X-Title"] = app_name
            params["extra_headers"] = extra_headers
            logger.debug(f"Added OpenRouter site URL and app name to headers")
    elif model_name.startswith("ollama/"):
        logger.debug(f"Preparing Ollama parameters for model: {model_name}")
        if config.OLLAMA_API_BASE:
            params["api_base"] = config.OLLAMA_API_BASE
            logger.debug(f"Set Ollama API base to: {config.OLLAMA_API_BASE}")
        else:
            logger.debug("No OLLAMA_API_BASE configured, Ollama will use default.")

        # Ensure api_key is None for Ollama if it's not a non-empty string
        if config.OLLAMA_API_KEY and config.OLLAMA_API_KEY.strip(): # Checks if key exists and is not just whitespace
            params["api_key"] = config.OLLAMA_API_KEY
            logger.debug("Using provided OLLAMA_API_KEY for Ollama.")
        else:
            params["api_key"] = None
            logger.debug("No OLLAMA_API_KEY provided or key is empty. Setting api_key to None for Ollama to ensure no authentication is attempted.")

        # Prevent OpenRouter interference for Ollama calls
        if params.get("extra_headers"):
            params["extra_headers"].pop("HTTP-Referer", None)
            params["extra_headers"].pop("X-Title", None)
            if not params["extra_headers"]: # If empty after popping, remove the key
                params.pop("extra_headers")
            logger.debug("Cleared OpenRouter specific headers for Ollama call, if they existed.")
        
        # Ensure that if this is an Ollama call, we are not accidentally using OpenRouter's base.
        # This is already handled by setting params["api_base"] = config.OLLAMA_API_BASE earlier in this block,
        # but we can double-check that it's not overridden by a general OpenRouter config later.
        # However, the order of `prepare_params` seems to set provider-specific things after general ones.
        # The main protection is that params["api_base"] is explicitly Ollama's.

    # Add Bedrock-specific parameters
    if model_name.startswith("bedrock/"):
        logger.debug(f"Preparing AWS Bedrock parameters for model: {model_name}")

        if not model_id and "anthropic.claude-3-7-sonnet" in model_name:
            params["model_id"] = "arn:aws:bedrock:us-west-2:935064898258:inference-profile/us.anthropic.claude-3-7-sonnet-20250219-v1:0"
            logger.debug(f"Auto-set model_id for Claude 3.7 Sonnet: {params['model_id']}")

    # Apply Anthropic prompt caching (minimal implementation)
    # Check model name *after* potential modifications (like adding bedrock/ prefix)
    effective_model_name = params.get("model", model_name) # Use model from params if set, else original
    if "claude" in effective_model_name.lower() or "anthropic" in effective_model_name.lower():
        messages = params["messages"] # Direct reference, modification affects params

        # Ensure messages is a list
        if not isinstance(messages, list):
            return params # Return early if messages format is unexpected

        # 1. Process the first message if it's a system prompt with string content
        if messages and messages[0].get("role") == "system":
            content = messages[0].get("content")
            if isinstance(content, str):
                # Wrap the string content in the required list structure
                messages[0]["content"] = [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
            elif isinstance(content, list):
                 # If content is already a list, check if the first text block needs cache_control
                 for item in content:
                     if isinstance(item, dict) and item.get("type") == "text":
                         if "cache_control" not in item:
                             item["cache_control"] = {"type": "ephemeral"}
                             break # Apply to the first text block only for system prompt

        # 2. Find and process relevant user and assistant messages
        last_user_idx = -1
        second_last_user_idx = -1
        last_assistant_idx = -1

        for i in range(len(messages) - 1, -1, -1):
            role = messages[i].get("role")
            if role == "user":
                if last_user_idx == -1:
                    last_user_idx = i
                elif second_last_user_idx == -1:
                    second_last_user_idx = i
            elif role == "assistant":
                if last_assistant_idx == -1:
                    last_assistant_idx = i

            # Stop searching if we've found all needed messages
            if last_user_idx != -1 and second_last_user_idx != -1 and last_assistant_idx != -1:
                 break

        # Helper function to apply cache control
        def apply_cache_control(message_idx: int, message_role: str):
            if message_idx == -1:
                return

            message = messages[message_idx]
            content = message.get("content")

            if isinstance(content, str):
                message["content"] = [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        if "cache_control" not in item:
                           item["cache_control"] = {"type": "ephemeral"}

        # Apply cache control to the identified messages
        apply_cache_control(last_user_idx, "last user")
        apply_cache_control(second_last_user_idx, "second last user")
        apply_cache_control(last_assistant_idx, "last assistant")

    # Add reasoning_effort for Anthropic models if enabled
    use_thinking = enable_thinking if enable_thinking is not None else False
    is_anthropic = "anthropic" in effective_model_name.lower() or "claude" in effective_model_name.lower()

    if is_anthropic and use_thinking:
        effort_level = reasoning_effort if reasoning_effort else 'low'
        params["reasoning_effort"] = effort_level
        params["temperature"] = 1.0 # Required by Anthropic when reasoning_effort is used
        logger.info(f"Anthropic thinking enabled with reasoning_effort='{effort_level}'")

    return params

async def make_llm_api_call(
    messages: List[Dict[str, Any]],
    model_name: str,
    response_format: Optional[Any] = None,
    temperature: float = 0,
    max_tokens: Optional[int] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    tool_choice: str = "auto",
    api_key: Optional[str] = None,
    api_base: Optional[str] = None,
    stream: bool = False,
    top_p: Optional[float] = None,
    model_id: Optional[str] = None,
    enable_thinking: Optional[bool] = False,
    reasoning_effort: Optional[str] = 'low'
) -> Union[Dict[str, Any], AsyncGenerator]:
    """
    Make an API call to a language model using LiteLLM.

    Args:
        messages: List of message dictionaries for the conversation
        model_name: Name of the model to use (e.g., "gpt-4", "claude-3", "openrouter/openai/gpt-4", "bedrock/anthropic.claude-3-sonnet-20240229-v1:0")
        response_format: Desired format for the response
        temperature: Sampling temperature (0-1)
        max_tokens: Maximum tokens in the response
        tools: List of tool definitions for function calling
        tool_choice: How to select tools ("auto" or "none")
        api_key: Override default API key
        api_base: Override default API base URL
        stream: Whether to stream the response
        top_p: Top-p sampling parameter
        model_id: Optional ARN for Bedrock inference profiles
        enable_thinking: Whether to enable thinking
        reasoning_effort: Level of reasoning effort

    Returns:
        Union[Dict[str, Any], AsyncGenerator]: API response or stream

    Raises:
        LLMRetryError: If API call fails after retries
        LLMError: For other API-related errors
    """
    # debug <timestamp>.json messages
    logger.info(f"Making LLM API call to model: {model_name} (Thinking: {enable_thinking}, Effort: {reasoning_effort})")
    logger.info(f"📡 API Call: Using model {model_name}")
    params = prepare_params(
        messages=messages,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
        response_format=response_format,
        tools=tools,
        tool_choice=tool_choice,
        api_key=api_key,
        api_base=api_base,
        stream=stream,
        top_p=top_p,
        model_id=model_id,
        enable_thinking=enable_thinking,
        reasoning_effort=reasoning_effort
    )
    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            logger.debug(f"Attempt {attempt + 1}/{MAX_RETRIES}")
            # logger.debug(f"API request parameters: {json.dumps(params, indent=2)}")

            response = await litellm.acompletion(**params)
            logger.debug(f"Successfully received API response from {model_name}")
            logger.debug(f"Response: {response}")
            return response

        except (litellm.exceptions.RateLimitError, OpenAIError, json.JSONDecodeError) as e:
            last_error = e
            await handle_error(e, attempt, MAX_RETRIES)

        except Exception as e:
            logger.error(f"Unexpected error during API call: {str(e)}", exc_info=True)
            raise LLMError(f"API call failed: {str(e)}")

    error_msg = f"Failed to make API call after {MAX_RETRIES} attempts"
    if last_error:
        error_msg += f". Last error: {str(last_error)}"
    logger.error(error_msg, exc_info=True)
    raise LLMRetryError(error_msg)

# Initialize API keys on module import
setup_api_keys()

# Test code for OpenRouter integration
async def test_openrouter():
    """Test the OpenRouter integration with a simple query."""
    test_messages = [
        {"role": "user", "content": "Hello, can you give me a quick test response?"}
    ]

    try:
        # Test with standard OpenRouter model
        print("\n--- Testing standard OpenRouter model ---")
        response = await make_llm_api_call(
            model_name="openrouter/openai/gpt-4o-mini",
            messages=test_messages,
            temperature=0.7,
            max_tokens=100
        )
        print(f"Response: {response.choices[0].message.content}")

        # Test with deepseek model
        print("\n--- Testing deepseek model ---")
        response = await make_llm_api_call(
            model_name="openrouter/deepseek/deepseek-r1-distill-llama-70b",
            messages=test_messages,
            temperature=0.7,
            max_tokens=100
        )
        print(f"Response: {response.choices[0].message.content}")
        print(f"Model used: {response.model}")

        # Test with Mistral model
        print("\n--- Testing Mistral model ---")
        response = await make_llm_api_call(
            model_name="openrouter/mistralai/mixtral-8x7b-instruct",
            messages=test_messages,
            temperature=0.7,
            max_tokens=100
        )
        print(f"Response: {response.choices[0].message.content}")
        print(f"Model used: {response.model}")

        return True
    except Exception as e:
        print(f"Error testing OpenRouter: {str(e)}")
        return False

async def test_bedrock():
    """Test the AWS Bedrock integration with a simple query."""
    test_messages = [
        {"role": "user", "content": "Hello, can you give me a quick test response?"}
    ]

    try:
        response = await make_llm_api_call(
            model_name="bedrock/anthropic.claude-3-7-sonnet-20250219-v1:0",
            model_id="arn:aws:bedrock:us-west-2:935064898258:inference-profile/us.anthropic.claude-3-7-sonnet-20250219-v1:0",
            messages=test_messages,
            temperature=0.7,
            # Claude 3.7 has issues with max_tokens, so omit it
            # max_tokens=100
        )
        print(f"Response: {response.choices[0].message.content}")
        print(f"Model used: {response.model}")

        return True
    except Exception as e:
        print(f"Error testing Bedrock: {str(e)}")
        return False

async def test_ollama():
    """Test the Ollama integration, especially the no-API-key scenario."""
    print("\n--- Testing Ollama model (No API Key Scenario) ---")
    logger.info("Starting Ollama test: No API Key scenario.")
    # The note about setting OLLAMA_API_BASE in .env is less critical here
    # as we are mocking it, but good for general knowledge.
    print("Note: For this specific test, OLLAMA_API_KEY and OLLAMA_API_BASE are mocked.")
    print("For general manual testing, ensure your Ollama server is running and accessible.")

    test_messages = [
        {"role": "user", "content": "Hello, can you give me a quick test response? Why is the sky blue?"}
    ]

    # Mock config attributes for this specific test duration
    # This ensures we test the scenario where no API key is provided in the config
    # and that api_base is correctly picked up (or defaulted by litellm if not set by prepare_params from config).
    with patch.object(config, 'OLLAMA_API_KEY', None), \
         patch.object(config, 'OLLAMA_API_BASE', 'http://localhost:11434') as mock_api_base:

        # This log confirms the values *within* the patched context
        logger.info(f"Mocked config for test: OLLAMA_API_KEY='{config.OLLAMA_API_KEY}', OLLAMA_API_BASE='{config.OLLAMA_API_BASE}'")
        # The following print statement helps confirm mocking in test output
        print(f"Mocked config during test: OLLAMA_API_KEY='{config.OLLAMA_API_KEY}', OLLAMA_API_BASE='{config.OLLAMA_API_BASE}'")

        try:
            # We do not pass api_key or api_base to make_llm_api_call directly.
            # The prepare_params function should pick them up from the (mocked) config.
            # Specifically, prepare_params should set params["api_key"] = None
            # and params["api_base"] = "http://localhost:11434"
            response = await make_llm_api_call(
                model_name="ollama/llama2", # A common Ollama model
                messages=test_messages,
                temperature=0.7,
                max_tokens=150
            )

            # Check for a valid response structure
            if response and response.choices and response.choices[0].message and response.choices[0].message.content:
                print(f"Response: {response.choices[0].message.content}")
                logger.info("Ollama test (No API Key) successful with valid response.")
            else:
                print(f"Received empty or unexpected response structure: {response}")
                logger.warning(f"Ollama test (No API Key) received empty/unexpected response: {response}")
                # Depending on strictness, this could be a failure.
                # For now, a non-error completion is the primary check against auth errors.

            print(f"Model used: {response.model}") # LiteLLM might modify this
            return True
        except litellm.exceptions.AuthenticationError as e:
            # This would be a direct indication that prepare_params didn't prevent an auth issue.
            logger.error(f"Ollama test (No API Key) FAILED directly with AuthenticationError: {e}", exc_info=True)
            print(f"Error testing Ollama (AuthenticationError): {str(e)}")
            return False
        except LLMRetryError as e:
            # This is a likely error if there's an underlying auth issue not caught by prepare_params,
            # or if the Ollama server is not reachable at the mocked address.
            logger.error(f"Ollama test (No API Key) FAILED due to LLMRetryError: {e}", exc_info=True)
            print(f"Error testing Ollama (LLMRetryError): {str(e)}")
            # Specifically check if the error message indicates an authentication problem
            if "authentication" in str(e).lower() or "auth" in str(e).lower() or "key" in str(e).lower():
                print("The LLMRetryError seems related to authentication, which this test aims to prevent.")
            elif "connection refused" in str(e).lower():
                print("The LLMRetryError seems related to a connection issue. Ensure Ollama server is running at the mocked address (http://localhost:11434).")
            return False
        except Exception as e:
            # Catch any other unexpected errors
            logger.error(f"Ollama test (No API Key) FAILED with unexpected error: {e}", exc_info=True)
            print(f"Error testing Ollama (Unexpected Error): {str(e)}")
            return False

if __name__ == "__main__":
    import asyncio
    from utils.config import config # Ensure config is loaded for the test
    from utils.logger import logger # Ensure logger is available

    print("----------------------------------------------------------------------")
    print("Starting Ollama Connection Test")
    print("----------------------------------------------------------------------")
    print(f"This test will attempt to connect to an Ollama model.")
    print(f"Using OLLAMA_API_BASE from config: {config.OLLAMA_API_BASE or 'http://localhost:11434 (default assumed by test)'}")
    print(f"Using OLLAMA_API_KEY from config: {'Not set (Correct for Ollama)' if not config.OLLAMA_API_KEY else 'Set (will be overridden to None by test logic if empty)'}")
    print(f"Target test model inside test_ollama(): ollama/llama2 (ensure this model is pulled: `ollama pull llama2`)")
    print("Your configured MODEL_TO_USE is: " + config.MODEL_TO_USE)
    print("If MODEL_TO_USE is an ollama model, this test should reflect its connectivity if llama2 is also available.")
    print("----------------------------------------------------------------------")
    
    # The test_ollama() function internally mocks OLLAMA_API_KEY to None and OLLAMA_API_BASE to http://localhost:11434
    # for the duration of the test call. This is to specifically test the no-auth scenario.
    # If you want to test with your actual .env settings, you would call make_llm_api_call directly.
    # However, the purpose of this test in llm.py is to validate the direct Ollama call logic within litellm.

    ollama_test_success = asyncio.run(test_ollama())

    print("----------------------------------------------------------------------")
    if ollama_test_success:
        print("✅ Ollama integration test completed successfully!")
        print("This means the test was able to make a call via LiteLLM to the specified Ollama model without authentication errors.")
    else:
        print("❌ Ollama integration test failed!")
        print("This could be due to several reasons:")
        print("  1. Ollama server is not running or not accessible at the API base (default test: http://localhost:11434).")
        print("  2. The 'ollama/llama2' model is not available on your Ollama server (run `ollama pull llama2`).")
        print("  3. A firewall is blocking the connection.")
        print("  4. An unexpected issue with LiteLLM or the script itself (check logs above).")
        print("  5. The 'OpenrouterException' might still be occurring if the fix was not effective.")
    print("----------------------------------------------------------------------")
