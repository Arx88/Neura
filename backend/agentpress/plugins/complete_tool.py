from ...agentpress.tool import Tool, ToolResult, openapi_schema
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class CompleteTool(Tool):
    PLUGIN_TOOL_ID = "SystemCompleteTask" # A distinct ID for this system tool

    @openapi_schema({
        "name": "task_complete",
        "description": "Use this tool to signal that the entire user request has been successfully completed. Call this only when all objectives have been met and verified. Provide a final summary message to the user.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "A brief summary of how the task was completed and the final outcome."
                }
            },
            "required": ["summary"]
        }
    })
    async def task_complete(self, summary: str) -> Dict[str, Any]: # Returns raw data
        logger.info(f"TASK_COMPLETE tool called by agent with summary: {summary}")
        # This tool doesn't perform an external action but signals completion.
        # The PlanExecutor will look for this tool's successful execution.
        return {
            "status": "success", # Indicates the tool itself executed successfully
            "message": "Task marked as complete by agent.",
            "summary": summary
        }
