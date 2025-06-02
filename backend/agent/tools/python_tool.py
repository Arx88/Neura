from backend.agentpress.tool import openapi_schema, xml_schema
from backend.sandbox.tool_base import SandboxToolsBase
from backend.agentpress.thread_manager import ThreadManager
from uuid import uuid4
import logging # Added for logging

# Custom Exceptions
class PythonToolError(Exception):
    """Base exception for Python tool errors."""
    pass

class PythonTool(SandboxToolsBase):
    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)

    @xml_schema(
        tag_name="execute_python_code",
        mappings=[
            {"param_name": "code", "node_type": "attribute", "path": "."}
        ],
        example='''
        <!-- Executes the provided Python code in a sandboxed environment. -->
        <!-- The code should be self-contained. -->
        <!-- Output from stdout and stderr will be captured. -->
        <execute_python_code
            code="print('Hello from Python!')"
        />

        <!-- Example: Reading a file (ensure file exists in /workspace) -->
        <!--
        <execute_python_code
            code="with open('my_file.txt', 'r') as f: print(f.read())"
        />
        -->
        '''
    )
    @openapi_schema({
        "name": "execute_python_code",
        "description": "Executes Python code in a sandboxed environment. The code should be self-contained and rely only on the Python standard library or packages available in the sandbox environment. Output from stdout and stderr will be captured.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python code to execute. Ensure the code is complete and executable as is. For example, include all necessary imports."
                }
            },
            "required": ["code"]
        }
    })
    async def execute_python_code(self, code: str) -> dict:
        """Executes Python code in a sandboxed environment.

        Args:
            code: The Python code to execute.

        Returns:
            A dictionary containing the output of the executed code.
            Example: {"output": "Hello, World!\n", "exit_code": 0}
        Raises:
            ValueError: If the provided code is empty or invalid.
            PythonToolError: If an error occurs during code execution or sandbox interaction.
        """
        if not code or not isinstance(code, str):
            raise ValueError("Python code to execute must be a non-empty string.")

        session_id = f"python_exec_{uuid4().hex[:8]}"

        try:
            await self._ensure_sandbox() # Ensure sandbox is initialized

            # Create session for this execution
            # Assuming create_session is synchronous or an error if it's async and not awaited
            # Based on other tools, these sandbox.process calls seem to be synchronous with Daytona SDK
            # If they were async, they'd need `await`.
            self.sandbox.process.create_session(session_id)

            # Prepare the python command
            # Escape double quotes in the code string
            escaped_code = code.replace('"', '\\"')
            # Using -u for unbuffered output might be beneficial for streaming logs in future,
            # but for now, it ensures output is not held back.
            python_command = f"python -u -c \"{escaped_code}\""

            from backend.sandbox.sandbox import SessionExecuteRequest # Keep import local
            req = SessionExecuteRequest(
                command=python_command,
                var_async=False,
                cwd="/workspace"
            )

            # Assuming execute_session_command and get_session_command_logs are synchronous
            # If async, they require `await`. The `await` was present in original, so keeping it.
            response = await self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=req,
                timeout=300
            )

            logs_result = await self.sandbox.process.get_session_command_logs(
                session_id=session_id,
                command_id=response.cmd_id
            )

            # Adapt log extraction based on actual structure of logs_result
            # For Daytona, logs_result is DaytonaApiModelsLog. For local, it might be a dict.
            output_str = ""
            if hasattr(logs_result, 'stdout') and logs_result.stdout:
                output_str += logs_result.stdout
            if hasattr(logs_result, 'stderr') and logs_result.stderr:
                if output_str and logs_result.stdout: output_str += "\n--- STDERR ---\n" # Separator if both exist
                output_str += logs_result.stderr
            elif isinstance(logs_result, str): # Fallback if it's just a string (old behavior)
                 output_str = logs_result

            if response.exit_code != 0:
                error_message = f"Python code execution failed with exit code {response.exit_code}.\nOutput:\n{output_str}"
                logging.error(error_message) # Log the detailed error
                raise PythonToolError(error_message) # Raise specific error for orchestrator

            return {"output": output_str, "exit_code": response.exit_code}

        except ValueError: # Re-raise specific error
            raise
        except PythonToolError: # Re-raise specific error
            raise
        except Exception as e:
            logging.error(f"An unexpected error occurred in PythonTool while executing code: {str(e)}", exc_info=True)
            raise PythonToolError(f"An unexpected error occurred in PythonTool: {str(e)}") from e
        finally:
            # Ensure session is cleaned up
            try:
                if hasattr(self, 'sandbox') and self.sandbox and hasattr(self.sandbox, 'process'): # Check if sandbox and process are available
                    # Check if session was actually created before attempting to delete
                    # This requires knowing if create_session succeeded.
                    # A simple way is to assume if we have a session_id, it might exist.
                    # More robust: flag for session creation success.
                    # For now, just try deleting. If it fails, log it.
                    self.sandbox.process.delete_session(session_id)
                    logging.debug(f"Python execution session {session_id} cleaned up.")
            except Exception as cleanup_e:
                logging.error(f"Error cleaning up Python execution session {session_id}: {cleanup_e}", exc_info=True)
