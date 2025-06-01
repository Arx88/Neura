import os
import json
import re
import time
from uuid import uuid4
from typing import Optional, Any, Dict # Added Dict

# from agent.tools.message_tool import MessageTool
from agent.tools.message_tool import MessageTool
from agent.tools.sb_deploy_tool import SandboxDeployTool
from agent.tools.sb_expose_tool import SandboxExposeTool
from agent.tools.web_search_tool import SandboxWebSearchTool
from dotenv import load_dotenv
from utils.config import config

from agentpress.thread_manager import ThreadManager
from agentpress.response_processor import ProcessorConfig
from agent.tools.sb_shell_tool import SandboxShellTool
from agent.tools.sb_files_tool import SandboxFilesTool
from agent.tools.sb_browser_tool import SandboxBrowserTool
from agent.tools.python_tool import PythonTool # Import PythonTool
from agent.tools.data_providers_tool import DataProvidersTool
from agent.tools.visualization_tool import DataVisualizationTool # Import DataVisualizationTool
from agent.prompt import get_system_prompt
from utils.logger import logger
from utils.auth_utils import get_account_id_from_thread
from services.billing import check_billing_status
from agent.tools.sb_vision_tool import SandboxVisionTool
from services.langfuse import langfuse
from langfuse.client import StatefulTraceClient
from services.langfuse import langfuse
from agent.gemini_prompt import get_gemini_system_prompt

# Imports for TaskPlanner
from agentpress.plan_executor import PlanExecutor
from agentpress.task_planner import TaskPlanner
# from agentpress.task_state_manager import TaskStateManager
# from agentpress.task_storage_supabase import SupabaseTaskStorage
from agentpress.tool_orchestrator import ToolOrchestrator
# from agentpress.utils.message_assembler import MessageAssembler # Removed
# from agentpress.utils.json_helpers import extract_json_from_response # Removed
# from agentpress.utils.json_helpers import format_for_yield

# from services.llm import make_llm_api_call # Removed
from agentpress.task_state_manager import TaskStateManager

load_dotenv()

# Helper function as suggested by user
async def _add_task_log_message(tsm: TaskStateManager, task_id_to_update: str, message_text: str, log_type: str = "info"):
    current_task = await tsm.get_task(task_id_to_update)
    if current_task:
        log_entry = {"timestamp": time.time(), "type": log_type, "message": message_text}
        run_logs = current_task.metadata.get("run_logs", [])
        if not isinstance(run_logs, list):
            run_logs = []
        updated_logs = run_logs + [log_entry]
        new_metadata = current_task.metadata.copy()
        new_metadata["run_logs"] = updated_logs
        await tsm.update_task(
            task_id_to_update,
            {"metadata": new_metadata}
        )
    else:
        logger.warning(f"Task {task_id_to_update} not found when trying to add log: '{message_text}'")

# Helper function for storing plan - might be simplified/removed if TaskPlanner handles it
async def _store_plan_in_task(tsm: TaskStateManager, task_id_to_update: str, plan_data: list, progress: float = 0.1):
    current_task = await tsm.get_task(task_id_to_update)
    if current_task:
        new_metadata = current_task.metadata.copy()
        new_metadata["execution_plan"] = plan_data # Store the plan
        updates = {"metadata": new_metadata, "progress": progress}
        if current_task.status == "pending_planning":
            updates["status"] = "planned"
        await tsm.update_task(task_id_to_update, updates)
    else:
        logger.error(f"Task {task_id_to_update} not found when trying to store plan.")

def detect_visualization_request(request_text: str):
    """Detect if a request is asking for a visualization."""
    visualization_keywords = [
        "gráfico", "grafico", "visualización", "visualizacion", "chart", "graph", "plot",
        "diagrama", "barras", "líneas", "lineas", "pastel", "pie", "histograma", "histogram"
    ]
    
    request_lower = request_text.lower()
    # Check if any visualization keyword is in the request
    if any(keyword in request_lower for keyword in visualization_keywords):
        # Determine the type of visualization
        if any(keyword in request_lower for keyword in ["barras", "bar"]):
            return "bar_chart"
        elif any(keyword in request_lower for keyword in ["líneas", "lineas", "line"]):
            return "line_chart"
        elif any(keyword in request_lower for keyword in ["pastel", "pie"]):
            return "pie_chart"
        elif any(keyword in request_lower for keyword in ["histograma", "histogram"]):
            return "histogram"
        else:
            return "generic_visualization" # Could be a more sophisticated detection or default
    
    return None

