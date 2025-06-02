"""
Handles the execution of a pre-defined plan consisting of a main task and its subtasks.

The PlanExecutor iterates through subtasks, respecting their dependencies,
and uses an LLM to determine parameters for assigned tools, then executes them
via the ToolOrchestrator.
"""
# Module-level constants
PARAM_GENERATION_LLM_MODEL = "gpt-3.5-turbo-0125"
MAX_PARAM_GENERATION_RETRIES = 2 # Max number of retries, so 3 attempts total
SUMMARY_MAX_RESULT_LENGTH = 200


from typing import Optional, List, Dict, Any, AsyncGenerator, Callable, Tuple # Updated imports
import json
import uuid
import asyncio

from ..services import redis
from .task_state_manager import TaskStateManager
from .tool_orchestrator import ToolOrchestrator
from .api_models_tasks import TaskState
from .tool import ToolResult
from .utils.json_helpers import format_for_yield
from ..services.llm import make_llm_api_call
from ..utils.logger import logger

class PlanExecutor:
    """
    Orchestrates the execution of a task plan.

    This class takes a main task ID, retrieves its subtasks, and executes them
    sequentially based on dependencies. For each subtask, if tools are assigned,
    it uses an LLM to generate parameters for the first assigned tool and then
    executes it. It provides feedback via an optional callback and updates
    task statuses in the TaskStateManager.
    """

    def __init__(self,
                 main_task_id: str,
                 task_manager: TaskStateManager,
                 tool_orchestrator: ToolOrchestrator):
        """
        Initializes the PlanExecutor.

        Args:
            main_task_id (str): The ID of the main task representing the overall plan.
            task_manager (TaskStateManager): An instance for managing task states.
            tool_orchestrator (ToolOrchestrator): An instance for executing tools.
                                                 It is assumed that this orchestrator
                                                 has its tools loaded.
        """
        self.main_task_id = main_task_id
        self.task_manager = task_manager
        self.tool_orchestrator = tool_orchestrator
        # self.user_message_callback is removed
        logger.info(f"PlanExecutor initialized for main_task_id: {self.main_task_id}")

    async def _send_user_message(self, message_data: Dict[str, Any]):
        '''
        Sends a message to the client via Redis Pub/Sub.
        message_data should be a dictionary (e.g., from run_agent format).
        '''
        if not self.main_task_id:
            logger.warning("PLAN_EXECUTOR: main_task_id is not set, cannot send user message.")
            return

        response_list_key = f"agent_run:{self.main_task_id}:responses"
        response_channel = f"agent_run:{self.main_task_id}:new_response"

        try:
            # Ensure message_data has a 'type' for client processing consistency
            if 'type' not in message_data:
                message_data['type'] = 'plan_update' # Default type for plan executor messages

            # Add metadata if not present, specifically thread_run_id
            if 'metadata' not in message_data:
                message_data['metadata'] = {}
            if 'thread_run_id' not in message_data['metadata']:
                 message_data['metadata']['thread_run_id'] = self.main_task_id

            message_json = json.dumps(message_data)

            # Create tasks for Redis operations to run them concurrently
            await asyncio.gather(
                redis.rpush(response_list_key, message_json),
                redis.publish(response_channel, "new")
            )
            logger.debug(f"PLAN_EXECUTOR: Sent message to Redis channel {response_channel} for main_task_id {self.main_task_id}: {message_json}")

        except Exception as e:
            logger.error(f"PLAN_EXECUTOR: Error sending message via Redis for main_task_id {self.main_task_id}: {e}", exc_info=True)

    async def execute_plan_for_task(self, main_task_id: str):
        """
        Executes the plan associated with the provided `main_task_id`.

        This method orchestrates the execution of subtasks according to their
        dependencies. It updates task statuses and reports progress via logs
        and the user message callback. If any subtask fails, the overall plan
        is marked as failed.
        """
        logger.info(f"PLAN_EXECUTOR: Starting execution of plan for main_task_id: {main_task_id}")
        await self.task_manager.update_task(main_task_id, {"status": "running"})

        main_task = await self.task_manager.get_task(main_task_id)
        if not main_task:
            logger.error(f"PLAN_EXECUTOR: Main task {main_task_id} not found. Cannot execute plan.")
            # Note: _send_user_message uses self.main_task_id from __init__. This is acceptable if they are the same.
            error_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Error: Could not find the main plan task (ID: {main_task_id})."}, "metadata": {"thread_run_id": main_task_id}}
            await self._send_user_message(error_event) # self.main_task_id used here for channel
            await self.task_manager.update_task(main_task_id, {"status": "failed", "output": "Main task not found during execution."})
            return

        logger.debug(f"PLAN_EXECUTOR: Main task '{main_task.name}' (ID: {main_task.id}) fetched. Description: {main_task.description}")

        subtasks: List[TaskState] = await self.task_manager.get_subtasks(main_task_id)
        subtasks.sort(key=lambda x: x.created_at if x.created_at else x.id) # Keep existing sort
        logger.debug(f"PLAN_EXECUTOR: Fetched {len(subtasks)} subtasks for plan {main_task_id}.")

        total_steps = len(subtasks)
        current_step_number = 0 # Will be incremented before processing each step

        start_plan_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Starting execution of plan: {main_task.name} (ID: {main_task.id}) with {total_steps} subtasks."}, "metadata": {"thread_run_id": main_task_id}}
        await self._send_user_message(start_plan_event) # self.main_task_id used here for channel

        completed_subtask_ids = set()
        plan_failed = False
        agent_signaled_completion = False # Flag for SystemCompleteTask
        completion_summary_from_agent = "" # To store summary from SystemCompleteTask
        # MAX_PARAM_GENERATION_RETRIES is now a module constant
        all_step_results = [] # New list to accumulate results

        while True: # Outer loop for dependency-aware execution
            if agent_signaled_completion: # If completion tool was called, exit outer loop
                break
            runnable_subtasks = []
            pending_subtasks_exist = False

            # Identify runnable subtasks in each pass
            for st in subtasks:
                if st.status == "pending":
                    pending_subtasks_exist = True
                    # Check if all dependencies for this subtask are met
                    dependencies_met = True
                    if st.dependencies: # Ensure dependencies is not None
                        for dep_id in st.dependencies:
                            if dep_id not in completed_subtask_ids:
                                dependencies_met = False
                                break
                    if dependencies_met:
                        runnable_subtasks.append(st)
                elif st.status == "completed":
                    # Ensure already completed tasks (e.g. from a previous run) are in the set
                    completed_subtask_ids.add(st.id)


            if not runnable_subtasks:
                if pending_subtasks_exist:
                    logger.error(f"PLAN_EXECUTOR: Deadlock detected in plan {main_task_id}. No runnable subtasks but pending tasks exist. Marking plan failed.")
                    deadlock_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": "[Plan Update] Error: Plan execution cannot continue due to a deadlock (circular dependency or failed upstream task)."}, "metadata": {"thread_run_id": main_task_id}}
                    await self._send_user_message(deadlock_event) # self.main_task_id used here for channel
                    plan_failed = True
                break

            progress_made_in_pass = False
            for subtask in runnable_subtasks:
                if plan_failed: # or agent_signaled_completion
                    break

                current_step_number += 1 # Increment for the current step being processed

                logger.info(f"PLAN_EXECUTOR: Processing step {current_step_number}/{total_steps}, Subtask ID: {subtask.id}, Name: '{subtask.name}'")

                subtask_start_message = f"[Paso {current_step_number} de {total_steps}] Iniciando: {subtask.name}"
                subtask_start_event = {
                    "type": "assistant_message_update",
                    "content": {"role": "assistant", "content": subtask_start_message},
                    "metadata": {"thread_run_id": main_task_id, "step_current": current_step_number, "step_total": total_steps}
                }
                await self._send_user_message(subtask_start_event) # self.main_task_id used here for channel
                await self.task_manager.update_task(subtask.id, {"status": "running"})
                progress_made_in_pass = True

                subtask_results: List[Dict[str, Any]] = []
                subtask_failed_flag = False
                logger.debug(f"PLAN_EXECUTOR: Subtask {subtask.id} assigned_tools: {subtask.assigned_tools}")

                if not subtask.assigned_tools:
                    output_data = {"message": "No tools assigned, subtask auto-completed."}
                    logger.info(f"PLAN_EXECUTOR: Subtask {subtask.id} ('{subtask.name}') status updated to 'completed'. Output: {json.dumps(output_data, indent=2)}")
                    await self.task_manager.update_task(subtask.id, {"status": "completed", "output": json.dumps(output_data)})
                    no_tool_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Subtask '{subtask.name}' completed (no tools were assigned)."}, "metadata": {"thread_run_id": main_task_id}}
                    await self._send_user_message(no_tool_event) # self.main_task_id used here for channel
                    completed_subtask_ids.add(subtask.id)
                    continue

                tool_string = subtask.assigned_tools[0]
                tool_id = ""
                method_name = ""

                try:
                    parts = tool_string.split("__", 1)
                    if len(parts) == 2:
                        tool_id, method_name = parts[0], parts[1]
                        logger.debug(f"PLAN_EXECUTOR: Subtask {subtask.id} - Parsing tool_string: '{tool_string}' -> tool_id='{tool_id}', method_name='{method_name}'")
                    else:
                        raise ValueError("Invalid tool string format. Expected 'ToolID__methodName'.")
                except ValueError as e:
                    logger.error(f"PLAN_EXECUTOR: Failed to parse tool_string '{tool_string}' for subtask {subtask.id}: {e}")
                    subtask_results.append({"error": f"Failed to parse tool_string: {tool_string}", "details": str(e)})
                    subtask_failed_flag = True

                if not subtask_failed_flag:
                    # logger.info(f"Attempting to use tool: {tool_id}, method: {method_name} for subtask {subtask.id}") # Already covered by parsing log

                    all_schemas = self.tool_orchestrator.get_tool_schemas_for_llm()
                    schema_for_tool = next((s for s in all_schemas if s.get('name') == tool_string), None)

                    if not schema_for_tool:
                        logger.error(f"PLAN_EXECUTOR: Schema not found for tool_string '{tool_string}' in subtask {subtask.id}.")
                        subtask_results.append({"error": f"Schema not found for tool: {tool_string}"})
                        subtask_failed_flag = True
                    else:
                        # Generate Parameters
                        generated_params = await self._generate_tool_parameters(
                            subtask,
                            main_task.description if main_task else "No main task description available.",
                            tool_string,
                            schema_for_tool
                        )

                        if generated_params is None:
                            logger.error(f"PLAN_EXECUTOR: Subtask {subtask.id} - Failed to generate parameters for tool {tool_string} after retries.")
                            subtask_results.append({"error": f"Failed to generate parameters for tool {tool_string} after retries."})
                            subtask_failed_flag = True
                        else:
                            # Execute Tool
                            tool_execution_result, agent_completion_signal, agent_summary = await self._execute_tool_for_subtask(
                                subtask,
                                tool_id,
                                method_name,
                                generated_params
                            )
                            subtask_results.append(tool_execution_result.to_dict() if hasattr(tool_execution_result, 'to_dict') else vars(tool_execution_result)) # type: ignore

                            if tool_execution_result.status == "failed":
                                logger.error(f"PLAN_EXECUTOR: Subtask {subtask.id} - Tool execution failed for '{tool_id}__{method_name}'. Error: {tool_execution_result.error}")
                                subtask_failed_flag = True
                            else:
                                logger.info(f"PLAN_EXECUTOR: Subtask {subtask.id} - Tool execution successful for '{tool_id}__{method_name}'.")
                                if tool_execution_result.result is not None:
                                     all_step_results.append({
                                        "step_name": subtask.name,
                                        "tool_used": f"{tool_id}__{method_name}",
                                        "result": tool_execution_result.result
                                    })
                                else:
                                     all_step_results.append({
                                        "step_name": subtask.name,
                                        "tool_used": f"{tool_id}__{method_name}",
                                        "result": "Tool executed successfully but returned no specific result content."
                                    })

                            if agent_completion_signal:
                                agent_signaled_completion = True
                                completion_summary_from_agent = agent_summary
                                # No 'break' here, if SystemCompleteTask is used, the loop naturally ends after this subtask.
                                # If it needs to break immediately, the 'break' should be here.
                                # For now, let's assume it completes the current subtask's processing.

                if subtask_failed_flag: # This flag is set by either param gen or tool exec failure
                    output_data_fail = json.dumps(subtask_results) # subtask_results contains errors
                    await self.task_manager.update_task(subtask.id, {"status": "failed", "output": output_data_fail})
                    logger.info(f"PLAN_EXECUTOR: Step {current_step_number}/{total_steps}, Subtask ID: {subtask.id} ('{subtask.name}') status updated to 'failed'.")

                    subtask_failed_message = f"[Paso {current_step_number} de {total_steps}] Falló: {subtask.name}."
                    subtask_failed_event = {
                        "type": "assistant_message_update",
                        "content": {"role": "assistant", "content": subtask_failed_message},
                        "metadata": {"thread_run_id": main_task_id, "step_current": current_step_number, "step_total": total_steps, "error_details": output_data_fail}
                    }
                    await self._send_user_message(subtask_failed_event) # self.main_task_id used here for channel
                    # logger.error(f"PLAN_EXECUTOR: Plan execution failed at subtask {subtask.id} ('{subtask.name}'). Stopping plan.") # Log already indicates failure
                    plan_failed = True
                    break
                else:
                    output_data_complete = json.dumps(subtask_results)
                    await self.task_manager.update_task(subtask.id, {"status": "completed", "output": output_data_complete})
                    completed_subtask_ids.add(subtask.id)
                    logger.info(f"PLAN_EXECUTOR: Step {current_step_number}/{total_steps}, Subtask ID: {subtask.id} ('{subtask.name}') status updated to 'completed'.")

                    subtask_complete_message = f"[Paso {current_step_number} de {total_steps}] Completado: {subtask.name}."
                    subtask_complete_event = {
                        "type": "assistant_message_update",
                        "content": {"role": "assistant", "content": subtask_complete_message},
                        "metadata": {"thread_run_id": main_task_id, "step_current": current_step_number, "step_total": total_steps, "raw_output": output_data_complete}
                    }
                    await self._send_user_message(subtask_complete_event) # self.main_task_id used here for channel

                if agent_signaled_completion:
                    break

            if plan_failed: # This handles subtask failures
                break

            if agent_signaled_completion: # Handles completion signal from the loop above
                break

            if not progress_made_in_pass and pending_subtasks_exist:
                 logger.error(f"PLAN_EXECUTOR: Deadlock detected in plan {main_task_id} on second check. No progress made but pending tasks exist. Marking plan failed.")
                 no_progress_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": "[Plan Update] Error: Plan execution cannot continue due to a deadlock (no progress made on pending tasks)."}, "metadata": {"thread_run_id": main_task_id}}
                 await self._send_user_message(no_progress_event) # self.main_task_id used here for channel
                 plan_failed = True
                 break

        if agent_signaled_completion:
            final_main_task_status = "completed"

            final_summary_parts = []
            if completion_summary_from_agent:
                final_summary_parts.append(completion_summary_from_agent)
            else:
                final_summary_parts.append("El agente ha completado la tarea.")

            if all_step_results:
                final_summary_parts.append("\n\nResumen de los pasos ejecutados:")
                for step_result in all_step_results:
                    result_str = ""
                    try:
                        if isinstance(step_result['result'], (dict, list)):
                            result_str = json.dumps(step_result['result'], indent=2, ensure_ascii=False)
                        else:
                            result_str = str(step_result['result'])
                    except Exception: # Broad catch for string conversion issues
                        result_str = str(step_result['result']) # Fallback

                    if len(result_str) > SUMMARY_MAX_RESULT_LENGTH:
                        result_str = result_str[:SUMMARY_MAX_RESULT_LENGTH] + "..."

                    final_summary_parts.append(f"- Paso '{step_result['step_name']}' (Herramienta: {step_result['tool_used']}):\n  Resultado: {result_str}")

            final_main_task_message = "\n".join(final_summary_parts)
            if not final_summary_parts: # Ensure message is not empty if no steps/summary
                final_main_task_message = "El agente ha completado la tarea sin resumen detallado."

        elif plan_failed:
            final_main_task_status = "failed"
            final_main_task_message = "La ejecución del plan falló debido a errores en uno o más subpasos o un interbloqueo."
        else:
            final_main_task_status = "completed"
            final_main_task_message = "Todos los subpasos se procesaron correctamente."
            if all_step_results:
                final_main_task_message += "\n\nResumen de los pasos ejecutados:"
                for step_result in all_step_results:
                    result_str = ""
                    try:
                        if isinstance(step_result['result'], (dict, list)):
                            result_str = json.dumps(step_result['result'], indent=2, ensure_ascii=False)
                        else:
                            result_str = str(step_result['result'])
                    except Exception:  # Broad catch for string conversion issues
                        result_str = str(step_result['result']) # Fallback

                    if len(result_str) > SUMMARY_MAX_RESULT_LENGTH:
                        result_str = result_str[:SUMMARY_MAX_RESULT_LENGTH] + "..."
                    final_main_task_message += f"\n- Paso '{step_result['step_name']}' (Herramienta: {step_result['tool_used']}):\n  Resultado: {result_str}"
            elif not all_step_results and final_main_task_status == "completed": # No steps but completed
                final_main_task_message = "El plan se completó sin ejecutar pasos específicos."


        logger.info(f"PLAN_EXECUTOR: Plan execution for main_task_id: {main_task_id} finished with status '{final_main_task_status}'. Summary: {final_main_task_message}")

        final_plan_status_event_content = f"[Plan '{main_task.name}' {final_main_task_status.upper()}] {final_main_task_message}"
        final_plan_status_event = {
            "type": "assistant_message_update",
            "content": {"role": "assistant", "content": final_plan_status_event_content},
            "metadata": {"thread_run_id": main_task_id, "final_status": final_main_task_status}
        }
        await self._send_user_message(final_plan_status_event) # self.main_task_id used here for channel

        await self.task_manager.update_task(
            main_task_id,
            {"status": final_main_task_status, "output": json.dumps({"message": final_main_task_message}, ensure_ascii=False)}
        )

    async def execute_json_plan(
        self,
        plan_data: Dict[str, Any],
        thread_id: str, # For context and message saving
        run_id: str,    # For linking status messages to this specific plan execution
        add_message_callback: Optional[Callable] = None # Callback to save messages to DB
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Executes a plan provided as a JSON object.
        The plan is expected to be a list of actions/tool calls.
        This method directly executes tools without creating tasks in TaskStateManager.
        Yields status messages and tool results compatible with ResponseProcessor.
        """
        logger.info(f"PLAN_EXECUTOR (JSON): Starting execution of JSON plan. Run ID: {run_id}, Thread ID: {thread_id}")
        logger.debug(f"PLAN_EXECUTOR (JSON): Plan data: {json.dumps(plan_data, indent=2)}")

        actions = plan_data.get("plan", plan_data.get("actions", plan_data.get("subtasks")))

        if not isinstance(actions, list):
            logger.error(f"PLAN_EXECUTOR (JSON): Plan data does not contain a list of actions/subtasks. Found: {type(actions)}. Run ID: {run_id}")
            error_message = {
                "role": "system", "status_type": "error",
                "message": "Invalid plan format: 'plan', 'actions', or 'subtasks' key must be a list."
            }
            if add_message_callback:
                err_msg_obj = await add_message_callback(
                    thread_id=thread_id, type="status", content=error_message,
                    is_llm_message=False, metadata={"thread_run_id": run_id}
                )
                if err_msg_obj: yield format_for_yield(err_msg_obj)
            else: # Fallback if no callback to save, just yield a basic structure
                yield {"type": "status", "content": json.dumps(error_message), "metadata": json.dumps({"thread_run_id": run_id})}
            return

        plan_name = plan_data.get("name", "Unnamed JSON Plan")
        logger.info(f"PLAN_EXECUTOR (JSON): Executing plan '{plan_name}' with {len(actions)} actions. Run ID: {run_id}")

        for index, action in enumerate(actions):
            tool_name = action.get("tool_name", action.get("function_name")) # Expecting "tool_id__method_name"
            params = action.get("parameters", action.get("arguments", {}))
            action_id = action.get("id", str(uuid.uuid4())) # For linking start/end status

            if not tool_name:
                logger.warning(f"PLAN_EXECUTOR (JSON): Action {index} missing 'tool_name' or 'function_name'. Action: {action}. Run ID: {run_id}")
                # Yield a warning/error status? For now, skip.
                continue

            tool_id_str, method_name_str = "", ""
            if "__" in tool_name:
                tool_id_str, method_name_str = tool_name.split("__", 1)
            else:
                logger.warning(f"PLAN_EXECUTOR (JSON): Action {index} 'tool_name' ('{tool_name}') not in 'ToolID__methodName' format. Skipping. Run ID: {run_id}")
                # Yield warning/error?
                continue

            logger.info(f"PLAN_EXECUTOR (JSON): Preparing to execute action {index + 1}/{len(actions)}: {tool_name} with params {params}. Run ID: {run_id}")

            # Yield tool_started status
            tool_started_content = {
                "role": "assistant", "status_type": "tool_started",
                "function_name": tool_name, "xml_tag_name": None, # Assuming no XML tools in this plan type
                "message": f"Starting execution of {tool_name}", "tool_index": index,
                "tool_call_id": action_id
            }
            if add_message_callback:
                started_msg_obj = await add_message_callback(
                    thread_id=thread_id, type="status", content=tool_started_content,
                    is_llm_message=False, metadata={"thread_run_id": run_id}
                )
                if started_msg_obj: yield format_for_yield(started_msg_obj)
            else:
                 yield {"type": "status", "content": json.dumps(tool_started_content), "metadata": json.dumps({"thread_run_id": run_id})}


            tool_result: Optional[ToolResult] = None
            try:
                tool_result = await self.tool_orchestrator.execute_tool(tool_id_str, method_name_str, params)
            except Exception as e_exec:
                logger.error(f"PLAN_EXECUTOR (JSON): Exception during tool execution for '{tool_name}': {e_exec}. Run ID: {run_id}", exc_info=True)
                tool_result = ToolResult(
                    tool_id=tool_id_str, execution_id=str(uuid.uuid4()),
                    status="failed", error=f"Exception during execution: {str(e_exec)}"
                )

            if not tool_result: # Should not happen if execute_tool always returns a ToolResult
                 tool_result = ToolResult(
                    tool_id=tool_id_str, execution_id=str(uuid.uuid4()),
                    status="failed", error="Tool execution returned None unexpectedly."
                )

            # Yield tool_completed / tool_failed status
            is_success = tool_result.status == "completed"
            status_type = "tool_completed" if is_success else "tool_failed"
            outcome_message = f"Tool {tool_name} {tool_result.status}"
            if not is_success and tool_result.error:
                outcome_message += f": {tool_result.error}"

            tool_outcome_content = {
                "role": "assistant", "status_type": status_type,
                "function_name": tool_name, "xml_tag_name": None,
                "message": outcome_message, "tool_index": index,
                "tool_call_id": action_id
            }

            if add_message_callback:
                # Save the actual tool result first (simplified, native-like)
                tool_result_content_for_db = {
                    "role": "tool",
                    "tool_call_id": action_id, # Link to the "call"
                    "name": tool_name,
                    "content": str(tool_result.result) if is_success else str(tool_result.error)
                }
                saved_tool_msg_obj = await add_message_callback(
                    thread_id=thread_id, type="tool", content=tool_result_content_for_db,
                    is_llm_message=True, # Considered part of LLM flow
                    metadata={"thread_run_id": run_id, "tool_execution_id": tool_result.execution_id}
                )

                outcome_metadata = {"thread_run_id": run_id}
                if saved_tool_msg_obj and saved_tool_msg_obj.get("message_id"):
                    tool_outcome_content["linked_tool_result_message_id"] = saved_tool_msg_obj["message_id"]
                    outcome_metadata["linked_tool_result_message_id"] = saved_tool_msg_obj["message_id"]

                completed_msg_obj = await add_message_callback(
                    thread_id=thread_id, type="status", content=tool_outcome_content,
                    is_llm_message=False, metadata=outcome_metadata
                )
                if completed_msg_obj: yield format_for_yield(completed_msg_obj)

                # Yield the saved tool result itself
                if saved_tool_msg_obj:
                    yield format_for_yield(saved_tool_msg_obj)

            else: # No callback, yield simplified structures
                yield {"type": "status", "content": json.dumps(tool_outcome_content), "metadata": json.dumps({"thread_run_id": run_id})}
                # Yield simplified tool result
                yield {
                    "type": "tool", # Mimicking OpenAI message structure
                    "content": json.dumps({
                        "tool_call_id": action_id,
                        "role": "tool",
                        "name": tool_name,
                        "content": str(tool_result.result) if is_success else str(tool_result.error)
                    }),
                    "metadata": json.dumps({"thread_run_id": run_id, "tool_execution_id": tool_result.execution_id})
                }

            if not is_success:
                logger.error(f"PLAN_EXECUTOR (JSON): Action {index} ('{tool_name}') failed. Stopping plan execution. Run ID: {run_id}")
                # Optionally yield a "plan_failed" status message here
                break # Stop plan on first failure

        logger.info(f"PLAN_EXECUTOR (JSON): Finished execution of JSON plan '{plan_name}'. Run ID: {run_id}")

# Example Usage (Conceptual - would require async setup and instances)
# async def main():
#     # Presuming task_manager and tool_orchestrator are initialized
#     # from backend.agentpress.task_state_manager import TaskStateManager
#     # from backend.agentpress.task_storage_supabase import SupabaseTaskStorage
#     # from backend.agentpress.tool_orchestrator import ToolOrchestrator
#     # from services.supabase import DBConnection
#
#     # db_conn = DBConnection()
#     # await db_conn.initialize()
#     # storage = SupabaseTaskStorage(db_conn)
#     # task_mgr = TaskStateManager(storage)
#     # await task_mgr.initialize()
#     # tool_orch = ToolOrchestrator() # Needs tools loaded
#     # tool_orch.load_tools_from_directory() # Example
#
#     # # Create a dummy plan first using TaskPlanner or manually for testing
#     # # This main_task_id should exist and have subtasks.
#     # main_task_id_to_execute = "some_existing_main_task_id"
#
#     # async def dummy_user_callback(message: str):
#     #     print(f"USER_MSG_CALLBACK: {message}")
#
#     # executor = PlanExecutor(
#     #     main_task_id=main_task_id_to_execute,
#     #     task_manager=task_mgr,
#     #     tool_orchestrator=tool_orch,
#     #     user_message_callback=dummy_user_callback
#     # )
#     # await executor.execute_plan()
#     # await db_conn.disconnect()

# if __name__ == "__main__":
#    # import asyncio
#    # asyncio.run(main())
#    pass
