from agentpress.tool import ToolResult, openapi_schema
from sandbox.tool_base import SandboxToolsBase # Import SandboxToolsBase
from agentpress.thread_manager import ThreadManager # Import ThreadManager
from uuid import uuid4

# Assuming Sandbox is the class for interacting with the sandbox (already handled by SandboxToolsBase)

class PythonTool(SandboxToolsBase): # Inherit from SandboxToolsBase
    def __init__(self, project_id: str, thread_manager: ThreadManager): # Modified constructor
        super().__init__(project_id, thread_manager)
        # self.sandbox is initialized in SandboxToolsBase

    @openapi_schema({
        "name": "execute_python_code",
        "description": "Executes Python code in a sandboxed environment.",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The Python code to execute."
                }
            },
            "required": ["code"]
        }
    })
    async def execute_python_code(self, code: str) -> ToolResult:
        """Executes Python code in a sandboxed environment.

        Args:
            code: The Python code to execute.

        Returns:
            A ToolResult object with the output of the executed code or an error message.
        """
        try:
            await self._ensure_sandbox() # Ensure sandbox is initialized

            # Create a unique session ID for this execution
            session_id = f"python_exec_{uuid4().hex[:8]}"
            try:
                self.sandbox.process.create_session(session_id)

                # Prepare the python command
                # Escape double quotes in the code string to avoid issues when wrapping it in double quotes for the shell command.
                escaped_code = code.replace('"', '\\"')
                python_command = f"python -c \"{escaped_code}\""

                # Execute the Python code in the session
                from sandbox.sandbox import SessionExecuteRequest # Import SessionExecuteRequest
                req = SessionExecuteRequest(
                    command=python_command,
                    var_async=False,  # Assuming synchronous execution is desired for Python scripts
                    cwd="/workspace"  # Default to workspace, can be parameterized if needed
                )
                
                response = await self.sandbox.process.execute_session_command( # Added await
                    session_id=session_id,
                    req=req,
                    timeout=300 # Set a reasonable timeout (e.g., 5 minutes)
                )

                # Get the logs/output
                # The actual method to get stdout/stderr might differ based on Daytona SDK
                # Assuming get_session_command_logs provides combined output or specific stdout/stderr
                logs = await self.sandbox.process.get_session_command_logs( # Added await
                    session_id=session_id,
                    command_id=response.cmd_id
                )
                
                # Clean up the session
                self.sandbox.process.delete_session(session_id)

                # Process the response
                # Assuming 'logs' contains stdout. If stderr is separate, adjust accordingly.
                # Also, Daytona's response.exit_code can be used to check for errors.
                if response.exit_code != 0:
                    # If there's an exit code, prefer logs as stderr
                    return self.fail_response(f"Error executing Python code (exit code {response.exit_code}): {logs}")
                return self.success_response(logs) # Assuming logs contain stdout

            except Exception as e:
                # Ensure session is cleaned up in case of an error during execution
                try:
                    self.sandbox.process.delete_session(session_id)
                except Exception as cleanup_e:
                    # Log cleanup error if necessary, but return the original execution error
                    print(f"Error cleaning up session {session_id}: {cleanup_e}")
                return self.fail_response(f"Failed to execute Python code: {str(e)}")
        except Exception as e: # Add except block for the outer try
            # This handles errors from _ensure_sandbox() or other unforeseen issues
            # before the inner try-except block is reached or after it successfully exits.
            return self.fail_response(f"An unexpected error occurred in PythonTool: {str(e)}")
