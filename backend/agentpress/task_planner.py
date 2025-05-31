from typing import List, Optional, Dict, Any
import json

from agentpress.task_state_manager import TaskStateManager
from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.task_types import TaskState # For type hinting
from services.llm import make_llm_api_call # Assuming this is the correct way to call LLM
from utils.logger import logger

class TaskPlanner:
    """
    Handles the decomposition of high-level tasks into smaller, manageable subtasks
    and coordinates with TaskStateManager to create and link these tasks.
    """

    def __init__(self, task_manager: TaskStateManager, tool_orchestrator: ToolOrchestrator):
        """
        Initializes the TaskPlanner.

        Args:
            task_manager: An instance of TaskStateManager to manage task states.
            tool_orchestrator: An instance of ToolOrchestrator to access available tools.
        """
        self.task_manager = task_manager
        self.tool_orchestrator = tool_orchestrator
        logger.info("TaskPlanner initialized.")

    async def _decompose_task(
        self,
        task_description: str,
        available_tools: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Decomposes a task description into a list of subtasks using an LLM.

        Args:
            task_description: The high-level description of the task to decompose.
            available_tools: A list of available tool schemas for the LLM to consider.
            context: Optional context to provide to the LLM.

        Returns:
            A list of dictionaries, where each dictionary represents a subtask
            and includes fields like 'name', 'description', 'dependencies' (list of indices),
            and 'assigned_tools' (list of tool names).
        """
        logger.debug(f"Decomposing task: {task_description}")

        formatted_tools = "\n".join([
            f"- {tool.get('name')}: {tool.get('description', 'No description')}"
            for tool in available_tools
        ])
        if not formatted_tools:
            formatted_tools = "No specific tools seem immediately available for this task, but you can still define subtasks that might require general capabilities."

        # Constructing the prompt for the LLM
        # The LLM should output a JSON list of subtasks.
        prompt_messages = [
            {
                "role": "system",
                "content": f"""\
You are an expert task planner. Your role is to break down a given task description into a list of smaller, actionable subtasks.
For each subtask, provide a name, a detailed description, a list of tools (from the provided list) that might be useful for this subtask, and a list of dependencies on other subtasks you are defining in this list (using their 0-based index).

Available tools:
Each tool is listed with its unique identifier (in the format ToolID__methodName) followed by its description. When assigning tools to a subtask, you MUST use these exact unique identifiers.
{formatted_tools}

Please output your plan as a JSON array of objects. Each object should have the following fields:
- "name": A concise name for the subtask (string).
- "description": A detailed step-by-step description of what needs to be done for this subtask (string).
- "dependencies": A list of 0-based indices of other subtasks in this plan that this subtask depends on. An empty list means no dependencies within this plan (List[int]).
- "assigned_tools": A list of unique tool identifiers (e.g., ToolID__methodName, from the 'Available tools' list) that are most relevant for this subtask. If no specific tool is relevant, provide an empty list. (List[str]).

Example JSON output for a task "Develop a new feature X":
[
  {{
    "name": "Design feature X",
    "description": "Create detailed design documents and mockups for feature X.",
    "dependencies": [],
    "assigned_tools": ["GraphicsGenerator__create_mockup", "CollaborationSuite__share_document"]
  }},
  {{
    "name": "Implement backend for feature X",
    "description": "Develop the server-side logic and APIs for feature X.",
    "dependencies": [0],
    "assigned_tools": ["CodeRepository__commit_changes", "APIDevelopmentTool__create_endpoint"]
  }},
  {{
    "name": "Implement frontend for feature X",
    "description": "Develop the user interface for feature X.",
    "dependencies": [0, 1],
    "assigned_tools": ["CodeRepository__commit_changes", "UIFramework__generate_component"]
  }}
]
Ensure the output is a valid JSON array.
"""
            },
            {
                "role": "user",
                "content": f"Please decompose the following task into subtasks:\n\nTask Description: {task_description}\n\n{('Context: ' + json.dumps(context)) if context else ''}"
            }
        ]

        try:
            # TODO: Determine appropriate model, temperature, max_tokens for planning.
            # Using a capable model is important for good decomposition.
            llm_response_str = await make_llm_api_call(
                messages=prompt_messages,
                llm_model="gpt-4o", # Or another suitable model like gpt-3.5-turbo
                temperature=0.2, # Lower temperature for more deterministic planning
                max_tokens=2048, # Adjust as needed
                # Not passing tools directly for function calling here, expecting JSON response.
                stream=False, # Expecting a single JSON output
                json_mode=True # Request JSON mode if supported by make_llm_api_call and model
            )

            # The make_llm_api_call might return a dict/object, or a raw string.
            # Assuming it returns something from which we can extract the text content.
            # If it's already a dict because of json_mode=True, this might be simpler.

            # For now, assuming llm_response_str is the string content of the LLM's message.
            # If make_llm_api_call returns a complex object:
            if isinstance(llm_response_str, dict) and 'choices' in llm_response_str and llm_response_str['choices']:
                 response_content = llm_response_str['choices'][0].get('message', {}).get('content', '')
            elif isinstance(llm_response_str, str): # Direct string response
                 response_content = llm_response_str
            else: # Fallback for unexpected structure (e.g. LiteLLM object)
                if hasattr(llm_response_str, 'choices') and llm_response_str.choices and \
                   hasattr(llm_response_str.choices[0], 'message') and \
                   hasattr(llm_response_str.choices[0].message, 'content'):
                    response_content = llm_response_str.choices[0].message.content
                else:
                    logger.error(f"Unexpected LLM response structure: {type(llm_response_str)}")
                    return []


            logger.debug(f"LLM response for task decomposition:\n{response_content}")

            # Attempt to parse the JSON response
            # The LLM might sometimes include markdown ```json ... ``` around the JSON.
            if response_content.strip().startswith("```json"):
                response_content = response_content.strip()[7:-3].strip()
            elif response_content.strip().startswith("```"): # Generic markdown block
                response_content = response_content.strip()[3:-3].strip()

            subtasks_data = json.loads(response_content)

            if not isinstance(subtasks_data, list):
                logger.error(f"LLM did not return a list of subtasks. Got: {type(subtasks_data)}")
                # Potentially try to wrap it in a list if it's a single dict?
                return []

            # Basic validation of subtask structure (can be enhanced)
            valid_subtasks = []
            for i, st_data in enumerate(subtasks_data):
                if isinstance(st_data, dict) and "name" in st_data and "description" in st_data:
                    # Ensure dependencies and assigned_tools are lists
                    st_data["dependencies"] = st_data.get("dependencies", [])
                    if not isinstance(st_data["dependencies"], list): st_data["dependencies"] = []

                    st_data["assigned_tools"] = st_data.get("assigned_tools", [])
                    if not isinstance(st_data["assigned_tools"], list): st_data["assigned_tools"] = []

                    valid_subtasks.append(st_data)
                else:
                    logger.warning(f"Subtask at index {i} has invalid structure: {st_data}")

            return valid_subtasks

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from LLM response for task decomposition: {e}")
            logger.error(f"LLM Raw Response was: {response_content}")
            return []
        except Exception as e:
            logger.error(f"Error during LLM call for task decomposition: {e}", exc_info=True)
            return []


    async def plan_task(
        self,
        task_description: str,
        context: Optional[Dict[str, Any]] = None
    ) -> Optional[TaskState]:
        """
        Plans a given task description by creating a main task and decomposing it into subtasks.

        Args:
            task_description: The high-level description of the task.
            context: Optional context for the planning process.

        Returns:
            The main TaskState object with its subtasks linked, or None if planning failed.
        """
        logger.info(f"Planning task: {task_description}")

        try:
            # 1. Create the main task
            main_task_name = f"Main plan for: {task_description[:50]}" + ("..." if len(task_description) > 50 else "")
            main_task = await self.task_manager.create_task(
                name=main_task_name,
                description=f"Overall task: {task_description}",
                status="pending_planning" # A custom status to indicate planning is in progress
            )
            if not main_task:
                logger.error("Failed to create main task.")
                return None

            logger.debug(f"Main task created with ID: {main_task.id}")

            # 2. Get available tools for the LLM to consider (OpenAPI schemas)
            # Use get_tool_schemas_for_llm which is formatted for this purpose.
            available_tools_schemas = self.tool_orchestrator.get_tool_schemas_for_llm()

            # 3. Decompose the task into subtasks
            subtasks_data_list = await self._decompose_task(task_description, available_tools_schemas, context)

            if not subtasks_data_list:
                logger.warning(f"LLM failed to decompose task or returned no subtasks for: {task_description}")
                await self.task_manager.update_task(main_task.id, {"status": "planning_failed", "error": "No subtasks generated."})
                return main_task # Return main task with failed status

            # 4. Create and link subtasks
            created_subtask_ids_in_order = [] # To resolve dependencies by index

            for subtask_data in subtasks_data_list:
                sub_name = subtask_data.get("name", "Unnamed Subtask")
                sub_description = subtask_data.get("description", "")
                dependency_indices = subtask_data.get("dependencies", [])
                assigned_tools_names = subtask_data.get("assigned_tools", [])

                # Resolve dependency indices to actual task IDs
                actual_dependency_ids = []
                for dep_index in dependency_indices:
                    if 0 <= dep_index < len(created_subtask_ids_in_order):
                        actual_dependency_ids.append(created_subtask_ids_in_order[dep_index])
                    else:
                        logger.warning(f"Invalid dependency index {dep_index} for subtask '{sub_name}'. Max index: {len(created_subtask_ids_in_order)-1}")

                subtask = await self.task_manager.create_task(
                    name=sub_name,
                    description=sub_description,
                    parentId=main_task.id, # Link to main task
                    dependencies=actual_dependency_ids,
                    assignedTools=assigned_tools_names, # Store names for now
                    status="pending" # Default status for new subtasks
                )
                if subtask:
                    created_subtask_ids_in_order.append(subtask.id)
                    logger.debug(f"Created subtask '{sub_name}' (ID: {subtask.id}) for main task {main_task.id}")
                else:
                    logger.error(f"Failed to create subtask '{sub_name}' for main task {main_task.id}")
                    # Decide on error handling: stop planning, or continue?
                    # For now, continue creating other subtasks.

            # Update main task status after planning
            # The main_task.subtasks list is updated by create_task within TaskStateManager when parentId is set.
            # We might need to refresh main_task from task_manager to get the most up-to-date subtasks list.
            refreshed_main_task = await self.task_manager.get_task(main_task.id)
            if refreshed_main_task:
                 await self.task_manager.update_task(
                    main_task.id,
                    {"status": "planned", "progress": 0.1} # Or some other appropriate status/progress
                )
                 logger.info(f"Task planning completed for '{task_description}'. Main task ID: {main_task.id} with {len(refreshed_main_task.subtasks)} subtasks.")
                 return refreshed_main_task
            else: # Should not happen if main_task was created
                logger.error(f"Could not retrieve main task {main_task.id} after planning subtasks.")
                return None

        except Exception as e:
            logger.error(f"Error during task planning for '{task_description}': {e}", exc_info=True)
            if 'main_task' in locals() and main_task:
                try:
                    await self.task_manager.update_task(main_task.id, {"status": "planning_failed", "error": str(e)})
                except Exception as e_update:
                    logger.error(f"Additionally failed to update main task status after planning error: {e_update}")
            return None

# Example Usage (Conceptual - would require async setup and instances)
# async def example():
#     # Presuming task_manager and tool_orchestrator are initialized
#     # from backend.agentpress.task_state_manager import TaskStateManager
#     # from backend.agentpress.task_storage_supabase import SupabaseTaskStorage
#     # from backend.agentpress.tool_orchestrator import ToolOrchestrator
#
#     # storage = SupabaseTaskStorage() # Needs DB connection setup
#     # task_mgr = TaskStateManager(storage)
#     # await task_mgr.initialize()
#     # tool_orch = ToolOrchestrator()
#     # Example: tool_orch.load_tools_from_directory()
#
#     # planner = TaskPlanner(task_mgr, tool_orch)
#     # main_planned_task = await planner.plan_task(
#     #     task_description="Develop and launch a new marketing campaign for product Y.",
#     #     context={"product_details": "Product Y is a new gadget for home automation."}
#     # )
#
#     # if main_planned_task:
#     #     print(f"Main task '{main_planned_task.name}' (ID: {main_planned_task.id}) planned with status: {main_planned_task.status}")
#     #     subtasks = await task_mgr.get_subtasks(main_planned_task.id)
#     #     for sub in subtasks:
#     #         print(f"  Subtask: {sub.name} (ID: {sub.id}), Depends on: {sub.dependencies}, Tools: {sub.assignedTools}")
#
# if __name__ == "__main__":
#    pass # Add asyncio.run(example()) if you want to test this standalone with proper setup.
