from typing import List, Optional, Dict, Any
import json
from pydantic import BaseModel, Field, ValidationError # Added imports

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

# Pydantic model for subtask structure
class SubtaskDecompositionItem(BaseModel):
    name: str
    description: str
    dependencies: List[int] = Field(default_factory=list)
    assigned_tools: List[str] = Field(default_factory=list)

    async def _decompose_task(
        self,
        task_description: str,
        available_tools: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        logger.debug(f"TASK_PLANNER: Decomposing task: {task_description}")

        formatted_tools = "\n".join([
            f"- {tool.get('name')}: {tool.get('description', 'No description')}"
            for tool in available_tools
        ])
        if not formatted_tools:
            formatted_tools = "No specific tools seem immediately available for this task, but you can still define subtasks that might require general capabilities."

        base_prompt_messages = [
            {
                "role": "system",
                "content": f"""\
You are an expert task planner. Your role is to break down a given task description into a list of smaller, actionable subtasks.
For each subtask, provide a name, a detailed description, a list of tools (from the provided list) that might be useful for this subtask, and a list of dependencies on other subtasks you are defining in this list (using their 0-based index).

Available tools:
Each tool is listed with its unique identifier (in the format ToolID__methodName) followed by its description. When assigning tools to a subtask, you MUST use these exact unique identifiers.
{formatted_tools}

Please output your plan as a JSON array of objects. Each object MUST have the following fields:
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
Ensure the output is ONLY a valid JSON array of objects adhering to this schema. Do not include any explanatory text before or after the JSON array.
"""
            },
            {
                "role": "user",
                "content": f"Please decompose the following task into subtasks:\n\nTask Description: {task_description}\n\n{('Context: ' + json.dumps(context)) if context else ''}"
            }
        ]

        max_retries = 2
        attempts = 0
        llm_response_content = "" # Ensure llm_response_content is defined for logging in case of early error

        while attempts <= max_retries:
            prompt_messages_for_attempt = list(base_prompt_messages) # Make a copy for this attempt
            if attempts > 0:
                # Add a system message to guide the LLM on retry
                retry_guidance = {
                    "role": "system",
                    "content": "Your previous response had a formatting or validation error. Please strictly adhere to the output format: A valid JSON array of objects, where each object has 'name' (string), 'description' (string), 'dependencies' (list of integers), and 'assigned_tools' (list of strings). Do not include any text outside the JSON array itself."
                }
                # Insert it after the initial system prompt
                prompt_messages_for_attempt.insert(1, retry_guidance)

            logger.info(f"TASK_PLANNER: Attempt {attempts + 1}/{max_retries + 1} to decompose task: {task_description}")

            try:
                llm_response_obj = await make_llm_api_call(
                    messages=prompt_messages_for_attempt,
                    model="gpt-4o",
                    temperature=0.1, # Slightly lower for more consistent JSON
                    max_tokens=2048,
                    stream=False,
                    json_mode=True
                )

                # Extract content based on expected structure (consistent with previous implementation)
                if isinstance(llm_response_obj, dict) and 'choices' in llm_response_obj and llm_response_obj['choices']:
                    llm_response_content = llm_response_obj['choices'][0].get('message', {}).get('content', '')
                elif isinstance(llm_response_obj, str):
                    llm_response_content = llm_response_obj
                elif hasattr(llm_response_obj, 'choices') and llm_response_obj.choices and \
                     hasattr(llm_response_obj.choices[0], 'message') and \
                     hasattr(llm_response_obj.choices[0].message, 'content'):
                    llm_response_content = llm_response_obj.choices[0].message.content
                else:
                    logger.error(f"TASK_PLANNER: Unexpected LLM response structure (Attempt {attempts + 1}): {type(llm_response_obj)}")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error("TASK_PLANNER: Max retries reached due to unexpected LLM response structure.")
                        return []
                    continue

                if not llm_response_content or not llm_response_content.strip():
                    logger.warning(f"TASK_PLANNER: LLM returned empty content (Attempt {attempts + 1}).")
                    attempts += 1
                    if attempts > max_retries:
                         logger.error("TASK_PLANNER: Max retries reached due to empty LLM response.")
                         return []
                    continue

                logger.debug(f"TASK_PLANNER: LLM response for task decomposition (Attempt {attempts + 1}):\n{llm_response_content}")

                # Clean potential markdown ```json ... ```
                cleaned_response_content = llm_response_content.strip()
                if cleaned_response_content.startswith("```json"):
                    cleaned_response_content = cleaned_response_content[7:-3].strip()
                elif cleaned_response_content.startswith("```"):
                    cleaned_response_content = cleaned_response_content[3:-3].strip()

                if not cleaned_response_content: # Check again after stripping markdown
                    logger.warning(f"TASK_PLANNER: LLM returned empty content after stripping markdown (Attempt {attempts + 1}). Raw: {llm_response_content}")
                    attempts += 1
                    if attempts > max_retries:
                         logger.error("TASK_PLANNER: Max retries reached due to empty LLM response after markdown stripping.")
                         return []
                    continue

                try:
                    subtasks_data = json.loads(cleaned_response_content)
                except json.JSONDecodeError as e_json:
                    logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: Failed to parse JSON: {e_json}. Response: '{cleaned_response_content}'")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error(f"TASK_PLANNER: Max retries reached. Final JSON parsing failed. LLM Raw Response: '{llm_response_content}'")
                        return []
                    continue # Retry

                try:
                    # Ensure it's a list before Pydantic validation
                    if not isinstance(subtasks_data, list):
                        logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: LLM did not return a list. Got: {type(subtasks_data)}. Data: '{subtasks_data}'")
                        # Attempt to recover if it's a single dict that should have been a list
                        if isinstance(subtasks_data, dict) and "name" in subtasks_data and "description" in subtasks_data:
                             logger.info(f"TASK_PLANNER: Attempt {attempts + 1}: Wrapping single dictionary response in a list for validation.")
                             subtasks_data = [subtasks_data]
                        else: # Not a list and not a recoverable single dict
                            raise ValidationError([{"loc": "__root__", "msg": "Data is not a list of subtasks", "type": "type_error"}], model=SubtaskDecompositionItem)


                    validated_subtasks = [SubtaskDecompositionItem.parse_obj(item) for item in subtasks_data]
                    logger.info(f"TASK_PLANNER: Successfully parsed and validated {len(validated_subtasks)} subtasks from LLM (Attempt {attempts + 1}).")
                    return [item.dict() for item in validated_subtasks] # Success

                except ValidationError as e_val:
                    logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: Pydantic validation failed: {e_val}. Data: '{subtasks_data}'")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error(f"TASK_PLANNER: Max retries reached. Final Pydantic validation failed. LLM Parsed Data: '{subtasks_data}'")
                        return []
                    continue # Retry

            except Exception as e_main: # Catch any other unexpected errors during LLM call or processing
                logger.error(f"TASK_PLANNER: Attempt {attempts + 1}: Unexpected error during task decomposition: {e_main}", exc_info=True)
                attempts += 1
                if attempts > max_retries:
                    logger.error(f"TASK_PLANNER: Max retries reached. Last error: {e_main}")
                    return []
                # Optional: await asyncio.sleep(1) # Brief pause before retry

        logger.error("TASK_PLANNER: Exhausted all retries for task decomposition.")
        return [] # Should only be reached if all retries fail


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

            logger.debug(f"TaskPlanner: Available tool schemas for LLM planning ({len(available_tools_schemas)} total):")
            for schema in available_tools_schemas:
                logger.debug(f"  - Tool: {schema.get('name')}")
            if not available_tools_schemas:
                logger.debug("  - No tools available to the planner.")

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
