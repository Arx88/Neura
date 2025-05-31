import asyncio
import time
import uuid
import os # Added
import importlib.util # Added
import inspect # Added
from typing import Dict, Any, Optional, List, Type # Added List, Type
from agentpress.tool import Tool, EnhancedToolResult, openapi_schema # Added openapi_schema for dummy tool
from utils.logger import logger # Changed import

# Define a default plugin directory at the module level or pass to orchestrator
DEFAULT_PLUGINS_DIR = "backend/agentpress/plugins"


class ToolOrchestrator:
    """
    Manages the registration and asynchronous execution of tools.
    """
    def __init__(self):
        self.tools: Dict[str, Tool] = {}
        self.tool_execution_tasks: Dict[str, asyncio.Task] = {}
        self.plugin_sources: Dict[str, str] = {} # tool_id -> path of plugin file
        logger.info("ToolOrchestrator initialized.")

    def register_tool(self, tool_instance: Tool, tool_id: Optional[str] = None, plugin_path: Optional[str] = None):
        """
        Registers a tool instance with the orchestrator.
        If tool_id is not provided, it defaults to the tool's class name.
        Stores the plugin path if provided.
        """
        if not tool_id:
            # Allow tools to define their own preferred ID via a class attribute
            tool_id = getattr(tool_instance.__class__, 'PLUGIN_TOOL_ID', tool_instance.__class__.__name__)

        if tool_id in self.tools:
            logger.warning(f"Tool with ID '{tool_id}' is already registered. Overwriting.")

        self.tools[tool_id] = tool_instance
        if plugin_path:
            self.plugin_sources[tool_id] = plugin_path
        logger.info(f"Tool '{tool_id}' registered successfully (Source: {plugin_path or 'Direct'}).")

    def unload_tool(self, tool_id: str):
        """Removes a tool from the orchestrator."""
        if tool_id in self.tools:
            del self.tools[tool_id]
            if tool_id in self.plugin_sources:
                del self.plugin_sources[tool_id]
            logger.info(f"Tool '{tool_id}' unloaded successfully.")
        else:
            logger.warning(f"Tool with ID '{tool_id}' not found, cannot unload.")

    def load_tools_from_directory(self, directory_path: str = DEFAULT_PLUGINS_DIR):
        """
        Scans a directory for Python files, imports them, finds Tool subclasses,
        instantiates them, and registers them.
        """
        if not os.path.isdir(directory_path):
            logger.warning(f"Plugin directory '{directory_path}' not found. Skipping plugin loading.")
            return

        logger.info(f"Scanning for tool plugins in directory: {directory_path}")
        for filename in os.listdir(directory_path):
            if filename.endswith(".py") and not filename.startswith("_"):
                file_path = os.path.join(directory_path, filename)
                module_name = f"backend.agentpress.plugins.{filename[:-3]}" # Needs to be unique and importable

                try:
                    spec = importlib.util.spec_from_file_location(module_name, file_path)
                    if spec and spec.loader:
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)

                        for attribute_name in dir(module):
                            attribute = getattr(module, attribute_name)
                            if inspect.isclass(attribute) and issubclass(attribute, Tool) and attribute is not Tool:
                                # Found a Tool subclass
                                try:
                                    tool_instance = attribute() # Assumes no-args constructor for plugins
                                    # Use PLUGIN_TOOL_ID if defined, else class name
                                    plugin_tool_id = getattr(attribute, 'PLUGIN_TOOL_ID', attribute.__name__)
                                    self.register_tool(tool_instance, tool_id=plugin_tool_id, plugin_path=file_path)
                                except Exception as e:
                                    logger.error(f"Failed to instantiate or register tool '{attribute.__name__}' from plugin '{filename}': {e}")
                    else:
                        logger.error(f"Could not create module spec for plugin: {filename}")
                except Exception as e:
                    logger.error(f"Error loading plugin '{filename}': {e}", exc_info=True)
        logger.info("Finished scanning for tool plugins.")

    def reload_tool(self, tool_id: str) -> bool:
        """Reloads a tool if it was loaded from a plugin file."""
        if tool_id not in self.plugin_sources:
            logger.warning(f"Tool '{tool_id}' was not loaded from a plugin or path is unknown. Cannot reload.")
            return False

        plugin_path = self.plugin_sources[tool_id]
        module_name_base = os.path.basename(plugin_path)[:-3]
        # Ensure module name is unique enough if paths could clash, e.g. by incorporating more of the path.
        # For now, using a simple plugins.<filename_no_ext> style.
        module_name = f"backend.agentpress.plugins.{module_name_base}"

        logger.info(f"Attempting to reload tool '{tool_id}' from plugin '{plugin_path}' (module: {module_name})")

        # Unload first
        self.unload_tool(tool_id)

        # Force module reload - this can be tricky.
        # If the module was already imported, Python might cache it.
        # A common way to force reload is to remove it from sys.modules.
        if module_name in inspect.sys.modules:
            del inspect.sys.modules[module_name]
            logger.debug(f"Removed module '{module_name}' from sys.modules for reloading.")

        try:
            spec = importlib.util.spec_from_file_location(module_name, plugin_path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                reloaded = False
                for attribute_name in dir(module):
                    attribute = getattr(module, attribute_name)
                    if inspect.isclass(attribute) and issubclass(attribute, Tool) and attribute is not Tool:
                        # Check if this is the class we want to reload
                        current_plugin_tool_id = getattr(attribute, 'PLUGIN_TOOL_ID', attribute.__name__)
                        if current_plugin_tool_id == tool_id:
                            try:
                                tool_instance = attribute()
                                self.register_tool(tool_instance, tool_id=tool_id, plugin_path=plugin_path)
                                logger.info(f"Successfully reloaded and registered tool '{tool_id}'.")
                                reloaded = True
                                break
                            except Exception as e:
                                logger.error(f"Failed to instantiate or re-register reloaded tool '{tool_id}': {e}")
                                return False # Failed to reload
                if not reloaded:
                    logger.error(f"Could not find class for tool_id '{tool_id}' in reloaded module '{module_name}'.")
                    return False
                return True
            else:
                logger.error(f"Could not create module spec for reloading plugin: {plugin_path}")
                return False
        except Exception as e:
            logger.error(f"Error reloading plugin for tool '{tool_id}': {e}", exc_info=True)
            return False

    async def execute_tool(self, tool_id: str, method_name: str, params: Dict[str, Any]) -> EnhancedToolResult:
        """
        Executes a specific method of a registered tool asynchronously.

        Args:
            tool_id: The ID of the tool to execute.
            method_name: The name of the method to call on the tool.
            params: A dictionary of parameters to pass to the tool method.

        Returns:
            An EnhancedToolResult object representing the outcome of the execution.
        """
        execution_id = str(uuid.uuid4())
        start_time = time.time()

        if tool_id not in self.tools:
            logger.error(f"Tool with ID '{tool_id}' not found.")
            # Create a dummy tool instance for fail_response, or handle directly
            # This assumes Tool class can be instantiated without specific args for this purpose,
            # or we construct EnhancedToolResult directly.
            # For now, let's assume direct construction or a generic fail response.
            return EnhancedToolResult(
                tool_id=tool_id,
                execution_id=execution_id,
                status="failed",
                error=f"Tool with ID '{tool_id}' not found.",
                end_time=time.time()
            )

        tool_instance = self.tools[tool_id]

        if not hasattr(tool_instance, method_name):
            logger.error(f"Method '{method_name}' not found on tool '{tool_id}'.")
            return tool_instance.fail_response(
                tool_id=tool_id, # or tool_instance.get_id() if that exists
                execution_id=execution_id,
                msg=f"Method '{method_name}' not found on tool '{tool_id}'."
            )

        method_to_call = getattr(tool_instance, method_name)

        enhanced_result = EnhancedToolResult(
            tool_id=tool_id, # or tool_instance.get_id()
            execution_id=execution_id,
            start_time=start_time
        )
        enhanced_result.update_progress(0.1, status="running")

        try:
            logger.info(f"Executing method '{method_name}' on tool '{tool_id}' with params: {params}")
            # This is where the actual tool method is called.
            # Tool methods themselves are not async, so we run them in the default executor.
            # The `success_response` or `fail_response` from the tool method will create an EnhancedToolResult.
            # However, the tool's own methods return an EnhancedToolResult directly now.

            # We need to adapt how parameters are passed if they are not directly kwargs
            # For now, assuming they are kwargs.
            # loop = asyncio.get_event_loop() # Not needed if method_to_call is already async
            # The method_to_call is an async method of the tool.
            # It should return raw data or raise an exception.
            # actual_result_data = await loop.run_in_executor(None, lambda: method_to_call(**params)) # This was incorrect for async tool methods
            actual_result_data = await method_to_call(**params)

            # The `method_to_call` should now return an EnhancedToolResult.
            # We need to ensure the tool_id and execution_id are correctly passed into it.
            # The tool methods themselves don't know their `tool_id` (as registered in orchestrator)
            # or the `execution_id`.
            # The `success_response` and `fail_response` methods in the base `Tool` class now
            # require `tool_id` and `execution_id`.

            # Let's adjust the design: the tool method itself should not call success/fail_response.
            # It should return the raw data or raise an exception.
            # The orchestrator then wraps this into an EnhancedToolResult.
            # This contradicts the previous subtask's changes to Tool.success/fail_response.
            # For now, I will assume the tool method returns raw data or raises an exception,
            # and the orchestrator will create the EnhancedToolResult.
            # This means I'll need to revert/adjust the success/fail_response in Tool later or here.

            # Let's stick to the new `Tool.success_response` and `Tool.fail_response` for now.
            # This means the `method_to_call` must be a wrapper provided by the `Tool` class
            # or the tool developer must explicitly call `self.success_response` or `self.fail_response`
            # with tool_id and execution_id.

            # The prompt says "tool_instance.execute(**params)". This is simpler.
            # This implies that the Tool class should have a generic execute method,
            # or each tool method is directly callable and expected to return the EnhancedToolResult.
            # The latter is what I've implemented in the previous step for success_response/fail_response.

            # So, the `method_to_call(**params)` should ideally return an EnhancedToolResult.
            # But it won't have tool_id and execution_id unless we pass them in.
            # This is getting complicated.

            # Let's refine: The `method_to_call` is the actual user-defined tool function.
            # It should return its raw result or raise an exception.
            # The `ToolOrchestrator` then uses `tool_instance.success_response` or `tool_instance.fail_response`
            # to construct the `EnhancedToolResult`.

            # actual_result_data = await loop.run_in_executor(None, lambda: method_to_call(**params)) # This was moved up

            # Now, use the tool's success_response to build the EnhancedToolResult
            final_enhanced_result = tool_instance.success_response(
                tool_id=tool_id, # The ID known to the orchestrator
                execution_id=execution_id,
                data=actual_result_data
            )
            # The start_time was set when enhanced_result was created. We should preserve it.
            final_enhanced_result.start_time = enhanced_result.start_time
            logger.info(f"Tool '{tool_id}' method '{method_name}' executed successfully.")
            return final_enhanced_result

        except Exception as e:
            logger.error(f"Error executing tool '{tool_id}' method '{method_name}': {e}", exc_info=True)
            # Use the tool's fail_response
            final_enhanced_result = tool_instance.fail_response(
                tool_id=tool_id,
                execution_id=execution_id,
                msg=str(e)
            )
            final_enhanced_result.start_time = enhanced_result.start_time
            return final_enhanced_result

    def cancel_tool_execution(self, execution_id: str):
        """
        Cancels a running tool execution.
        Note: Actual cancellation depends on the tool's implementation.
        This method primarily cancels the asyncio task.
        """
        if execution_id in self.tool_execution_tasks:
            task = self.tool_execution_tasks[execution_id]
            if not task.done():
                task.cancel()
                logger.info(f"Attempted to cancel tool execution ID: {execution_id}")
                # Optionally, update the EnhancedToolResult status to 'cancelled'
                # This requires storing/accessing the result object by execution_id
            else:
                logger.info(f"Tool execution ID: {execution_id} already completed or cancelled.")
            del self.tool_execution_tasks[execution_id] # Clean up
        else:
            logger.warning(f"No active task found for execution ID: {execution_id} to cancel.")

    # Placeholder for schema methods to be added from ToolRegistry
    def get_openapi_schemas(self) -> List[Dict[str, Any]]: # Changed return type
        """
        Retrieves OpenAPI schemas for all registered tools and their methods.
        Returns a list of schema definitions, similar to ToolRegistry.
        The 'name' in each schema should be unique for LLM consumption,
        ideally tool_id__method_name.
        """
        schemas_list = []
        for tool_id, tool_instance in self.tools.items():
            tool_method_schemas = tool_instance.get_schemas() # Dict[str, List[ToolSchema]]
            for method_name, schema_list in tool_method_schemas.items():
                for schema_obj in schema_list:
                    if schema_obj.schema_type.value == "openapi":
                        # Create a copy to avoid modifying the original schema
                        schema_copy = schema_obj.schema.copy()
                        # Ensure a unique name, critical for LLMs
                        schema_copy['name'] = f"{tool_id}__{method_name}"
                        schemas_list.append(schema_copy)
        logger.debug(f"ToolOrchestrator: Retrieved {len(schemas_list)} OpenAPI schemas for general use.")
        return schemas_list

    def get_xml_examples(self) -> Dict[str, str]: # Changed return type
        """
        Retrieves XML examples for all registered tools.
        Returns a dictionary mapping an identifier (e.g., tool_id__method_name or tag_name) to its example.
        ToolRegistry used tag_name as key.
        """
        examples_dict = {}
        for tool_id, tool_instance in self.tools.items():
            tool_schemas = tool_instance.get_schemas() # Dict[str, List[ToolSchema]]
            for method_name, schema_list in tool_schemas.items():
                for schema_obj in schema_list:
                    if schema_obj.xml_schema and schema_obj.xml_schema.example:
                        # Use tag_name as key to match ToolRegistry
                        key = schema_obj.xml_schema.tag_name
                        # To make it unique if multiple tools use the same tag (though unlikely for XML):
                        # key = f"{tool_id}__{schema_obj.xml_schema.tag_name}"
                        examples_dict[key] = schema_obj.xml_schema.example.strip()
        logger.debug(f"ToolOrchestrator: Retrieved {len(examples_dict)} XML examples.")
        return examples_dict

    def get_tool_method_description(self, tool_id: str, method_name: str) -> Optional[str]:
        """
        Retrieves the description for a specific tool method.
        This might be useful for providing context to the LLM.
        """
        if tool_id in self.tools:
            tool_instance = self.tools[tool_id]
            tool_schemas = tool_instance.get_schemas()
            if method_name in tool_schemas:
                for schema_obj in tool_schemas[method_name]:
                    # Assuming OpenAPI schema and description is at schema['description']
                    if schema_obj.schema_type.value == "openapi" and "description" in schema_obj.schema:
                        return schema_obj.schema["description"]
                    # Add other schema types if necessary
        return None

    def get_all_tool_descriptions(self) -> Dict[str, str]:
        """
        Retrieves descriptions for all methods of all registered tools.
        Format: {"tool_id.method_name": "description"}
        """
        all_descriptions = {}
        for tool_id, tool_instance in self.tools.items():
            tool_schemas = tool_instance.get_schemas() # Dict[str, List[ToolSchema]]
            for method_name, schema_list in tool_schemas.items():
                for schema_obj in schema_list:
                    if schema_obj.schema_type.value == "openapi" and "description" in schema_obj.schema:
                         all_descriptions[f"{tool_id}.{method_name}"] = schema_obj.schema["description"]
                    elif schema_obj.xml_schema: # Fallback for XML tools if no separate description
                        # For XML, we might just use the tag name or a generic description
                        all_descriptions[f"{tool_id}.{method_name}"] = f"XML tool with tag <{schema_obj.xml_schema.tag_name}>"
        return all_descriptions

    def get_tool_schemas_for_llm(self) -> List[Dict[str, Any]]:
        """
        Returns a list of schemas formatted for an LLM, typically OpenAPI.
        This will replace get_openapi_schemas() for LLM consumption.
        """
        llm_schemas = []
        for tool_id, tool_instance in self.tools.items():
            tool_method_schemas = tool_instance.get_schemas() # Dict[str, List[ToolSchema]]
            for method_name, schema_list in tool_method_schemas.items():
                for schema_obj in schema_list:
                    if schema_obj.schema_type.value == "openapi":
                        # Ensure the schema has a name, often derived from the function name
                        # The LLM usually expects a list of function definitions.
                        # The schema_obj.schema IS the OpenAPI schema for that function.
                        # We might need to inject tool_id and method_name if not present.
                        # The current openapi_schema decorator in tool.py creates a schema
                        # that is a dict, often with 'name', 'description', 'parameters'.

                        # Let's assume schema_obj.schema is already in the correct format
                        # for a single tool function.
                        # We might want to prefix the function name with tool_id for uniqueness.
                        # e.g., schema_obj.schema['name'] = f"{tool_id}__{method_name}"

                        # Create a copy to avoid modifying the original schema
                        schema_copy = schema_obj.schema.copy()

                        # If the schema doesn't have a 'name' field, or to ensure uniqueness:
                        # The original tool registration likely used the method's actual name.
                        # For LLMs, it's common to have a "functions" list, where each item is an OpenAPI spec for a function.
                        # The 'name' field in that spec is what the LLM uses to call the function.
                        # We need to make sure this name is unique and resolvable by execute_tool.
                        # A good convention: "toolId_methodName"
                        schema_copy['name'] = f"{tool_id}__{method_name}" # Ensure this is how LLM will call it

                        llm_schemas.append(schema_copy)

        logger.debug(f"Prepared {len(llm_schemas)} schemas for LLM consumption.")
        return llm_schemas

    def get_xml_schemas_for_llm(self) -> str:
        """
        Returns a string containing XML schema examples, formatted for an LLM.
        This will replace get_xml_examples() for LLM consumption.
        """
        xml_schema_parts = []
        for tool_id, tool_instance in self.tools.items():
            tool_method_schemas = tool_instance.get_schemas() # Dict[str, List[ToolSchema]]
            for method_name, schema_list in tool_method_schemas.items():
                for schema_obj in schema_list:
                    if schema_obj.xml_schema and schema_obj.xml_schema.example:
                        # Add a more descriptive header for the LLM
                        header = f"Tool Name: {tool_id}\nMethod: {method_name}\nXML Tag: <{schema_obj.xml_schema.tag_name}>"
                        # The LLM needs to know how to invoke this. Maybe include the callable name, e.g., {tool_id}__{method_name}
                        # For XML, the invocation is by producing the XML string itself.
                        # The example should be self-contained.
                        xml_schema_parts.append(
                            f"{header}\n{schema_obj.xml_schema.example.strip()}"
                        )

        if not xml_schema_parts:
            return ""

        return (
            "You can use the following XML tools. Wrap the XML in <tool_code>...</tool_code> tags.\n\n"
            + "\n\n---\n\n".join(xml_schema_parts)
        )

# Example Usage (for testing purposes, if run directly)
if __name__ == '__main__':
    # This part would require a concrete Tool implementation for testing
    # For now, it's just a placeholder to ensure the file is syntactically valid.

    # Example dummy tool for __main__ testing
    class MySampleTool(Tool):
        PLUGIN_TOOL_ID = "MyAwesomeTool" # Example of custom tool ID

        @openapi_schema({
            "name": "my_sample_method", # This will be prefixed with tool_id in get_tool_schemas_for_llm
            "description": "A sample tool method from MySampleTool.",
            "parameters": {
                "type": "object",
                "properties": {"param1": {"type": "string", "description": "A parameter"}},
                "required": ["param1"],
            },
        })
        def my_sample_method(self, param1: str):
            logger.info(f"{self.PLUGIN_TOOL_ID}.my_sample_method called with param1: {param1}")
            if param1 == "trigger_error":
                raise ValueError("This is a simulated error in my_sample_method.")
            return {"status": "success", "message": f"MySampleTool processed: {param1}"}

    async def main():
        # Create dummy plugins directory for testing
        current_script_dir = os.path.dirname(os.path.abspath(__file__))
        test_plugins_dir = os.path.join(current_script_dir, "plugins") # Relative to tool_orchestrator.py

        if not os.path.exists(test_plugins_dir):
            os.makedirs(test_plugins_dir)

        # Create a dummy plugin file
        dummy_plugin_content = """
from agentpress.tool import Tool, openapi_schema
from utils.logger import logger # Changed import for dummy plugin content as well

class TestPluginTool(Tool):
    PLUGIN_TOOL_ID = "FileSystemHelper" # Custom ID for this tool

    @openapi_schema({
        "name": "list_files", # Will become FileSystemHelper__list_files for LLM
        "description": "Lists files in a given directory.",
        "parameters": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "The directory to list files from."}
            },
            "required": ["directory"],
        },
    })
    def list_files_method(self, directory: str):
        logger.info(f"TestPluginTool.list_files_method called for directory: {directory}")
        # In a real scenario, you'd use os.listdir, but for testing:
        if directory == "error_dir":
            raise SystemError("Cannot access error_dir")
        return {"files": ["file1.txt", "file2.py"], "path": directory}

class AnotherToolInSameFile(Tool):
    # No PLUGIN_TOOL_ID, so class name "AnotherToolInSameFile" will be used.

    @openapi_schema({
        "name": "dummy_action",
        "description": "A dummy action from another tool in the same file.",
        "parameters": {"type": "object", "properties": {}},
    })
    def dummy_action_method(self):
        return {"message": "Dummy action executed!"}
"""
        dummy_plugin_path = os.path.join(test_plugins_dir, "dummy_example_plugin.py")
        with open(dummy_plugin_path, "w") as f:
            f.write(dummy_plugin_content)

        # Add __init__.py to make 'plugins' a package if it's not already (for cleaner imports)
        init_py_path = os.path.join(test_plugins_dir, "__init__.py")
        if not os.path.exists(init_py_path):
            with open(init_py_path, "w") as f:
                f.write("# This file makes Python treat the 'plugins' directory as a package.\n")

        orchestrator = ToolOrchestrator()

        # Load tools from the test directory
        orchestrator.load_tools_from_directory(test_plugins_dir)

        # Register an internal tool as well
        sample_internal_tool = MySampleTool()
        orchestrator.register_tool(sample_internal_tool) # Will use PLUGIN_TOOL_ID "MyAwesomeTool"

        print("\n--- Registered Tools (After Loading) ---")
        for tool_id in orchestrator.tools.keys():
            print(f"- {tool_id} (Source: {orchestrator.plugin_sources.get(tool_id, 'Direct')})")

        print("\n--- OpenAPI Schemas (LLM Formatted) ---")
        for schema in orchestrator.get_tool_schemas_for_llm():
            print(schema)

        print("\n--- Executing Plugin Tool Method (FileSystemHelper__list_files) ---")
        plugin_exec_result = await orchestrator.execute_tool(
            tool_id="FileSystemHelper",
            method_name="list_files_method",
            params={"directory": "/test/path"}
        )
        print("Plugin Execution Result:", plugin_exec_result)

        print("\n--- Executing Internal Tool Method (MyAwesomeTool__my_sample_method) ---")
        internal_exec_result = await orchestrator.execute_tool(
            tool_id="MyAwesomeTool",
            method_name="my_sample_method",
            params={"param1": "test value"}
        )
        print("Internal Tool Execution Result:", internal_exec_result)

        print("\n--- Testing Reload ---")
        # Modify the dummy plugin file to simulate a change
        new_plugin_content = dummy_plugin_content.replace(
            "# In a real scenario, you'd use os.listdir, but for testing:",
            "# MODIFIED: In a real scenario, you'd use os.listdir, but for testing:"
        )
        new_plugin_content = new_plugin_content.replace(
            'return {"files": ["file1.txt", "file2.py"], "path": directory}',
            'return {"files": ["MODIFIED_file1.txt", "file2.py"], "path": directory, "version": 2}'
        )
        with open(dummy_plugin_path, "w") as f:
            f.write(new_plugin_content)

        print(f"Attempting to reload tool 'FileSystemHelper' from {orchestrator.plugin_sources.get('FileSystemHelper')}")
        reload_success = orchestrator.reload_tool("FileSystemHelper")
        print(f"Reload successful: {reload_success}")

        if reload_success:
            print("\n--- Executing Plugin Tool Method (FileSystemHelper__list_files) AFTER RELOAD ---")
            plugin_exec_result_reloaded = await orchestrator.execute_tool(
                tool_id="FileSystemHelper",
                method_name="list_files_method",
                params={"directory": "/test/path/reloaded"}
            )
            print("Reloaded Plugin Execution Result:", plugin_exec_result_reloaded)

        print("\n--- Testing Unload ---")
        orchestrator.unload_tool("FileSystemHelper")
        print(f"'FileSystemHelper' unloaded. Current tools: {list(orchestrator.tools.keys())}")

        # Clean up dummy plugin file and directory (optional)
        # os.remove(dummy_plugin_path)
        # os.remove(init_py_path) # if created
        # if not os.listdir(test_plugins_dir): # Only remove if empty
        #     os.rmdir(test_plugins_dir)

    # To run this main:
    # Ensure AgentPress modules are in PYTHONPATH.
    # From the root of your project (e.g., where 'backend' folder is):
    # python -m backend.agentpress.tool_orchestrator

if __name__ == '__main__':
    # This setup is to allow the logger and other relative imports to work correctly
    # when running this script directly for testing.
    import sys
    # Assuming the script is in backend/agentpress/
    # Add 'backend' to sys.path to allow 'from backend.agentpress.tool import ...'
    # And add the parent of 'backend' to allow 'from utils.logger import ...'
    # This might need adjustment based on your exact project structure and how you run it.

    # Get the directory of the current script
    script_dir = os.path.dirname(os.path.abspath(__file__))
    # Path to 'backend/agentpress', then 'backend', then project root
    backend_dir = script_dir
    project_root_dir = os.path.dirname(backend_dir)

    # Add project root to sys.path for 'utils'
    if project_root_dir not in sys.path:
        sys.path.insert(0, project_root_dir)
    # Add backend dir to sys.path for 'backend.agentpress.tool'
    # Actually, if project_root is in path, then `from backend.agentpress...` should work.
    # Let's ensure 'backend' itself is not what we need for relative imports within backend.

    # The module_name for plugins like `backend.agentpress.plugins...` implies that
    # the directory containing `backend` should be in PYTHONPATH.
    # This is usually the project root.

    asyncio.run(main())