async def run_agent(
    thread_id: str,
    project_id: str,
    stream: bool,
    tool_orchestrator: ToolOrchestrator,
    task_state_manager: TaskStateManager,  # New parameter
    thread_manager: Optional[ThreadManager] = None,
    native_max_auto_continues: int = 25,
    max_iterations: int = 100,
    model_name: str = "anthropic/claude-3-7-sonnet-latest",
    enable_thinking: Optional[bool] = False,
    reasoning_effort: Optional[str] = 'low',
    enable_context_manager: bool = True,
    trace: Optional[StatefulTraceClient] = None
) -> None: # Changed return type
    """Run the development agent with specified configuration."""
    # Old add_task_message (inner function) is removed.
    # message_assembler = MessageAssembler() # Removed

    try:
        logger.info(f"Entering run_agent function: thread_id={{thread_id}}, project_id={{project_id}}, agent_run_id={{trace.id if trace else 'N/A'}}, model_name={{model_name}}, stream_param_ignored={{stream}}")
        if not trace:
            trace = langfuse.trace(name="run_agent_orchestration", session_id=thread_id, metadata={{"project_id": project_id}})
        
        if not thread_manager: # Added this check as per existing code
           thread_manager = ThreadManager(tool_orchestrator=tool_orchestrator, trace=trace)
        client = await thread_manager.db.client

        account_id = await get_account_id_from_thread(client, thread_id)
        if not account_id:
            logger.error(f"Could not determine account ID for thread_id: {{thread_id}}")
            await task_state_manager.fail_task(task_id=thread_id, error=f"Could not determine account ID for thread {{thread_id}}")
            raise ValueError(f"Could not determine account ID for thread {{thread_id}}")

        project_result = await client.table('projects').select('*').eq('project_id', project_id).execute()
        if not project_result.data or len(project_result.data) == 0:
            logger.error(f"Project {{project_id}} not found.")
            await task_state_manager.fail_task(task_id=thread_id, error=f"Project {{project_id}} not found")
            raise ValueError(f"Project {{project_id}} not found")

        # Tool initialization (kept)
        shell_tool = SandboxShellTool(project_id=project_id, thread_manager=thread_manager)
        thread_manager.add_tool(shell_tool)
        files_tool = SandboxFilesTool(project_id=project_id, thread_manager=thread_manager)
        thread_manager.add_tool(files_tool)
        browser_tool = SandboxBrowserTool(project_id=project_id, thread_id=thread_id, thread_manager=thread_manager)
        thread_manager.add_tool(browser_tool)
        deploy_tool = SandboxDeployTool(project_id=project_id, thread_manager=thread_manager)
        thread_manager.add_tool(deploy_tool)
        expose_tool = SandboxExposeTool(project_id=project_id, thread_manager=thread_manager)
        thread_manager.add_tool(expose_tool)
        message_tool = MessageTool()
        thread_manager.add_tool(message_tool)
        web_search_tool = SandboxWebSearchTool(project_id=project_id, thread_manager=thread_manager)
        thread_manager.add_tool(web_search_tool)
        vision_tool = SandboxVisionTool(project_id=project_id, thread_id=thread_id, thread_manager=thread_manager)
        thread_manager.add_tool(vision_tool)
        python_tool = PythonTool(project_id=project_id, thread_manager=thread_manager)
        thread_manager.add_tool(python_tool)
        visualization_tool = DataVisualizationTool(project_id=project_id, thread_manager=thread_manager)
        thread_manager.add_tool(visualization_tool)
        if config.RAPID_API_KEY:
            data_providers_tool = DataProvidersTool()
            thread_manager.add_tool(data_providers_tool)
        logger.debug("Tools initialized.")

        initial_prompt_text = None
        first_user_message_query = await client.table('messages').select('content').eq('thread_id', thread_id).eq('type', 'user').order('created_at', desc=False).limit(1).execute()
        
        if first_user_message_query.data:
            try:
                content_json_str = first_user_message_query.data[0]['content']
                content_data = json.loads(content_json_str)
                initial_prompt_text = content_data.get('content', '')
                if not initial_prompt_text:
                     logger.warning(f"First user message for task {thread_id} has empty content.")
            except json.JSONDecodeError:
                logger.error(f"Failed to parse first user message content JSON for task {thread_id}: {first_user_message_query.data[0]['content']}", exc_info=True)
                initial_prompt_text = ""
            except Exception as e:
                logger.error(f"Error extracting prompt from first user message for task {thread_id}: {e}", exc_info=True)
                initial_prompt_text = ""

        if initial_prompt_text is None:
            logger.error(f"No initial user message object found for task {{thread_id}}.")
            await task_state_manager.fail_task(task_id=thread_id, error="No initial message object found.")
            return
        if not initial_prompt_text:
            logger.error(f"No initial user message content found for task {{thread_id}}. Cannot proceed.")
            await task_state_manager.fail_task(task_id=thread_id, error="No initial prompt content found for planning.")
            return

        main_task = await task_state_manager.get_task(thread_id)
        current_task_id: str
        if not main_task:
            logger.info(f"Main task with ID {{thread_id}} not found. Creating a new planning task.")
            new_planning_task = await task_state_manager.create_task(
                name=f"Planning for: {{initial_prompt_text[:50]}}...",
                description=f"Full prompt: {{initial_prompt_text}}",
                status="pending_planning",
                metadata={{"original_thread_id": thread_id, "prompt": initial_prompt_text, "model_name": model_name}}
            )
            if not new_planning_task:
                logger.error(f"Failed to create main planning task for thread_id {{thread_id}}.")
                raise Exception(f"TaskStateManager failed to create a new planning task for thread_id {{thread_id}}")
            current_task_id = new_planning_task.id
            logger.info(f"New planning task created with ID: {{current_task_id}} for thread_id {{thread_id}}.")
            await task_state_manager.set_task_status(task_id=current_task_id, status="running", progress=0.01)
        else:
            logger.info(f"Using existing task {{thread_id}} as current_task_id.")
            current_task_id = thread_id
            await task_state_manager.set_task_status(task_id=current_task_id, status="running", progress=main_task.progress or 0.01, metadata={{**main_task.metadata, "model_name": model_name}})

        await _add_task_log_message(task_state_manager, current_task_id, "Agent run started. Initializing planning.")
        await task_state_manager.update_task(current_task_id, {{"progress": 0.05}})

        task_planner = TaskPlanner(
            task_manager=task_state_manager,
            tool_orchestrator=tool_orchestrator
        )

        planned_main_task = await task_planner.plan_task(
            task_description=initial_prompt_text,
            # Context for plan_task to update current_task_id directly (requires change in plan_task)
            # For now, assuming plan_task creates its own main task as per its original design.
            # If plan_task is modified to update an existing task:
            # parent_task_id_for_planning=current_task_id
        )

        if not planned_main_task or planned_main_task.status == "planning_failed":
            error_msg = planned_main_task.metadata.get("error", "Planning failed, no subtasks generated.") if planned_main_task else "TaskPlanner.plan_task returned None."
            logger.error(f"Planning phase failed for task description: '{{initial_prompt_text[:100]}}...'. Error: {{error_msg}}")
            await _add_task_log_message(task_state_manager, current_task_id, f"ERROR: Planning phase failed: {{error_msg}}", log_type="error")
            await task_state_manager.fail_task(task_id=current_task_id, error=error_msg, progress=0.1)
            return

        final_main_task_id = planned_main_task.id
        await _add_task_log_message(task_state_manager, current_task_id, f"Planning phase completed. Main plan task ID: {{final_main_task_id}}.")
        await _add_task_log_message(task_state_manager, final_main_task_id, "Phase 1 (Planning) completed by TaskPlanner.")

        await _add_task_log_message(task_state_manager, final_main_task_id, "Phase 2: Execution starting.")
        await task_state_manager.update_task(final_main_task_id, {{"status": "executing", "progress": 0.2}})

        plan_executor = PlanExecutor(
            tool_orchestrator=tool_orchestrator,
            task_state_manager=task_state_manager,
            main_task_id=final_main_task_id
        )

        await plan_executor.execute_plan_for_task(final_main_task_id)

        logger.info(f"Plan execution completed for main task {{final_main_task_id}}")
        await _add_task_log_message(task_state_manager, final_main_task_id, "Plan execution completed.")

        final_task_state = await task_state_manager.get_task(final_main_task_id)
        if final_task_state and final_task_state.status not in ["failed", "completed"]:
            logger.warning(f"Main plan task {{final_main_task_id}} ended in status '{{final_task_state.status}}'. Forcing complete.")
            await task_state_manager.complete_task(task_id=final_main_task_id, result={{"summary": "Task execution phase concluded."}}, progress=1.0)

        if current_task_id != final_main_task_id:
            # Need to fetch main_task again in case its metadata was updated by previous set_task_status
            current_run_task_state = await task_state_manager.get_task(current_task_id)
            current_run_task_metadata = current_run_task_state.metadata if current_run_task_state else {}

            if final_task_state and final_task_state.status == "completed":
                await task_state_manager.complete_task(task_id=current_task_id, result={{"summary": f"Agent run completed. Plan executed under task {{final_main_task_id}}."}}, progress=1.0)
            elif final_task_state and final_task_state.status == "failed":
                await task_state_manager.fail_task(task_id=current_task_id, error=f"Agent run failed. Plan execution failed under task {{final_main_task_id}}. Reason: {{final_task_state.error}}", progress=final_task_state.progress)
            else:
                await task_state_manager.update_task(current_task_id, {{"status":"unknown_completion", "metadata": {{**current_run_task_metadata, "final_plan_task_id": final_main_task_id, "final_plan_task_status": final_task_state.status if final_task_state else 'unknown' }} }})

        langfuse.flush()
        return

    except Exception as e:
        logger.error(f"Error in run_agent orchestration for thread {{thread_id}}: {{e}}", exc_info=True)
        error_summary = f"An orchestration error occurred: {{str(e)}}"

        task_id_for_failure = thread_id
        if 'current_task_id' in locals() and locals()['current_task_id']: # Check if current_task_id was defined
            task_id_for_failure = locals()['current_task_id']
        elif 'final_main_task_id' in locals() and locals()['final_main_task_id']:
            task_id_for_failure = locals()['final_main_task_id']

        try:
            if task_state_manager:
                log_attempt_message = f"Attempting to mark task {{task_id_for_failure}} as failed due to orchestration error: {{error_summary}}"
                logger.debug(log_attempt_message)
                await task_state_manager.fail_task(task_id=task_id_for_failure, error=error_summary)
        except Exception as tse:
            logger.error(f"Further error when trying to mark task {{task_id_for_failure}} as failed: {{tse}}", exc_info=True)
        
        raise
    finally:
        if trace and trace.client:
             langfuse.flush()


