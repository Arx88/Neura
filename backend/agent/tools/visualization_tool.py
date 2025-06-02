from agentpress.tool import openapi_schema # ToolResult removed
from sandbox.tool_base import SandboxToolsBase
from agentpress.thread_manager import ThreadManager
from uuid import uuid4
from sandbox.sandbox import SessionExecuteRequest
import logging # Added for logging

# Custom Exceptions
class VisualizationToolError(Exception):
    """Base exception for visualization tool errors."""
    pass

class DataVisualizationTool(SandboxToolsBase):
    """Tool for creating data visualizations in the sandbox."""

    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self.workspace_path = "/workspace"
        self.visualizations_path = f"{self.workspace_path}/visualizations"

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "create_bar_chart",
            "description": "Create a bar chart visualization from data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Title of the chart."
                    },
                    "x_label": {
                        "type": "string",
                        "description": "Label for the x-axis."
                    },
                    "y_label": {
                        "type": "string",
                        "description": "Label for the y-axis."
                    },
                    "categories": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Categories for the x-axis."
                    },
                    "values": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Values for the y-axis."
                    },
                    "output_file": {
                        "type": "string",
                        "description": "Name of the output file (without extension)."
                    }
                },
                "required": ["title", "categories", "values", "output_file"]
            }
        }
    })
    async def create_bar_chart(
        self,
        title: str,
        categories: list,
        values: list,
        output_file: str,
        x_label: str = "",
        y_label: str = ""
    ) -> dict:
        if not all([title, categories, values, output_file]):
            raise ValueError("title, categories, values, and output_file are required parameters.")
        if not isinstance(categories, list) or not isinstance(values, list):
            raise ValueError("categories and values must be lists.")
        if len(categories) != len(values):
            raise ValueError("Length of categories and values must be the same.")
        if not output_file.isalnum() or '_' in output_file or '-' in output_file: # Basic check for safe filename
             # More robust sanitization might be needed depending on how output_file is used.
             pass # Allow for now, but consider stricter validation or sanitization if it's part of a path directly.

        session_id = f"viz_create_{uuid4().hex[:8]}"
        script_path = f"{self.workspace_path}/temp_viz_script_{uuid4().hex[:8]}.py"

        try:
            await self._ensure_sandbox()
            self.sandbox.process.create_session(session_id)
            
            mkdir_req = SessionExecuteRequest(command=f"mkdir -p {self.visualizations_path}", var_async=False, cwd=self.workspace_path)
            await self.sandbox.process.execute_session_command(session_id=session_id, req=mkdir_req)
            
            # Ensure string representations in the script are properly quoted for Python syntax
            # For example, category strings should be like ['cat1', 'cat2']
            py_categories = f"[{', '.join(repr(str(c)) for c in categories)}]"
            py_values = f"[{', '.join(repr(v) for v in values)}]" # Assuming values are numbers, repr is fine.

            script_content = f'''
import matplotlib
matplotlib.use('Agg') # Use non-interactive backend
import matplotlib.pyplot as plt
import numpy as np

# Data
categories = {py_categories}
values = {py_values}

# Create the bar chart
plt.figure(figsize=(10, 6))
plt.bar(categories, values, color='skyblue')
plt.title({repr(title)})
plt.xlabel({repr(x_label)})
plt.ylabel({repr(y_label)})
plt.xticks(rotation=45, ha='right')
plt.tight_layout()

# Save the figure
output_image_path = "{self.visualizations_path}/{output_file}.png"
plt.savefig(output_image_path)
print(f"Bar chart saved to {{output_image_path}}")
'''
            
            escaped_script_content = script_content.replace("'", "'\\''") # Basic shell escape
            write_script_req = SessionExecuteRequest(command=f"cat > {script_path} << 'EOL'\n{escaped_script_content}\nEOL", var_async=False, cwd=self.workspace_path)
            await self.sandbox.process.execute_session_command(session_id=session_id, req=write_script_req)
            
            exec_req = SessionExecuteRequest(command=f"python {script_path}", var_async=False, cwd=self.workspace_path)
            response = await self.sandbox.process.execute_session_command(session_id=session_id, req=exec_req, timeout=120) # Increased timeout
            
            logs_result = await self.sandbox.process.get_session_command_logs(session_id=session_id, command_id=response.cmd_id)
            # Adapt log extraction
            log_output = ""
            if hasattr(logs_result, 'stdout') and logs_result.stdout: log_output += logs_result.stdout
            if hasattr(logs_result, 'stderr') and logs_result.stderr:
                 if log_output and logs_result.stdout: log_output += "\n--- STDERR ---\n"
                 log_output += logs_result.stderr
            elif isinstance(logs_result, str): log_output = logs_result

            if response.exit_code != 0:
                error_message = f"Failed to create bar chart. Exit code: {response.exit_code}. Logs: {log_output}"
                logging.error(error_message)
                raise VisualizationToolError(error_message)
            
            final_output_file_path = f"{self.visualizations_path}/{output_file}.png"
            return {
                "output_file": final_output_file_path,
                "message": f"Bar chart created successfully: {final_output_file_path}.",
                "logs": log_output.strip()
            }
        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error creating bar chart '{title}': {str(e)}", exc_info=True)
            raise VisualizationToolError(f"Error creating bar chart: {str(e)}") from e
        finally:
            try:
                if self.sandbox and self.sandbox.process:
                    # Clean up the script file first, then the session
                    if script_path: # Check if script_path was defined
                        cleanup_req = SessionExecuteRequest(command=f"rm -f {script_path}", var_async=False, cwd=self.workspace_path)
                        # Use a new session or an existing utility session for cleanup if the original session might be compromised
                        # For simplicity, using the same session if it's still expected to be valid.
                        try:
                             await self.sandbox.process.execute_session_command(session_id=session_id, req=cleanup_req)
                        except Exception as e_cleanup_script:
                             logging.warning(f"Failed to cleanup script {script_path} in session {session_id}: {e_cleanup_script}")
                    await self.sandbox.process.delete_session(session_id)
                    logging.debug(f"Visualization session {session_id} and script {script_path} cleaned up.")
            except Exception as cleanup_e:
                logging.error(f"Failed to delete session {session_id} or script {script_path} during cleanup: {cleanup_e}", exc_info=True)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "view_visualization",
            "description": "View a generated visualization.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Path to the visualization image file."
                    }
                },
                "required": ["image_path"]
            }
        }
    })
    async def view_visualization(
        self,
        image_path: str
    ) -> dict:
        if not image_path or not isinstance(image_path, str):
            raise ValueError("A valid image_path is required.")

        session_id = f"viz_view_{uuid4().hex[:8]}"
        try:
            await self._ensure_sandbox()
            
            cleaned_image_path = self.clean_path(image_path)
            if not cleaned_image_path.startswith(self.workspace_path):
                # Ensure it's treated as relative to workspace if not already absolute within it
                cleaned_image_path = f"{self.workspace_path}/{cleaned_image_path.lstrip('/')}"

            self.sandbox.process.create_session(session_id)
            
            check_req = SessionExecuteRequest(
                command=f"test -f \"{cleaned_image_path}\" && echo 'exists' || echo 'not_exists'", # Quote path
                var_async=False,
                cwd=self.workspace_path 
            )
            response = await self.sandbox.process.execute_session_command(session_id=session_id, req=check_req)
            
            logs_result = await self.sandbox.process.get_session_command_logs(session_id=session_id, command_id=response.cmd_id)
            log_output = logs_result.stdout if hasattr(logs_result, 'stdout') else (logs_result if isinstance(logs_result, str) else "")
            
            if "not_exists" in log_output:
                raise FileNotFoundError(f"Visualization file not found: {cleaned_image_path}.")
            
            return {
                "image_path": cleaned_image_path,
                "message": "Visualization file exists and is ready for display by compatible renderers."
            }
        except (ValueError, FileNotFoundError):
            raise
        except Exception as e:
            logging.error(f"Error viewing visualization '{image_path}': {str(e)}", exc_info=True)
            raise VisualizationToolError(f"Error viewing visualization: {str(e)}") from e
        finally:
            try:
                if self.sandbox and self.sandbox.process: # Check if sandbox and process objects exist
                     self.sandbox.process.delete_session(session_id)
                     logging.debug(f"Visualization view session {session_id} cleaned up.")
            except Exception as cleanup_e:
                logging.error(f"Failed to delete session {session_id} during view_visualization cleanup: {cleanup_e}", exc_info=True)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "display_visualization_in_browser",
            "description": "Display a visualization in the browser by generating an HTML file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Path to the visualization image file within the sandbox."
                    }
                },
                "required": ["image_path"]
            }
        }
    })
    async def display_visualization_in_browser(
        self,
        image_path: str
    ) -> dict:
        if not image_path or not isinstance(image_path, str):
            raise ValueError("A valid image_path is required.")

        session_id = f"viz_display_{uuid4().hex[:8]}"
        try:
            await self._ensure_sandbox()

            cleaned_image_path = self.clean_path(image_path)
            if not cleaned_image_path.startswith(self.workspace_path):
                 cleaned_image_path = f"{self.workspace_path}/{cleaned_image_path.lstrip('/')}"
            
            self.sandbox.process.create_session(session_id)

            mime_type = "image/png" # Default
            if cleaned_image_path.lower().endswith((".jpg", ".jpeg")): mime_type = "image/jpeg"
            elif cleaned_image_path.lower().endswith(".gif"): mime_type = "image/gif"
            elif cleaned_image_path.lower().endswith(".svg"): mime_type = "image/svg+xml"

            # Quote path for shell command
            read_image_cmd = f"cat \"{cleaned_image_path}\" | base64 --wrap=0"
            exec_read_req = SessionExecuteRequest(command=read_image_cmd, var_async=False, cwd=self.workspace_path)
            response_read = await self.sandbox.process.execute_session_command(session_id=session_id, req=exec_read_req, timeout=60)

            base64_image_data_result = await self.sandbox.process.get_session_command_logs(session_id=session_id, command_id=response_read.cmd_id)
            base64_image_data = base64_image_data_result.stdout if hasattr(base64_image_data_result, 'stdout') else (base64_image_data_result if isinstance(base64_image_data_result, str) else "")


            if response_read.exit_code != 0 or not base64_image_data.strip():
                error_logs = base64_image_data_result.stderr if hasattr(base64_image_data_result, 'stderr') else base64_image_data
                raise VisualizationToolError(f"Failed to read and encode image file '{cleaned_image_path}'. Exit: {response_read.exit_code}. Logs: {error_logs}")
            
            base64_image_data_cleaned = base64_image_data.strip()

            html_content_to_write = f'''
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Visualization: {image_path.split("/")[-1]}</title><style>body {{ margin: 0; padding: 20px; display: flex; flex-direction: column; justify-content: center; align-items: center; min-height: 95vh; background-color: #f0f0f0; font-family: Arial, sans-serif; text-align: center; }} h1 {{ margin-bottom: 20px; }} img {{ max-width: 95%; max-height: 85vh; object-fit: contain; border: 1px solid #ccc; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}</style></head>
<body><h1>Displaying: {image_path.split("/")[-1]}</h1><img src="data:{mime_type};base64,{base64_image_data_cleaned}" alt="Generated Visualization: {image_path.split("/")[-1]}"></body></html>'''

            html_filename = f"viz_display_{uuid4().hex[:8]}.html"
            html_full_path = f"{self.visualizations_path}/{html_filename}" # Use full path for clarity
            
            escaped_html_to_write = html_content_to_write.replace("'", "'\\''")
            # Ensure visualizations_path exists before writing
            write_html_cmd = f"mkdir -p \"{self.visualizations_path}\" && cat > \"{html_full_path}\" << 'EOL'\n{escaped_html_to_write}\nEOL"
            write_html_req = SessionExecuteRequest(command=write_html_cmd, var_async=False, cwd=self.workspace_path)
            response_write = await self.sandbox.process.execute_session_command(session_id=session_id, req=write_html_req)

            if response_write.exit_code != 0:
                logs_write_result = await self.sandbox.process.get_session_command_logs(session_id, response_write.cmd_id)
                logs_write_output = logs_write_result.stderr if hasattr(logs_write_result, 'stderr') else (logs_write_result if isinstance(logs_write_result, str) else "")
                raise VisualizationToolError(f"Failed to write HTML for visualization. Exit: {response_write.exit_code}. Logs: {logs_write_output}")
            
            return {
                "html_file_sandbox_path": html_full_path,
                "html_file_name": html_filename, # For potential use in URL construction by frontend
                "html_content": html_content_to_write,
                "message": f"HTML file for displaying visualization created at {html_full_path}. Its content is also available directly."
            }
        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error preparing visualization '{image_path}' for browser: {str(e)}", exc_info=True)
            raise VisualizationToolError(f"Error preparing visualization for browser display: {str(e)}") from e
        finally:
            try:
                if self.sandbox and self.sandbox.process:
                     self.sandbox.process.delete_session(session_id)
                     logging.debug(f"Visualization display session {session_id} cleaned up.")
            except Exception as cleanup_e:
                logging.error(f"Failed to delete session {session_id} during display_visualization cleanup: {cleanup_e}", exc_info=True)
