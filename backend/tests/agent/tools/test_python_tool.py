import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import asyncio
import os
import sys
import types # For creating a new module object

# --- Strategy 2: Set Environment Variables for Tests ---
# Set dummy environment variables BEFORE any application imports that might load config
# This aims to satisfy the Configuration class's _validate method.

# Core requirements that were causing AttributeError
os.environ['SUPABASE_URL'] = 'http://dummy.supabase.co'
os.environ['SUPABASE_ANON_KEY'] = 'dummy_anon_key'
os.environ['SUPABASE_SERVICE_ROLE_KEY'] = 'dummy_service_key'

# LLM API Keys (set to dummy if not Optional and no default)
os.environ['ANTHROPIC_API_KEY'] = 'dummy_anthropic_key' 
# OPENAI_API_KEY is Optional
# GROQ_API_KEY is Optional
# OPENROUTER_API_KEY is Optional
# OLLAMA_API_KEY is Optional

# AWS (all Optional)

# Redis
os.environ['REDIS_HOST'] = 'dummy_redis_host'
os.environ['REDIS_PASSWORD'] = 'dummy_redis_password'
# REDIS_PORT has default
# REDIS_SSL has default

# Daytona
os.environ['DAYTONA_API_KEY'] = 'dummy_daytona_key'
os.environ['DAYTONA_SERVER_URL'] = 'http://dummy.daytona.url'
os.environ['DAYTONA_TARGET'] = 'eu' # Valid enum value

# Search & Other APIs
os.environ['TAVILY_API_KEY'] = 'dummy_tavily_key'
os.environ['RAPID_API_KEY'] = 'dummy_rapidapi_key'
os.environ['FIRECRAWL_API_KEY'] = 'dummy_firecrawl_key'
# CLOUDFLARE_API_TOKEN is Optional

# Stripe (explicitly listed as str type hints without Optional)
# The config class has defaults for these, but _load_from_env might set them to None if not in os.environ,
# causing _validate to fail. So, we provide dummy values.
os.environ['STRIPE_FREE_TIER_ID_PROD'] = 'price_dummy_free_prod'
os.environ['STRIPE_TIER_2_20_ID_PROD'] = 'price_dummy_t2_20_prod'
os.environ['STRIPE_TIER_6_50_ID_PROD'] = 'price_dummy_t6_50_prod'
os.environ['STRIPE_TIER_12_100_ID_PROD'] = 'price_dummy_t12_100_prod'
os.environ['STRIPE_TIER_25_200_ID_PROD'] = 'price_dummy_t25_200_prod'
os.environ['STRIPE_TIER_50_400_ID_PROD'] = 'price_dummy_t50_400_prod'
os.environ['STRIPE_TIER_125_800_ID_PROD'] = 'price_dummy_t125_800_prod'
os.environ['STRIPE_TIER_200_1000_ID_PROD'] = 'price_dummy_t200_1000_prod'
os.environ['STRIPE_FREE_TIER_ID_STAGING'] = 'price_dummy_free_staging'
os.environ['STRIPE_TIER_2_20_ID_STAGING'] = 'price_dummy_t2_20_staging'
os.environ['STRIPE_TIER_6_50_ID_STAGING'] = 'price_dummy_t6_50_staging'
os.environ['STRIPE_TIER_12_100_ID_STAGING'] = 'price_dummy_t12_100_staging'
os.environ['STRIPE_TIER_25_200_ID_STAGING'] = 'price_dummy_t25_200_staging'
os.environ['STRIPE_TIER_50_400_ID_STAGING'] = 'price_dummy_t50_400_staging'
os.environ['STRIPE_TIER_125_800_ID_STAGING'] = 'price_dummy_t125_800_staging'
os.environ['STRIPE_TIER_200_1000_ID_STAGING'] = 'price_dummy_t200_1000_staging'
os.environ['STRIPE_PRODUCT_ID_PROD'] = 'prod_dummy_prod'
os.environ['STRIPE_PRODUCT_ID_STAGING'] = 'prod_dummy_staging'
# STRIPE_SECRET_KEY is Optional
# STRIPE_WEBHOOK_SECRET is Optional
# STRIPE_DEFAULT_PLAN_ID is Optional