# # TESTING

# async def test_agent():
#     """Test function to run the agent with a sample query"""
#     from agentpress.thread_manager import ThreadManager
#     from services.supabase import DBConnection

#     # Initialize ThreadManager
#     thread_manager = ThreadManager()

#     # Create a test thread directly with Postgres function
#     client = await DBConnection().client

#     try:
#         # Get user's personal account
#         account_result = await client.rpc('get_personal_account').execute()

#         # if not account_result.data:
#         #     print("Error: No personal account found")
#         #     return

#         account_id = "a5fe9cb6-4812-407e-a61c-fe95b7320c59"

#         if not account_id:
#             print("Error: Could not get account ID")
#             return

#         # Find or create a test project in the user's account
#         project_result = await client.table('projects').select('*').eq('name', 'test11').eq('account_id', account_id).execute()

#         if project_result.data and len(project_result.data) > 0:
#             # Use existing test project
#             project_id = project_result.data[0]['project_id']
#             print(f"\n🔄 Using existing test project: {project_id}")
#         else:
#             # Create new test project if none exists
#             project_result = await client.table('projects').insert({
#                 "name": "test11",
#                 "account_id": account_id
#             }).execute()
#             project_id = project_result.data[0]['project_id']
#             print(f"\n✨ Created new test project: {project_id}")

