from typing import Optional

from agentpress.thread_manager import ThreadManager
from agentpress.tool import Tool
from daytona_sdk import Sandbox
from sandbox.sandbox import get_or_start_sandbox
from utils.logger import logger
from utils.files_utils import clean_path
import json # Added for JSON parsing
import asyncio # Added for asyncio.to_thread
from typing import Dict, Any, Optional # Added for type hints
# from daytona_api_client.models import SessionExecuteRequest # If using daytona_sdk directly for types - keep commented for now
# from sandbox.sandbox import use_daytona # To check which sandbox type is active - keep commented for now


class SandboxToolsBase(Tool):
    """Base class for all sandbox tools that provides project-based sandbox access."""
    
    # Class variable to track if sandbox URLs have been printed
    _urls_printed = False
    
    def __init__(self, project_id: str, thread_manager: Optional[ThreadManager] = None):
        super().__init__()
        self.project_id = project_id
        self.thread_manager = thread_manager
        self.workspace_path = "/workspace"
        self._sandbox = None
        self._sandbox_id = None
        self._sandbox_pass = None

    async def _ensure_sandbox(self) -> Sandbox:
        """Ensure we have a valid sandbox instance, retrieving it from the project if needed."""
        if self._sandbox is None:
            try:
                # Get database client
                client = await self.thread_manager.db.client
                
                # Get project data
                project = await client.table('projects').select('*').eq('project_id', self.project_id).execute()
                if not project.data or len(project.data) == 0:
                    raise ValueError(f"Project {self.project_id} not found")
                
                project_data = project.data[0]
                sandbox_info = project_data.get('sandbox', {})
                
                if not sandbox_info.get('id'):
                    raise ValueError(f"No sandbox found for project {self.project_id}")
                
                # Store sandbox info
                self._sandbox_id = sandbox_info['id']
                self._sandbox_pass = sandbox_info.get('pass')
                
                # Get or start the sandbox
                self._sandbox = await get_or_start_sandbox(self._sandbox_id)
                
                # # Log URLs if not already printed
                # if not SandboxToolsBase._urls_printed:
                #     vnc_link = self._sandbox.get_preview_link(6080)
                #     website_link = self._sandbox.get_preview_link(8080)
                    
                #     vnc_url = vnc_link.url if hasattr(vnc_link, 'url') else str(vnc_link)
                #     website_url = website_link.url if hasattr(website_link, 'url') else str(website_link)
                    
                #     print("\033[95m***")
                #     print(f"VNC URL: {vnc_url}")
                #     print(f"Website URL: {website_url}")
                #     print("***\033[0m")
                #     SandboxToolsBase._urls_printed = True
                
            except Exception as e:
                logger.error(f"Error retrieving sandbox for project {self.project_id}: {str(e)}", exc_info=True)
                raise e
        
        return self._sandbox

    @property
    def sandbox(self) -> Sandbox:
        """Get the sandbox instance, ensuring it exists."""
        if self._sandbox is None:
            raise RuntimeError("Sandbox not initialized. Call _ensure_sandbox() first.")
        return self._sandbox

    @property
    def sandbox_id(self) -> str:
        """Get the sandbox ID, ensuring it exists."""
        if self._sandbox_id is None:
            raise RuntimeError("Sandbox ID not initialized. Call _ensure_sandbox() first.")
        return self._sandbox_id

    def clean_path(self, path: str) -> str:
        """Clean and normalize a path to be relative to /workspace."""
        cleaned_path = clean_path(path, self.workspace_path)
        logger.debug(f"Cleaned path: {path} -> {cleaned_path}")
        return cleaned_path

    async def _execute_in_sandbox(
        self,
        command: str,
        session_id: Optional[str] = None,
        is_blocking: bool = True, # Default to blocking for simpler direct calls
        timeout: int = 60,
        cwd: Optional[str] = None, # Working directory within the sandbox
        expected_content_type: Optional[str] = None # e.g., "json"
    ) -> Dict[str, Any]:
        await self._ensure_sandbox() # Ensures self.sandbox is available

        # Determine working directory
        effective_cwd = cwd if cwd else self.workspace_path

        logger.debug(f"Executing in sandbox (session: {session_id}, blocking: {is_blocking}, cwd: {effective_cwd}): {command}")

        try:
            if session_id:
                # Using session-based execution
                from daytona_sdk import SessionExecuteRequest # Local import for now
                exec_req = SessionExecuteRequest(
                    command=command,
                    var_async=not is_blocking, # var_async is true for non-blocking
                    cwd=effective_cwd
                )
                # Assuming self.sandbox.process.execute_session_command exists and is async
                response = await self.sandbox.process.execute_session_command(
                    session_id=session_id,
                    req=exec_req,
                    # Timeout for blocking calls; Daytona SDK might handle timeout differently for async
                    # For now, only pass timeout if blocking, assuming SDK handles it or it's for the await.
                    timeout=timeout if is_blocking else 300 # Default timeout for async session commands if not specified
                )
                # Fetch logs for session command
                logs = await self.sandbox.process.get_session_command_logs(session_id, response.cmd_id)

                stdout = getattr(logs, 'stdout', '') if hasattr(logs, 'stdout') else logs.get('stdout', '') if isinstance(logs, dict) else ''
                stderr = getattr(logs, 'stderr', '') if hasattr(logs, 'stderr') else logs.get('stderr', '') if isinstance(logs, dict) else ''
                output = stdout
                if stderr: # Append stderr to output if it exists
                    output += ("\nSTDERR:\n" + stderr) if output else stderr


                result_data = {
                    "output": output,
                    "exit_code": response.exit_code,
                    "cmd_id": response.cmd_id
                }

            else:
                # Using direct exec (non-session based)
                # The `exec` method in Daytona SDK's `ProcessAPI` is synchronous.
                # Use asyncio.to_thread to run it in a separate thread.
                response = await asyncio.to_thread(
                    self.sandbox.process.exec, # This is the synchronous method
                    command,
                    timeout=timeout, # Pass timeout here
                    cwd=effective_cwd
                )
                # The response from `exec` in Daytona SDK is `ExecResponse` with `result`, `exit_code`.
                # `result` contains combined stdout/stderr for `exec`.
                result_data = {
                    "output": response.result, # `result` here is the combined stdout/stderr
                    "exit_code": response.exit_code
                }

            if result_data["exit_code"] != 0:
                logger.warning(f"Sandbox command failed (exit code {result_data['exit_code']}) for command: {command}. Output: {str(result_data.get('output',''))[:500]}")
                # Don't raise exception here, let caller decide based on exit code.

            if expected_content_type == "json" and result_data["exit_code"] == 0:
                try:
                    # Ensure output is not None or empty before trying to parse
                    if result_data["output"] and result_data["output"].strip():
                        parsed_json = json.loads(result_data["output"])
                        result_data["parsed_json"] = parsed_json
                    else:
                        logger.warning(f"Sandbox command output was empty, cannot parse as JSON: {command}")
                        result_data["json_parse_error"] = "Output was empty"
                        # Set a default empty JSON object or list depending on expectation if needed
                        # For now, just note the error. The caller will see 'parsed_json' is missing.
                except json.JSONDecodeError as e_json:
                    logger.warning(f"Failed to parse expected JSON output from sandbox command: {command}. Error: {e_json}. Output: {str(result_data.get('output',''))[:500]}")
                    result_data["json_parse_error"] = str(e_json)

            return result_data

        except Exception as e:
            logger.error(f"Error calling sandbox process for command '{command}': {e}", exc_info=True)
            # Re-raise or return a structured error
            raise RuntimeError(f"Sandbox execution failed for command '{command}': {str(e)}") from e