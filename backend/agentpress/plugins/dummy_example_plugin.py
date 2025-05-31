from agentpress.tool import Tool, openapi_schema
from utils.logger import logger
import os

class TestPluginTool(Tool):
    """
    A test tool that was loaded from a plugin.
    It provides a simple file listing capability.
    """
    PLUGIN_TOOL_ID = "FileSystemHelper" # Custom ID for this tool

    @openapi_schema({
        "name": "list_files_in_directory", # Will become FileSystemHelper__list_files_in_directory for LLM
        "description": "Lists files and subdirectories in a given directory path.",
        "parameters": {
            "type": "object",
            "properties": {
                "directory_path": {
                    "type": "string",
                    "description": "The path of the directory to inspect."
                }
            },
            "required": ["directory_path"],
        },
    })
    def list_files_method(self, directory_path: str):
        """
        Simulates listing files in a directory.
        For security and simplicity, this example does not actually access the filesystem.
        In a real tool, you would use os.listdir() and handle exceptions.
        """
        logger.info(f"Plugin Tool '{self.PLUGIN_TOOL_ID}': Method 'list_files_method' called for directory: {directory_path}")

        if not directory_path or not isinstance(directory_path, str):
            logger.warning("Directory path is invalid.")
            # Using self.fail_response (which is now part of Tool base class via EnhancedToolResult logic)
            # The orchestrator will call this if an exception is raised.
            # So, we can just raise an error or return data for success_response.
            raise ValueError("A valid directory path must be provided.")

        if "fail_test" in directory_path:
            logger.info(f"Simulating failure for path: {directory_path}")
            raise PermissionError(f"Simulated permission error for {directory_path}.")

        # Simulate some file listing
        simulated_files = ["example.txt", "image.png", "report.pdf"]
        simulated_dirs = ["documents", "photos"]

        return {
            "path": directory_path,
            "files": simulated_files,
            "directories": simulated_dirs,
            "message": f"Successfully listed contents for '{directory_path}' (simulated)."
        }

class AnotherToolInSameFile(Tool):
    """
    Another example tool within the same plugin file.
    This one uses its class name as the tool_id by default.
    """

    @openapi_schema({
        "name": "simple_echo", # Will become AnotherToolInSameFile__simple_echo for LLM
        "description": "Echoes back the provided text.",
        "parameters": {
            "type": "object",
            "properties": {
                "text_to_echo": {"type": "string", "description": "The text to be echoed back by the tool."}
            },
            "required": ["text_to_echo"],
        },
    })
    def echo_text_method(self, text_to_echo: str):
        logger.info(f"Plugin Tool 'AnotherToolInSameFile': Method 'echo_text_method' called with: {text_to_echo}")
        if text_to_echo == "raise_error":
            raise RuntimeError("Echo tool forced error!")
        return {"echo": text_to_echo, "status": "ok"}

logger.info("Dummy example plugin 'dummy_example_plugin.py' loaded.")
