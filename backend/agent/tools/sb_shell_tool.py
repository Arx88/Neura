from typing import Optional, Dict, Any
import time
from uuid import uuid4
from agentpress.tool import openapi_schema, xml_schema # ToolResult removed
from sandbox.tool_base import SandboxToolsBase
from agentpress.thread_manager import ThreadManager
import logging # Added for logging
# Custom Exceptions
class ShellToolError(Exception):
    """Base exception for shell tool errors."""
    pass

class SandboxShellTool(SandboxToolsBase):
    """Tool for executing tasks in a Daytona sandbox with browser-use capabilities. 
    Uses sessions for maintaining state between commands and provides comprehensive process management."""

    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self._sessions: Dict[str, str] = {}  # Maps session names to session IDs
        self.workspace_path = "/workspace"  # Ensure we're always operating in /workspace

    async def _ensure_session(self, session_name: str = "default") -> str:
        """Ensure a session exists and return its ID."""
        if session_name not in self._sessions:
            session_id = str(uuid4())
            try:
                await self._ensure_sandbox()  # Ensure sandbox is initialized
                self.sandbox.process.create_session(session_id)
                self._sessions[session_name] = session_id
            except Exception as e:
                raise RuntimeError(f"Failed to create session: {str(e)}")
        return self._sessions[session_name]

    async def _cleanup_session(self, session_name: str):
        """Clean up a session if it exists."""
        if session_name in self._sessions:
            try:
                await self._ensure_sandbox()  # Ensure sandbox is initialized
                self.sandbox.process.delete_session(self._sessions[session_name])
                del self._sessions[session_name]
            except Exception as e:
                print(f"Warning: Failed to cleanup session {session_name}: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "Execute a shell command in the workspace directory. IMPORTANT: Commands are non-blocking by default and run in a tmux session. This is ideal for long-running operations like starting servers or build processes. Uses sessions to maintain state between commands. This tool is essential for running CLI tools, installing packages, and managing system operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute. Use this for running CLI tools, installing packages, or system operations. Commands can be chained using &&, ||, and | operators."
                    },
                    "folder": {
                        "type": "string",
                        "description": "Optional relative path to a subdirectory of /workspace where the command should be executed. Example: 'data/pdfs'"
                    },
                    "session_name": {
                        "type": "string",
                        "description": "Optional name of the tmux session to use. Use named sessions for related commands that need to maintain state. Defaults to a random session name.",
                    },
                    "blocking": {
                        "type": "boolean",
                        "description": "Whether to wait for the command to complete. Defaults to false for non-blocking execution.",
                        "default": False
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Optional timeout in seconds for blocking commands. Defaults to 60. Ignored for non-blocking commands.",
                        "default": 60
                    }
                },
                "required": ["command"]
            }
        }
    })
    @xml_schema(
        tag_name="execute-command",
        mappings=[
            {"param_name": "command", "node_type": "content", "path": "."},
            {"param_name": "folder", "node_type": "attribute", "path": ".", "required": False},
            {"param_name": "session_name", "node_type": "attribute", "path": ".", "required": False},
            {"param_name": "blocking", "node_type": "attribute", "path": ".", "required": False},
            {"param_name": "timeout", "node_type": "attribute", "path": ".", "required": False}
        ],
        example='''
        <!-- NON-BLOCKING COMMANDS (Default) -->
        <!-- Example 1: Start a development server -->
        <execute-command session_name="dev_server">
        npm run dev
        </execute-command>

        <!-- Example 2: Running in Specific Directory -->
        <execute-command session_name="build_process" folder="frontend">
        npm run build
        </execute-command>

        <!-- BLOCKING COMMANDS (Wait for completion) -->
        <!-- Example 3: Install dependencies and wait for completion -->
        <execute-command blocking="true" timeout="300">
        npm install
        </execute-command>

        <!-- Example 4: Complex Command with Environment Variables -->
        <execute-command blocking="true">
        export NODE_ENV=production && npm run build
        </execute-command>
        '''
    )
    async def execute_command(
        self, 
        command: str, 
        folder: Optional[str] = None,
        session_name: Optional[str] = None,
        blocking: bool = False,
        timeout: int = 60
    ) -> Dict[str, Any]: # Return dict on success
        session_to_cleanup_on_error = None # Keep track of session if created
        try:
            if not command or not isinstance(command, str):
                raise ValueError("A valid command string is required.")

            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            # Set up working directory
            cwd = self.workspace_path
            if folder:
                folder = folder.strip('/')
                cwd = f"{self.workspace_path}/{folder}"
            
            # Generate a session name if not provided
            if not session_name:
                session_name = f"session_{str(uuid4())[:8]}"
            session_to_cleanup_on_error = session_name # Mark for potential cleanup

            # Check if tmux session already exists
            # Assuming _execute_raw_command raises on significant sandbox/process error
            check_session_result = await self._execute_raw_command(f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'")
            session_exists = "not_exists" not in check_session_result.get("output", "")
            
            if not session_exists:
                # Create a new tmux session
                await self._execute_raw_command(f"tmux new-session -d -s {session_name}")
                
            # Ensure we're in the correct directory and send command to tmux
            full_command = f"cd {cwd} && {command}"
            # More robust escaping for commands sent to tmux send-keys
            # This simple replacement might not be enough for all complex commands.
            # Consider a more thorough shell escaping function if issues arise.
            escaped_inner_command = full_command.replace('\\', '\\\\').replace('"', '\\"')
            tmux_send_keys_command = f'tmux send-keys -t {session_name} "{escaped_inner_command}" Enter'
            
            await self._execute_raw_command(tmux_send_keys_command)
            
            if blocking:
                # For blocking execution, wait and capture output
                start_time = time.time()
                final_output = ""
                while (time.time() - start_time) < timeout:
                    await asyncio.sleep(2) # Use asyncio.sleep
                    
                    check_result = await self._execute_raw_command(f"tmux has-session -t {session_name} 2>/dev/null || echo 'ended'")
                    if "ended" in check_result.get("output", ""):
                        # Session ended, try to capture final output one last time if possible
                        # This might capture the state just before exit.
                        output_result_final_attempt = await self._execute_raw_command(f"tmux capture-pane -t {session_name} -p -S - -E - || true")
                        final_output = output_result_final_attempt.get("output", "Session ended, final output capture might be incomplete.")
                        break
                        
                    output_result = await self._execute_raw_command(f"tmux capture-pane -t {session_name} -p -S - -E -")
                    current_output = output_result.get("output", "")
                    final_output = current_output # Keep updating final_output
                    
                    last_lines = current_output.strip().split('\n')[-3:] # .strip() to handle trailing newlines
                    # More robust completion check: look for shell prompt patterns
                    # This is heuristic and might need adjustment based on typical shell prompts in the sandbox.
                    shell_prompt_patterns = [f"{cwd} $", f"{cwd} #", f"{os.path.basename(cwd)} $", f"{os.path.basename(cwd)} #"] # Common prompts
                    if any(prompt in line for prompt in shell_prompt_patterns for line in last_lines):
                        # Check if the command itself is still in the line, indicating it hasn't returned to prompt yet
                        if not any(command.split()[-1] in line for line in last_lines if any(prompt in line for prompt in shell_prompt_patterns)):
                             logging.info(f"Command in session {session_name} likely completed based on prompt detection.")
                             break
                else: # Loop completed due to timeout
                    logging.warning(f"Command '{command}' in session '{session_name}' timed out after {timeout} seconds.")
                
                # Kill the session after capture (if it still exists)
                check_result_before_kill = await self._execute_raw_command(f"tmux has-session -t {session_name} 2>/dev/null || echo 'ended'")
                if "ended" not in check_result_before_kill.get("output", ""):
                    await self._execute_raw_command(f"tmux kill-session -t {session_name}")
                session_to_cleanup_on_error = None # Session handled

                return {
                    "output": final_output,
                    "session_name": session_name,
                    "cwd": cwd,
                    "completed": True
                }
            else:
                # For non-blocking, just return immediately
                session_to_cleanup_on_error = None # Command sent, user responsible for session
                return {
                    "session_name": session_name,
                    "cwd": cwd,
                    "message": f"Command sent to tmux session '{session_name}'. Use check_command_output to view results.",
                    "completed": False
                }
        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error executing command '{command}': {str(e)}", exc_info=True)
            if session_to_cleanup_on_error: # Attempt to clean up session if one was potentially started by this call
                try:
                    logging.info(f"Attempting to cleanup session {session_to_cleanup_on_error} due to error.")
                    await self._execute_raw_command(f"tmux kill-session -t {session_to_cleanup_on_error} 2>/dev/null || true")
                except Exception as e_cleanup:
                    logging.error(f"Failed to cleanup session {session_to_cleanup_on_error} during error handling: {str(e_cleanup)}")
            raise ShellToolError(f"Error executing command: {str(e)}") from e

    async def _execute_raw_command(self, command: str) -> Dict[str, Any]:
        """Execute a raw command directly in the sandbox."""
        # Ensure sandbox is up.
        await self._ensure_sandbox()
        # Ensure a utility session exists for these raw tmux/utility commands.
        # This session is managed by _ensure_session and _cleanup_session.
        utility_session_id = await self._ensure_session(session_name="kortix_raw_utility_session")

        # Use the new _execute_in_sandbox method
        # These raw commands are typically short-lived and blocking.
        sandbox_result = await self._execute_in_sandbox(
            command=command,
            session_id=utility_session_id,
            is_blocking=True, # Raw commands are generally expected to complete quickly
            timeout=60,       # Default timeout, can be adjusted if specific raw commands need more
            cwd=self.workspace_path # Default to workspace path for these utility commands
        )
        
        # _execute_in_sandbox already returns a dict with "output" and "exit_code"
        # "output" from _execute_in_sandbox includes combined stdout and stderr.
        return {
            "output": sandbox_result.get("output", ""),
            "exit_code": sandbox_result.get("exit_code", -1) # Provide a default exit code if missing
        }

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "check_command_output",
            "description": "Check the output of a previously executed command in a tmux session. Use this to monitor the progress or results of non-blocking commands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "The name of the tmux session to check."
                    },
                    "kill_session": {
                        "type": "boolean",
                        "description": "Whether to terminate the tmux session after checking. Set to true when you're done with the command.",
                        "default": False
                    }
                },
                "required": ["session_name"]
            }
        }
    })
    @xml_schema(
        tag_name="check-command-output",
        mappings=[
            {"param_name": "session_name", "node_type": "attribute", "path": ".", "required": True},
            {"param_name": "kill_session", "node_type": "attribute", "path": ".", "required": False}
        ],
        example='''
        <!-- Example 1: Check output without killing session -->
        <check-command-output session_name="dev_server"></check-command-output>
        
        <!-- Example 2: Check final output and kill session -->
        <check-command-output session_name="build_process" kill_session="true"></check-command-output>
        '''
    )
    async def check_command_output(
        self,
        session_name: str,
        kill_session: bool = False
    ) -> Dict[str, Any]: # Return dict on success
        try:
            if not session_name or not isinstance(session_name, str):
                raise ValueError("A valid session name is required.")

            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            # Check if session exists
            check_result = await self._execute_raw_command(f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'")
            if "not_exists" in check_result.get("output", ""):
                raise ShellToolError(f"Tmux session '{session_name}' does not exist.")
            
            # Get output from tmux pane
            output_result = await self._execute_raw_command(f"tmux capture-pane -t {session_name} -p -S - -E -")
            output = output_result.get("output", "")
            
            termination_status = "Session still running."
            if kill_session:
                kill_result = await self._execute_raw_command(f"tmux kill-session -t {session_name} 2>/dev/null || echo 'kill_failed'")
                if "kill_failed" in kill_result.get("output", "") and kill_result.get("exit_code") != 0 :
                    # This case means `tmux kill-session` failed, which is unusual if `has-session` passed.
                    # It might mean the session ended just before kill command.
                    logging.warning(f"Attempted to kill session '{session_name}', but kill command indicated failure or session already gone. Output: {kill_result.get('output')}")
                    # Check again if it truly exists
                    final_check = await self._execute_raw_command(f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'")
                    if "not_exists" in final_check.get("output", ""):
                         termination_status = "Session was likely already terminated or ended."
                    else:
                         termination_status = "Session termination command failed, session might still be running."
                else:
                    termination_status = "Session terminated successfully."

            return {
                "output": output,
                "session_name": session_name,
                "status": termination_status
            }
        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error checking command output for session '{session_name}': {str(e)}", exc_info=True)
            raise ShellToolError(f"Error checking command output: {str(e)}") from e

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "terminate_command",
            "description": "Terminate a running command by killing its tmux session.",
            "parameters": {
                "type": "object",
                "properties": {
                    "session_name": {
                        "type": "string",
                        "description": "The name of the tmux session to terminate."
                    }
                },
                "required": ["session_name"]
            }
        }
    })
    @xml_schema(
        tag_name="terminate-command",
        mappings=[
            {"param_name": "session_name", "node_type": "attribute", "path": ".", "required": True}
        ],
        example='''
        <!-- Example: Terminate a running server -->
        <terminate-command session_name="dev_server"></terminate-command>
        '''
    )
    async def terminate_command(
        self,
        session_name: str
    ) -> Dict[str, Any]: # Return dict on success
        try:
            if not session_name or not isinstance(session_name, str):
                raise ValueError("A valid session name is required.")

            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            # Check if session exists
            check_result = await self._execute_raw_command(f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'")
            if "not_exists" in check_result.get("output", ""):
                # Consider if this should be an error or a "no-op success"
                # For consistency, let's make it an error if user tries to terminate non-existent session.
                raise ShellToolError(f"Tmux session '{session_name}' does not exist, cannot terminate.")
            
            # Kill the session
            kill_command_result = await self._execute_raw_command(f"tmux kill-session -t {session_name} 2>/dev/null || echo 'kill_failed'")
            
            if kill_command_result.get("exit_code") == 0 and "kill_failed" not in kill_command_result.get("output",""):
                return {
                    "message": f"Tmux session '{session_name}' terminated successfully."
                }
            else:
                # This means `tmux kill-session` command itself failed.
                # This is unusual if `has-session` passed. Session might have ended right before.
                # Check again to provide a more accurate message.
                final_check = await self._execute_raw_command(f"tmux has-session -t {session_name} 2>/dev/null || echo 'not_exists'")
                if "not_exists" in final_check.get("output", ""):
                    return { "message": f"Tmux session '{session_name}' was already not running or ended during termination."}
                else:
                    # If it still exists, then kill-session truly failed for some reason.
                    raise ShellToolError(f"Failed to terminate tmux session '{session_name}'. Command output: {kill_command_result.get('output')}")

        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error terminating command in session '{session_name}': {str(e)}", exc_info=True)
            raise ShellToolError(f"Error terminating command: {str(e)}") from e

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "list_commands",
            "description": "List all running tmux sessions and their status.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    })
    @xml_schema(
        tag_name="list-commands",
        mappings=[],
        example='''
        <!-- Example: List all running commands -->
        <list-commands></list-commands>
        '''
    )
    async def list_commands(self) -> Dict[str, Any]: # Return dict on success
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            # List all tmux sessions
            result = await self._execute_raw_command("tmux list-sessions -F '#S' 2>/dev/null || echo 'LIST_SESSIONS_FAILED_OR_EMPTY'") # -F for format
            output = result.get("output", "")
            
            sessions = []
            if "LIST_SESSIONS_FAILED_OR_EMPTY" in output or not output.strip():
                # This could mean either the command failed OR there are no sessions.
                # If exit_code is 0, it means no sessions. Otherwise, command failed.
                if result.get("exit_code") == 0 and "LIST_SESSIONS_FAILED_OR_EMPTY" in output : # tmux list-sessions exits 0 if no sessions
                     message = "No active tmux sessions found."
                elif result.get("exit_code") != 0 : # Error running list-sessions
                     raise ShellToolError(f"Failed to list tmux sessions. Command output: {output}")
                else: # No sessions found, command was successful
                     message = "No active tmux sessions found."

            else:
                # Parse session list. Each session name is on a new line due to -F '#S'.
                sessions = [line.strip() for line in output.split('\n') if line.strip()]
                message = f"Found {len(sessions)} active sessions."
            
            return {
                "message": message,
                "sessions": sessions
            }
        except Exception as e:
            logging.error(f"Error listing commands: {str(e)}", exc_info=True)
            raise ShellToolError(f"Error listing commands: {str(e)}") from e

    async def cleanup(self):
        """Clean up all kortix_raw_utility_session and attempt to kill tmux server."""
        # Specifically clean up the utility session used by _execute_raw_command
        await self._cleanup_session("kortix_raw_utility_session")
        
        # Also attempt to clean up any remaining tmux sessions by killing the server
        # This is a more aggressive cleanup for the end of the tool's lifecycle.
        try:
            await self._ensure_sandbox() # Ensure sandbox is available for this final command
            # The `|| true` ensures the command doesn't fail if the server isn't running.
            kill_server_result = await self._execute_raw_command("tmux kill-server 2>/dev/null || true")
            logging.info(f"Attempted tmux kill-server during cleanup. Output: {kill_server_result.get('output')}, Exit Code: {kill_server_result.get('exit_code')}")
        except Exception as e_cleanup_tmux:
            # Log if even this attempt fails, but don't let it break cleanup.
            logging.error(f"Error during tmux kill-server in cleanup: {str(e_cleanup_tmux)}")