#         # Create a thread for this project
#         thread_result = await client.table('threads').insert({
#             'project_id': project_id,
#             'account_id': account_id
#         }).execute()
#         thread_data = thread_result.data[0] if thread_result.data else None

#         if not thread_data:
#             print("Error: No thread data returned")
#             return

#         thread_id = thread_data['thread_id']
#     except Exception as e:
#         print(f"Error setting up thread: {str(e)}")
#         return

#     print(f"\n🤖 Agent Thread Created: {thread_id}\n")

#     # Interactive message input loop
#     while True:
#         # Get user input
#         user_message = input("\n💬 Enter your message (or 'exit' to quit): ")
#         if user_message.lower() == 'exit':
#             break

#         if not user_message.strip():
#             print("\n🔄 Running agent...\n")
#             await process_agent_response(thread_id, project_id, thread_manager)
#             continue

#         # Add the user message to the thread
#         await thread_manager.add_message(
#             thread_id=thread_id,
#             type="user",
#             content={
#                 "role": "user",
#                 "content": user_message
#             },
#             is_llm_message=True
#         )

#         print("\n🔄 Running agent...\n")
#         await process_agent_response(thread_id, project_id, thread_manager)

#     print("\n👋 Test completed. Goodbye!")

# async def process_agent_response(
#     thread_id: str,
#     project_id: str,
#     thread_manager: ThreadManager,
#     stream: bool = True,
#     model_name: str = "anthropic/claude-3-7-sonnet-latest",
#     enable_thinking: Optional[bool] = False,
#     reasoning_effort: Optional[str] = 'low',
#     enable_context_manager: bool = True
# ):
#     """Process the streaming response from the agent."""
#     chunk_counter = 0
#     current_response = ""
#     tool_usage_counter = 0 # Renamed from tool_call_counter as we track usage via status

