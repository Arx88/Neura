import os
import json
import re
from uuid import uuid4
from typing import Optional, Any

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
# from agentpress.task_state_manager import TaskStateManager # Removed - Assuming this is not the one from the prompt, let's see if it's needed later
# from agentpress.task_storage_supabase import SupabaseTaskStorage # Removed
from agentpress.tool_orchestrator import ToolOrchestrator # Keep - used by ThreadManager
from agentpress.utils.message_assembler import MessageAssembler
from agentpress.utils.json_helpers import extract_json_from_response
# from agentpress.utils.json_helpers import format_for_yield # Removed

# Assuming make_llm_api_call and TaskStateManager from the prompt are different
# and might be part of a services directory if they were to be used directly here.
# For now, I'll rely on existing mechanisms or what's available in ThreadManager.
# If direct LLM calls or a different TaskStateManager are needed, the subtask might be underspecified for this file structure.
from services.llm import make_llm_api_call # Adding as per prompt
from agentpress.task_state_manager import TaskStateManager # Corrected import path to direct import

load_dotenv()

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
):
    """Run the development agent with specified configuration."""
    message_assembler = MessageAssembler()
    # task_state_manager = None # Removed as it's now a parameter
    try:
        logger.info(f"Entering run_agent function: thread_id={thread_id}, project_id={project_id}, agent_run_id=trace.id if trace else 'N/A', model_name={model_name}, stream={stream}")
        logger.info(f"🚀 Starting agent with model: {model_name} for thread_id: {thread_id}, project_id: {project_id}")

        if not trace:
            logger.debug("No existing trace found, creating new trace for run_agent.")
            trace = langfuse.trace(name="run_agent", session_id=thread_id, metadata={"project_id": project_id})
        else:
            logger.debug("Using existing trace for run_agent.")
        
        logger.debug("Initializing ThreadManager...")
        # Ensure thread_manager is initialized if not passed (though it's expected to be)
        if not thread_manager:
            thread_manager = ThreadManager(tool_orchestrator=tool_orchestrator, trace=trace)
        client = await thread_manager.db.client
        logger.debug("ThreadManager initialized and database client obtained.")

        # TaskStateManager is now passed as a parameter, so local instantiation is removed.
        # The check for config.redis_client and the instantiation line:
        # task_state_manager = TaskStateManager(thread_id, config.redis_client)
        # are removed.

        logger.debug(f"Attempting to get account ID for thread_id: {thread_id}...")
        account_id = await get_account_id_from_thread(client, thread_id)
        if not account_id:
            logger.error(f"Could not determine account ID for thread_id: {thread_id}")
            raise ValueError(f"Could not determine account ID for thread {thread_id}")
        logger.debug(f"Account ID {account_id} retrieved for thread_id: {thread_id}.")

        logger.debug(f"Attempting to get project {project_id}...")
        project_result = await client.table('projects').select('*').eq('project_id', project_id).execute()
        if not project_result.data or len(project_result.data) == 0:
            logger.error(f"Project {project_id} not found.")
            raise ValueError(f"Project {project_id} not found")
        logger.debug(f"Project {project_id} retrieved successfully.")

        project_data = project_result.data[0]
        sandbox_info = project_data.get('sandbox', {})
        if not sandbox_info.get('id'): # This check might be too strict if planning doesn't need a sandbox immediately
            logger.warning(f"No sandbox ID found in project_data for project {project_id}. Continuing with planning.")
            # raise ValueError(f"No sandbox found for project {project_id}") # Soften this for planning
        logger.debug(f"Sandbox ID {sandbox_info.get('id')} retrieved for project {project_id}.")

        logger.debug("Initializing tools (for potential use by planner or executor)...")
        # Tools are initialized and added to thread_manager, which makes them available via tool_orchestrator
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

        # Retrieve the initial prompt for planning
        initial_prompt_text = None
        # Fetch the earliest user message in the thread
        first_user_message_query = await client.table('messages').select('content').eq('thread_id', thread_id).eq('type', 'user').order('created_at', ascending=True).limit(1).execute()
        
        if first_user_message_query.data:
            try:
                # Message content is a JSON string, parse it
                content_json_str = first_user_message_query.data[0]['content']
                content_data = json.loads(content_json_str)
                # The actual user text is within content_data['content']
                initial_prompt_text = content_data.get('content', '')
                if not initial_prompt_text: # Handles case where 'content' key holds empty string
                     logger.warning(f"First user message for task {thread_id} has empty content.")
                     # Proceed with empty string to allow planner to decide, or error out if required by business logic
            except json.JSONDecodeError:
                logger.error(f"Failed to parse first user message content JSON for task {thread_id}: {first_user_message_query.data[0]['content']}", exc_info=True)
                initial_prompt_text = "" # Fallback to empty string to avoid None
            except Exception as e:
                logger.error(f"Error extracting prompt from first user message for task {thread_id}: {e}", exc_info=True)
                initial_prompt_text = "" # Fallback

        if initial_prompt_text is None: # Only if query returned no data
            logger.error(f"No initial user message found for task {thread_id}. Cannot proceed with planning.")
            await task_state_manager.complete_task(status="error", summary="No initial prompt found for planning.")
            return # Exit run_agent

        # Step 1: Planning
        await task_state_manager.set_status("running") # As per original prompt
        await task_state_manager.add_message("Starting task...") # As per original prompt
        await task_state_manager.add_message("Phase 1: Planning...") # Log planning phase start

        task_planner = TaskPlanner(
            tool_orchestrator=tool_orchestrator,
            task_state_manager=task_state_manager, # Pass the new TSM
            model_name=model_name,
        )
        
        # Files are not explicitly passed; TaskPlanner might need to handle this or assume None
        planning_messages = task_planner.construct_planning_messages(initial_prompt_text, files=None) # Assuming files=None

        logger.info(f"Phase 1: Planning for task {thread_id} with prompt: '{initial_prompt_text[:100]}...'")

        llm_response_for_plan = make_llm_api_call(
            messages=planning_messages,
            model_name=model_name,
            tool_orchestrator=None, # No tools executed during planning phase
            tools=None,
            thread_id=thread_id, # task_id from prompt
        )

        llm_response_str_plan = ""
        async for chunk in llm_response_for_plan:
            # Ensure chunk structure is as expected by make_llm_api_call's streaming response
            if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
                content = chunk.choices[0].delta.content
                llm_response_str_plan += content

        logger.info(f"Raw LLM response for plan (task {thread_id}): {llm_response_str_plan}")
        # await task_state_manager.add_log_message(f"LLM response for plan: {llm_response_str_plan}") # Method not in prompt TSM

        plan_json = extract_json_from_response(llm_response_str_plan)

        if not plan_json or "plan" not in plan_json:
            error_summary = "Failed to generate a valid plan from LLM response."
            logger.error(f"{error_summary} Task ID: {thread_id}. LLM response: {llm_response_str_plan}")
            # await task_state_manager.add_error_message(error_summary) # Method not in prompt TSM
            await task_state_manager.fail_task(task_id=thread_id, error=error_summary)
            return

        # Step 2: Execution
        logger.info(f"Phase 1 (Planning) completed for task {thread_id}. Plan: {plan_json['plan']}. Phase 2 (Execution) starting...")
        await task_state_manager.add_message("Phase 1 (Planning) completed. Phase 2 (Execution) starting...")
        await task_state_manager.set_plan(plan_json["plan"])

        plan_executor = PlanExecutor(
            tool_orchestrator=tool_orchestrator,
            task_state_manager=task_state_manager, # Pass the new TSM
        )

        # The execute_plan method will internally handle calls to tools via tool_orchestrator
        # and update task state via task_state_manager.
        # It should also handle yielding messages if 'stream' is True.
        # This part needs to align with how PlanExecutor is implemented.
        # For now, assuming execute_plan is a comprehensive blocking call as per prompt.
        await plan_executor.execute_plan(plan_json["plan"])

        logger.info(f"Plan execution completed for task {thread_id}")
        await task_state_manager.add_message("Plan execution completed.") # Log completion
        await task_state_manager.complete_task(task_id=thread_id, result={"summary": "Task completed successfully via plan."})

        # If streaming is enabled, the PlanExecutor should handle yielding.
        # The original while loop for streaming is bypassed by this new flow.
        # If run_agent itself is expected to yield, PlanExecutor must yield back to here.
        # This is a complex interaction not fully specified by the prompt for this refactoring.
        # For now, assuming execute_plan handles all necessary output and state changes.

        langfuse.flush() # Flush at the end of successful execution
        return # End of new two-step flow

    except Exception as e:
        logger.error(f"Error in run_agent for task {thread_id}: {e}", exc_info=True)
        error_summary = f"An error occurred: {str(e)}"
        if task_state_manager: # Check if TSM was initialized
            # await task_state_manager.add_error_message(f"Agent Error: {e.message} Details: {e.details}" if isinstance(e, AgentError) else error_summary)
            await task_state_manager.fail_task(task_id=thread_id, error=error_summary)
        
        # If streaming, yield an error status
        if stream:
            yield {
                "type": "status",
                "status": "error",
                "message": error_summary
            }
        # Re-raise or handle as per agent's top-level error policy
        # If this function is a generator due to 'yield', re-raising might be complex.
        # For now, let the calling context handle the exception if not streaming.
        # If streaming, the yield above signals the error.
        if not stream:
             raise # Re-raise if not a streaming context that handled it via yield.
    finally:
        if trace and trace.client: # Ensure langfuse client is available
             langfuse.flush() # Ensure langfuse flushes even on error


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