from typing import List, Optional, Dict, Any # Removed BaseModel, Field, ValidationError here, will add them with Pydantic models
import json
# Pydantic models will be defined below
from pydantic import BaseModel, Field, ValidationError

from agent.prompt import get_system_prompt
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
        context: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]: # Return type is a list of dicts for subtasks
        logger.debug(f"TASK_PLANNER: Decomposing task: {task_description}")

        available_tools_str = "[]"
        try:
            tool_schemas = self.tool_orchestrator.get_tool_schemas_for_llm()
            tool_identifiers = [schema['name'] for schema in tool_schemas if 'name' in schema]
            if not tool_identifiers:
                logger.warning("TASK_PLANNER: No tool identifiers found from get_tool_schemas_for_llm(). LLM will not be shown available tools.")
                available_tools_str = "No tools available. Use 'SystemCompleteTask' for simple tasks."
            else:
                available_tools_str = json.dumps(tool_identifiers)
        except Exception as e_tools:
            logger.warning(f"Could not get tool schemas for system prompt: {e_tools}")
            available_tools_str = "Error retrieving tools. Use 'SystemCompleteTask' for simple tasks."

        SYSTEM_PROMPT = f"""
        Eres un planificador de tareas experto. Tu objetivo es descomponer una tarea principal en una secuencia de subtareas ejecutables por un agente de IA.
        Debes devolver SIEMPRE un objeto JSON válido con una única clave "plan", que contiene una lista de subtareas.
        Cada subtarea en la lista debe ser un objeto con las siguientes claves: "tool_identifier" (string, en formato ToolID__methodName) y "thought" (string, una descripción de la subtarea).

        Ejemplo de salida esperada:
        {{
        "plan": [
        {{
        "tool_identifier": "WebSearchTool__search_web",
        "thought": "Buscar en internet los mejores hoteles en Valencia."
        }},
        {{
        "tool_identifier": "DatabaseTool__query_data",
        "thought": "Consultar la base de datos de clientes para obtener información sobre Valencia."
        }}
        ]
        }}

        No incluyas absolutamente ningún texto fuera del objeto JSON. La respuesta debe ser solo el JSON.
        Las herramientas disponibles (tool_identifier) son: {available_tools_str}.
        Si la tarea es muy simple y puede ser respondida directamente sin herramientas (ej. "hola"), o si ninguna herramienta parece adecuada, puedes devolver un plan con una única tarea usando la herramienta "SystemCompleteTask__task_complete" y el "thought" conteniendo la respuesta o resumen.
        Asegúrate que el "tool_identifier" que elijas exista EXACTAMENTE en la lista de herramientas disponibles.
        """

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
                    "content": "Your previous response had a formatting or Pydantic validation error. Please strictly adhere to the output format: A valid JSON object with a single key 'plan', which is a list of objects. Each object in the 'plan' list must have 'tool_identifier' (string, format: ToolID__methodName) and 'thought' (string). Do not include any text outside the JSON object itself."
                }
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
                    logger.debug(f"TASK_PLANNER: Raw LLM response that failed parsing (Attempt {attempts + 1}):\n---\n{llm_response_content}\n---")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error(f"TASK_PLANNER: Max retries reached. Final JSON parsing failed. LLM Raw Response: '{llm_response_content}'")
                        return []
                    continue

                validated_plan: PlanResponse
                try:
                    validated_plan = PlanResponse.parse_obj(parsed_json_response)
                except ValidationError as e_val:
                    logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: Pydantic validation failed: {e_val}. Response: '{parsed_json_response}'")
                    attempts += 1
                    if attempts > max_retries:
                        logger.error(f"TASK_PLANNER: Max retries reached. Pydantic validation failed. Last error: {e_val}")
                        return []
                    continue

                plan_steps_data = validated_plan.plan # Now a list of SubtaskDefinition objects

                subtasks_for_creation = []
                if not plan_steps_data: # Handle empty plan list from LLM
                    logger.warning(f"TASK_PLANNER: Attempt {attempts+1}: LLM returned an empty 'plan' list.")
                    # It might be valid for an LLM to return an empty plan if the task is unplannable
                    # or requires no action. For now, we treat this as success with zero tasks.
                    # If this should be an error, then set attempts and continue.
                    # For now, let's assume SystemCompleteTask should be used by LLM in such cases.
                    # If the prompt guides it to use SystemCompleteTask for very simple tasks,
                    # an empty plan might indicate an LLM failure to follow instructions.
                    # Let's retry if plan is empty.
                    attempts +=1
                    if attempts > max_retries:
                        logger.error("TASK_PLANNER: Max retries reached. LLM returned empty plan.")
                        return []
                    continue


                for i, step_model in enumerate(plan_steps_data): # step_model is SubtaskDefinition
                    # Truncate name for display, ensuring it's not too long
                    base_name = step_model.thought
                    max_name_len = 100 # Define a reasonable max length for task names
                    if len(base_name) > max_name_len:
                        mapped_name = base_name[:max_name_len-3] + "..."
                    else:
                        mapped_name = base_name

                    subtask_dict = {
                        "name": mapped_name,
                        "description": step_model.thought, # Full thought for description
                        "dependencies": [],
                        "assigned_tools": [step_model.tool_identifier] if step_model.tool_identifier else [],
                        # No llm_parameters or tool_input in metadata from planner anymore
                    }
                    subtasks_for_creation.append(subtask_dict)

                if not subtasks_for_creation and plan_steps_data:
                    # This case should ideally be caught by Pydantic validation if individual steps are malformed
                    # or if the plan list itself is present but items are invalid.
                    # Given Pydantic validation passes for PlanResponse, this means plan_steps_data was a list of valid SubtaskDefinition.
                    # So, if subtasks_for_creation is empty, it means plan_steps_data was empty.
                    # This is handled by the check above `if not plan_steps_data:`.
                    # This block might be redundant or indicate a logic flaw if reached.
                    logger.warning(f"TASK_PLANNER: Attempt {attempts + 1}: No subtasks were mapped, though validated plan data existed. Original plan had {len(plan_steps_data)} steps.")
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

                # Parameters are no longer generated by the planner.
                # Metadata will be empty unless other contextual info needs to be added here.
                current_metadata = {}

                subtask = await self.task_manager.create_task(
                    name=sub_name,
                    description=sub_description,
                    parentId=main_task.id,
                    dependencies=current_dependencies,
                    assignedTools=assigned_tools_names,
                    status="pending",
                    metadata=current_metadata # Empty or context-specific, no llm_parameters
                )
                if subtask:
                    # created_subtask_ids_in_order.append(subtask.id) # For linear dependency if implemented above
                    logger.debug(f"Created subtask '{sub_name}' (ID: {subtask.id}) for main task {main_task.id}")
                else:
                    logger.error(f"Failed to create subtask '{sub_name}' for main task {main_task.id}. Halting planning.")
                    # Update the main task to reflect planning failure due to subtask creation issue
                    await self.task_manager.update_task(
                        main_task.id,
                        {"status": "planning_failed", "error": f"Failed to create necessary subtask: {sub_name}"}
                    )
                    return main_task # Return the main task, now marked as failed

            # Update main task status after planning if all subtasks were created successfully
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
