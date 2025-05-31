"""
Handles the execution of a pre-defined plan consisting of a main task and its subtasks.

The PlanExecutor iterates through subtasks, respecting their dependencies,
and uses an LLM to determine parameters for assigned tools, then executes them
via the ToolOrchestrator.
"""
from typing import Callable, Optional, List, Dict, Any
import json

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

    async def _send_user_message(self, message: str):
        """
        Helper method to safely invoke the user_message_callback if provided.

        Args:
            message (str): The message string to send.
        """
        if self.user_message_callback:
            try:
                # The callback might be an async generator or a regular async function.
                # Awaiting it directly is suitable for async functions.
                # If it's an async generator, the caller (e.g., run_agent) would typically iterate over it.
                # For this simple callback, direct await is assumed.
                await self.user_message_callback(message)
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
        logger.info(f"Starting execution of plan for main_task_id: {self.main_task_id}")
        await self.task_manager.update_task(self.main_task_id, {"status": "running"})


        main_task = await self.task_manager.get_task(self.main_task_id)
        if not main_task:
            logger.error(f"Main task {self.main_task_id} not found. Cannot execute plan.")
            await self._send_user_message(f"Error: Could not find the main plan task (ID: {self.main_task_id}).")
            # Update status to failed if task object was initially found but now gone, or if it never existed (though can't update if never existed)
            await self.task_manager.update_task(self.main_task_id, {"status": "failed", "output": "Main task not found during execution."})
            return

        subtasks: List[TaskState] = await self.task_manager.get_subtasks(self.main_task_id)
        # Sort by created_at for a defined order (assuming id is a UUID and created_at is more sequential)
        subtasks.sort(key=lambda x: x.created_at if x.created_at else x.id)

        await self._send_user_message(f"Starting execution of plan: {main_task.name} (ID: {main_task.id}) with {len(subtasks)} subtasks.")

        completed_subtask_ids = set()
        plan_failed = False
        MAX_PARAM_GENERATION_RETRIES = 2

        while True: # Outer loop for dependency-aware execution
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
                    # If there are still pending tasks but none are runnable, it's a deadlock or upstream failure.
                    logger.error(f"Deadlock detected or failed dependency in plan {self.main_task_id}. No runnable subtasks but pending tasks exist.")
                    await self._send_user_message("Error: Plan execution cannot continue due to a deadlock (circular dependency or failed upstream task).")
                    plan_failed = True
                # If no pending tasks and no runnable tasks, all tasks are processed.
                break # Exit the main while loop

            progress_made_in_pass = False
            for subtask in runnable_subtasks: # Process all subtasks identified as runnable in this pass
                if plan_failed: # If a prior subtask in this pass (or a previous pass) failed the plan
                    break

                logger.info(f"Processing subtask: {subtask.id} - {subtask.name} (Status: {subtask.status})")
                await self._send_user_message(f"Now working on: {subtask.name} (ID: {subtask.id})")
                await self.task_manager.update_task(subtask.id, {"status": "running"})
                progress_made_in_pass = True # Mark that we are attempting to process a task

                subtask_results: List[Dict[str, Any]] = [] # Stores results of tool executions for this subtask
                subtask_failed_flag = False # Flag for failure of the current subtask

                if not subtask.assigned_tools: # Or check `if not subtask.assigned_tools:`
                    logger.info(f"No tools assigned for subtask {subtask.id} ('{subtask.name}'). Marking as completed.")
                    await self.task_manager.update_task(subtask.id, {"status": "completed", "output": json.dumps({"message": "No tools assigned, subtask auto-completed."})})
                    await self._send_user_message(f"Subtask '{subtask.name}' completed (no tools were assigned).")
                    completed_subtask_ids.add(subtask.id)
                    continue # Move to the next runnable subtask in this pass

                # As per current design, only process the first tool string if multiple are present.
                tool_string = subtask.assigned_tools[0]
                tool_id = ""
                method_name = ""

                try:
                    parts = tool_string.split("__", 1)
                    if len(parts) == 2:
                        tool_id, method_name = parts[0], parts[1]
                    else:
                        raise ValueError("Invalid tool string format. Expected 'ToolID__methodName'.")
                except ValueError as e:
                    logger.error(f"Failed to parse tool_string '{tool_string}' for subtask {subtask.id}: {e}")
                    subtask_results.append({"error": f"Failed to parse tool_string: {tool_string}", "details": str(e)})
                    subtask_failed_flag = True

                if not subtask_failed_flag:
                    logger.info(f"Attempting to use tool: {tool_id}, method: {method_name} for subtask {subtask.id}")

                    all_schemas = self.tool_orchestrator.get_tool_schemas_for_llm()
                    schema_for_tool = next((s for s in all_schemas if s.get('name') == tool_string), None)

                    if not schema_for_tool:
                        logger.error(f"Schema not found for tool_string '{tool_string}' in subtask {subtask.id}.")
                        subtask_results.append({"error": f"Schema not found for tool: {tool_string}"})
                        subtask_failed_flag = True
                    else:
                        params = {}
                        llm_param_generation_attempts = 0
                        raw_llm_output_for_error = "Not available"

                        while llm_param_generation_attempts <= MAX_PARAM_GENERATION_RETRIES:
                            llm_param_generation_attempts += 1
                            logger.info(f"Attempt {llm_param_generation_attempts}/{MAX_PARAM_GENERATION_RETRIES+1} to generate parameters for tool {tool_string} for subtask {subtask.id}")
                            try:
                                param_prompt_messages = [
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

                                llm_response_for_params = await make_llm_api_call(
                                    messages=param_prompt_messages,
                                    llm_model="gpt-3.5-turbo-0125",
                                    temperature=0.0,
                                    json_mode=True
                                )

                                if isinstance(llm_response_for_params, dict): # json_mode=True should return dict
                                    params = llm_response_for_params
                                    raw_llm_output_for_error = json.dumps(params) # For logging on subsequent errors
                                elif isinstance(llm_response_for_params, str): # Fallback parsing
                                    raw_llm_output_for_error = llm_response_for_params
                                    params = json.loads(llm_response_for_params)
                                else:
                                    raw_llm_output_for_error = str(llm_response_for_params)
                                    logger.error(f"Unexpected parameter type from LLM: {type(llm_response_for_params)}. Content: {llm_response_for_params}")
                                    raise TypeError(f"Expected dict or JSON string from LLM, got {type(llm_response_for_params)}")

                                logger.info(f"LLM generated parameters for {tool_string}: {params}")
                                subtask_failed_flag = False # Clear flag as params generated
                                break # Success, exit retry loop

                            except json.JSONDecodeError as e:
                                logger.warning(f"Attempt {llm_param_generation_attempts}: Failed to decode JSON parameters from LLM for tool {tool_string}: {e}. Response: {raw_llm_output_for_error}")
                                if llm_param_generation_attempts > MAX_PARAM_GENERATION_RETRIES:
                                    subtask_results.append({"error": "LLM failed to generate valid JSON parameters after retries", "details": str(e), "raw_llm_output": raw_llm_output_for_error})
                                    subtask_failed_flag = True
                            except Exception as e_llm:
                                logger.warning(f"Attempt {llm_param_generation_attempts}: Error calling LLM for parameters for tool {tool_string}: {e_llm}", exc_info=True)
                                if llm_param_generation_attempts > MAX_PARAM_GENERATION_RETRIES:
                                    subtask_results.append({"error": "Error obtaining parameters from LLM after retries", "details": str(e_llm)})
                                    subtask_failed_flag = True

                        if not subtask_failed_flag: # If params were generated successfully
                            try:
                                tool_result: ToolResult = await self.tool_orchestrator.execute_tool(tool_id, method_name, params)
                                # Assuming ToolResult has a to_dict() method
                                if hasattr(tool_result, 'to_dict') and callable(tool_result.to_dict):
                                    subtask_results.append(tool_result.to_dict())
                                else: # Fallback if to_dict() is missing
                                    subtask_results.append({
                                        "tool_id": tool_result.tool_id, "execution_id": tool_result.execution_id,
                                        "status": tool_result.status, "result": tool_result.result,
                                        "error": tool_result.error, "start_time": str(tool_result.start_time), # Ensure serializable
                                        "end_time": str(tool_result.end_time) # Ensure serializable
                                    })

                                if tool_result.status == "failed":
                                    logger.error(f"Tool execution failed for subtask {subtask.id}: {tool_result.error}")
                                    subtask_failed_flag = True
                                else:
                                    logger.info(f"Tool execution successful for subtask {subtask.id}. Result: {tool_result.result}")

                            except Exception as e_exec:
                                logger.error(f"Exception during tool execution for subtask {subtask.id}, tool {tool_string}: {e_exec}", exc_info=True)
                                subtask_results.append({"error": f"Exception during tool execution: {tool_string}", "details": str(e_exec)})
                                subtask_failed_flag = True


                # After processing (or attempting to process) the first tool for the current subtask
                if subtask_failed_flag:
                    await self.task_manager.update_task(subtask.id, {"status": "failed", "output": json.dumps(subtask_results)})
                    await self._send_user_message(f"Subtask FAILED: {subtask.name}. Details: {json.dumps(subtask_results)}")
                    logger.error(f"Plan execution failed at subtask {subtask.id} ({subtask.name}). Stopping plan.")
                    plan_failed = True # Mark entire plan as failed
                    break # Break from loop over runnable_subtasks for this pass
                else:
                    await self.task_manager.update_task(subtask.id, {"status": "completed", "output": json.dumps(subtask_results)})
                    completed_subtask_ids.add(subtask.id) # Add to completed set
                    await self._send_user_message(f"Subtask COMPLETED: {subtask.name}. Output: {json.dumps(subtask_results)}")
                    logger.info(f"Subtask {subtask.id} ({subtask.name}) completed successfully.")

            if plan_failed: # If a subtask failure occurred in the inner loop, break outer while loop
                break

            if not progress_made_in_pass and pending_subtasks_exist:
                 logger.error(f"Deadlock detected in plan {self.main_task_id} on second check. No progress made but pending tasks exist.")
                 await self._send_user_message("Error: Plan execution cannot continue due to a deadlock (no progress made on pending tasks).")
                 plan_failed = True
                 break


        # After outer while loop
        final_main_task_status = "completed" if not plan_failed else "failed"
        final_main_task_message = "All subtasks processed successfully." if not plan_failed else "Plan execution failed due to one or more subtask failures or deadlock."

        logger.info(f"Plan execution for main_task_id: {self.main_task_id} finished with status: {final_main_task_status}")
        await self._send_user_message(f"Plan '{main_task.name}' {final_main_task_status.upper()}. {final_main_task_message}")
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
