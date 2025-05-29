# Tool Creation Best Practices for Neura Agent System

## 1. Introduction

This document provides guidelines and best practices for developers creating new tools for the Neura agent system. Well-defined and robust tools are crucial for expanding the agent's capabilities and ensuring its effective operation. Adhering to these practices will help maintain consistency, reliability, and security across the agent's toolset.

## 2. Core Principles for Tool Design

When designing a new tool, consider the following core principles:

*   **Clear Purpose:**
    *   Each tool should have a single, well-defined responsibility. Avoid creating tools that perform many unrelated tasks.
    *   The agent should be able to easily determine if a tool is suitable for a given sub-task based on its purpose.

*   **Atomic Operations:**
    *   Tools should ideally perform atomic operations. This means the operation either completes successfully or fails without leaving the system in an inconsistent state.
    *   If a tool performs multiple steps, ensure these steps form a single logical unit.

*   **Robust Error Handling:**
    *   Tools must handle potential errors gracefully. This includes validating inputs, catching exceptions, and managing external service failures.
    *   Return informative error messages to the agent via `ToolResult` (using `self.fail_response()`). These messages help the agent understand the cause of failure and potentially retry or adapt its approach.

*   **Clear Schema (OpenAPI/XML):**
    *   The schema is the primary way the agent understands how to use the tool. It's critical for the agent's ability to correctly select and invoke tools.
    *   **`name`**: The tool name (and its methods) must be concise, descriptive, and follow a consistent naming convention (e.g., `snake_case` for Python methods that become tool functions).
    *   **`description`**: This is CRITICAL. The description should clearly explain:
        *   What the tool (or tool method) does.
        *   When the agent should use it (e.g., "Use this tool to execute a Python script in the sandbox").
        *   What its expected outcome is (e.g., "Returns the standard output and standard error of the script").
        *   Any important prerequisites or limitations.
        The agent relies heavily on this description for tool selection and invocation.
    *   **`parameters`**:
        *   Clearly define all parameters, their data types (e.g., string, integer, boolean, list, object), and whether they are required or optional.
        *   Use descriptive names for parameters.
        *   Provide clear descriptions for each parameter, explaining its purpose and any formatting requirements.

*   **Security:**
    *   Tools that execute code, interact with the file system, or access external services must be designed with security as a top priority.
    *   For tools that need to run commands or manage files within a sandboxed environment, inheriting from `sandbox.tool_base.SandboxToolsBase` is highly recommended. This base class provides foundational security measures and sandboxing capabilities.
    *   Always validate inputs to prevent injection attacks or unintended behavior.
    *   Avoid exposing sensitive information in tool outputs unless absolutely necessary and clearly documented.

*   **Idempotency (if applicable):**
    *   Consider whether your tool should be idempotent. An idempotent operation can be called multiple times with the same input parameters and will produce the same result without causing additional side effects.
    *   For example, a tool that creates a file might be idempotent if calling it again with the same filename and content doesn't create a duplicate or throw an error.
    *   Idempotency can be beneficial for retries and error recovery.

*   **Logging:**
    *   Incorporate logging within your tool using the provided `utils.logger`.
    *   Log important events, parameter values (be mindful of sensitive data), and errors.
    *   Good logging is invaluable for debugging issues and monitoring the agent's behavior.

## 3. Implementation Guidelines

### Tool Class Structure

*   Tools should typically inherit from `agentpress.tool.Tool`.
*   If a tool requires interaction with a sandboxed environment (e.g., executing commands, accessing a restricted file system), it should inherit from `sandbox.tool_base.SandboxToolsBase`.

```python
# Basic structure for a general tool
from agentpress.tool import Tool, ToolResult, openapi_schema, xml_schema
from agentpress.utils import logger

class MyCustomTool(Tool):
    def __init__(self, config=None):
        super().__init__()
        self.config = config
        # Initialize any required resources, API clients, etc.

    @openapi_schema
    def my_tool_method(self, param1: str, param2: int) -> ToolResult:
        """
        Description of what my_tool_method does.
        This is critical for the agent to understand how to use this method.
        
        :param param1: Description of the first parameter.
        :type param1: str
        :param param2: Description of the second parameter.
        :type param2: int
        :return: A ToolResult object.
        :rtype: ToolResult
        """
        logger.info(f"Executing my_tool_method with param1: {param1}, param2: {param2}")
        try:
            # ... tool logic ...
            result_data = f"Processed {param1} and {param2}"
            return self.success_response(result_data)
        except Exception as e:
            logger.error(f"Error in my_tool_method: {e}", exc_info=True)
            return self.fail_response(f"Failed to execute: {str(e)}")

# For tools interacting with the sandbox
# from sandbox.tool_base import SandboxToolsBase
# class MySandboxTool(SandboxToolsBase):
#     # ... implementation ...
```