# LANGFUSE_HOST has a default, LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY are Optional.

# We also need to ensure ENV_MODE is set for predictable behavior of properties
os.environ['ENV_MODE'] = 'local' 
# --- End of Environment Variable Setup ---


# Now, these imports should succeed as Configuration() will find the env vars
from backend.agent.tools.python_tool import PythonTool
from agentpress.tool import ToolResult


class MockExecuteResponse:
    def __init__(self, cmd_id, exit_code=0):
        self.cmd_id = cmd_id
        self.exit_code = exit_code

class TestPythonTool(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.project_id = "test_project_id"
        self.mock_thread_manager = AsyncMock()

        # 1. Create the mock_sandbox instance
        self.mock_sandbox = MagicMock()
        self.mock_sandbox.process = MagicMock()
        self.mock_sandbox.process.create_session = MagicMock()
        self.mock_sandbox.process.execute_session_command = AsyncMock()
        self.mock_sandbox.process.get_session_command_logs = AsyncMock()
        self.mock_sandbox.process.delete_session = MagicMock()

        # 2. Instantiate the tool FIRST.
        self.tool = PythonTool(project_id=self.project_id, thread_manager=self.mock_thread_manager)

        # 3. Then, patch the _ensure_sandbox method directly on the instance.
        # This ensures that calls to self.tool._ensure_sandbox() use the mock.
        self.patcher_ensure_sandbox = patch.object(self.tool, '_ensure_sandbox', new_callable=AsyncMock)
        self.mock_ensure_sandbox_method = self.patcher_ensure_sandbox.start()
        self.mock_ensure_sandbox_method.return_value = self.mock_sandbox # Ensure it returns the configured mock_sandbox
        # Additionally, the tool's internal _sandbox attribute should be set by this mock's execution.
        # When self.tool._ensure_sandbox() is awaited, the original method is replaced by self.mock_ensure_sandbox_method.
        # The PythonTool's execute_python_code calls `await self._ensure_sandbox()`.
        # The original _ensure_sandbox would set self._sandbox.
        # Our mock_ensure_sandbox_method needs to ensure self.tool._sandbox is set to self.mock_sandbox
        # if other parts of the code access self.tool.sandbox (the property) which relies on self.tool._sandbox.
        # A simple way is that the mock not only returns self.mock_sandbox, but also sets it on the instance.
        async def side_effect_ensure_sandbox(*args, **kwargs):
            self.tool._sandbox = self.mock_sandbox # Set the internal attribute
            return self.mock_sandbox
        self.mock_ensure_sandbox_method.side_effect = side_effect_ensure_sandbox


    async def asyncTearDown(self):
        self.patcher_ensure_sandbox.stop()
        await asyncio.sleep(0)

    async def test_execute_python_code_success(self):
        code = "print('Hello from Python tool')"
        expected_output = "Hello from Python tool\n" 

        self.mock_sandbox.process.create_session.return_value = None 
        mock_exec_response = MockExecuteResponse(cmd_id="cmd_123", exit_code=0)
        self.mock_sandbox.process.execute_session_command.return_value = mock_exec_response
        self.mock_sandbox.process.get_session_command_logs.return_value = expected_output
        self.mock_sandbox.process.delete_session.return_value = None

        result = await self.tool.execute_python_code(code)

        self.assertTrue(result.success)
        self.assertEqual(result.output, expected_output)
        self.mock_ensure_sandbox_method.assert_called_once() # Corrected name
        self.mock_sandbox.process.create_session.assert_called_once()
        session_id_call_arg = self.mock_sandbox.process.create_session.call_args[0][0]
        
        self.mock_sandbox.process.execute_session_command.assert_called_once()
        execute_call_args = self.mock_sandbox.process.execute_session_command.call_args
        self.assertEqual(execute_call_args[1]['session_id'], session_id_call_arg)
        escaped_code_for_command = code.replace('"', '\\"')
        expected_python_command = 'python -c "{}"'.format(escaped_code_for_command)
        self.assertIn(expected_python_command, execute_call_args[1]['req'].command)

        self.mock_sandbox.process.get_session_command_logs.assert_called_once_with(
            session_id=session_id_call_arg,
            command_id="cmd_123"
        )
        self.mock_sandbox.process.delete_session.assert_called_once_with(session_id_call_arg)

    async def test_execute_python_code_runtime_error(self):
        code = "print(1/0)"
        error_output = "Traceback (most recent call last):\n  File \"<string>\", line 1, in <module>\nZeroDivisionError: division by zero\n"

        self.mock_sandbox.process.create_session.return_value = None
        mock_exec_response = MockExecuteResponse(cmd_id="cmd_456", exit_code=1)
        self.mock_sandbox.process.execute_session_command.return_value = mock_exec_response
        self.mock_sandbox.process.get_session_command_logs.return_value = error_output
        self.mock_sandbox.process.delete_session.return_value = None

        result = await self.tool.execute_python_code(code)

        self.assertFalse(result.success)
        self.assertIn("Error executing Python code (exit code 1)", result.output)
        self.assertIn(error_output, result.output)
        session_id_call_arg = self.mock_sandbox.process.create_session.call_args[0][0]
        self.mock_sandbox.process.delete_session.assert_called_once_with(session_id_call_arg)


    async def test_execute_python_code_syntax_error(self):
        code = "print('Hello'" 
        error_output = "  File \"<string>\", line 1\n    print('Hello'\n              ^\nSyntaxError: unexpected EOF while parsing\n"

        self.mock_sandbox.process.create_session.return_value = None
        mock_exec_response = MockExecuteResponse(cmd_id="cmd_789", exit_code=1)
        self.mock_sandbox.process.execute_session_command.return_value = mock_exec_response
        self.mock_sandbox.process.get_session_command_logs.return_value = error_output
        self.mock_sandbox.process.delete_session.return_value = None

        result = await self.tool.execute_python_code(code)

        self.assertFalse(result.success)
        self.assertIn("Error executing Python code (exit code 1)", result.output)
        self.assertIn(error_output, result.output)
        session_id_call_arg = self.mock_sandbox.process.create_session.call_args[0][0]
        self.mock_sandbox.process.delete_session.assert_called_once_with(session_id_call_arg)

    async def test_execute_multiline_python_code_success(self):
        code = "x = 10\ny = 20\nprint(x + y)"
        expected_output = "30\n"

        self.mock_sandbox.process.create_session.return_value = None
        mock_exec_response = MockExecuteResponse(cmd_id="cmd_abc", exit_code=0)
        self.mock_sandbox.process.execute_session_command.return_value = mock_exec_response
        self.mock_sandbox.process.get_session_command_logs.return_value = expected_output
        self.mock_sandbox.process.delete_session.return_value = None
        
        result = await self.tool.execute_python_code(code)

        self.assertTrue(result.success)
        self.assertEqual(result.output, expected_output)
        session_id_call_arg = self.mock_sandbox.process.create_session.call_args[0][0]
        escaped_code = code.replace('"', '\\"') 
        self.mock_sandbox.process.execute_session_command.assert_called_once()
        execute_call_args = self.mock_sandbox.process.execute_session_command.call_args
        self.assertEqual(execute_call_args[1]['session_id'], session_id_call_arg)
        expected_python_command_multiline = 'python -c "{}"'.format(escaped_code)
        self.assertIn(expected_python_command_multiline, execute_call_args[1]['req'].command)
        self.mock_sandbox.process.delete_session.assert_called_once_with(session_id_call_arg)

    async def test_execute_python_code_exception_in_tool(self):
        code = "print('test')"
        
        self.mock_sandbox.process.execute_session_command.side_effect = Exception("💥 Kaboom!")
        self.mock_sandbox.process.delete_session.return_value = None

        result = await self.tool.execute_python_code(code)

        self.assertFalse(result.success)
        self.assertIn("Failed to execute Python code: 💥 Kaboom!", result.output)
        session_id_call_arg = self.mock_sandbox.process.create_session.call_args[0][0]
        self.mock_sandbox.process.delete_session.assert_called_once_with(session_id_call_arg)

if __name__ == '__main__':
    # This is important: If you have module-level os.environ changes, 
    # you might need to ensure they are applied before unittest.main() discovers tests
    # or that tests are run in a way that each test file is a separate process if necessary.
    # For python -m unittest, this should generally be fine as imports happen once per file.
    unittest.main()