#     # Create a test sandbox for processing with a unique test prefix to avoid conflicts with production sandboxes
#     sandbox_pass = str(uuid4())
#     sandbox = create_sandbox(sandbox_pass)

#     # Store the original ID so we can refer to it
#     original_sandbox_id = sandbox.id

#     # Generate a clear test identifier
#     test_prefix = f"test_{uuid4().hex[:8]}_"
#     logger.info(f"Created test sandbox with ID {original_sandbox_id} and test prefix {test_prefix}")

#     # Log the sandbox URL for debugging
#     print(f"\033[91mTest sandbox created: {str(sandbox.get_preview_link(6080))}/vnc_lite.html?password={sandbox_pass}\033[0m")

#     async for chunk in run_agent(
#         thread_id=thread_id,
#         project_id=project_id,
#         sandbox=sandbox,
#         stream=stream,
#         thread_manager=thread_manager,
#         native_max_auto_continues=25,
#         model_name=model_name,
#         enable_thinking=enable_thinking,
#         reasoning_effort=reasoning_effort,
#         enable_context_manager=enable_context_manager
#     ):
#         chunk_counter += 1
#         # print(f"CHUNK: {chunk}") # Uncomment for debugging

#         if chunk.get('type') == 'assistant':
#             # Try parsing the content JSON
#             try:
#                 # Handle content as string or object
#                 content = chunk.get('content', '{}')
#                 if isinstance(content, str):
#                     content_json = json.loads(content)
#                 else:
#                     content_json = content

#                 actual_content = content_json.get('content', '')
#                 # Print the actual assistant text content as it comes
#                 if actual_content:
#                      # Check if it contains XML tool tags, if so, print the whole tag for context
#                     if '<' in actual_content and '>' in actual_content:
#                          # Avoid printing potentially huge raw content if it's not just text
#                          if len(actual_content) < 500: # Heuristic limit
#                             print(actual_content, end='', flush=True)
#                          else:
#                              # Maybe just print a summary if it's too long or contains complex XML
#                              if '</ask>' in actual_content: print("<ask>...</ask>", end='', flush=True)
#                              elif '</complete>' in actual_content: print("<complete>...</complete>", end='', flush=True)
#                              else: print("<tool_call>...</tool_call>", end='', flush=True) # Generic case
#                     else:
#                         # Regular text content
#                          print(actual_content, end='', flush=True)
#                     current_response += actual_content # Accumulate only text part
#             except json.JSONDecodeError:
#                  # If content is not JSON (e.g., just a string chunk), print directly
#                  raw_content = chunk.get('content', '')
#                  print(raw_content, end='', flush=True)
#                  current_response += raw_content
#             except Exception as e:
#                  print(f"\nError processing assistant chunk: {e}\n")

#         elif chunk.get('type') == 'tool': # Updated from 'tool_result'
#             # Add timestamp and format tool result nicely
#             tool_name = "UnknownTool" # Try to get from metadata if available
#             result_content = "No content"

