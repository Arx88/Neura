from typing import List, Optional, Union, Dict, Any
from agentpress.tool import Tool, openapi_schema, xml_schema # ToolResult removed
import logging # Added for logging

# Custom Exceptions
class MessageToolError(Exception):
    """Base exception for message tool errors."""
    pass

class MessageTool(Tool):
    """Tool for user communication and interaction.

    This tool provides methods for asking questions, with support for
    attachments and user takeover suggestions.
    """

    def __init__(self):
        super().__init__()

    # Commented out as we are just doing this via prompt as there is no need to call it as a tool

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "ask",
            "description": "Ask user a question and wait for response. Use for: 1) Requesting clarification on ambiguous requirements, 2) Seeking confirmation before proceeding with high-impact changes, 3) Gathering additional information needed to complete a task, 4) Offering options and requesting user preference, 5) Validating assumptions when critical to task success. IMPORTANT: Use this tool only when user input is essential to proceed. Always provide clear context and options when applicable. Include relevant attachments when the question relates to specific files or resources.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Question text to present to user - should be specific and clearly indicate what information you need. Include: 1) Clear question or request, 2) Context about why the input is needed, 3) Available options if applicable, 4) Impact of different choices, 5) Any relevant constraints or considerations."
                    },
                    "attachments": {
                        "anyOf": [
                            {"type": "string"},
                            {"items": {"type": "string"}, "type": "array"}
                        ],
                        "description": "(Optional) List of files or URLs to attach to the question. Include when: 1) Question relates to specific files or configurations, 2) User needs to review content before answering, 3) Options or choices are documented in files, 4) Supporting evidence or context is needed. Always use relative paths to /workspace directory."
                    }
                },
                "required": ["text"]
            }
        }
    })
    @xml_schema(
        tag_name="ask",
        mappings=[
            {"param_name": "text", "node_type": "content", "path": "."},
            {"param_name": "attachments", "node_type": "attribute", "path": ".", "required": False}
        ],
        example='''
Ask user a question and wait for response. Use for: 1) Requesting clarification on ambiguous requirements, 2) Seeking confirmation before proceeding with high-impact changes, 3) Gathering additional information needed to complete a task, 4) Offering options and requesting user preference, 5) Validating assumptions when critical to task success. IMPORTANT: Use this tool only when user input is essential to proceed. Always provide clear context and options when applicable. Include relevant attachments when the question relates to specific files or resources.

        <!-- Use ask when you need user input to proceed -->
        <!-- Examples of when to use ask: -->
        <!-- 1. Clarifying ambiguous requirements -->
        <!-- 2. Confirming high-impact changes -->
        <!-- 3. Choosing between implementation options -->
        <!-- 4. Validating critical assumptions -->
        <!-- 5. Getting missing information -->
        <!-- IMPORTANT: Always if applicable include representable files as attachments - this includes HTML files, presentations, writeups, visualizations, reports, and any other viewable content -->

        <ask attachments="recipes/chocolate_cake.txt,photos/cake_examples.jpg">
            I'm planning to bake the chocolate cake for your birthday party. The recipe mentions "rich frosting" but doesn't specify what type. Could you clarify your preferences? For example:
            1. Would you prefer buttercream or cream cheese frosting?
            2. Do you want any specific flavor added to the frosting (vanilla, coffee, etc.)?
            3. Should I add any decorative toppings like sprinkles or fruit?
            4. Do you have any dietary restrictions I should be aware of?

            This information will help me make sure the cake meets your expectations for the celebration.
        </ask>
        '''
    )
    async def ask(self, text: str, attachments: Optional[Union[str, List[str]]] = None) -> Dict[str, Any]:
        """Ask the user a question and wait for a response.

        Args:
            text: The question to present to the user.
            attachments: Optional file paths or URLs to attach to the question.

        Returns:
            A dictionary indicating the action status.
        Raises:
            ValueError: If text is not provided.
            MessageToolError: For other errors.
        """
        if not text or not isinstance(text, str):
            raise ValueError("Question text must be provided.")
        try:
            # Attachments are handled by the orchestrator when constructing the final ToolResult
            # The primary job of this method is to signal the intent and provide the necessary data (text, attachments).
            # The orchestrator will use self.success_response, passing this dict as output.
            # The `attachments` parameter is already part of the method signature and will be available
            # to the orchestrator via `tool_input_kwargs`. The orchestrator then needs to ensure
            # this `attachments` field is correctly placed into the `ToolResult`'s `output` or a specific field
            # if the `MessageTool.success_response` doesn't automatically include all passed args.
            # For now, the `success_response` just takes `output`.
            # The orchestrator will need to combine `text` and `attachments` into the `output` for `success_response`.
            # This means the orchestrator should create the dict that goes into `success_response`.
            # So, this method should just return the core data.

            # The orchestrator will construct the final ToolResult.
            # This method just needs to return the data that should go into ToolResult.output.
            output_payload = {"text": text, "status": "Awaiting user response..."}
            if attachments:
                if isinstance(attachments, str):
                    output_payload["attachments"] = [attachments]
                else:
                    output_payload["attachments"] = attachments
            return output_payload
        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error in 'ask' tool: {str(e)}", exc_info=True)
            raise MessageToolError(f"Error asking user: {str(e)}") from e

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "web_browser_takeover",
            "description": "Request user takeover of browser interaction. Use this tool when: 1) The page requires complex human interaction that automated tools cannot handle, 2) Authentication or verification steps require human input, 3) The page has anti-bot measures that prevent automated access, 4) Complex form filling or navigation is needed, 5) The page requires human verification (CAPTCHA, etc.). IMPORTANT: This tool should be used as a last resort after web-search and crawl-webpage have failed, and when direct browser tools are insufficient. Always provide clear context about why takeover is needed and what actions the user should take.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Instructions for the user about what actions to take in the browser. Include: 1) Clear explanation of why takeover is needed, 2) Specific steps the user should take, 3) What information to look for or extract, 4) How to indicate when they're done, 5) Any important context about the current page state."
                    },
                    "attachments": {
                        "anyOf": [
                            {"type": "string"},
                            {"items": {"type": "string"}, "type": "array"}
                        ],
                        "description": "(Optional) List of files or URLs to attach to the takeover request. Include when: 1) Screenshots or visual references are needed, 2) Previous search results or crawled content is relevant, 3) Supporting documentation is required. Always use relative paths to /workspace directory."
                    }
                },
                "required": ["text"]
            }
        }
    })
    @xml_schema(
        tag_name="web-browser-takeover",
        mappings=[
            {"param_name": "text", "node_type": "content", "path": "."},
            {"param_name": "attachments", "node_type": "attribute", "path": ".", "required": False}
        ],
        example='''
        <!-- Use web-browser-takeover when automated tools cannot handle the page interaction -->
        <!-- Examples of when takeover is needed: -->
        <!-- 1. CAPTCHA or human verification required -->
        <!-- 2. Anti-bot measures preventing access -->
        <!-- 3. Authentication requiring human input -->

        <web-browser-takeover>
            I've encountered a CAPTCHA verification on the page. Please:
            1. Solve the CAPTCHA puzzle
            2. Let me know once you've completed it
            3. I'll then continue with the automated process

            If you encounter any issues or need to take additional steps, please let me know.
        </web-browser-takeover>
        '''
    )
    async def web_browser_takeover(self, text: str, attachments: Optional[Union[str, List[str]]] = None) -> Dict[str, Any]:
        """Request user takeover of browser interaction.

        Args:
            text: Instructions for the user about what actions to take.
            attachments: Optional file paths or URLs to attach to the request.

        Returns:
            A dictionary indicating the action status.
        Raises:
            ValueError: If text is not provided.
            MessageToolError: For other errors.
        """
        if not text or not isinstance(text, str):
            raise ValueError("Takeover instructions text must be provided.")
        try:
            output_payload = {"text": text, "status": "Awaiting user browser takeover..."}
            if attachments:
                if isinstance(attachments, str):
                    output_payload["attachments"] = [attachments]
                else:
                    output_payload["attachments"] = attachments
            return output_payload
        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error in 'web_browser_takeover' tool: {str(e)}", exc_info=True)
            raise MessageToolError(f"Error requesting browser takeover: {str(e)}") from e

