from agentpress.tool import ToolResult, openapi_schema
from sandbox.tool_base import SandboxToolsBase
from agentpress.thread_manager import ThreadManager
from uuid import uuid4
from sandbox.sandbox import SessionExecuteRequest # Make sure this import is correct

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
    ) -> ToolResult:
        session_id = f"viz_create_{uuid4().hex[:8]}"
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            self.sandbox.process.create_session(session_id)
            
            # Create visualizations directory if it doesn't exist
            # This is already created by setup_visualization_environment, but good to ensure
            mkdir_req = SessionExecuteRequest(
                command=f"mkdir -p {self.visualizations_path}",
                var_async=False,
                cwd=self.workspace_path
            )
            await self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=mkdir_req
            )
            
            # Create a Python script for the visualization
            script_content = f'''
import matplotlib.pyplot as plt
import numpy as np

# Data
categories = {categories}
values = {values}

# Create the bar chart
plt.figure(figsize=(10, 6))
plt.bar(categories, values, color='skyblue')
plt.title("{title}")
plt.xlabel("{x_label}")
plt.ylabel("{y_label}")
plt.xticks(rotation=45, ha='right')
plt.tight_layout()

# Save the figure
plt.savefig("{self.visualizations_path}/{output_file}.png")
print(f"Bar chart saved to {self.visualizations_path}/{output_file}.png")
'''
            
            # Write the script to a temporary file
            script_path = f"{self.workspace_path}/temp_viz_script_{uuid4().hex[:8]}.py"
            # Ensure script_content is properly escaped for the shell command
            # Basic escaping for single quotes within the script content for 'cat << EOL'
            escaped_script_content = script_content.replace("'", "'\\''")
            write_script_req = SessionExecuteRequest(
                command=f"cat > {script_path} << 'EOL'\n{escaped_script_content}\nEOL",
                var_async=False,
                cwd=self.workspace_path
            )
            await self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=write_script_req
            )
            
            # Execute the script
            exec_req = SessionExecuteRequest(
                command=f"python {script_path}",
                var_async=False,
                cwd=self.workspace_path
            )
            response = await self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=exec_req,
                timeout=60 # Increased timeout for potentially long plotting
            )
            
            # Get logs
            logs = await self.sandbox.process.get_session_command_logs(
                session_id=session_id,
                command_id=response.cmd_id
            )
            
            # Clean up
            cleanup_req = SessionExecuteRequest(
                command=f"rm {script_path}",
                var_async=False,
                cwd=self.workspace_path
            )
            await self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=cleanup_req
            )
            
            if response.exit_code != 0:
                # It's better to include logs in the failure message
                return self.fail_response(f"Failed to create bar chart. Exit code: {response.exit_code}. Logs: {logs}")
            
            return self.success_response({{
                "output_file": f"{self.visualizations_path}/{output_file}.png",
                "message": f"Bar chart created successfully: {self.visualizations_path}/{output_file}.png. Logs: {logs}"
            }})
            
        except Exception as e:
            return self.fail_response(f"Error creating bar chart: {str(e)}")
        finally:
            try:
                if self.sandbox and self.sandbox.process:
                     await self.sandbox.process.delete_session(session_id)
            except Exception as cleanup_e:
                # Log or handle cleanup error if necessary
                # logger.error(f"Failed to delete session {session_id}: {cleanup_e}")
                pass

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
    ) -> ToolResult:
        session_id = f"viz_view_{uuid4().hex[:8]}"
        try:
            await self._ensure_sandbox()
            
            # Clean the image_path to ensure it's within the workspace
            # and prevent path traversal issues.
            safe_image_path = self.clean_path(image_path) # Assuming clean_path is synchronous

            # Check if path is absolute and within workspace, otherwise prepend workspace_path
            if not safe_image_path.startswith(self.workspace_path):
                safe_image_path = f"{self.workspace_path}/{safe_image_path.lstrip('/')}"


            await self.sandbox.process.create_session(session_id)
            
            check_req = SessionExecuteRequest(
                command=f"test -f {safe_image_path} && echo 'exists' || echo 'not_exists'",
                var_async=False,
                cwd=self.workspace_path 
            )
            response = await self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=check_req
            )
            
            logs = await self.sandbox.process.get_session_command_logs(
                session_id=session_id,
                command_id=response.cmd_id
            )
            
            if "not_exists" in logs:
                return self.fail_response(f"Visualization file not found: {safe_image_path}. Logs: {logs}")
            
            return self.success_response({{
                "image_path": safe_image_path,
                "message": "Visualization ready for display. File exists."
            }})
            
        except Exception as e:
            return self.fail_response(f"Error viewing visualization: {str(e)}")
        finally:
            try:
                if self.sandbox and self.sandbox.process:
                     await self.sandbox.process.delete_session(session_id)
            except Exception as cleanup_e:
                pass

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
    ) -> ToolResult:
        session_id = f"viz_display_{uuid4().hex[:8]}"
        try:
            await self._ensure_sandbox()

            safe_image_path = self.clean_path(image_path)
            if not safe_image_path.startswith(self.workspace_path):
                 safe_image_path = f"{self.workspace_path}/{safe_image_path.lstrip('/')}"
            
            await self.sandbox.process.create_session(session_id)

            # 1. Read the image file and base64 encode it
            # Assuming image_path points to a PNG file for now.
            # In a real scenario, MIME type detection might be needed.
            mime_type = "image/png" # Defaulting to PNG
            # Check file extension for a slightly more dynamic mime_type
            if safe_image_path.lower().endswith(".jpg") or safe_image_path.lower().endswith(".jpeg"):
                mime_type = "image/jpeg"
            elif safe_image_path.lower().endswith(".gif"):
                mime_type = "image/gif"
            elif safe_image_path.lower().endswith(".svg"):
                mime_type = "image/svg+xml"

            read_image_cmd = f"cat {safe_image_path} | base64 --wrap=0"
            exec_read_req = SessionExecuteRequest(
                command=read_image_cmd,
                var_async=False,
                cwd=self.workspace_path
            )
            response_read = await self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=exec_read_req,
                timeout=30 
            )

            base64_image_data = await self.sandbox.process.get_session_command_logs(
                session_id=session_id,
                command_id=response_read.cmd_id
            )

            if response_read.exit_code != 0 or not base64_image_data:
                return self.fail_response(f"Failed to read and encode image file '{safe_image_path}'. Exit code: {response_read.exit_code}. Logs: {base64_image_data}")
            
            # Clean up base64 data (remove potential newlines or extra spaces from logs)
            base64_image_data = base64_image_data.strip()

            # 2. Generate self-contained HTML
            html_content_to_write = f'''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Visualization</title>
    <style>
        body {{ margin: 0; padding: 10px; display: flex; justify-content: center; align-items: center; min-height: 95vh; background-color: #f0f0f0; font-family: Arial, sans-serif; }}
        img {{ max-width: 100%; max-height: 90vh; object-fit: contain; border: 1px solid #ccc; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
    </style>
</head>
<body>
    <img src="data:{mime_type};base64,{base64_image_data}" alt="Generated Visualization">
</body>
</html>
'''
            html_filename = f"visualization_display_{uuid4().hex[:8]}.html"
            html_path = f"{self.visualizations_path}/{html_filename}"
            
            # Basic escaping for single quotes for 'cat << EOL'
            escaped_html_to_write = html_content_to_write.replace("'", "'\\''")

            write_html_req = SessionExecuteRequest(
                command=f"mkdir -p {self.visualizations_path} && cat > {html_path} << 'EOL'\n{escaped_html_to_write}\nEOL",
                var_async=False,
                cwd=self.workspace_path
            )
            response_write = await self.sandbox.process.execute_session_command(
                session_id=session_id,
                req=write_html_req
            )

            if response_write.exit_code != 0:
                logs_write = await self.sandbox.process.get_session_command_logs(session_id, response_write.cmd_id)
                return self.fail_response(f"Failed to write self-contained HTML for visualization. Exit code: {response_write.exit_code}. Logs: {logs_write}")
            
            return self.success_response({{
                "html_path": html_path, # Path where the HTML file is saved in the sandbox
                "html_file_name": html_filename,
                "html_content": html_content_to_write, # The actual HTML content string
                "message": f"Self-contained HTML file for displaying visualization created at {html_path} and content is available for direct rendering."
            }})
            
        except Exception as e:
            return self.fail_response(f"Error preparing self-contained visualization for browser display: {str(e)}")
        finally:
            try:
                if self.sandbox and self.sandbox.process:
                     await self.sandbox.process.delete_session(session_id)
            except Exception as cleanup_e:
                pass