#             # Parse metadata - handle both string and dict formats
#             metadata = chunk.get('metadata', {})
#             if isinstance(metadata, str):
#                 try:
#                     metadata = json.loads(metadata)
#                 except json.JSONDecodeError:
#                     metadata = {}

#             linked_assistant_msg_id = metadata.get('assistant_message_id')
#             parsing_details = metadata.get('parsing_details')
#             if parsing_details:
#                 tool_name = parsing_details.get('xml_tag_name', 'UnknownTool') # Get name from parsing details

#             try:
#                 # Content is a JSON string or object
#                 content = chunk.get('content', '{}')
#                 if isinstance(content, str):
#                     content_json = json.loads(content)
#                 else:
#                     content_json = content

#                 # The actual tool result is nested inside content.content
#                 tool_result_str = content_json.get('content', '')
#                  # Extract the actual tool result string (remove outer <tool_result> tag if present)
#                 match = re.search(rf'<{tool_name}>(.*?)</{tool_name}>', tool_result_str, re.DOTALL)
#                 if match:
#                     result_content = match.group(1).strip()
#                     # Try to parse the result string itself as JSON for pretty printing
#                     try:
#                         result_obj = json.loads(result_content)
#                         result_content = json.dumps(result_obj, indent=2)
#                     except json.JSONDecodeError:
#                          # Keep as string if not JSON
#                          pass
#                 else:
#                      # Fallback if tag extraction fails
#                      result_content = tool_result_str

#             except json.JSONDecodeError:
#                 result_content = chunk.get('content', 'Error parsing tool content')
#             except Exception as e:
#                 result_content = f"Error processing tool chunk: {e}"

#             print(f"\n\n🛠️  TOOL RESULT [{tool_name}] → {result_content}")

#         elif chunk.get('type') == 'status':
#             # Log tool status changes
#             try:
#                 # Handle content as string or object
#                 status_content = chunk.get('content', '{}')
#                 if isinstance(status_content, str):
#                     status_content = json.loads(status_content)

#                 status_type = status_content.get('status_type')
#                 function_name = status_content.get('function_name', '')
#                 xml_tag_name = status_content.get('xml_tag_name', '') # Get XML tag if available
#                 tool_name = xml_tag_name or function_name # Prefer XML tag name

#                 if status_type == 'tool_started' and tool_name:
#                     tool_usage_counter += 1
#                     print(f"\n⏳ TOOL STARTING #{tool_usage_counter} [{tool_name}]")
#                     print("  " + "-" * 40)
#                     # Return to the current content display
#                     if current_response:
#                         print("\nContinuing response:", flush=True)
#                         print(current_response, end='', flush=True)
#                 elif status_type == 'tool_completed' and tool_name:
#                      status_emoji = "✅"
#                      print(f"\n{status_emoji} TOOL COMPLETED: {tool_name}")
#                 elif status_type == 'finish':
#                      finish_reason = status_content.get('finish_reason', '')
#                      if finish_reason:
#                          print(f"\n📌 Finished: {finish_reason}")
#                 # else: # Print other status types if needed for debugging
#                 #    print(f"\nℹ️ STATUS: {chunk.get('content')}")

#             except json.JSONDecodeError:
#                  print(f"\nWarning: Could not parse status content JSON: {chunk.get('content')}")
#             except Exception as e:
#                 print(f"\nError processing status chunk: {e}")


#         # Removed elif chunk.get('type') == 'tool_call': block

#     # Update final message
#     print(f"\n\n✅ Agent run completed with {tool_usage_counter} tool executions")

#     # Try to clean up the test sandbox if possible
#     try:
#         # Attempt to delete/archive the sandbox to clean up resources
#         # Note: Actual deletion may depend on the Daytona SDK's capabilities
#         logger.info(f"Attempting to clean up test sandbox {original_sandbox_id}")
#         # If there's a method to archive/delete the sandbox, call it here
#         # Example: daytona.archive_sandbox(sandbox.id)
#     except Exception as e:
#         logger.warning(f"Failed to clean up test sandbox {original_sandbox_id}: {str(e)}")

# if __name__ == "__main__":
#     import asyncio

#     # Configure any environment variables or setup needed for testing
#     load_dotenv()  # Ensure environment variables are loaded

#     # Run the test function
#     asyncio.run(test_agent())