#     @openapi_schema({
#         "type": "function",
#         "function": {
#             "name": "inform",
#             "description": "Inform the user about progress, completion of a major step, or important context. Use this tool: 1) To provide updates between major sections of work, 2) After accomplishing significant milestones, 3) When transitioning to a new phase of work, 4) To confirm actions were completed successfully, 5) To provide context about upcoming steps. IMPORTANT: Use FREQUENTLY throughout execution to provide UI context to the user. The user CANNOT respond to this tool - they can only respond to the 'ask' tool. Use this tool to keep the user informed without requiring their input.",
#             "parameters": {
#                 "type": "object",
#                 "properties": {
#                     "text": {
#                         "type": "string",
#                         "description": "Information to present to the user. Include: 1) Clear statement of what has been accomplished or what is happening, 2) Relevant context or impact, 3) Brief indication of next steps if applicable."
#                     },
#                     "attachments": {
#                         "anyOf": [
#                             {"type": "string"},
#                             {"items": {"type": "string"}, "type": "array"}
#                         ],
#                         "description": "(Optional) List of files or URLs to attach to the information. Include when: 1) Information relates to specific files or resources, 2) Showing intermediate results or outputs, 3) Providing supporting documentation. Always use relative paths to /workspace directory."
#                     }
#                 },
#                 "required": ["text"]
#             }
#         }
#     })
#     @xml_schema(
#         tag_name="inform",
#         mappings=[
#             {"param_name": "text", "node_type": "content", "path": "."},
#             {"param_name": "attachments", "node_type": "attribute", "path": ".", "required": False}
#         ],
#         example='''