### Schema Decorators

*   The `@openapi_schema` decorator is used to define the schema for tool methods that will be exposed to the agent via an OpenAPI specification. This is the primary way the agent understands the tool's interface.
*   The `@xml_schema` decorator can be used if an XML-based schema representation is also required (less common for primary agent interaction but may be used by other systems).
*   The docstring of the decorated method is crucial as it's used to generate the `description` field in the schema. Ensure it is detailed and accurate. Type hints in the method signature are used to infer parameter types.

```python
from agentpress.tool import Tool, ToolResult, openapi_schema

class ExampleTool(Tool):
    @openapi_schema
    def get_user_details(self, user_id: int, include_email: bool = False) -> ToolResult:
        """
        Fetches details for a given user ID.

        Use this tool to retrieve information about a specific user.
        The user_id is required. By default, the user's email is not included.
        Set include_email to true to include the email address in the response.

        :param user_id: The unique identifier for the user.
        :type user_id: int
        :param include_email: Whether to include the user's email. Defaults to False.
        :type include_email: bool
        :return: A dictionary containing user details or an error message.
        :rtype: ToolResult
        """
        # ... implementation ...
        if user_id == 1:
            details = {"name": "Agent Smith", "status": "active"}
            if include_email:
                details["email"] = "smith@example.com"
            return self.success_response(details)
        else:
            return self.fail_response(f"User with ID {user_id} not found.")
```

### Returning Results

*   Tool methods should return an instance of `ToolResult`.
*   `ToolResult` encapsulates whether the operation was successful and the data returned.
*   Use `self.success_response(data)` to return a successful result. The `data` can be a string, dictionary, list, or any JSON-serializable type.
*   Use `self.fail_response(error_message)` to return a failure result. The `error_message` should be a clear string explaining the error.

### Configuration and Initialization

*   If your tool requires configuration parameters (e.g., API keys, base URLs, project IDs), these should typically be passed to the `__init__` method.
*   Configuration can be loaded from environment variables or a configuration file, and then passed to the tool instance when it's created and registered.

```python
classApiClientTool(Tool):
    def __init__(self, api_key: str, base_url: str):
        super().__init__()
        self.api_key = api_key
        self.base_url = base_url
        # Potentially initialize an HTTP client here
        # import httpx
        # self.client = httpx.Client(base_url=self.base_url, headers={"Authorization": f"Bearer {self.api_key}"})

    # ... tool methods ...
```

## 4. Good Examples in the Codebase

For practical examples, refer to the existing tools in the Neura codebase:

*   **`backend/agent/tools/python_tool.py` (`PythonTool`):**
    *   A good example of a tool that executes code (Python scripts) within the sandbox.
    *   Demonstrates use of `SandboxToolsBase`.
    *   Shows how to capture and return `stdout` and `stderr`.

*   **`backend/agent/tools/sb_shell_tool.py` (`SandboxShellTool`):**
    *   A more complex example illustrating robust shell command execution in the sandbox.
    *   Demonstrates session management for commands.
    *   Handles both blocking and non-blocking command execution.
    *   Provides multiple related tool functions within one class (`execute_command`, `check_command_output`, `terminate_command`), each with its own clear schema and purpose.

## 5. Integrating New Tools into the System

### Tool Registration

*   The Neura agent system uses a `ToolRegistry` (found in `backend/agentpress/tool_registry.py`).
*   This registry typically auto-discovers tools that are correctly defined (i.e., inherit from `Tool` or `SandboxToolsBase` and have methods decorated with `@openapi_schema`).
*   Ensure your new tool class is accessible for discovery (e.g., imported in a relevant `__init__.py` if necessary, depending on the project structure).

### **Updating System Prompts (CRITICAL)**

*   **This is a crucial step.** Simply creating a tool class is not enough for the agent to use it effectively.
*   The agent's behavior is heavily guided by its system prompts, located in:
    *   `backend/agent/prompt.py`
    *   `backend/agent/gemini_prompt.py` (if applicable for different LLM backends)
*   When adding a new tool or significantly changing an existing one (e.g., adding new methods, changing parameters), you **MUST** update these system prompts.
*   **What to include in the prompt update:**
    *   A clear description of the new tool and its capabilities.
    *   When the agent should consider using this tool.
    *   Concrete examples of how the agent should format its requests to use the tool (e.g., the XML or JSON structure it should generate for the tool call).
    *   If the tool has multiple methods, provide examples for each important method.
*   Without these prompt updates, the agent will likely be unaware of the new tool or unsure how to use it correctly, significantly limiting its effectiveness.

## 6. Formatting

Ensure this document and any related documentation use clear Markdown formatting (headers, lists, code blocks, emphasis) to enhance readability and structure.
```
