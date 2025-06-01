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
from agentpress.task_planner import TaskPlanner
from agentpress.task_state_manager import TaskStateManager
from agentpress.task_storage_supabase import SupabaseTaskStorage
from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.plan_executor import PlanExecutor # Import PlanExecutor
from agentpress.utils.message_assembler import MessageAssembler
from agentpress.utils.json_helpers import format_for_yield # Added for plan_executor_message_callback


load_dotenv()

PLANNING_KEYWORDS = ["plan this:", "create a plan for:", "complex task:"]

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
    tool_orchestrator: ToolOrchestrator, # CORRECTED PLACEMENT
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
    try:
        logger.info(f"Entering run_agent function: thread_id={thread_id}, project_id={project_id}, agent_run_id=trace.id if trace else 'N/A', model_name={model_name}, stream={stream}")
        logger.info(f"🚀 Starting agent with model: {model_name} for thread_id: {thread_id}, project_id: {project_id}")

        if not trace:
            logger.debug("No existing trace found, creating new trace for run_agent.")
            trace = langfuse.trace(name="run_agent", session_id=thread_id, metadata={"project_id": project_id})
        else:
            logger.debug("Using existing trace for run_agent.")
        
        logger.debug("Initializing ThreadManager...")
        thread_manager = ThreadManager(tool_orchestrator=tool_orchestrator, trace=trace)
        client = await thread_manager.db.client
        logger.debug("ThreadManager initialized and database client obtained.")

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
        if not sandbox_info.get('id'):
            logger.error(f"No sandbox ID found in project_data for project {project_id}.")
            raise ValueError(f"No sandbox found for project {project_id}")
        logger.debug(f"Sandbox ID {sandbox_info.get('id')} retrieved for project {project_id}.")

        logger.debug("Initializing tools...")
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
            logger.debug("RAPID_API_KEY found, adding DataProvidersTool.")
            data_providers_tool = DataProvidersTool()
            thread_manager.add_tool(data_providers_tool)
        else:
            logger.debug("No RAPID_API_KEY found, skipping DataProvidersTool.")
        logger.debug("Tools initialized.")

        logger.debug(f"Generating system prompt for model: {model_name}...")
        if "anthropic" in model_name.lower():
            system_message = { "role": "system", "content": get_system_prompt() }
            logger.debug("Using Anthropic system prompt (no sample response).")
        else:
            sample_response_path = os.path.join(os.path.dirname(__file__), 'sample_responses/1.txt')
            with open(sample_response_path, 'r') as file:
                sample_response = file.read()
            system_message = { "role": "system", "content": get_system_prompt() + "\n\n <sample_assistant_response>" + sample_response + "</sample_assistant_response>" }
            logger.debug(f"Using default system prompt with sample response for {model_name}.")
        logger.debug("System prompt generated.")
        logger.debug(f"SYSTEM_PROMPT_LOG: Constructed system_message: {json.dumps(system_message, indent=2)}")

        logger.debug(f"Performing initial billing check for account {account_id}...")
        can_run_initial, message_initial, _ = await check_billing_status(client, account_id)
        if not can_run_initial:
            logger.error(f"Initial billing check failed for account {account_id}: {message_initial}")
            raise ValueError(f"Billing limit reached before agent start: {message_initial}")
        logger.debug("Initial billing check passed.")

    except Exception as e:
        logger.error(f"Failed to initialize agent setup for thread_id {thread_id}, project_id {project_id}", exc_info=True)
        raise

    iteration_count = 0
    continue_execution = True
    image_message_id_to_delete = None # Initialize image_message_id_to_delete

    latest_user_message = await client.table('messages').select('*').eq('thread_id', thread_id).eq('type', 'user').order('created_at', desc=True).limit(1).execute()
    if latest_user_message.data and len(latest_user_message.data) > 0:
        data = json.loads(latest_user_message.data[0]['content'])
        trace.update(input=data['content'])

    while continue_execution and iteration_count < max_iterations:
        iteration_count += 1
        logger.info(f"🔄 Running iteration {iteration_count} of {max_iterations}...")

        # Cleanup stale buffers at the beginning of each iteration
        if message_assembler: # Ensure it's initialized (it is, at the start of run_agent)
            message_assembler.cleanup_stale_buffers() # Default max_age_seconds is 60

        # Billing check on each iteration - still needed within the iterations
        can_run, message, subscription = await check_billing_status(client, account_id)
        if not can_run:
            error_msg = f"Billing limit reached: {message}"
            trace.event(name="billing_limit_reached", level="ERROR", status_message=(f"{error_msg}"))
            # Yield a special message to indicate billing limit reached
            yield {
                "type": "status",
                "status": "stopped",
                "message": error_msg
            }
            break
        # Check if last message is from assistant using direct Supabase query
        latest_message_query = await client.table('messages').select('*').eq('thread_id', thread_id).in_('type', ['assistant', 'tool', 'user']).order('created_at', desc=True).limit(1).execute()
        
        visualization_hint_message = None
        if latest_message_query.data and len(latest_message_query.data) > 0:
            latest_msg_data = latest_message_query.data[0]
            message_type = latest_msg_data.get('type')
            message_metadata = latest_msg_data.get('metadata', {})
            if isinstance(message_metadata, str): # Ensure metadata is a dict
                try:
                    message_metadata = json.loads(message_metadata)
                except json.JSONDecodeError:
                    message_metadata = {}

            processed_by_planner_flag = message_metadata.get('processed_by_planner', False)

            if message_type == 'user' and not processed_by_planner_flag:
                user_content_json_str = latest_msg_data.get('content', '{}')
                user_content_json = {}
                try:
                    user_content_json = json.loads(user_content_json_str)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse user message content as JSON: {user_content_json_str}. Error: {str(e)}", exc_info=True)
                    trace.event(name="user_content_parse_error", level="ERROR", status_message="Failed to parse user message content JSON")
                    # Potentially yield an error or handle as non-plannable

                original_user_text_content = user_content_json.get('content', '') # Original case for description
                user_text_content_lower = original_user_text_content.lower() if isinstance(original_user_text_content, str) else ''

                # Intensive logging for keyword detection
                logger.debug(f"PLANNER_TRIGGER_CHECK: User message content (lowercase): '{user_text_content_lower}'")
                logger.debug(f"PLANNER_TRIGGER_CHECK: Checking against PLANNING_KEYWORDS: {PLANNING_KEYWORDS}")

                planning_triggered = False
                actual_task_description = ""
                keyword_found = ""

                if isinstance(user_text_content_lower, str) and user_text_content_lower: # Ensure not empty string
                    for keyword in PLANNING_KEYWORDS:
                        if keyword in user_text_content_lower:
                            planning_triggered = True
                            keyword_found = keyword # Store the keyword that triggered planning
                            # Extract text *after* the found keyword from the original content
                            keyword_original_case_index = original_user_text_content.lower().find(keyword) # find on lowercased
                            actual_task_description = original_user_text_content[keyword_original_case_index + len(keyword):].strip()
                            break

                if planning_triggered:
                    # --- Keyword-based Task Planning and Execution ---
                    logger.info(f"Planning mode triggered by keyword '{keyword_found}'. Task description for planner: '{actual_task_description}'") # Verified/Ensured
                    # logger.info(f"PLANNER_TRIGGER_SUCCESS: Planning keyword '{keyword_found}' found in user message.") # Old log
                    # logger.debug(f"PLANNER_TRIGGER_SUCCESS: Extracted task description for planner: '{actual_task_description}'") # Old log
                    # The trace event below already logs keyword and description.
                    # trace.event(name="planning_keywords_triggered", level="DEFAULT", status_message=(f"Keyword: '{keyword_found}', Task: {actual_task_description}")) # This line is effectively duplicated by the log above.

                    # Define a callback for PlanExecutor to send messages/updates back to the user during execution.
                    async def plan_executor_message_callback(message_data: dict[str, Any]):
                        message_type = message_data.get("type")
                        # Ensure thread_manager and thread_id are accessible from the outer scope
                        # (they are, as this is a nested function in run_agent)

                        if message_type == "status":
                            # Yield status messages directly
                            yield {
                                "type": "status",
                                "content": json.dumps(message_data.get("content", {})),
                                "metadata": json.dumps(message_data.get("metadata", {}))
                            }
                        elif message_type == "plan_tool_result_data":
                            tool_name = message_data.get("tool_name", "unknown_tool")
                            tool_call_id = message_data.get("tool_call_id", str(uuid4()))
                            status = message_data.get("status", "error")
                            result_content = message_data.get("result")
                            error_content = message_data.get("error")

                            db_content_str = json.dumps(result_content) if status == "completed" else json.dumps(error_content)
                            tool_message_db_content = {
                                "role": "tool",
                                "tool_call_id": tool_call_id,
                                "name": tool_name, # This should be ToolID__MethodName
                                "content": db_content_str
                            }

                            # Save to database
                            # Ensure thread_manager is available in this scope
                            saved_tool_msg = await thread_manager.add_message(
                                thread_id=thread_id, # thread_id from outer scope
                                type="tool",
                                content=tool_message_db_content,
                                is_llm_message=True, # Tool results are part of the LLM flow
                                metadata=message_data.get("metadata", {})
                            )
                            if saved_tool_msg:
                                yield format_for_yield(saved_tool_msg)

                        elif message_type == "assistant_message_update":
                            yield {
                                "type": "assistant",
                                "content": json.dumps(message_data.get("content", {})),
                                "metadata": json.dumps(message_data.get("metadata", {}))
                            }
                        else:
                            logger.warning(f"PlanExecutor sent unhandled message type via callback: {message_type}. Data: {message_data}")


                    try:
                        # Instantiate dependencies for the planning and execution process.
                        # Use the ToolOrchestrator from the ThreadManager, which has all agent tools registered.
                        # This ensures the planner is aware of the same tools as the main agent.
                        planning_process_orchestrator = thread_manager.tool_orchestrator
                        # No need to call planning_process_orchestrator.load_tools_from_directory() here,
                        # as the main agent's ToolOrchestrator should already be populated.

                        # TaskStateManager requires a storage backend.
                        # The `thread_manager.db` is the DBConnection instance.
                        task_storage = SupabaseTaskStorage(db_connection=thread_manager.db)
                        task_manager = TaskStateManager(storage=task_storage)
                        await task_manager.initialize() # Initialize to load any existing task data if needed.

                        # Log the schemas from the orchestrator instance being passed to the planner.
                        tool_orch_for_planner = planning_process_orchestrator # This is thread_manager.tool_orchestrator
                        schemas_for_planner_llm = tool_orch_for_planner.get_tool_schemas_for_llm()
                        logger.debug(f"PLANNER_ORCHESTRATOR_CHECK: Tool Orchestrator for TaskPlanner has {len(schemas_for_planner_llm)} schemas. Schemas: {json.dumps(schemas_for_planner_llm, indent=2)}")

                        # Instantiate the TaskPlanner.
                        planner = TaskPlanner(task_manager=task_manager, tool_orchestrator=tool_orch_for_planner) # Pass the correct orchestrator

                        logger.debug(f"Invoking TaskPlanner with description: '{actual_task_description}'") # Ensure/Add
                        # Create the plan (main task and subtasks).
                        main_planned_task = await planner.plan_task(task_description=actual_task_description)
                        logger.debug(f"Main planned task object received from TaskPlanner: {main_planned_task.model_dump_json(indent=2) if main_planned_task else 'None'}") # Verified

                        if main_planned_task:
                            logger.info(f"Task planned successfully. Main task ID: {main_planned_task.id}, Status: {main_planned_task.status}") # Keep this
                            trace.event(name="task_planning_successful", level="DEFAULT", status_message=(f"Main task ID: {main_planned_task.id}, Status: {main_planned_task.status}"))

                            # Mark the original user message as processed by the planner to avoid re-triggering.
                            current_message_metadata = latest_msg_data.get('metadata', {})
                            if isinstance(current_message_metadata, str):
                                try: current_message_metadata = json.loads(current_message_metadata)
                                except: current_message_metadata = {}
                            elif current_message_metadata is None: current_message_metadata = {}
                            current_message_metadata['processed_by_planner'] = True
                            await client.table('messages').update({'metadata': current_message_metadata}).eq('message_id', latest_msg_data['message_id']).execute()

                            # Inform the user that the plan has been created and execution will start.
                            plan_confirmation_content = {
                                "role": "assistant",
                                "content": f"I have created a plan with ID: {main_planned_task.id} (status: {main_planned_task.status}). I will now proceed to execute this plan."
                            }
                            yield {
                                "type": "assistant",
                                "content": json.dumps(plan_confirmation_content),
                                "metadata": json.dumps({"thread_run_id": trace.id if trace else None, "planned_task_id": main_planned_task.id})
                            }

                            # Instantiate the PlanExecutor with the created plan and shared dependencies.
                            plan_executor = PlanExecutor(
                                main_task_id=main_planned_task.id,
                                task_manager=task_manager, # Reuse the task_manager
                                tool_orchestrator=planning_process_orchestrator, # Reuse the orchestrator
                                user_message_callback=plan_executor_message_callback # Pass the callback for updates
                            )
                            logger.info(f"Invoking PlanExecutor for main_task_id: {main_planned_task.id}") # Ensure/Add

                            # Yield a status message indicating the start of plan execution.
                            yield {
                                "type": "status",
                                "content": json.dumps({"status_type": "plan_execution_start", "message": f"Starting execution of plan {main_planned_task.id}..."}),
                                "metadata": json.dumps({"thread_run_id": trace.id if trace else None, "planned_task_id": main_planned_task.id})
                            }

                            # Execute the plan.
                            await plan_executor.execute_plan()

                            # Yield a status message indicating the end of plan execution.
                            yield {
                                "type": "status",
                                "content": json.dumps({"status_type": "plan_execution_end", "message": f"Execution of plan {main_planned_task.id} finished."}),
                                "metadata": json.dumps({"thread_run_id": trace.id if trace else None, "planned_task_id": main_planned_task.id})
                            }

                            # After plan creation and execution, stop the current agent cycle
                            # to prevent the normal LLM response flow for this user message.
                            continue_execution = False
                        else:
                            # If planning itself failed (e.g., LLM couldn't generate subtasks).
                            logger.error(f"Task planning failed. Description: '{actual_task_description}'. Main task: {main_planned_task.model_dump_json(indent=2) if main_planned_task else 'None'}") # Ensure/Add
                            # logger.error(f"Task planning failed for: {actual_task_description}") # Old log
                            trace.event(name="task_planning_failed", level="ERROR", status_message=(f"Task: {actual_task_description}"))
                            error_content = { "role": "assistant", "content": "I tried to create a plan, but something went wrong. Please try again or rephrase."}
                            yield {
                                "type": "assistant", "content": json.dumps(error_content),
                                "metadata": json.dumps({"thread_run_id": trace.id if trace else None})
                            }
                            continue_execution = False # ADDED LINE
                            # Let normal execution proceed to respond to the user message.
                    except Exception as e_planner:
                        logger.error(f"Exception during task planning: {e_planner}", exc_info=True)
                        trace.event(name="task_planning_exception", level="CRITICAL", status_message=str(e_planner))
                        error_content = { "role": "assistant", "content": f"An error occurred while trying to plan your request: {str(e_planner)}"}
                        yield {
                            "type": "assistant", "content": json.dumps(error_content),
                            "metadata": json.dumps({"thread_run_id": trace.id if trace else None})
                        }
                        # Let normal execution proceed.
                else: # Corresponds to 'if planning_triggered:'
                    logger.info("PLANNER_TRIGGER_FAIL: No planning keyword detected in user message. Proceeding with normal agent response.")


            if not continue_execution: # If planning happened and we decided to stop this cycle
                break

            # Original user message processing for visualization hint (if not planned)
            # This needs to be part of the `if message_type == 'user' and not processed_by_planner_flag:` block
            # or an `elif message_type == 'user':` if planning didn't trigger.
            # For simplicity, let's assume if planning was triggered, viz detection on the same message is skipped.
            # If planning was NOT triggered, then viz detection runs.

            if message_type == 'user' and not planning_triggered: # Check planning_triggered here
                try:
                    # Ensure user_content_json and original_user_text_content are defined if not set by planner block
                    if 'user_content_json' not in locals():
                         user_content_json_str = latest_msg_data.get('content', '{}')
                         user_content_json = json.loads(user_content_json_str)
                    if 'original_user_text_content' not in locals():
                        original_user_text_content = user_content_json.get('content', '')

                    # Use original_user_text_content for visualization detection, as it's the full, unaltered user text
                    user_text_content = user_content_json.get('content', '')
                    if isinstance(user_text_content, str) and user_text_content: # Ensure it's a non-empty string
                        detected_viz_type = detect_visualization_request(user_text_content)
                        if detected_viz_type:
                            logger.info(f"Detected visualization request: {detected_viz_type} in user message.")
                            trace.event(name="visualization_request_detected", level="DEFAULT",
                                        status_message=f"Type: {detected_viz_type}", 
                                        input={"user_message": user_text_content})
                            # Construct a system message to hint the LLM
                            hint_text = f"The user seems to be asking for a '{detected_viz_type}' visualization. Consider using the 'DataVisualizationTool' if appropriate to generate it. Relevant keywords: {user_text_content[:200]}"
                            visualization_hint_message = {"role": "system", "content": hint_text}
                except json.JSONDecodeError:
                    logger.warning(f"Could not parse user message content for visualization detection: {latest_msg_data.get('content')}")
                except Exception as e:
                    logger.error(f"Error during visualization detection: {e}", exc_info=True)

            if message_type == 'assistant':
                logger.info(f"Last message was from assistant, stopping execution")
                trace.event(name="last_message_from_assistant", level="DEFAULT", status_message=(f"Last message was from assistant, stopping execution"))
                continue_execution = False
                break

        # ---- Temporary Message Handling (Browser State & Image Context) ----
        # This list will now also include the visualization hint message if generated
        temp_messages_for_llm = [] 
        
        if visualization_hint_message:
            temp_messages_for_llm.append(visualization_hint_message)

        temp_message_content_list = [] # List to hold text/image blocks for the user-facing temporary message

        # Get the latest browser_state message
        latest_browser_state_msg = await client.table('messages').select('*').eq('thread_id', thread_id).eq('type', 'browser_state').order('created_at', desc=True).limit(1).execute()
        if latest_browser_state_msg.data and len(latest_browser_state_msg.data) > 0:
            browser_message_content_raw = latest_browser_state_msg.data[0]["content"]
            try:
                browser_content = json.loads(browser_message_content_raw)
                screenshot_base64 = browser_content.get("screenshot_base64")
                screenshot_url = browser_content.get("screenshot_url")
                
                # Create a copy of the browser state without screenshot data
                browser_state_text = browser_content.copy()
                browser_state_text.pop('screenshot_base64', None)
                browser_state_text.pop('screenshot_url', None)

                if browser_state_text:
                    temp_message_content_list.append({
                        "type": "text",
                        "text": f"The following is the current state of the browser:\n{json.dumps(browser_state_text, indent=2)}"
                    })
                    
                # Prioritize screenshot_url if available
                if screenshot_url:
                    temp_message_content_list.append({
                        "type": "image_url",
                        "image_url": {
                            "url": screenshot_url,
                        }
                    })
                elif screenshot_base64:
                    # Fallback to base64 if URL not available
                    temp_message_content_list.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{screenshot_base64}",
                        }
                    })
                else:
                    # This is a scenario where content might be missing
                    logger.warning("Browser state message is missing both screenshot_url and screenshot_base64. Content: %s", browser_content)

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse browser state JSON: {browser_message_content_raw}. Error: {str(e)}", exc_info=True)
                trace.event(name="json_decode_error_browser_state", level="ERROR", status_message=(f"Failed to parse browser state: {str(e)}"))
                yield {"type": "status", "status": "error", "message": f"Failed to parse browser state: {str(e)}"}
                continue_execution = False
                break # Break from the while loop
            except Exception as e: # Catch other potential errors
                logger.error(f"Error processing browser state: {e}", exc_info=True)
                trace.event(name="error_processing_browser_state", level="ERROR", status_message=(f"{e}"))
                # Optionally, yield a generic error or break depending on severity
                yield {"type": "status", "status": "error", "message": f"Error processing browser state: {e}"}
                continue_execution = False
                break


        # Get the latest image_context message
        latest_image_context_msg = await client.table('messages').select('*').eq('thread_id', thread_id).eq('type', 'image_context').order('created_at', desc=True).limit(1).execute()
        if latest_image_context_msg.data and len(latest_image_context_msg.data) > 0:
            image_message_content_raw = latest_image_context_msg.data[0]["content"]
            image_msg_id_candidate = latest_image_context_msg.data[0]["message_id"]
            try:
                image_context_content = json.loads(image_message_content_raw)
                base64_image = image_context_content.get("base64")
                mime_type = image_context_content.get("mime_type")
                file_path = image_context_content.get("file_path", "unknown file")

                if base64_image and mime_type:
                    temp_message_content_list.append({
                        "type": "text",
                        "text": f"Here is the image you requested to see: '{file_path}'"
                    })
                    temp_message_content_list.append({
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{base64_image}",
                        }
                    })
                    image_message_id_to_delete = image_msg_id_candidate # Mark for deletion after LLM call
                else:
                    logger.warning(f"Image context for '{file_path}' is missing base64_image or mime_type. Content: %s", image_context_content)
                    # Do not mark for deletion if content is invalid, it might need to be inspected or retried.
                    # However, if it's just a warning, we might still want to delete it to prevent reprocessing.
                    # For now, let's assume if it's a warning, we still mark for deletion to avoid loop.
                    image_message_id_to_delete = image_msg_id_candidate


            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse image context JSON: {image_message_content_raw}. Error: {str(e)}", exc_info=True)
                trace.event(name="json_decode_error_image_context", level="ERROR", status_message=(f"Failed to parse image context: {str(e)}"))
                yield {"type": "status", "status": "error", "message": f"Failed to parse image context: {str(e)}"}
                continue_execution = False
                break # Break from the while loop
            except Exception as e: # Catch other potential errors
                logger.error(f"Error processing image context: {e}", exc_info=True)
                trace.event(name="error_processing_image_context", level="ERROR", status_message=(f"{e}"))
                yield {"type": "status", "status": "error", "message": f"Error processing image context: {e}"}
                continue_execution = False
                break

        # If we have any content for the user-facing temporary message
        if temp_message_content_list:
            # This message is specifically for context like browser state or image previews
            user_context_message = {"role": "user", "content": temp_message_content_list}
            temp_messages_for_llm.append(user_context_message)
            # logger.debug(f"Constructed user context temporary message with {len(temp_message_content_list)} content blocks.")
        
        # The 'temporary_message' parameter for run_thread can take a single message or a list.
        # If temp_messages_for_llm is empty, it will be handled by run_thread.
        # ---- End Temporary Message Handling ----

        # Set max_tokens based on model
        max_tokens = None
        if "sonnet" in model_name.lower():
            max_tokens = 64000
        elif "gpt-4" in model_name.lower():
            max_tokens = 4096
            
        generation = trace.generation(name="thread_manager.run_thread")
        try:
            logger.info(f"Iteration {iteration_count}: About to call thread_manager.run_thread for thread {thread_id}")
            # Make the LLM call and process the response
            response = await thread_manager.run_thread(
                thread_id=thread_id,
                system_prompt=system_message,
                stream=stream,
                llm_model=model_name,
                llm_temperature=0,
                llm_max_tokens=max_tokens,
                tool_choice="auto",
                max_xml_tool_calls=1,
                # Pass the list of temporary messages (could be just viz hint, just user context, both, or none)
                temporary_message=temp_messages_for_llm if temp_messages_for_llm else None,
                processor_config=ProcessorConfig(
                    xml_tool_calling=True,
                    native_tool_calling=True, # Ensure this is True
                    execute_tools=True,
                    execute_on_stream=True,
                    tool_execution_strategy="parallel",
                    xml_adding_strategy="user_message"
                ),
                native_max_auto_continues=native_max_auto_continues,
                include_xml_examples=True,
                enable_thinking=enable_thinking,
                reasoning_effort=reasoning_effort,
                enable_context_manager=enable_context_manager,
                generation=generation
            )
            logger.info(f"Iteration {iteration_count}: thread_manager.run_thread returned. Type of response: {type(response)}")

            if isinstance(response, dict) and "status" in response and response["status"] == "error":
                logger.error(f"Error response from run_thread: {response.get('message', 'Unknown error')}")
                trace.event(name="error_response_from_run_thread", level="ERROR", status_message=(f"{response.get('message', 'Unknown error')}"))
                yield response
                break

            # Track if we see ask, complete, or web-browser-takeover tool calls
            last_tool_call = None

            # Process the response
            error_detected = False
            try:
                full_response = ""
                async for chunk in response:
                    logger.debug(f"Iteration {iteration_count}: Received raw chunk from run_thread: {json.dumps(chunk)}")
                    # If we receive an error chunk, we should stop after this iteration
                    if isinstance(chunk, dict) and chunk.get('type') == 'status' and chunk.get('status') == 'error':
                        logger.error(f"Error chunk detected: {json.dumps(chunk)}")
                        trace.event(name="error_chunk_detected", level="ERROR", status_message=(f"{chunk.get('message', 'Unknown error')}"))
                        error_detected = True
                        yield chunk  # Forward the error chunk
                        continue     # Continue processing other chunks but don't break yet

                    if chunk.get('type') == 'assistant' and isinstance(chunk.get('content'), str):
                        chunk_for_assembler = chunk.copy()
                        chunk_for_assembler['thread_id'] = thread_id
                        assembled_message_content = message_assembler.process_chunk(chunk_for_assembler)

                        if isinstance(assembled_message_content, dict):
                            logger.info(f"Assembled complete message: {json.dumps(assembled_message_content)}")
                            assistant_content_json = assembled_message_content
                            assistant_text = assistant_content_json.get('content', '')
                            if not isinstance(assistant_text, str):
                                assistant_text = json.dumps(assistant_text)

                            full_response += assistant_text
                            if isinstance(assistant_text, str):
                                if '</ask>' in assistant_text or '</complete>' in assistant_text or '</web-browser-takeover>' in assistant_text:
                                    if '</ask>' in assistant_text:
                                        xml_tool = 'ask'
                                    elif '</complete>' in assistant_text:
                                        xml_tool = 'complete'
                                    elif '</web-browser-takeover>' in assistant_text:
                                        xml_tool = 'web-browser-takeover'
                                    last_tool_call = xml_tool
                                    logger.info(f"Agent used XML tool: {xml_tool} via assembled message")
                                    trace.event(name="agent_used_xml_tool_assembled", level="DEFAULT", status_message=(f"Agent used XML tool: {xml_tool}"))
                            yield chunk # Yield the original chunk that completed assembly
                        else:
                            logger.debug(f"Assistant chunk processed by assembler, but no complete message yet. Original chunk yielded.")
                            yield chunk

                    # Else (chunk is not an assistant chunk with string content, or already processed by assembler)
                    else:
                        if chunk.get('type') == 'assistant' and 'content' in chunk: # e.g. already a dict
                            try:
                                content = chunk.get('content', '{}')
                                if isinstance(content, str):
                                    # This case should ideally be handled by the assembler if it's a fragment.
                                    # If it's a full JSON string not caught by assembler (e.g. no thread_id initially), try to parse.
                                    try:
                                        assistant_content_json = json.loads(content)
                                    except json.JSONDecodeError:
                                        logger.warning(f"Assistant content is string but not valid JSON, treating as plain text: {json.dumps(content)}")
                                        assistant_content_json = {"role": "assistant", "content": content} # Wrap it
                                else: # It's already a dict
                                    assistant_content_json = content

                                assistant_text = assistant_content_json.get('content', '')
                                if not isinstance(assistant_text, str): # Ensure text is string
                                    assistant_text = json.dumps(assistant_text)

                                full_response += assistant_text
                                if isinstance(assistant_text, str):
                                    if '</ask>' in assistant_text or '</complete>' in assistant_text or '</web-browser-takeover>' in assistant_text:
                                        if '</ask>' in assistant_text: xml_tool = 'ask'
                                        elif '</complete>' in assistant_text: xml_tool = 'complete'
                                        elif '</web-browser-takeover>' in assistant_text: xml_tool = 'web-browser-takeover'
                                        last_tool_call = xml_tool
                                        logger.info(f"Agent used XML tool: {xml_tool} via non-string or pre-parsed content")
                                        trace.event(name="agent_used_xml_tool_direct", level="DEFAULT", status_message=(f"Agent used XML tool: {xml_tool}"))
                            except json.JSONDecodeError:
                                # If chunk.get('content') was the string that failed to parse, dumping it as JSON string is fine.
                                logger.warning(f"Warning: Could not parse non-string assistant content JSON: {json.dumps(chunk.get('content'))}")
                                trace.event(name="warning_could_not_parse_assistant_content_json_direct", level="WARNING", status_message=(f"Warning: Could not parse assistant content JSON: {json.dumps(chunk.get('content'))}"))
                            except Exception as e:
                                logger.error(f"Error processing non-string/pre-parsed assistant chunk: {e}", exc_info=True)
                                trace.event(name="error_processing_assistant_chunk_direct", level="ERROR", status_message=(f"Error processing assistant chunk: {e}"))

                        yield chunk # Yield original chunk if not processed by assembler or if it's not an assistant string content

                logger.info(f"Iteration {iteration_count}: Finished iterating over run_thread response for thread {thread_id}")
                # Check if we should stop based on the last tool call or error
                if error_detected:
                    logger.info(f"Stopping due to error detected in response")
                    trace.event(name="stopping_due_to_error_detected_in_response", level="DEFAULT", status_message=(f"Stopping due to error detected in response"))
                    generation.end(output=full_response, status_message="error_detected", level="ERROR")
                    break
                    
                if last_tool_call in ['ask', 'complete', 'web-browser-takeover']:
                    logger.info(f"Agent decided to stop with tool: {last_tool_call}")
                    trace.event(name="agent_decided_to_stop_with_tool", level="DEFAULT", status_message=(f"Agent decided to stop with tool: {last_tool_call}"))
                    generation.end(output=full_response, status_message="agent_stopped")
                    continue_execution = False
            except Exception as e:
                # Just log the error and re-raise to stop all iterations
                error_msg = f"Error during response streaming: {str(e)}" # This is used in the logger below
                logger.error(f"Error during response streaming: {str(e)}", exc_info=True) # Ensuring format from subtask
                trace.event(name="error_during_response_streaming", level="ERROR", status_message=(f"Error during response streaming: {str(e)}"))
                generation.end(output=full_response, status_message=error_msg, level="ERROR")
                yield {
                    "type": "status",
                    "status": "error",
                    "message": error_msg
                }
                # Stop execution immediately on any error
                break
                
        except Exception as e:
            # Just log the error and re-raise to stop all iterations
            error_msg = f"Error running thread: {str(e)}"
            logger.error(f"Error: {error_msg}", exc_info=True) # Added exc_info
            trace.event(name="error_running_thread", level="ERROR", status_message=(f"Error running thread: {str(e)}"))
            yield {
                "type": "status",
                "status": "error",
                "message": error_msg
            }
            # Stop execution immediately on any error
            break

        # Moved image deletion to after the LLM call and response processing
        if image_message_id_to_delete:
            logger.info(f"Attempting to delete processed image context message with ID: {image_message_id_to_delete}")
            try:
                delete_result = await client.table('messages').delete().eq('message_id', image_message_id_to_delete).execute()
                if delete_result.data or (hasattr(delete_result, 'count') and delete_result.count > 0) or (hasattr(delete_result, 'status_code') and 200 <= delete_result.status_code < 300) :
                    logger.info(f"Successfully deleted image context message: {image_message_id_to_delete}")
                else:
                    logger.warning(f"No data returned or count was zero when deleting image message {image_message_id_to_delete}. Result: {delete_result}")
            except Exception as e:
                logger.error(f"Error deleting image context message {image_message_id_to_delete}: {e}", exc_info=True)
                trace.event(name="error_deleting_image_context", level="ERROR", status_message=(f"{e}"))
            image_message_id_to_delete = None # Reset for the next iteration

        generation.end(output=full_response)

    langfuse.flush() # Flush Langfuse events at the end of the run


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