from agentpress.tool import openapi_schema, xml_schema # ToolResult removed
from sandbox.tool_base import SandboxToolsBase
from agentpress.thread_manager import ThreadManager
import logging # Added for logging

# Custom Exceptions
class ExposeToolError(Exception):
    """Base exception for port exposing tool errors."""
    pass

class SandboxExposeTool(SandboxToolsBase):
    """Tool for exposing and retrieving preview URLs for sandbox ports."""

    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "expose_port",
            "description": "Expose a port from the agent's sandbox environment to the public internet and get its preview URL. This is essential for making services running in the sandbox accessible to users, such as web applications, APIs, or other network services. The exposed URL can be shared with users to allow them to interact with the sandbox environment.",
            "parameters": {
                "type": "object",
                "properties": {
                    "port": {
                        "type": "integer",
                        "description": "The port number to expose. Must be a valid port number between 1 and 65535.",
                        "minimum": 1,
                        "maximum": 65535
                    }
                },
                "required": ["port"]
            }
        }
    })
    @xml_schema(
        tag_name="expose-port",
        mappings=[
            {"param_name": "port", "node_type": "content", "path": "."}
        ],
        example='''
        <!-- Example 1: Expose a web server running on port 8000 -->
        <!-- This will generate a public URL that users can access to view the web application -->
        <expose-port>
        8000
        </expose-port>

        <!-- Example 2: Expose an API service running on port 3000 -->
        <!-- This allows users to interact with the API endpoints from their browser -->
        <expose-port>
        3000
        </expose-port>

        <!-- Example 3: Expose a development server running on port 5173 -->
        <!-- This is useful for sharing a development environment with users -->
        <expose-port>
        5173
        </expose-port>

        <!-- Example 4: Expose a database management interface on port 8081 -->
        <!-- This allows users to access database management tools like phpMyAdmin -->
        <expose-port>
        8081
        </expose-port>
        '''
    )
    async def expose_port(self, port: int) -> dict: # Return dict on success
        try:
            # Convert port to integer early, handle potential ValueError from int()
            try:
                port_num = int(port)
            except ValueError:
                raise ValueError(f"Invalid port number: '{port}'. Must be a valid integer.")

            # Validate port number
            if not 1 <= port_num <= 65535:
                raise ValueError(f"Invalid port number: {port_num}. Must be between 1 and 65535.")

            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            # Get the preview link for the specified port
            # Assuming self.sandbox.get_preview_link raises an exception on failure (e.g., port not exposable, sandbox error)
            preview_link_obj = self.sandbox.get_preview_link(port_num) # Renamed to avoid confusion with url variable
            
            # Extract the actual URL from the preview link object
            # This part depends on the structure of preview_link_obj.
            # If it can be None or not have 'url', handle appropriately.
            if not preview_link_obj or not hasattr(preview_link_obj, 'url') or not preview_link_obj.url:
                # Log details of preview_link_obj if it's not as expected
                logging.error(f"Failed to get a valid URL from preview_link object for port {port_num}. Object: {preview_link_obj}")
                raise ExposeToolError(f"Could not retrieve a valid preview URL for port {port_num}.")

            url = preview_link_obj.url
            
            return {
                "url": url,
                "port": port_num,
                "message": f"Successfully exposed port {port_num}. Preview URL: {url}"
            }
        except ValueError: # Catches errors from int(port) and manual port validation
            raise
        except Exception as e:
            # Log the full error for debugging
            logging.error(f"Error exposing port {port}: {str(e)}", exc_info=True)
            # Raise a more specific error to the orchestrator
            raise ExposeToolError(f"An unexpected error occurred while exposing port {port}: {str(e)}") from e
