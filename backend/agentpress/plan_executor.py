"""
Handles the execution of a pre-defined plan consisting of a main task and its subtasks.

The PlanExecutor iterates through subtasks, respecting their dependencies,
and uses an LLM to determine parameters for assigned tools, then executes them
via the ToolOrchestrator.
"""
from typing import Callable, Optional, List, Dict, Any
import json
import uuid # Added

from agentpress.task_state_manager import TaskStateManager
from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.task_types import TaskState # Correctly import TaskState
from agentpress.tool import ToolResult # Correctly import ToolResult
from services.llm import make_llm_api_call
from utils.logger import logger

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
                 tool_orchestrator: ToolOrchestrator,
                 user_message_callback: Optional[Callable[[str], Any]] = None):
        """
        Initializes the PlanExecutor.

        Args:
            main_task_id (str): The ID of the main task representing the overall plan.
            task_manager (TaskStateManager): An instance for managing task states.
            tool_orchestrator (ToolOrchestrator): An instance for executing tools.
                                                 It is assumed that this orchestrator
                                                 has its tools loaded.
            user_message_callback (Optional[Callable[[str], Any]]):
                An optional asynchronous callback function to send updates/messages
                to the user or another system. It should accept a string message.
        """
        self.main_task_id = main_task_id
        self.task_manager = task_manager
        self.tool_orchestrator = tool_orchestrator
        self.user_message_callback = user_message_callback
        logger.info(f"PlanExecutor initialized for main_task_id: {self.main_task_id}")

    async def _send_user_message(self, message_data: Dict[str, Any]):
        """
        Helper method to safely invoke the user_message_callback if provided.

        Args:
            message_data (Dict[str, Any]): The message data to send.
        """
        if self.user_message_callback:
            try:
                # The callback might be an async generator or a regular async function.
                # Awaiting it directly is suitable for async functions.
                # If it's an async generator, the caller (e.g., run_agent) would typically iterate over it.
                # For this simple callback, direct await is assumed.
                await self.user_message_callback(message_data)
            except Exception as e:
                logger.error(f"Error in user_message_callback: {e}", exc_info=True)

    async def execute_plan(self):
        """
        Executes the plan associated with `self.main_task_id`.

        This method orchestrates the execution of subtasks according to their
        dependencies. It updates task statuses and reports progress via logs
        and the user message callback. If any subtask fails, the overall plan
        is marked as failed.
        """
        logger.info(f"PLAN_EXECUTOR: Starting execution of plan for main_task_id: {self.main_task_id}")
        await self.task_manager.update_task(self.main_task_id, {"status": "running"})

        main_task = await self.task_manager.get_task(self.main_task_id)
        if not main_task:
            logger.error(f"PLAN_EXECUTOR: Main task {self.main_task_id} not found. Cannot execute plan.")
            error_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Error: Could not find the main plan task (ID: {self.main_task_id})."}, "metadata": {"thread_run_id": self.main_task_id}}
            await self._send_user_message(error_event)
            await self.task_manager.update_task(self.main_task_id, {"status": "failed", "output": "Main task not found during execution."})
            return

        logger.debug(f"PLAN_EXECUTOR: Main task '{main_task.name}' (ID: {main_task.id}) fetched. Description: {main_task.description}")

        subtasks: List[TaskState] = await self.task_manager.get_subtasks(self.main_task_id)
        subtasks.sort(key=lambda x: x.created_at if x.created_at else x.id) # Sort for deterministic order
        logger.debug(f"PLAN_EXECUTOR: Fetched {len(subtasks)} subtasks for plan {self.main_task_id}. Sorted by created_at/id.")

        start_plan_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Starting execution of plan: {main_task.name} (ID: {main_task.id}) with {len(subtasks)} subtasks."}, "metadata": {"thread_run_id": self.main_task_id}}
        await self._send_user_message(start_plan_event)

        completed_subtask_ids = set()
        plan_failed = False
        agent_signaled_completion = False # Flag for SystemCompleteTask
        completion_summary_from_agent = "" # To store summary from SystemCompleteTask
        MAX_PARAM_GENERATION_RETRIES = 2

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
                    logger.error(f"PLAN_EXECUTOR: Deadlock detected in plan {self.main_task_id}. No runnable subtasks but pending tasks exist. Marking plan failed.")
                    deadlock_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": "[Plan Update] Error: Plan execution cannot continue due to a deadlock (circular dependency or failed upstream task)."}, "metadata": {"thread_run_id": self.main_task_id}}
                    await self._send_user_message(deadlock_event)
                    plan_failed = True
                break

            progress_made_in_pass = False
            for subtask in runnable_subtasks:
                if plan_failed:
                    break

                logger.info(f"PLAN_EXECUTOR: Processing subtask ID: {subtask.id}, Name: '{subtask.name}', Status: {subtask.status}, Dependencies: {subtask.dependencies}")
                subtask_start_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Now working on: {subtask.name} (ID: {subtask.id})"}, "metadata": {"thread_run_id": self.main_task_id}}
                await self._send_user_message(subtask_start_event)
                await self.task_manager.update_task(subtask.id, {"status": "running"})
                progress_made_in_pass = True

                subtask_results: List[Dict[str, Any]] = []
                subtask_failed_flag = False
                logger.debug(f"PLAN_EXECUTOR: Subtask {subtask.id} assigned_tools: {subtask.assigned_tools}")

                if not subtask.assigned_tools:
                    output_data = {"message": "No tools assigned, subtask auto-completed."}
                    logger.info(f"PLAN_EXECUTOR: Subtask {subtask.id} ('{subtask.name}') status updated to 'completed'. Output: {json.dumps(output_data, indent=2)}")
                    await self.task_manager.update_task(subtask.id, {"status": "completed", "output": json.dumps(output_data)})
                    no_tool_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Subtask '{subtask.name}' completed (no tools were assigned)."}, "metadata": {"thread_run_id": self.main_task_id}}
                    await self._send_user_message(no_tool_event)
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
                        params = {}
                        llm_param_generation_attempts = 0
                        raw_llm_output_for_error = "Not available"
                        params_generated_successfully = False

                        base_param_prompt_messages = [
                            {
                                "role": "system",
                                "content": f"""\
You are a helpful AI assistant. Your task is to generate the JSON parameters required to execute a specific tool method based on a subtask description, the overall goal, and the tool's OpenAPI schema.

Overall Goal: {main_task.description}
Subtask Description: {subtask.description}
Tool Information:
  Name: {schema_for_tool.get('name')}
  Description: {schema_for_tool.get('description')}
  Schema (parameters): {json.dumps(schema_for_tool.get('parameters', {}))}

Output only the JSON object containing the parameters. If the tool requires no parameters according to its schema (e.g., parameters is empty or not defined), output an empty JSON object {{}}.
Ensure your output is ONLY the valid JSON object of parameters, with no other text before or after it.
"""
                            },
                            {
                                "role": "user",
                                "content": f"Please generate the JSON parameters for the tool '{schema_for_tool.get('name')}' to achieve the subtask: '{subtask.description}'."
                            }
                        ]

                        while llm_param_generation_attempts <= MAX_PARAM_GENERATION_RETRIES:
                            llm_param_generation_attempts += 1
                            param_prompt_messages = list(base_param_prompt_messages) # Copy base prompt

                            if llm_param_generation_attempts > 1: # Add retry guidance
                                retry_message = {"role": "system", "content": "Your previous response for tool parameters was not a valid JSON object or did not parse correctly. Please ensure your output is ONLY a valid JSON object of parameters, with no other text before or after it. For example: {\"param_name\": \"value\"}. If the tool takes no parameters, output an empty JSON object: {}."}
                                param_prompt_messages.insert(1, retry_message) # Insert after initial system prompt

                            logger.debug(f"PLAN_EXECUTOR: Subtask {subtask.id} - Attempting LLM call (Attempt {llm_param_generation_attempts}/{MAX_PARAM_GENERATION_RETRIES + 1}) to generate parameters for tool '{tool_string}'. Schema provided: {json.dumps(schema_for_tool.get('parameters', {}))}")

                            try:
                                llm_response_for_params = await make_llm_api_call(
                                    messages=param_prompt_messages,
                                    llm_model="gpt-3.5-turbo-0125", # Consider using a more capable model if issues persist
                                    temperature=0.0, # Low temperature for deterministic JSON
                                    json_mode=True
                                )

                                # Process response
                                if isinstance(llm_response_for_params, dict):
                                    params = llm_response_for_params
                                    raw_llm_output_for_error = json.dumps(params) # For logging if subsequent validation fails
                                elif isinstance(llm_response_for_params, str):
                                    raw_llm_output_for_error = llm_response_for_params
                                    try:
                                        params = json.loads(llm_response_for_params)
                                    except json.JSONDecodeError as e_json_load:
                                        logger.warning(f"PLAN_EXECUTOR: Subtask {subtask.id} - Attempt {llm_param_generation_attempts}: Failed to decode JSON parameters from LLM string response: {e_json_load}. Response: {raw_llm_output_for_error}")
                                        if llm_param_generation_attempts > MAX_PARAM_GENERATION_RETRIES:
                                            subtask_results.append({"error": "LLM failed to generate valid JSON parameters after retries (JSONDecodeError)", "details": str(e_json_load), "raw_llm_output": raw_llm_output_for_error})
                                            subtask_failed_flag = True
                                        continue # Retry
                                else: # Unexpected type from LLM
                                    raw_llm_output_for_error = str(llm_response_for_params)
                                    logger.warning(f"PLAN_EXECUTOR: Subtask {subtask.id} - Attempt {llm_param_generation_attempts}: Unexpected parameter type from LLM: {type(llm_response_for_params)}. Content: {raw_llm_output_for_error}")
                                    if llm_param_generation_attempts > MAX_PARAM_GENERATION_RETRIES:
                                        subtask_results.append({"error": "LLM returned unexpected data type for parameters after retries", "raw_llm_output": raw_llm_output_for_error})
                                        subtask_failed_flag = True
                                    continue # Retry

                                # Validate if params is a dictionary
                                if not isinstance(params, dict):
                                    logger.warning(f"PLAN_EXECUTOR: Subtask {subtask.id} - Attempt {llm_param_generation_attempts}: LLM output for parameters is not a dictionary. Type: {type(params)}, Value: {params}")
                                    raw_llm_output_for_error = str(params) # Update raw output to current params
                                    if llm_param_generation_attempts > MAX_PARAM_GENERATION_RETRIES:
                                        subtask_results.append({"error": "LLM failed to generate a valid JSON object (dictionary) for parameters after retries", "raw_llm_output": raw_llm_output_for_error})
                                        subtask_failed_flag = True
                                    continue # Retry

                                logger.info(f"PLAN_EXECUTOR: Subtask {subtask.id} - LLM generated parameters for tool {tool_string}: {json.dumps(params, indent=2)}")
                                params_generated_successfully = True
                                break # Successfully generated and validated params

                            except Exception as e_llm_call: # Catch errors during make_llm_api_call or unexpected issues
                                logger.warning(f"PLAN_EXECUTOR: Subtask {subtask.id} - Attempt {llm_param_generation_attempts}: Error during LLM call or processing for parameters: {e_llm_call}", exc_info=True)
                                raw_llm_output_for_error = f"Error during LLM call: {str(e_llm_call)}"
                                if llm_param_generation_attempts > MAX_PARAM_GENERATION_RETRIES:
                                    subtask_results.append({"error": "Error obtaining parameters from LLM after retries (call failed)", "details": str(e_llm_call)})
                                    subtask_failed_flag = True
                                # continue will be hit by the loop if not max retries

                        if not params_generated_successfully and not subtask_failed_flag: # Ensure failure is marked if loop finishes without success
                            logger.error(f"PLAN_EXECUTOR: Subtask {subtask.id} - Exhausted all {MAX_PARAM_GENERATION_RETRIES + 1} attempts to generate parameters for tool {tool_string}.")
                            subtask_results.append({"error": f"Exhausted all attempts to generate parameters for tool {tool_string}", "raw_llm_output": raw_llm_output_for_error})
                            subtask_failed_flag = True

                        if not subtask_failed_flag: # This means params_generated_successfully is True
                            try:
                                logger.info(f"PLAN_EXECUTOR: Subtask {subtask.id} - Executing tool '{tool_id}__{method_name}' with generated parameters.")

                                tool_name_for_event = f"{tool_id}__{method_name}"
                                tool_call_id_for_event = str(uuid.uuid4())
                                tool_started_event = {
                                    "type": "status",
                                    "content": {
                                        "status_type": "tool_started",
                                        "function_name": tool_name_for_event,
                                        "tool_call_id": tool_call_id_for_event,
                                        "message": f"Starting execution of tool {tool_name_for_event} for subtask '{subtask.name}'",
                                        "tool_index": 0 # Placeholder, could be subtask index or tool index within subtask
                                    },
                                    "metadata": {"thread_run_id": self.main_task_id }
                                }
                                await self._send_user_message(tool_started_event)

                                tool_result: ToolResult = await self.tool_orchestrator.execute_tool(tool_id, method_name, params)

                                tool_name_from_result = tool_result.tool_id # This is usually ToolID
                                if "__" not in tool_name_from_result: # if it's just ToolID, append method_name
                                     tool_name_from_result = f"{tool_result.tool_id}__{method_name}"


                                tool_data_event = {
                                    "type": "plan_tool_result_data",
                                    "tool_name": tool_name_from_result,
                                    "tool_call_id": tool_call_id_for_event,
                                    "status": tool_result.status,
                                    "result": tool_result.result,
                                    "error": tool_result.error,
                                    "metadata": {"thread_run_id": self.main_task_id }
                                }
                                await self._send_user_message(tool_data_event)

                                status_type = "tool_completed" if tool_result.status == "completed" else "tool_failed"
                                outcome_message = f"Tool {tool_name_from_result} {tool_result.status}"
                                if tool_result.error: outcome_message += f": {tool_result.error}"
                                tool_outcome_event = {
                                    "type": "status",
                                    "content": {
                                        "status_type": status_type,
                                        "function_name": tool_name_from_result,
                                        "tool_call_id": tool_call_id_for_event,
                                        "message": outcome_message,
                                    },
                                    "metadata": {"thread_run_id": self.main_task_id }
                                }
                                await self._send_user_message(tool_outcome_event)

                                tool_result_dict = {}
                                if hasattr(tool_result, 'to_dict') and callable(tool_result.to_dict):
                                    tool_result_dict = tool_result.to_dict()
                                else:
                                    tool_result_dict = {
                                        "tool_id": tool_result.tool_id, "execution_id": tool_result.execution_id,
                                        "status": tool_result.status, "result": tool_result.result,
                                        "error": tool_result.error, "start_time": str(tool_result.start_time),
                                        "end_time": str(tool_result.end_time)
                                    }
                                subtask_results.append(tool_result_dict)
                                logger.debug(f"PLAN_EXECUTOR: Subtask {subtask.id} - Tool '{tool_id}__{method_name}' execution result: {json.dumps(tool_result_dict, indent=2)}")

                                if tool_result.status == "failed":
                                    logger.error(f"PLAN_EXECUTOR: Subtask {subtask.id} - Tool execution failed for '{tool_id}__{method_name}'. Error: {tool_result.error}")
                                    subtask_failed_flag = True
                                else:
                                    logger.info(f"PLAN_EXECUTOR: Subtask {subtask.id} - Tool execution successful for '{tool_id}__{method_name}'.")
                                    # Check for SystemCompleteTask
                                    if tool_id == "SystemCompleteTask" and method_name == "task_complete":
                                        logger.info(f"PLAN_EXECUTOR: Agent signaled task completion via SystemCompleteTask. Main task {self.main_task_id} will be marked as completed.")
                                        completion_summary_from_agent = tool_result.result.get("summary", "Agent marked task as complete.")
                                        agent_signaled_completion = True
                                        # subtask_failed_flag remains False as this is a successful completion signal
                                        # No need to set plan_failed = False explicitly here, it's already False.
                                        # Break this inner loop; the outer loop will check agent_signaled_completion.
                                        break

                            except Exception as e_exec:
                                logger.error(f"PLAN_EXECUTOR: Subtask {subtask.id} - Exception during tool execution for '{tool_id}__{method_name}': {e_exec}", exc_info=True)
                                tool_execution_error_details = {"error": f"Exception during tool execution: {tool_id}__{method_name}", "details": str(e_exec)}
                                subtask_results.append(tool_execution_error_details)
                                subtask_failed_flag = True


                if subtask_failed_flag:
                    output_data_fail = json.dumps(subtask_results)
                    await self.task_manager.update_task(subtask.id, {"status": "failed", "output": output_data_fail})
                    logger.info(f"PLAN_EXECUTOR: Subtask {subtask.id} ('{subtask.name}') status updated to 'failed'. Output: {json.dumps(output_data_fail, indent=2)}")
                    subtask_failed_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Subtask FAILED: {subtask.name}. Details: {output_data_fail}"}, "metadata": {"thread_run_id": self.main_task_id}}
                    await self._send_user_message(subtask_failed_event)
                    logger.error(f"PLAN_EXECUTOR: Plan execution failed at subtask {subtask.id} ('{subtask.name}'). Stopping plan.")
                    plan_failed = True
                    break
                else:
                    output_data_complete = json.dumps(subtask_results)
                    await self.task_manager.update_task(subtask.id, {"status": "completed", "output": output_data_complete})
                    completed_subtask_ids.add(subtask.id)
                    logger.info(f"PLAN_EXECUTOR: Subtask {subtask.id} ('{subtask.name}') status updated to 'completed'. Output: {json.dumps(output_data_complete, indent=2)}")
                    subtask_complete_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Subtask COMPLETED: {subtask.name}. Output: {output_data_complete}"}, "metadata": {"thread_run_id": self.main_task_id}}
                    await self._send_user_message(subtask_complete_event)
                    # logger.info(f"Subtask {subtask.id} ('{subtask.name}') completed successfully.") # Redundant with status update log

                if agent_signaled_completion: # If completion tool was called in the last subtask, break outer loop
                    break

            if plan_failed: # This handles subtask failures
                break

            if agent_signaled_completion: # Handles completion signal from the loop above
                break

            if not progress_made_in_pass and pending_subtasks_exist:
                 logger.error(f"PLAN_EXECUTOR: Deadlock detected in plan {self.main_task_id} on second check. No progress made but pending tasks exist. Marking plan failed.")
                 no_progress_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": "[Plan Update] Error: Plan execution cannot continue due to a deadlock (no progress made on pending tasks)."}, "metadata": {"thread_run_id": self.main_task_id}}
                 await self._send_user_message(no_progress_event)
                 plan_failed = True
                 break

        if agent_signaled_completion:
            final_main_task_status = "completed"
            final_main_task_message = completion_summary_from_agent
            # logger.info(f"PLAN_EXECUTOR: Plan execution for main_task_id: {self.main_task_id} completed by agent signal.") # Replaced by more descriptive log below
        elif plan_failed:
            final_main_task_status = "failed"
            final_main_task_message = "Plan execution failed due to one or more subtask failures or deadlock."
            # logger.error(f"PLAN_EXECUTOR: Plan execution for main_task_id: {self.main_task_id} failed.") # Replaced by more descriptive log below
        else: # All subtasks completed normally without explicit agent signal or failure
            final_main_task_status = "completed"
            final_main_task_message = "All subtasks processed successfully without explicit agent completion signal."
            # logger.info(f"PLAN_EXECUTOR: Plan execution for main_task_id: {self.main_task_id} completed (all subtasks done).") # Replaced by more descriptive log below

        logger.info(f"PLAN_EXECUTOR: Plan execution for main_task_id: {self.main_task_id} finished with status '{final_main_task_status}'. Summary: {final_main_task_message}")

        final_plan_status_event = {"type": "assistant_message_update", "content": {"role": "assistant", "content": f"[Plan Update] Plan '{main_task.name}' {final_main_task_status.upper()}. {final_main_task_message}"}, "metadata": {"thread_run_id": self.main_task_id}}
        await self._send_user_message(final_plan_status_event)
        await self.task_manager.update_task(self.main_task_id, {"status": final_main_task_status, "output": json.dumps({"message": final_main_task_message})})

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
