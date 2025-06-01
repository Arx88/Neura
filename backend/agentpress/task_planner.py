from typing import List, Optional, Dict, Any
import json
from pydantic import BaseModel, Field, ValidationError # Added imports

from agent.prompt import get_system_prompt # Added import
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
    dependencies: List[int] = Field(default_factory=list) # Kept for SubtaskDecompositionItem structure, but new prompt won't generate indices.
    assigned_tools: List[str] = Field(default_factory=list)

    async def _decompose_task(
        self,
        task_description: str,
        context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]: # Return type is a list of dicts for subtasks
        logger.debug(f"TASK_PLANNER: Decomposing task using global SYSTEM_PROMPT: {task_description}")

        SYSTEM_PROMPT = get_system_prompt() # Get the global system prompt

        # The user message is now simpler as the SYSTEM_PROMPT handles the main instruction
        user_message_content = f"Task Description: {task_description}"
        if context:
            user_message_content += f"\nContext: {json.dumps(context)}"

        prompt_messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_message_content}
        ]

        max_retries = 2
        attempts = 0
        llm_response_content = ""

        while attempts <= max_retries:
            current_prompt_messages = list(prompt_messages) # Use a copy
            if attempts > 0:
                retry_guidance = {
                    "role": "system",
                    "content": "Your previous response had a formatting or validation error. Please strictly adhere to the output format: A valid JSON object with a single key 'plan', which is a list of objects. Each object in the 'plan' list must have 'tool_code' (string), 'thought' (string), and 'parameters' (object). Do not include any text outside the JSON object itself."
                }
                # Insert retry guidance after the main system prompt
                current_prompt_messages.insert(1, retry_guidance)

            logger.info(f"TASK_PLANNER: Attempt {attempts + 1}/{max_retries + 1} to generate plan for: {task_description}")

            try:
                llm_response_obj = await make_llm_api_call(
                    messages=current_prompt_messages,
                    model="gpt-4o", # Or the model specified in the new system prompt if different
                    temperature=0.1,
                    max_tokens=2048, # Adjust if necessary
                    stream=False,
                    json_mode=True # Crucial for ensuring JSON output
                )

                # Standard response extraction
                if isinstance(llm_response_obj, dict) and 'choices' in llm_response_obj and llm_response_obj['choices']:
                    llm_response_content = llm_response_obj['choices'][0].get('message', {}).get('content', '')
                elif isinstance(llm_response_obj, str): # Added for direct string response
                    llm_response_content = llm_response_obj
                elif hasattr(llm_response_obj, 'choices') and llm_response_obj.choices and \
                     hasattr(llm_response_obj.choices[0], 'message') and \
                     hasattr(llm_response_obj.choices[0].message, 'content'): # Added for OpenAIObject-like response
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

                logger.debug(f"TASK_PLANNER: LLM plan response (Attempt {attempts + 1}):\n{llm_response_content}")

                cleaned_response_content = llm_response_content.strip()
                # No need to strip ```json anymore if json_mode=True works as expected,
                # but keep it as a fallback if issues are seen.
                if cleaned_response_content.startswith("```json"):
                    cleaned_response_content = cleaned_response_content[7:-3].strip()
                elif cleaned_response_content.startswith("```"):
                     cleaned_response_content = cleaned_response_content[3:-3].strip()

                if not cleaned_response_content:
                    logger.warning(f"TASK_PLANNER: LLM returned empty content after stripping markdown (Attempt {attempts + 1}).")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error("TASK_PLANNER: Max retries reached due to empty LLM response after markdown stripping.")
                        return []
                    continue

                parsed_json_response = None
                try:
                    parsed_json_response = json.loads(cleaned_response_content)
                except json.JSONDecodeError as e_json:
                    logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: Failed to parse JSON: {e_json}. Response: '{cleaned_response_content}'")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error(f"TASK_PLANNER: Max retries reached. Final JSON parsing failed. LLM Raw Response: '{llm_response_content}'")
                        return []
                    continue

                # Validate the structure {"plan": [...]}
                if not isinstance(parsed_json_response, dict) or "plan" not in parsed_json_response or not isinstance(parsed_json_response.get("plan"), list):
                    logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: LLM response is not a dict with a 'plan' list. Got: {type(parsed_json_response)}. Data: '{parsed_json_response}'")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error("TASK_PLANNER: Max retries reached. LLM response not a dict with 'plan' list.")
                        return []
                    continue

                plan_steps_data = parsed_json_response["plan"]

                subtasks_for_creation = []
                for i, step in enumerate(plan_steps_data):
                    if not isinstance(step, dict) or not all(k in step for k in ["tool_code", "thought", "parameters"]):
                        logger.warning(f"TASK_PLANNER: Invalid step structure in plan (Attempt {attempts + 1}): {step}")
                        continue # Skip this invalid step

                    mapped_name = step.get("tool_code", f"Step {i+1}")
                    if step.get("thought"):
                        mapped_name += f": {step['thought'][:30]}" + ("..." if len(step['thought']) > 30 else "")

                    # Populate description only with thought or a default
                    mapped_description = step.get("thought")
                    if not mapped_description: # Handles None or empty string
                        mapped_description = "No specific thought provided."

                    # Get raw parameters for llm_parameters
                    llm_params = step.get("parameters")

                    subtask_dict = {
                        "name": mapped_name,
                        "description": mapped_description,
                        "dependencies": [], # New prompt does not ask for 0-indexed dependencies.
                                           # Actual dependencies will be handled by PlanExecutor or similar.
                        "assigned_tools": [step.get("tool_code")] if step.get("tool_code") else [],
                        "llm_parameters": llm_params # Add raw parameters here
                    }

                    try:
                        SubtaskDecompositionItem.parse_obj(subtask_dict) # Validate against Pydantic
                        subtasks_for_creation.append(subtask_dict)
                    except ValidationError as e_val_item:
                        logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: Pydantic validation failed for mapped step: {e_val_item}. Original step: '{step}', Mapped: '{subtask_dict}'")

                if not subtasks_for_creation and plan_steps_data:
                    logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: All plan steps failed validation after mapping. Original plan had {len(plan_steps_data)} steps.")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error("TASK_PLANNER: Max retries reached. All plan steps failed validation.")
                        return []
                    continue

                logger.info(f"TASK_PLANNER: Successfully parsed and mapped {len(subtasks_for_creation)} steps from LLM plan (Attempt {attempts + 1}).")
                return subtasks_for_creation # Success

            except Exception as e_main:
                logger.error(f"TASK_PLANNER: Attempt {attempts + 1}: Unexpected error during plan generation: {e_main}", exc_info=True)
                attempts += 1
                if attempts > max_retries:
                    logger.error(f"TASK_PLANNER: Max retries reached. Last error: {e_main}")
                    return []

        logger.error("TASK_PLANNER: Exhausted all retries for plan generation.")
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

            # 2. Decompose the task into subtasks
            # available_tools_schemas is removed as per new _decompose_task signature
            subtasks_data_list = await self._decompose_task(task_description, context)

            if not subtasks_data_list:
                logger.warning(f"LLM failed to decompose task or returned no subtasks for: {task_description}")
                await self.task_manager.update_task(main_task.id, {"status": "planning_failed", "error": "No subtasks generated."})
                return main_task # Return main task with failed status

            # 3. Create and link subtasks
            # created_subtask_ids_in_order is no longer needed for 0-indexed dependencies from planner
            # Dependencies will be handled by a different mechanism if needed (e.g. linear, or by PlanExecutor)

            for subtask_data in subtasks_data_list:
                # Validated by SubtaskDecompositionItem during _decompose_task mapping
                sub_name = subtask_data["name"]
                sub_description = subtask_data["description"]
                # dependencies list from subtask_data is now empty based on current mapping.
                # If a linear dependency is desired, it could be added here:
                # current_dependencies = [created_subtask_ids_in_order[-1]] if created_subtask_ids_in_order else []
                current_dependencies = [] # Defaulting to no dependencies as per current mapping logic.
                assigned_tools_names = subtask_data["assigned_tools"]

                # Retrieve llm_parameters and construct metadata
                llm_params = subtask_data.get("llm_parameters")
                current_metadata = {"tool_input": llm_params}

                subtask = await self.task_manager.create_task(
                    name=sub_name,
                    description=sub_description,
                    parentId=main_task.id,
                    dependencies=current_dependencies, # Using the potentially linear dependency
                    assignedTools=assigned_tools_names,
                    status="pending",
                    metadata=current_metadata # Pass metadata here
                )
                if subtask:
                    # created_subtask_ids_in_order.append(subtask.id) # For linear dependency if implemented above
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