# Inform the user about progress, completion of a major step, or important context. Use this tool: 1) To provide updates between major sections of work, 2) After accomplishing significant milestones, 3) When transitioning to a new phase of work, 4) To confirm actions were completed successfully, 5) To provide context about upcoming steps. IMPORTANT: Use FREQUENTLY throughout execution to provide UI context to the user. The user CANNOT respond to this tool - they can only respond to the 'ask' tool. Use this tool to keep the user informed without requiring their input."

#         <!-- Use inform FREQUENTLY to provide UI context and progress updates - THE USER CANNOT RESPOND to this tool -->
#         <!-- The user can ONLY respond to the ask tool, not to inform -->
#         <!-- Examples of when to use inform: -->
#         <!-- 1. Completing major milestones -->
#         <!-- 2. Transitioning between work phases -->
#         <!-- 3. Confirming important actions -->
#         <!-- 4. Providing context about upcoming steps -->
#         <!-- 5. Sharing significant intermediate results -->
#         <!-- 6. Providing regular UI updates throughout execution -->

#         <inform attachments="analysis_results.csv,summary_chart.png">
#             I've completed the data analysis of the sales figures. Key findings include:
#             - Q4 sales were 28% higher than Q3
#             - Product line A showed the strongest performance
#             - Three regions missed their targets

#             I'll now proceed with creating the executive summary report based on these findings.
#         </inform>
#         '''
#     )
#     async def inform(self, text: str, attachments: Optional[Union[str, List[str]]] = None) -> ToolResult:
#         """Inform the user about progress or important updates without requiring a response.

#         Args:
#             text: The information to present to the user
#             attachments: Optional file paths or URLs to attach

#         Returns:
#             ToolResult indicating the information was successfully sent
#         """
#         try:
#             # Convert single attachment to list for consistent handling
#             if attachments and isinstance(attachments, str):
#                 attachments = [attachments]

#             return self.success_response({"status": "Information sent"})
#         except Exception as e:
#             return self.fail_response(f"Error informing user: {str(e)}")

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "complete",
            "description": "A special tool to indicate you have completed all tasks and are about to enter complete state. Use ONLY when: 1) All tasks in todo.md are marked complete [x], 2) The user's original request has been fully addressed, 3) There are no pending actions or follow-ups required, 4) You've delivered all final outputs and results to the user. IMPORTANT: This is the ONLY way to properly terminate execution. Never use this tool unless ALL tasks are complete and verified. Always ensure you've provided all necessary outputs and references before using this tool.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    })
    @xml_schema(
        tag_name="complete",
        mappings=[],
        example='''
        <!-- Use complete ONLY when ALL tasks are finished -->
        <!-- Prerequisites for using complete: -->
        <!-- 1. All todo.md items marked complete [x] -->
        <!-- 2. User's original request fully addressed -->
        <!-- 3. All outputs and results delivered -->
        <!-- 4. No pending actions or follow-ups -->
        <!-- 5. All tasks verified and validated -->

        <complete>
        <!-- This tool indicates successful completion of all tasks -->
        <!-- The system will stop execution after this tool is used -->
        </complete>
        '''
    )
    async def complete(self) -> Dict[str, Any]:
        """Indicate that the agent has completed all tasks and is entering complete state.

        Returns:
            A dictionary indicating the action status.
        Raises:
            MessageToolError: For errors during completion signal.
        """
        try:
            # This output will be passed to tool_instance.success_response by the orchestrator
            return {"status": "complete"}
        except Exception as e:
            logging.error(f"Error in 'complete' tool: {str(e)}", exc_info=True)
            raise MessageToolError(f"Error entering complete state: {str(e)}") from e


if __name__ == "__main__":
    # The __main__ block needs to be updated if direct testing is desired,
    # as the methods now return dicts instead of ToolResult objects directly.
    # import asyncio
    #
    # async def test_message_tool():
    #     message_tool = MessageTool() # This tool doesn't require project_id or thread_manager
    #
    #     # Test question
    #     try:
    #         ask_output = await message_tool.ask(
    #             text="Would you like to proceed with the next phase?",
    #             attachments="summary.pdf"
    #         )
    #         # In real scenario, orchestrator would create ToolResult from this dict
    #         print("Ask output data:", ask_output)
    #     except Exception as e:
    #         print("Ask failed:", e)
    #
    #     # Test complete
    #     try:
    #         complete_output = await message_tool.complete()
    #         print("Complete output data:", complete_output)
    #     except Exception as e:
    #         print("Complete failed:", e)
    #
    # if __name__ == "__main__":
    #     asyncio.run(test_message_tool())
    pass
