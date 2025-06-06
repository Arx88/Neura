"""
Response processing module for AgentPress.

This module handles the processing of LLM responses, including:
- Streaming and non-streaming response handling
- XML and native tool call detection and parsing
- Tool execution orchestration
- Message formatting and persistence
- Cost calculation and tracking
"""

import json
import re
import uuid
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, AsyncGenerator, Tuple, Union, Callable, Literal
from dataclasses import dataclass
from utils.logger import logger
from .tool import ToolResult
from .tool_orchestrator import ToolOrchestrator
from .plan_executor import PlanExecutor
from litellm import completion_cost
from langfuse.client import StatefulTraceClient
from services.langfuse import langfuse
from .utils.json_helpers import (
    ensure_dict, ensure_list, safe_json_parse, 
    to_json_string, format_for_yield
)

# Type alias for XML result adding strategy
XmlAddingStrategy = Literal["user_message", "assistant_message", "inline_edit"]

# Type alias for tool execution strategy
ToolExecutionStrategy = Literal["sequential", "parallel"]

@dataclass
class ToolExecutionContext:
    """Context for a tool execution including call details, result, and display info."""
    tool_call: Dict[str, Any]
    tool_index: int
    result: Optional[ToolResult] = None # Changed from ToolResult
    function_name: Optional[str] = None
    xml_tag_name: Optional[str] = None
    error: Optional[Exception] = None
    assistant_message_id: Optional[str] = None
    parsing_details: Optional[Dict[str, Any]] = None

@dataclass
class ProcessorConfig:
    """
    Configuration for response processing and tool execution.
    
    This class controls how the LLM's responses are processed, including how tool calls
    are detected, executed, and their results handled.
    
    Attributes:
        xml_tool_calling: Enable XML-based tool call detection (<tool>...</tool>)
        native_tool_calling: Enable OpenAI-style function calling format
        execute_tools: Whether to automatically execute detected tool calls
        execute_on_stream: For streaming, execute tools as they appear vs. at the end
        tool_execution_strategy: How to execute multiple tools ("sequential" or "parallel")
        xml_adding_strategy: How to add XML tool results to the conversation
        max_xml_tool_calls: Maximum number of XML tool calls to process (0 = no limit)
    """

    xml_tool_calling: bool = True  
    native_tool_calling: bool = False

    execute_tools: bool = True
    execute_on_stream: bool = False
    tool_execution_strategy: ToolExecutionStrategy = "sequential"
    xml_adding_strategy: XmlAddingStrategy = "assistant_message"
    max_xml_tool_calls: int = 0  # 0 means no limit
    
    def __post_init__(self):
        """Validate configuration after initialization."""
        if self.xml_tool_calling is False and self.native_tool_calling is False and self.execute_tools:
            raise ValueError("At least one tool calling format (XML or native) must be enabled if execute_tools is True")
            
        if self.xml_adding_strategy not in ["user_message", "assistant_message", "inline_edit"]:
            raise ValueError("xml_adding_strategy must be 'user_message', 'assistant_message', or 'inline_edit'")
        
        if self.max_xml_tool_calls < 0:
            raise ValueError("max_xml_tool_calls must be a non-negative integer (0 = no limit)")

class ResponseProcessor:
    """Processes LLM responses, extracting and executing tool calls."""
    
    def __init__(self, tool_orchestrator: ToolOrchestrator, add_message_callback: Callable, plan_executor: PlanExecutor, trace: Optional[StatefulTraceClient] = None):
        """Initialize the ResponseProcessor.
        
        Args:
            tool_orchestrator: Orchestrator for executing tools.
            add_message_callback: Callback function to add messages to the thread.
                MUST return the full saved message object (dict) or None.
            plan_executor: Executor for plans.
        """
        self.tool_orchestrator = tool_orchestrator
        self.add_message = add_message_callback
        self.plan_executor = plan_executor
        self.trace = trace
        if not self.trace:
            self.trace = langfuse.trace(name="anonymous:response_processor")
        
        self.is_plan = False
        self.plan_buffer: List[str] = []

    def is_complete_json(self, json_str: str) -> bool:
        """
        Checks if a string is a complete JSON object.
        """
        try:
            json.loads(json_str)
            return True
        except json.JSONDecodeError:
            return False

    async def _yield_message(self, message_obj: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """Helper to yield a message with proper formatting.
        
        Ensures that content and metadata are JSON strings for client compatibility.
        """
        if message_obj:
            return format_for_yield(message_obj)

    async def process_streaming_response(
        self,
        llm_response: AsyncGenerator,
        thread_id: str,
        prompt_messages: List[Dict[str, Any]],
        llm_model: str,
        config: ProcessorConfig = ProcessorConfig(),
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process a streaming LLM response, handling tool calls and execution.
        
        Args:
            llm_response: Streaming response from the LLM
            thread_id: ID of the conversation thread
            prompt_messages: List of messages sent to the LLM (the prompt)
            llm_model: The name of the LLM model used
            config: Configuration for parsing and execution
            
        Yields:
            Complete message objects matching the DB schema, except for content chunks.
        """
        accumulated_content = ""
        tool_calls_buffer = {}
        current_xml_content = ""
        xml_chunks_buffer = []
        pending_tool_executions = []
        yielded_tool_indices = set() # Stores indices of tools whose *status* has been yielded
        tool_index = 0
        xml_tool_call_count = 0
        finish_reason = None
        last_assistant_message_object = None # Store the final saved assistant message object
        tool_result_message_objects = {} # tool_index -> full saved message object
        has_printed_thinking_prefix = False # Flag for printing thinking prefix only once

        logger.info(f"Streaming Config: XML={config.xml_tool_calling}, Native={config.native_tool_calling}, "
                   f"Execute on stream={config.execute_on_stream}, Strategy={config.tool_execution_strategy}")

        thread_run_id = str(uuid.uuid4())

        try:
            # --- Save and Yield Start Events ---
            start_content = {"status_type": "thread_run_start", "thread_run_id": thread_run_id}
            start_msg_obj = await self.add_message(
                thread_id=thread_id, type="status", content=start_content, 
                is_llm_message=False, metadata={"thread_run_id": thread_run_id}
            )
            if start_msg_obj: yield format_for_yield(start_msg_obj)

            assist_start_content = {"status_type": "assistant_response_start"}
            assist_start_msg_obj = await self.add_message(
                thread_id=thread_id, type="status", content=assist_start_content, 
                is_llm_message=False, metadata={"thread_run_id": thread_run_id}
            )
            if assist_start_msg_obj: yield format_for_yield(assist_start_msg_obj)
            # --- End Start Events ---

            __sequence = 0

            async for chunk in llm_response:
                if hasattr(chunk, 'choices') and chunk.choices and hasattr(chunk.choices[0], 'finish_reason') and chunk.choices[0].finish_reason:
                    finish_reason = chunk.choices[0].finish_reason
                    logger.debug(f"Detected finish_reason: {finish_reason}")

                if hasattr(chunk, 'choices') and chunk.choices:
                    delta = chunk.choices[0].delta if hasattr(chunk.choices[0], 'delta') else None
                    
                    # Check for and log Anthropic thinking content
                    if delta and hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                        if not has_printed_thinking_prefix:
                            # print("[THINKING]: ", end='', flush=True)
                            has_printed_thinking_prefix = True
                        # print(delta.reasoning_content, end='', flush=True)
                        # Append reasoning to main content to be saved in the final message
                        accumulated_content += delta.reasoning_content

                    # Process content chunk
                    if delta and hasattr(delta, 'content') and delta.content:
                        chunk_content = delta.content
                        processed_as_plan_chunk = False

                        # Plan Detection Logic
                        plan_marker = '"plan":' # Or a more specific marker like '"actions":' or '"subtasks":' based on expected LLM output for plans.
                        if not self.is_plan and plan_marker in chunk_content:
                            logger.debug(f"Plan detected in response stream by marker: {plan_marker}")
                            self.is_plan = True

                        if self.is_plan:
                            processed_as_plan_chunk = True
                            self.plan_buffer.append(chunk_content)
                            full_json_str = "".join(self.plan_buffer)

                            if self.is_complete_json(full_json_str):
                                logger.info("Complete plan JSON assembled from stream.")
                                try:
                                    plan_data = json.loads(full_json_str)
                                    # Validate actual plan structure (e.g., presence of a specific key)
                                    if isinstance(plan_data, dict) and plan_data and (plan_marker.strip('":') in plan_data or "actions" in plan_data or "subtasks" in plan_data): # Example validation
                                        logger.info(f"Valid plan structure detected. Parsed plan data snippet: {str(plan_data)[:200]}...")
                                        if hasattr(self, 'plan_executor') and self.plan_executor:
                                            plan_execution_run_id = str(uuid.uuid4())

                                            plan_start_content = {"status_type": "plan_execution_start", "plan_name": plan_data.get("name", "Unnamed Plan")}
                                            plan_start_msg_obj = await self.add_message(
                                                thread_id=thread_id, type="status", content=plan_start_content,
                                                is_llm_message=False, metadata={"thread_run_id": plan_execution_run_id, "original_run_id": thread_run_id, "plan_data_snippet": str(plan_data)[:200]}
                                            )
                                            if plan_start_msg_obj: yield format_for_yield(plan_start_msg_obj)

                                            logger.info(f"Calling plan_executor's method for plan in thread {thread_id} (run {plan_execution_run_id})")
                                            # Assumes PlanExecutor will have a method like `execute_json_plan(self, plan_data, thread_id, run_id)`
                                            async for plan_result_part in self.plan_executor.execute_json_plan(plan_data, thread_id, plan_execution_run_id):
                                                yield plan_result_part
                                            logger.info(f"Plan execution finished for thread {thread_id} (run {plan_execution_run_id}).")

                                            plan_end_content = {"status_type": "plan_execution_end"}
                                            plan_end_msg_obj = await self.add_message(
                                                thread_id=thread_id, type="status", content=plan_end_content,
                                                is_llm_message=False, metadata={"thread_run_id": plan_execution_run_id}
                                            )
                                            if plan_end_msg_obj: yield format_for_yield(plan_end_msg_obj)
                                        else: # No plan_executor
                                            logger.error("PlanExecutor not available. Cannot execute detected plan.")
                                            err_content = {"role": "system", "status_type": "error", "message": "Plan detected but PlanExecutor is not available."}
                                            err_msg_obj = await self.add_message(thread_id=thread_id, type="status", content=err_content, is_llm_message=False, metadata={"thread_run_id": thread_run_id})
                                            if err_msg_obj: yield format_for_yield(err_msg_obj)
                                    else: # Not a valid plan structure
                                        logger.warning(f"JSON assembled, but not a recognized plan structure. Content: {str(plan_data)[:200]}. Resetting is_plan state.")
                                        logger.info(f"Dropping buffered content that was not a valid plan: {''.join(self.plan_buffer)}")
                                        processed_as_plan_chunk = False # Fall through to regular processing for current chunk_content

                                    self.plan_buffer = []
                                    self.is_plan = False
                                    if processed_as_plan_chunk: # If it was handled as a plan (executed or error during exec)
                                        continue # Skip regular processing for this chunk
                                except json.JSONDecodeError:
                                    logger.debug("Plan JSONDecodeError (plan buffer likely incomplete), waiting for more chunks...")
                                    # processed_as_plan_chunk is True, original logic will be skipped. Loop continues.
                                except Exception as e_plan_exec:
                                    logger.error(f"Error during plan processing or execution: {e_plan_exec}", exc_info=True)
                                    err_content = {"role": "system", "status_type": "error", "message": f"Error processing/executing plan: {str(e_plan_exec)}"}
                                    err_msg_obj = await self.add_message(thread_id=thread_id, type="status", content=err_content, is_llm_message=False, metadata={"thread_run_id": thread_run_id})
                                    if err_msg_obj: yield format_for_yield(err_msg_obj)
                                    self.plan_buffer = []
                                    self.is_plan = False
                                    continue # Skip original logic for this chunk
                            # else: (JSON not complete yet)
                                # logger.debug("Plan JSON not complete, waiting for more chunks...")
                                # processed_as_plan_chunk is True. Loop will iterate for the next chunk.
                                # No `continue` here is needed.

                        if not processed_as_plan_chunk:
                            # print(chunk_content, end='', flush=True)
                            accumulated_content += chunk_content
                            current_xml_content += chunk_content

                            if not (config.max_xml_tool_calls > 0 and xml_tool_call_count >= config.max_xml_tool_calls):
                                # Yield ONLY content chunk (don't save)
                                now_chunk = datetime.now(timezone.utc).isoformat()
                                yield {
                                    "sequence": __sequence,
                                    "message_id": None, "thread_id": thread_id, "type": "assistant",
                                    "is_llm_message": True,
                                    "content": to_json_string({"role": "assistant", "content": chunk_content}),
                                    "metadata": to_json_string({"stream_status": "chunk", "thread_run_id": thread_run_id}),
                                    "created_at": now_chunk, "updated_at": now_chunk
                                }
                                __sequence += 1
                            else:
                                logger.info("XML tool call limit reached - not yielding more content chunks")
                                self.trace.event(name="xml_tool_call_limit_reached", level="DEFAULT", status_message=(f"XML tool call limit reached - not yielding more content chunks"))

                            # --- Process XML Tool Calls (if enabled and limit not reached) ---
                            if config.xml_tool_calling and not (config.max_xml_tool_calls > 0 and xml_tool_call_count >= config.max_xml_tool_calls):
                                xml_chunks = self._extract_xml_chunks(current_xml_content)
                                for xml_chunk in xml_chunks:
                                    current_xml_content = current_xml_content.replace(xml_chunk, "", 1)
                                    xml_chunks_buffer.append(xml_chunk)
                                    result = self._parse_xml_tool_call(xml_chunk)
                                    if result:
                                        tool_call, parsing_details = result
                                        xml_tool_call_count += 1
                                        current_assistant_id = last_assistant_message_object['message_id'] if last_assistant_message_object else None
                                        context = self._create_tool_context(
                                            tool_call, tool_index, current_assistant_id, parsing_details
                                        )

                                        if config.execute_tools and config.execute_on_stream:
                                            # Save and Yield tool_started status
                                            started_msg_obj = await self._yield_and_save_tool_started(context, thread_id, thread_run_id)
                                            if started_msg_obj: yield format_for_yield(started_msg_obj)
                                            yielded_tool_indices.add(tool_index) # Mark status as yielded

                                            execution_task = asyncio.create_task(self._execute_tool(tool_call))
                                            pending_tool_executions.append({
                                                "task": execution_task, "tool_call": tool_call,
                                                "tool_index": tool_index, "context": context
                                            })
                                            tool_index += 1

                                        if config.max_xml_tool_calls > 0 and xml_tool_call_count >= config.max_xml_tool_calls:
                                            logger.debug(f"Reached XML tool call limit ({config.max_xml_tool_calls})")
                                            finish_reason = "xml_tool_limit_reached"
                                            break # Stop processing more XML chunks in this delta

                    # --- Process Native Tool Call Chunks ---
                    if config.native_tool_calling and delta and hasattr(delta, 'tool_calls') and delta.tool_calls:
                        if not self.is_plan: # Only process native tools if not currently handling a plan via content stream
                            for tool_call_chunk in delta.tool_calls:
                                # Yield Native Tool Call Chunk (transient status, not saved)
                                # ... (safe extraction logic for tool_call_data_chunk) ...
                                tool_call_data_chunk = {} # Placeholder for extracted data
                                if hasattr(tool_call_chunk, 'model_dump'): tool_call_data_chunk = tool_call_chunk.model_dump()
                                else: # Manual extraction...
                                    if hasattr(tool_call_chunk, 'id'): tool_call_data_chunk['id'] = tool_call_chunk.id
                                    if hasattr(tool_call_chunk, 'index'): tool_call_data_chunk['index'] = tool_call_chunk.index
                                    if hasattr(tool_call_chunk, 'type'): tool_call_data_chunk['type'] = tool_call_chunk.type
                                    if hasattr(tool_call_chunk, 'function'):
                                        tool_call_data_chunk['function'] = {}
                                        if hasattr(tool_call_chunk.function, 'name'): tool_call_data_chunk['function']['name'] = tool_call_chunk.function.name
                                        if hasattr(tool_call_chunk.function, 'arguments'): tool_call_data_chunk['function']['arguments'] = tool_call_chunk.function.arguments if isinstance(tool_call_chunk.function.arguments, str) else to_json_string(tool_call_chunk.function.arguments)

                                # Log native tool call detection when fully assembled
                                if tool_call_data_chunk.get('id') and tool_call_data_chunk.get('function', {}).get('name') and tool_call_data_chunk.get('function', {}).get('arguments'):
                                    arguments_json_string = tool_call_data_chunk['function']['arguments']
                                    # Ensure arguments is a string for logging, might already be if from to_json_string
                                    if not isinstance(arguments_json_string, str):
                                        arguments_json_string = to_json_string(arguments_json_string)
                                    logger.debug(f"Native tool call detected: ID={tool_call_data_chunk['id']}, Function={tool_call_data_chunk['function']['name']}, Args={arguments_json_string}")

                                now_tool_chunk = datetime.now(timezone.utc).isoformat()
                                yield {
                                    "message_id": None, "thread_id": thread_id, "type": "status", "is_llm_message": True,
                                    "content": to_json_string({"role": "assistant", "status_type": "tool_call_chunk", "tool_call_chunk": tool_call_data_chunk}),
                                    "metadata": to_json_string({"thread_run_id": thread_run_id}),
                                    "created_at": now_tool_chunk, "updated_at": now_tool_chunk
                                }

                                # --- Buffer and Execute Complete Native Tool Calls ---
                                if not hasattr(tool_call_chunk, 'function'): continue
                                idx = tool_call_chunk.index if hasattr(tool_call_chunk, 'index') else 0
                                # ... (buffer update logic remains same) ...
                                # Initialize buffer for this index if it doesn't exist
                                if idx not in tool_calls_buffer:
                                    tool_calls_buffer[idx] = {"id": None, "function": {"name": None, "arguments": ""}}

                                # Update buffer with new data from the chunk
                                if hasattr(tool_call_chunk, 'id') and tool_call_chunk.id:
                                    tool_calls_buffer[idx]['id'] = tool_call_chunk.id
                                if hasattr(tool_call_chunk, 'function') and hasattr(tool_call_chunk.function, 'name') and tool_call_chunk.function.name:
                                    tool_calls_buffer[idx]['function']['name'] = tool_call_chunk.function.name
                                if hasattr(tool_call_chunk, 'function') and hasattr(tool_call_chunk.function, 'arguments') and tool_call_chunk.function.arguments:
                                    tool_calls_buffer[idx]['function']['arguments'] += tool_call_chunk.function.arguments

                                # ... (check complete logic remains same) ...
                                has_complete_tool_call = False # Placeholder
                                if (tool_calls_buffer.get(idx) and
                                    tool_calls_buffer[idx]['id'] and
                                    tool_calls_buffer[idx]['function']['name'] and
                                    tool_calls_buffer[idx]['function']['arguments']):
                                    try:
                                        safe_json_parse(tool_calls_buffer[idx]['function']['arguments'])
                                        has_complete_tool_call = True
                                    except json.JSONDecodeError: pass


                                if has_complete_tool_call and config.execute_tools and config.execute_on_stream:
                                    current_tool = tool_calls_buffer[idx]
                                    tool_call_data = {
                                        "function_name": current_tool['function']['name'],
                                        "arguments": safe_json_parse(current_tool['function']['arguments']),
                                        "id": current_tool['id']
                                    }
                                    current_assistant_id = last_assistant_message_object['message_id'] if last_assistant_message_object else None
                                    context = self._create_tool_context(
                                        tool_call_data, tool_index, current_assistant_id
                                    )

                                    # Save and Yield tool_started status
                                    started_msg_obj = await self._yield_and_save_tool_started(context, thread_id, thread_run_id)
                                    if started_msg_obj: yield format_for_yield(started_msg_obj)
                                    yielded_tool_indices.add(tool_index) # Mark status as yielded

                                    execution_task = asyncio.create_task(self._execute_tool(tool_call_data))
                                    pending_tool_executions.append({
                                        "task": execution_task, "tool_call": tool_call_data,
                                        "tool_index": tool_index, "context": context
                                    })
                                    tool_index += 1
                        else:
                            logger.debug("Skipping native tool_calls processing because a plan is currently being handled via content stream.")

                if finish_reason == "xml_tool_limit_reached":
                    logger.info("Stopping stream processing after loop due to XML tool call limit")
                    self.trace.event(name="stopping_stream_processing_after_loop_due_to_xml_tool_call_limit", level="DEFAULT", status_message=(f"Stopping stream processing after loop due to XML tool call limit"))
                    break

            # print() # Add a final newline after the streaming loop finishes

            # --- After Streaming Loop ---

            # Wait for pending tool executions from streaming phase
            tool_results_buffer = [] # Stores (tool_call, result, tool_index, context)
            if pending_tool_executions:
                logger.info(f"Waiting for {len(pending_tool_executions)} pending streamed tool executions")
                self.trace.event(name="waiting_for_pending_streamed_tool_executions", level="DEFAULT", status_message=(f"Waiting for {len(pending_tool_executions)} pending streamed tool executions"))
                # ... (asyncio.wait logic) ...
                pending_tasks = [execution["task"] for execution in pending_tool_executions]
                done, _ = await asyncio.wait(pending_tasks)

                for execution in pending_tool_executions:
                    # Log before tool execution (even if status already yielded)
                    tool_call_details_string = f"Name='{execution['context'].function_name or execution['context'].xml_tag_name}', Args={execution['context'].tool_call.get('arguments', {})}"
                    logger.info(f"Preparing to execute tool (streamed): {tool_call_details_string}")
                    tool_idx = execution.get("tool_index", -1)
                    context = execution["context"]
                    # Check if status was already yielded during stream run
                    if tool_idx in yielded_tool_indices:
                         logger.debug(f"Status for tool index {tool_idx} already yielded.")
                         # Still need to process the result for the buffer
                         try:
                             if execution["task"].done():
                                 result = execution["task"].result()
                                 context.result = result
                                 tool_results_buffer.append((execution["tool_call"], result, tool_idx, context))
                             else: # Should not happen with asyncio.wait
                                logger.warning(f"Task for tool index {tool_idx} not done after wait.")
                                self.trace.event(name="task_for_tool_index_not_done_after_wait", level="WARNING", status_message=(f"Task for tool index {tool_idx} not done after wait."))
                         except Exception as e:
                             logger.error(f"Error getting result for pending tool execution {tool_idx}: {str(e)}")
                             self.trace.event(name="error_getting_result_for_pending_tool_execution", level="ERROR", status_message=(f"Error getting result for pending tool execution {tool_idx}: {str(e)}"))
                             context.error = e
                             # Save and Yield tool error status message (even if started was yielded)
                             error_msg_obj = await self._yield_and_save_tool_error(context, thread_id, thread_run_id)
                             if error_msg_obj: yield format_for_yield(error_msg_obj)
                         continue # Skip further status yielding for this tool index

                    # If status wasn't yielded before (shouldn't happen with current logic), yield it now
                    try:
                        if execution["task"].done():
                            result = execution["task"].result()
                            context.result = result
                            tool_results_buffer.append((execution["tool_call"], result, tool_idx, context))
                            # Save and Yield tool completed/failed status
                            completed_msg_obj = await self._yield_and_save_tool_completed(
                                context, None, thread_id, thread_run_id
                            )
                            if completed_msg_obj: yield format_for_yield(completed_msg_obj)
                            yielded_tool_indices.add(tool_idx)
                    except Exception as e:
                        logger.error(f"Error getting result/yielding status for pending tool execution {tool_idx}: {str(e)}")
                        self.trace.event(name="error_getting_result_yielding_status_for_pending_tool_execution", level="ERROR", status_message=(f"Error getting result/yielding status for pending tool execution {tool_idx}: {str(e)}"))
                        context.error = e
                        # Save and Yield tool error status
                        error_msg_obj = await self._yield_and_save_tool_error(context, thread_id, thread_run_id)
                        if error_msg_obj: yield format_for_yield(error_msg_obj)
                        yielded_tool_indices.add(tool_idx)


            # Save and yield finish status if limit was reached
            if finish_reason == "xml_tool_limit_reached":
                finish_content = {"status_type": "finish", "finish_reason": "xml_tool_limit_reached"}
                finish_msg_obj = await self.add_message(
                    thread_id=thread_id, type="status", content=finish_content, 
                    is_llm_message=False, metadata={"thread_run_id": thread_run_id}
                )
                if finish_msg_obj: yield format_for_yield(finish_msg_obj)
                logger.info(f"Stream finished with reason: xml_tool_limit_reached after {xml_tool_call_count} XML tool calls")
                self.trace.event(name="stream_finished_with_reason_xml_tool_limit_reached_after_xml_tool_calls", level="DEFAULT", status_message=(f"Stream finished with reason: xml_tool_limit_reached after {xml_tool_call_count} XML tool calls"))

            # --- SAVE and YIELD Final Assistant Message ---
            if accumulated_content:
                # ... (Truncate accumulated_content logic) ...
                if config.max_xml_tool_calls > 0 and xml_tool_call_count >= config.max_xml_tool_calls and xml_chunks_buffer:
                    last_xml_chunk = xml_chunks_buffer[-1]
                    last_chunk_end_pos = accumulated_content.find(last_xml_chunk) + len(last_xml_chunk)
                    if last_chunk_end_pos > 0:
                        accumulated_content = accumulated_content[:last_chunk_end_pos]

                # ... (Extract complete_native_tool_calls logic) ...
                complete_native_tool_calls = []
                if config.native_tool_calling:
                    for idx, tc_buf in tool_calls_buffer.items():
                        if tc_buf['id'] and tc_buf['function']['name'] and tc_buf['function']['arguments']:
                            try:
                                args = safe_json_parse(tc_buf['function']['arguments'])
                                complete_native_tool_calls.append({
                                    "id": tc_buf['id'], "type": "function",
                                    "function": {"name": tc_buf['function']['name'],"arguments": args}
                                })
                            except json.JSONDecodeError: continue

                message_data = { # Dict to be saved in 'content'
                    "role": "assistant", "content": accumulated_content,
                    "tool_calls": complete_native_tool_calls or None
                }

                last_assistant_message_object = await self.add_message(
                    thread_id=thread_id, type="assistant", content=message_data,
                    is_llm_message=True, metadata={"thread_run_id": thread_run_id}
                )

                if last_assistant_message_object:
                    # Yield the complete saved object, adding stream_status metadata just for yield
                    yield_metadata = ensure_dict(last_assistant_message_object.get('metadata'), {})
                    yield_metadata['stream_status'] = 'complete'
                    # Format the message for yielding
                    yield_message = last_assistant_message_object.copy()
                    yield_message['metadata'] = yield_metadata
                    yield format_for_yield(yield_message)
                else:
                    logger.error(f"Failed to save final assistant message for thread {thread_id}")
                    self.trace.event(name="failed_to_save_final_assistant_message_for_thread", level="ERROR", status_message=(f"Failed to save final assistant message for thread {thread_id}"))
                    # Save and yield an error status
                    err_content = {"role": "system", "status_type": "error", "message": "Failed to save final assistant message"}
                    err_msg_obj = await self.add_message(
                        thread_id=thread_id, type="status", content=err_content, 
                        is_llm_message=False, metadata={"thread_run_id": thread_run_id}
                    )
                    if err_msg_obj: yield format_for_yield(err_msg_obj)

            # --- Process All Tool Results Now ---
            if config.execute_tools:
                final_tool_calls_to_process = []
                # ... (Gather final_tool_calls_to_process from native and XML buffers) ...
                 # Gather native tool calls from buffer
                if config.native_tool_calling and complete_native_tool_calls:
                    for tc in complete_native_tool_calls:
                        final_tool_calls_to_process.append({
                            "function_name": tc["function"]["name"],
                            "arguments": tc["function"]["arguments"], # Already parsed object
                            "id": tc["id"]
                        })
                 # Gather XML tool calls from buffer (up to limit)
                parsed_xml_data = []
                if config.xml_tool_calling:
                    # Reparse remaining content just in case (should be empty if processed correctly)
                    xml_chunks = self._extract_xml_chunks(current_xml_content)
                    xml_chunks_buffer.extend(xml_chunks)
                    # Process only chunks not already handled in the stream loop
                    remaining_limit = config.max_xml_tool_calls - xml_tool_call_count if config.max_xml_tool_calls > 0 else len(xml_chunks_buffer)
                    xml_chunks_to_process = xml_chunks_buffer[:remaining_limit] # Ensure limit is respected

                    for chunk in xml_chunks_to_process:
                         parsed_result = self._parse_xml_tool_call(chunk)
                         if parsed_result:
                             tool_call, parsing_details = parsed_result
                             # Avoid adding if already processed during streaming
                             if not any(exec['tool_call'] == tool_call for exec in pending_tool_executions):
                                 final_tool_calls_to_process.append(tool_call)
                                 parsed_xml_data.append({'tool_call': tool_call, 'parsing_details': parsing_details})


                all_tool_data_map = {} # tool_index -> {'tool_call': ..., 'parsing_details': ...}
                 # Add native tool data
                native_tool_index = 0
                if config.native_tool_calling and complete_native_tool_calls:
                     for tc in complete_native_tool_calls:
                         # Find the corresponding entry in final_tool_calls_to_process if needed
                         # For now, assume order matches if only native used
                         exec_tool_call = {
                             "function_name": tc["function"]["name"],
                             "arguments": tc["function"]["arguments"],
                             "id": tc["id"]
                         }
                         all_tool_data_map[native_tool_index] = {"tool_call": exec_tool_call, "parsing_details": None}
                         native_tool_index += 1

                 # Add XML tool data
                xml_tool_index_start = native_tool_index
                for idx, item in enumerate(parsed_xml_data):
                    all_tool_data_map[xml_tool_index_start + idx] = item


                tool_results_map = {} # tool_index -> (tool_call, result, context)

                # Populate from buffer if executed on stream
                if config.execute_on_stream and tool_results_buffer:
                    logger.info(f"Processing {len(tool_results_buffer)} buffered tool results")
                    self.trace.event(name="processing_buffered_tool_results", level="DEFAULT", status_message=(f"Processing {len(tool_results_buffer)} buffered tool results"))
                    for tool_call, result, tool_idx, context in tool_results_buffer:
                        if last_assistant_message_object: context.assistant_message_id = last_assistant_message_object['message_id']
                        tool_results_map[tool_idx] = (tool_call, result, context)

                # Or execute now if not streamed
                elif final_tool_calls_to_process and not config.execute_on_stream:
                    logger.info(f"Executing {len(final_tool_calls_to_process)} tools ({config.tool_execution_strategy}) after stream")
                    self.trace.event(name="executing_tools_after_stream", level="DEFAULT", status_message=(f"Executing {len(final_tool_calls_to_process)} tools ({config.tool_execution_strategy}) after stream"))
                    results_list = await self._execute_tools(final_tool_calls_to_process, config.tool_execution_strategy)
                    current_tool_idx = 0
                    for tc, res in results_list:
                       # Map back using all_tool_data_map which has correct indices
                       if current_tool_idx in all_tool_data_map:
                           tool_data = all_tool_data_map[current_tool_idx]
                           context = self._create_tool_context(
                               tc, current_tool_idx,
                               last_assistant_message_object['message_id'] if last_assistant_message_object else None,
                               tool_data.get('parsing_details')
                           )
                           context.result = res
                           tool_results_map[current_tool_idx] = (tc, res, context)
                       else:
                           logger.warning(f"Could not map result for tool index {current_tool_idx}")
                           self.trace.event(name="could_not_map_result_for_tool_index", level="WARNING", status_message=(f"Could not map result for tool index {current_tool_idx}"))
                       current_tool_idx += 1

                # Save and Yield each result message
                if tool_results_map:
                    logger.info(f"Saving and yielding {len(tool_results_map)} final tool result messages")
                    self.trace.event(name="saving_and_yielding_final_tool_result_messages", level="DEFAULT", status_message=(f"Saving and yielding {len(tool_results_map)} final tool result messages"))
                    for tool_idx in sorted(tool_results_map.keys()):
                        tool_call, result, context = tool_results_map[tool_idx]
                        context.result = result
                        if not context.assistant_message_id and last_assistant_message_object:
                            context.assistant_message_id = last_assistant_message_object['message_id']

                        # Yield start status ONLY IF executing non-streamed (already yielded if streamed)
                        if not config.execute_on_stream and tool_idx not in yielded_tool_indices:
                            started_msg_obj = await self._yield_and_save_tool_started(context, thread_id, thread_run_id)
                            if started_msg_obj: yield format_for_yield(started_msg_obj)
                            yielded_tool_indices.add(tool_idx) # Mark status yielded

                        # Save the tool result message to DB
                        saved_tool_result_object = await self._add_tool_result( # Returns full object or None
                            thread_id, tool_call, result, config.xml_adding_strategy,
                            context.assistant_message_id, context.parsing_details
                        )

                        # Yield completed/failed status (linked to saved result ID if available)
                        completed_msg_obj = await self._yield_and_save_tool_completed(
                            context,
                            saved_tool_result_object['message_id'] if saved_tool_result_object else None,
                            thread_id, thread_run_id
                        )
                        if completed_msg_obj: yield format_for_yield(completed_msg_obj)
                        # Don't add to yielded_tool_indices here, completion status is separate yield

                        # Yield the saved tool result object
                        if saved_tool_result_object:
                            tool_result_message_objects[tool_idx] = saved_tool_result_object
                            yield format_for_yield(saved_tool_result_object)
                        else:
                             logger.error(f"Failed to save tool result for index {tool_idx}, not yielding result message.")
                             self.trace.event(name="failed_to_save_tool_result_for_index", level="ERROR", status_message=(f"Failed to save tool result for index {tool_idx}, not yielding result message."))
                             # Optionally yield error status for saving failure?

            # --- Calculate and Store Cost ---
            if last_assistant_message_object: # Only calculate if assistant message was saved
                try:
                    # Use accumulated_content for streaming cost calculation
                    final_cost = completion_cost(
                        model=llm_model,
                        messages=prompt_messages, # Use the prompt messages provided
                        completion=accumulated_content
                    )
                    if final_cost is not None and final_cost > 0:
                        logger.info(f"Calculated final cost for stream: {final_cost}")
                        await self.add_message(
                            thread_id=thread_id,
                            type="cost",
                            content={"cost": final_cost},
                            is_llm_message=False, # Cost is metadata
                            metadata={"thread_run_id": thread_run_id} # Keep track of the run
                        )
                        logger.info(f"Cost message saved for stream: {final_cost}")
                        self.trace.update(metadata={"cost": final_cost})
                    else:
                         logger.info("Stream cost calculation resulted in zero or None, not storing cost message.")
                         self.trace.update(metadata={"cost": 0})
                except Exception as e:
                    logger.error(f"Error calculating final cost for stream: {str(e)}")
                    self.trace.event(name="error_calculating_final_cost_for_stream", level="ERROR", status_message=(f"Error calculating final cost for stream: {str(e)}"))


            # --- Final Finish Status ---
            if finish_reason and finish_reason != "xml_tool_limit_reached":
                finish_content = {"status_type": "finish", "finish_reason": finish_reason}
                finish_msg_obj = await self.add_message(
                    thread_id=thread_id, type="status", content=finish_content, 
                    is_llm_message=False, metadata={"thread_run_id": thread_run_id}
                )
                if finish_msg_obj: yield format_for_yield(finish_msg_obj)

        except Exception as e:
            logger.error(f"Error processing stream: {str(e)}", exc_info=True)
            self.trace.event(name="error_processing_stream", level="ERROR", status_message=(f"Error processing stream: {str(e)}"))
            # Save and yield error status message
            err_content = {"role": "system", "status_type": "error", "message": str(e)}
            err_msg_obj = await self.add_message(
                thread_id=thread_id, type="status", content=err_content, 
                is_llm_message=False, metadata={"thread_run_id": thread_run_id if 'thread_run_id' in locals() else None}
            )
            if err_msg_obj: yield format_for_yield(err_msg_obj) # Yield the saved error message
            
            # Re-raise the same exception (not a new one) to ensure proper error propagation
            logger.critical(f"Re-raising error to stop further processing: {str(e)}")
            self.trace.event(name="re_raising_error_to_stop_further_processing", level="ERROR", status_message=(f"Re-raising error to stop further processing: {str(e)}"))
            raise # Use bare 'raise' to preserve the original exception with its traceback

        finally:
            # Save and Yield the final thread_run_end status
            try:
                end_content = {"status_type": "thread_run_end"}
                end_msg_obj = await self.add_message(
                    thread_id=thread_id, type="status", content=end_content, 
                    is_llm_message=False, metadata={"thread_run_id": thread_run_id if 'thread_run_id' in locals() else None}
                )
                if end_msg_obj: yield format_for_yield(end_msg_obj)
            except Exception as final_e:
                logger.error(f"Error in finally block: {str(final_e)}", exc_info=True)
                self.trace.event(name="error_in_finally_block", level="ERROR", status_message=(f"Error in finally block: {str(final_e)}"))

    async def process_non_streaming_response(
        self,
        llm_response: Any,
        thread_id: str,
        prompt_messages: List[Dict[str, Any]],
        llm_model: str,
        config: ProcessorConfig = ProcessorConfig(),
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Process a non-streaming LLM response, handling tool calls and execution.
        
        Args:
            llm_response: Response from the LLM
            thread_id: ID of the conversation thread
            prompt_messages: List of messages sent to the LLM (the prompt)
            llm_model: The name of the LLM model used
            config: Configuration for parsing and execution
            
        Yields:
            Complete message objects matching the DB schema.
        """
        content = ""
        thread_run_id = str(uuid.uuid4())
        all_tool_data = [] # Stores {'tool_call': ..., 'parsing_details': ...}
        tool_index = 0
        assistant_message_object = None
        tool_result_message_objects = {}
        finish_reason = None
        native_tool_calls_for_message = []

        try:
            # Save and Yield thread_run_start status message
            start_content = {"status_type": "thread_run_start", "thread_run_id": thread_run_id}
            start_msg_obj = await self.add_message(
                thread_id=thread_id, type="status", content=start_content,
                is_llm_message=False, metadata={"thread_run_id": thread_run_id}
            )
            if start_msg_obj: yield format_for_yield(start_msg_obj)

            # Extract finish_reason, content, tool calls
            if hasattr(llm_response, 'choices') and llm_response.choices:
                 if hasattr(llm_response.choices[0], 'finish_reason'):
                     finish_reason = llm_response.choices[0].finish_reason
                     logger.info(f"Non-streaming finish_reason: {finish_reason}")
                     self.trace.event(name="non_streaming_finish_reason", level="DEFAULT", status_message=(f"Non-streaming finish_reason: {finish_reason}"))
                 response_message = llm_response.choices[0].message if hasattr(llm_response.choices[0], 'message') else None
                 if response_message:
                     if hasattr(response_message, 'content') and response_message.content:
                         content = response_message.content
                         if config.xml_tool_calling:
                             parsed_xml_data = self._parse_xml_tool_calls(content)
                             if config.max_xml_tool_calls > 0 and len(parsed_xml_data) > config.max_xml_tool_calls:
                                 # Truncate content and tool data if limit exceeded
                                 # ... (Truncation logic similar to streaming) ...
                                 if parsed_xml_data:
                                     xml_chunks = self._extract_xml_chunks(content)[:config.max_xml_tool_calls]
                                     if xml_chunks:
                                         last_chunk = xml_chunks[-1]
                                         last_chunk_pos = content.find(last_chunk)
                                         if last_chunk_pos >= 0: content = content[:last_chunk_pos + len(last_chunk)]
                                 parsed_xml_data = parsed_xml_data[:config.max_xml_tool_calls]
                                 finish_reason = "xml_tool_limit_reached"
                             all_tool_data.extend(parsed_xml_data)

                     if config.native_tool_calling and hasattr(response_message, 'tool_calls') and response_message.tool_calls:
                        for tool_call_obj in response_message.tool_calls: # Renamed to avoid conflict
                            if hasattr(tool_call_obj, 'function'):
                                tool_call_id = tool_call_obj.id if hasattr(tool_call_obj, 'id') else str(uuid.uuid4())
                                function_name = tool_call_obj.function.name
                                arguments_raw = tool_call_obj.function.arguments
                                arguments_parsed = safe_json_parse(arguments_raw) if isinstance(arguments_raw, str) else arguments_raw

                                # Log native tool call detection
                                arguments_json_string = arguments_raw if isinstance(arguments_raw, str) else to_json_string(arguments_parsed)
                                logger.debug(f"Native tool call detected: ID={tool_call_id}, Function={function_name}, Args={arguments_json_string}")

                                exec_tool_call = {
                                    "function_name": function_name,
                                    "arguments": arguments_parsed,
                                    "id": tool_call_id
                                }
                                all_tool_data.append({"tool_call": exec_tool_call, "parsing_details": None})
                                native_tool_calls_for_message.append({
                                    "id": tool_call_id, "type": "function",
                                     "function": {
                                         "name": tool_call.function.name,
                                         "arguments": tool_call.function.arguments if isinstance(tool_call.function.arguments, str) else to_json_string(tool_call.function.arguments)
                                     }
                                 })


            # --- SAVE and YIELD Final Assistant Message ---
            message_data = {"role": "assistant", "content": content, "tool_calls": native_tool_calls_for_message or None}
            assistant_message_object = await self.add_message(
                thread_id=thread_id, type="assistant", content=message_data,
                is_llm_message=True, metadata={"thread_run_id": thread_run_id}
            )
            if assistant_message_object:
                 yield assistant_message_object
            else:
                 logger.error(f"Failed to save non-streaming assistant message for thread {thread_id}")
                 self.trace.event(name="failed_to_save_non_streaming_assistant_message_for_thread", level="ERROR", status_message=(f"Failed to save non-streaming assistant message for thread {thread_id}"))
                 err_content = {"role": "system", "status_type": "error", "message": "Failed to save assistant message"}
                 err_msg_obj = await self.add_message(
                     thread_id=thread_id, type="status", content=err_content, 
                     is_llm_message=False, metadata={"thread_run_id": thread_run_id}
                 )
                 if err_msg_obj: yield format_for_yield(err_msg_obj)

            # --- Calculate and Store Cost ---
            if assistant_message_object: # Only calculate if assistant message was saved
                try:
                    # Use the full llm_response object for potentially more accurate cost calculation
                    final_cost = None
                    if hasattr(llm_response, '_hidden_params') and 'response_cost' in llm_response._hidden_params and llm_response._hidden_params['response_cost'] is not None and llm_response._hidden_params['response_cost'] != 0.0:
                        final_cost = llm_response._hidden_params['response_cost']
                        logger.info(f"Using response_cost from _hidden_params: {final_cost}")

                    if final_cost is None: # Fall back to calculating cost if direct cost not available or zero
                        logger.info("Calculating cost using completion_cost function.")
                        # Note: litellm might need 'messages' kwarg depending on model/provider
                        final_cost = completion_cost(
                            completion_response=llm_response,
                            model=llm_model, # Explicitly pass the model name
                            # messages=prompt_messages # Pass prompt messages if needed by litellm for this model
                        )

                    if final_cost is not None and final_cost > 0:
                        logger.info(f"Calculated final cost for non-stream: {final_cost}")
                        await self.add_message(
                            thread_id=thread_id,
                            type="cost",
                            content={"cost": final_cost},
                            is_llm_message=False, # Cost is metadata
                            metadata={"thread_run_id": thread_run_id} # Keep track of the run
                        )
                        logger.info(f"Cost message saved for non-stream: {final_cost}")
                        self.trace.update(metadata={"cost": final_cost})
                    else:
                        logger.info("Non-stream cost calculation resulted in zero or None, not storing cost message.")
                        self.trace.update(metadata={"cost": 0})

                except Exception as e:
                    logger.error(f"Error calculating final cost for non-stream: {str(e)}")
                    self.trace.event(name="error_calculating_final_cost_for_non_stream", level="ERROR", status_message=(f"Error calculating final cost for non-stream: {str(e)}"))
            # --- Execute Tools and Yield Results ---
            tool_calls_to_execute = [item['tool_call'] for item in all_tool_data]
            if config.execute_tools and tool_calls_to_execute:
                logger.info(f"Executing {len(tool_calls_to_execute)} tools with strategy: {config.tool_execution_strategy}")
                self.trace.event(name="executing_tools_with_strategy", level="DEFAULT", status_message=(f"Executing {len(tool_calls_to_execute)} tools with strategy: {config.tool_execution_strategy}"))

                # Log before calling _execute_tools for non-streaming
                for tc_to_exec in tool_calls_to_execute:
                    tool_name_for_log = tc_to_exec.get('function_name') or f"{tc_to_exec.get('tool_id')}__{tc_to_exec.get('method_name')}"
                    tool_call_details_string = f"Name='{tool_name_for_log}', Args={tc_to_exec.get('arguments', {})}"
                    logger.info(f"Preparing to execute tool (non-streamed): {tool_call_details_string}")

                tool_results = await self._execute_tools(tool_calls_to_execute, config.tool_execution_strategy)

                for i, (returned_tool_call, result) in enumerate(tool_results):
                    original_data = all_tool_data[i]
                    tool_call_from_data = original_data['tool_call']
                    parsing_details = original_data['parsing_details']
                    current_assistant_id = assistant_message_object['message_id'] if assistant_message_object else None

                    context = self._create_tool_context(
                        tool_call_from_data, tool_index, current_assistant_id, parsing_details
                    )
                    context.result = result

                    # Save and Yield start status
                    started_msg_obj = await self._yield_and_save_tool_started(context, thread_id, thread_run_id)
                    if started_msg_obj: yield format_for_yield(started_msg_obj)

                    # Save tool result
                    saved_tool_result_object = await self._add_tool_result(
                        thread_id, tool_call_from_data, result, config.xml_adding_strategy,
                        current_assistant_id, parsing_details
                    )

                    # Save and Yield completed/failed status
                    completed_msg_obj = await self._yield_and_save_tool_completed(
                        context,
                        # Access 'message_id' from the object if it exists
                        saved_tool_result_object['message_id'] if saved_tool_result_object else None,
                        thread_id, thread_run_id
                    )
                    if completed_msg_obj: yield format_for_yield(completed_msg_obj)

                    # Yield the saved tool result object
                    if saved_tool_result_object: # saved_tool_result_object is now the full message dict or None
                        tool_result_message_objects[tool_index] = saved_tool_result_object
                        yield format_for_yield(saved_tool_result_object)
                    else:
                         logger.error(f"Failed to save tool result for index {tool_index}")
                         self.trace.event(name="failed_to_save_tool_result_for_index", level="ERROR", status_message=(f"Failed to save tool result for index {tool_index}"))

                    tool_index += 1

            # --- Save and Yield Final Status ---
            if finish_reason:
                finish_content = {"status_type": "finish", "finish_reason": finish_reason}
                finish_msg_obj = await self.add_message(
                    thread_id=thread_id, type="status", content=finish_content, 
                    is_llm_message=False, metadata={"thread_run_id": thread_run_id}
                )
                if finish_msg_obj: yield format_for_yield(finish_msg_obj)

        except Exception as e:
             logger.error(f"Error processing non-streaming response: {str(e)}", exc_info=True)
             self.trace.event(name="error_processing_non_streaming_response", level="ERROR", status_message=(f"Error processing non-streaming response: {str(e)}"))
             # Save and yield error status
             err_content = {"role": "system", "status_type": "error", "message": str(e)}
             err_msg_obj = await self.add_message(
                 thread_id=thread_id, type="status", content=err_content, 
                 is_llm_message=False, metadata={"thread_run_id": thread_run_id if 'thread_run_id' in locals() else None}
             )
             if err_msg_obj: yield format_for_yield(err_msg_obj)
             
             # Re-raise the same exception (not a new one) to ensure proper error propagation
             logger.critical(f"Re-raising error to stop further processing: {str(e)}")
             self.trace.event(name="re_raising_error_to_stop_further_processing", level="CRITICAL", status_message=(f"Re-raising error to stop further processing: {str(e)}"))
             raise # Use bare 'raise' to preserve the original exception with its traceback

        finally:
             # Save and Yield the final thread_run_end status
            end_content = {"status_type": "thread_run_end"}
            end_msg_obj = await self.add_message(
                thread_id=thread_id, type="status", content=end_content, 
                is_llm_message=False, metadata={"thread_run_id": thread_run_id if 'thread_run_id' in locals() else None}
            )
            if end_msg_obj: yield format_for_yield(end_msg_obj)

    # XML parsing methods
    def _extract_tag_content(self, xml_chunk: str, tag_name: str) -> Tuple[Optional[str], Optional[str]]:
        """Extract content between opening and closing tags, handling nested tags."""
        start_tag = f'<{tag_name}'
        end_tag = f'</{tag_name}>'
        
        try:
            # Find start tag position
            start_pos = xml_chunk.find(start_tag)
            if start_pos == -1:
                return None, xml_chunk
                
            # Find end of opening tag
            tag_end = xml_chunk.find('>', start_pos)
            if tag_end == -1:
                return None, xml_chunk
                
            # Find matching closing tag
            content_start = tag_end + 1
            nesting_level = 1
            pos = content_start
            
            while nesting_level > 0 and pos < len(xml_chunk):
                next_start = xml_chunk.find(start_tag, pos)
                next_end = xml_chunk.find(end_tag, pos)
                
                if next_end == -1:
                    return None, xml_chunk
                    
                if next_start != -1 and next_start < next_end:
                    nesting_level += 1
                    pos = next_start + len(start_tag)
                else:
                    nesting_level -= 1
                    if nesting_level == 0:
                        content = xml_chunk[content_start:next_end]
                        remaining = xml_chunk[next_end + len(end_tag):]
                        return content, remaining
                    else:
                        pos = next_end + len(end_tag)
            
            return None, xml_chunk
            
        except Exception as e:
            logger.error(f"Error extracting tag content: {e}")
            self.trace.event(name="error_extracting_tag_content", level="ERROR", status_message=(f"Error extracting tag content: {e}"))
            return None, xml_chunk

    def _extract_attribute(self, opening_tag: str, attr_name: str) -> Optional[str]:
        """Extract attribute value from opening tag."""
        try:
            # Handle both single and double quotes with raw strings
            patterns = [
                fr'{attr_name}="([^"]*)"',  # Double quotes
                fr"{attr_name}='([^']*)'",  # Single quotes
                fr'{attr_name}=([^\s/>;]+)'  # No quotes - fixed escape sequence
            ]
            
            for pattern in patterns:
                match = re.search(pattern, opening_tag)
                if match:
                    value = match.group(1)
                    # Unescape common XML entities
                    value = value.replace('&quot;', '"').replace('&apos;', "'")
                    value = value.replace('&lt;', '<').replace('&gt;', '>')
                    value = value.replace('&amp;', '&')
                    return value
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting attribute: {e}")
            self.trace.event(name="error_extracting_attribute", level="ERROR", status_message=(f"Error extracting attribute: {e}"))
            return None

    def _extract_xml_chunks(self, content: str) -> List[str]:
        """Extract complete XML chunks using start and end pattern matching."""
        chunks = []
        pos = 0
        
        try:
            while pos < len(content):
                # Find the next tool tag
                next_tag_start = -1
                current_tag = None
                
                # Find the earliest occurrence of any registered tag
                # ToolOrchestrator.get_xml_examples() returns a dict with tag_name as keys.
                # This part needs to get XML tags that are known to the orchestrator.
                # One way is to iterate registered tools and their XML schemas.

                # Let's get the registered tools and check their schemas for XML tags
                registered_xml_tags = []
                if self.tool_orchestrator and self.tool_orchestrator.tools:
                    for tool_id, tool_instance in self.tool_orchestrator.tools.items():
                        schemas = tool_instance.get_schemas()
                        for method_name, schema_list in schemas.items():
                            for schema_obj in schema_list:
                                if schema_obj.xml_schema and schema_obj.xml_schema.tag_name:
                                    registered_xml_tags.append(schema_obj.xml_schema.tag_name)

                # Remove duplicates
                registered_xml_tags = list(set(registered_xml_tags))

                for tag_name in registered_xml_tags: # Use the dynamically obtained tags
                    start_pattern = f'<{tag_name}'
                    tag_pos = content.find(start_pattern, pos)
                    
                    if tag_pos != -1 and (next_tag_start == -1 or tag_pos < next_tag_start):
                        next_tag_start = tag_pos
                        current_tag = tag_name
                
                if next_tag_start == -1 or not current_tag:
                    break
                
                # Find the matching end tag
                end_pattern = f'</{current_tag}>'
                tag_stack = []
                chunk_start = next_tag_start
                current_pos = next_tag_start
                
                while current_pos < len(content):
                    # Look for next start or end tag of the same type
                    next_start = content.find(f'<{current_tag}', current_pos + 1)
                    next_end = content.find(end_pattern, current_pos)
                    
                    if next_end == -1:  # No closing tag found
                        break
                    
                    if next_start != -1 and next_start < next_end:
                        # Found nested start tag
                        tag_stack.append(next_start)
                        current_pos = next_start + 1
                    else:
                        # Found end tag
                        if not tag_stack:  # This is our matching end tag
                            chunk_end = next_end + len(end_pattern)
                            chunk = content[chunk_start:chunk_end]
                            logger.debug(f"Extracted XML chunk: {chunk}") # Logging extracted chunk
                            chunks.append(chunk)
                            pos = chunk_end
                            break
                        else:
                            # Pop nested tag
                            tag_stack.pop()
                            current_pos = next_end + 1
                
                if current_pos >= len(content):  # Reached end without finding closing tag
                    break
                
                pos = max(pos + 1, current_pos)
        
        except Exception as e:
            logger.error(f"Error extracting XML chunks: {e}")
            logger.error(f"Content was: {content}")
            self.trace.event(name="error_extracting_xml_chunks", level="ERROR", status_message=(f"Error extracting XML chunks: {e}"), metadata={"content": content})
        
        return chunks

    def _parse_xml_tool_call(self, xml_chunk: str) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """Parse XML chunk into tool call format and return parsing details.
        
        Returns:
            Tuple of (tool_call, parsing_details) or None if parsing fails.
            - tool_call: Dict with 'function_name', 'xml_tag_name', 'arguments'
            - parsing_details: Dict with 'attributes', 'elements', 'text_content', 'root_content'
        """
        try:
            # Extract tag name and validate
            tag_match = re.match(r'<([^\s>]+)', xml_chunk)
            if not tag_match:
                logger.error(f"No tag found in XML chunk: {xml_chunk}")
                self.trace.event(name="no_tag_found_in_xml_chunk", level="ERROR", status_message=(f"No tag found in XML chunk: {xml_chunk}"))
                return None
            
            # This is the XML tag as it appears in the text (e.g., "create-file")
            xml_tag_name = tag_match.group(1)
            # logger.info(f"Found XML tag: {xml_tag_name}") # Reduced noise, covered by Parsed XML tool call
            # self.trace.event(name="found_xml_tag", level="DEFAULT", status_message=(f"Found XML tag: {xml_tag_name}")) # Covered by Parsed XML tool call
            
            # Get tool info and schema from registry
            # With ToolOrchestrator, we need to find which registered tool_id and method_name correspond to this xml_tag_name.
            target_tool_id = None
            target_method_name = None
            target_schema_obj = None

            if self.tool_orchestrator and self.tool_orchestrator.tools:
                for tool_id, tool_instance in self.tool_orchestrator.tools.items():
                    schemas = tool_instance.get_schemas()
                    for method_name, schema_list in schemas.items():
                        for schema_obj in schema_list:
                            if schema_obj.xml_schema and schema_obj.xml_schema.tag_name == xml_tag_name:
                                target_tool_id = tool_id
                                target_method_name = method_name
                                target_schema_obj = schema_obj
                                break
                        if target_tool_id: break
                    if target_tool_id: break

            if not target_tool_id or not target_method_name or not target_schema_obj:
                logger.error(f"Parsing failed for tag '{xml_tag_name}': No tool or schema found in ToolOrchestrator. Problematic chunk: {xml_chunk}")
                self.trace.event(name="no_tool_or_schema_found_for_tag_orchestrator", level="ERROR", status_message=(f"No tool or schema found for tag: {xml_tag_name}"))
                return None
            
            # This is the actual function name to call (e.g., "create_file")
            # In the orchestrator context, this is target_method_name
            # The tool_id is target_tool_id
            
            schema = target_schema_obj.xml_schema # This is XMLTagSchema
            params = {}
            remaining_chunk = xml_chunk
            
            # --- Store detailed parsing info ---
            parsing_details = {
                "attributes": {},
                "elements": {},
                "text_content": None,
                "root_content": None,
                "raw_chunk": xml_chunk # Store the original chunk for reference
            }
            # ---
            
            # Process each mapping
            for mapping in schema.mappings:
                try:
                    if mapping.node_type == "attribute":
                        # Extract attribute from opening tag
                        opening_tag = remaining_chunk.split('>', 1)[0]
                        value = self._extract_attribute(opening_tag, mapping.param_name)
                        if value is not None:
                            params[mapping.param_name] = value
                            parsing_details["attributes"][mapping.param_name] = value # Store raw attribute
                            # logger.info(f"Found attribute {mapping.param_name}: {value}")
                
                    elif mapping.node_type == "element":
                        # Extract element content
                        content, remaining_chunk = self._extract_tag_content(remaining_chunk, mapping.path)
                        if content is not None:
                            params[mapping.param_name] = content.strip()
                            parsing_details["elements"][mapping.param_name] = content.strip() # Store raw element content
                            # logger.info(f"Found element {mapping.param_name}: {content.strip()}")
                
                    elif mapping.node_type == "text":
                        # Extract text content
                        content, _ = self._extract_tag_content(remaining_chunk, xml_tag_name)
                        if content is not None:
                            params[mapping.param_name] = content.strip()
                            parsing_details["text_content"] = content.strip() # Store raw text content
                            # logger.info(f"Found text content for {mapping.param_name}: {content.strip()}")
                
                    elif mapping.node_type == "content":
                        # Extract root content
                        content, _ = self._extract_tag_content(remaining_chunk, xml_tag_name)
                        if content is not None:
                            params[mapping.param_name] = content.strip()
                            parsing_details["root_content"] = content.strip() # Store raw root content
                            # logger.info(f"Found root content for {mapping.param_name}")
                
                except Exception as e:
                    logger.error(f"Error processing mapping {mapping}: {e}")
                    self.trace.event(name="error_processing_mapping", level="ERROR", status_message=(f"Error processing mapping {mapping}: {e}"))
                    continue

            # Create tool call with clear separation between function_name and xml_tag_name
            # Also include tool_id for the orchestrator
            tool_call = {
                "tool_id": target_tool_id,       # ID of the tool in the orchestrator
                "method_name": target_method_name, # The actual method to call (e.g., create_file)
                "xml_tag_name": xml_tag_name,    # The original XML tag (e.g., create-file)
                "arguments": params              # The extracted parameters
            }
            
            logger.info(f"Parsed XML tool call: Tag='{xml_tag_name}', ToolID='{target_tool_id}', Method='{target_method_name}', Args={params}")
            logger.debug(f"XML parsing details: {parsing_details}")
            # logger.debug(f"Created tool call for orchestrator: {tool_call}") # Redundant with above
            return tool_call, parsing_details # Return both dicts
            
        except Exception as e:
            logger.error(f"Error parsing XML chunk: {e}. Problematic chunk: {xml_chunk}", exc_info=True)
            # logger.error(f"XML chunk was: {xml_chunk}") # Covered by above
            self.trace.event(name="error_parsing_xml_chunk", level="ERROR", status_message=(f"Error parsing XML chunk: {e}"), metadata={"xml_chunk": xml_chunk})
            return None

    def _parse_xml_tool_calls(self, content: str) -> List[Dict[str, Any]]:
        """Parse XML tool calls from content string.
        
        Returns:
            List of dictionaries, each containing {'tool_call': ..., 'parsing_details': ...}
        """
        parsed_data = []
        
        try:
            xml_chunks = self._extract_xml_chunks(content)
            
            for xml_chunk in xml_chunks:
                result = self._parse_xml_tool_call(xml_chunk)
                if result:
                    tool_call, parsing_details = result
                    parsed_data.append({
                        "tool_call": tool_call,
                        "parsing_details": parsing_details
                    })
                    
        except Exception as e:
            logger.error(f"Error parsing XML tool calls: {e}", exc_info=True)
            self.trace.event(name="error_parsing_xml_tool_calls", level="ERROR", status_message=(f"Error parsing XML tool calls: {e}"), metadata={"content": content})
        
        return parsed_data

    # Tool execution methods
    async def _execute_tool(self, tool_call: Dict[str, Any]) -> ToolResult: # Changed return type
        """
        Execute a single tool call using ToolOrchestrator and return the ToolResult.
        """
        # Determine tool_id and method_name from tool_call
        # Native calls: tool_call['function_name'] is "tool_id__method_name"
        # XML calls: tool_call contains 'tool_id' and 'method_name' directly (from _parse_xml_tool_call)

        tool_id_for_orchestrator: Optional[str] = None
        method_name_for_orchestrator: Optional[str] = None

        if "tool_id" in tool_call and "method_name" in tool_call: # Likely XML parsed call
            tool_id_for_orchestrator = tool_call["tool_id"]
            method_name_for_orchestrator = tool_call["method_name"]
        elif "function_name" in tool_call: # Likely native call
            parts = tool_call["function_name"].split("__", 1)
            if len(parts) == 2:
                tool_id_for_orchestrator = parts[0]
                method_name_for_orchestrator = parts[1]
            else:
                # Fallback or error: could not parse tool_id and method_name
                logger.error(f"Could not parse tool_id and method_name from function_name: {tool_call['function_name']}")
                # Create a failed ToolResult directly
                return ToolResult(
                    tool_id=tool_call.get("function_name", "unknown_tool"),
                    execution_id=str(uuid.uuid4()), # Generate a new execution ID
                    status="failed",
                    error=f"Invalid function name format: {tool_call['function_name']}"
                )
        else:
            logger.error(f"Tool call dictionary is missing 'function_name' or 'tool_id'/'method_name': {tool_call}")
            return ToolResult(
                tool_id="unknown_tool",
                execution_id=str(uuid.uuid4()),
                status="failed",
                error="Malformed tool_call object"
            )

        arguments = tool_call.get("arguments", {})
        # Logging "Preparing to execute tool" is handled by the caller of _execute_tool for non-streaming,
        # and within the streaming loop for streamed execution.

        span_name = f"execute_tool.{tool_id_for_orchestrator}.{method_name_for_orchestrator}"
        span = self.trace.span(name=span_name, input=arguments)

        try:
            # logger.info(f"Orchestrating tool: {tool_id_for_orchestrator}, method: {method_name_for_orchestrator} with arguments: {arguments}") # Covered by "Preparing to execute"
            self.trace.event(name="orchestrating_tool", level="DEFAULT",
                             status_message=(f"Tool: {tool_id_for_orchestrator}, Method: {method_name_for_orchestrator}"))
            
            if isinstance(arguments, str): # Ensure arguments is a dict
                try:
                    arguments = safe_json_parse(arguments)
                except json.JSONDecodeError: # If not JSON, wrap it as per previous logic
                    arguments = {"text": arguments}
            
            # Call the orchestrator's execute_tool method
            enhanced_result = await self.tool_orchestrator.execute_tool(
                tool_id=tool_id_for_orchestrator,
                method_name=method_name_for_orchestrator,
                params=arguments
            )
            
            # logger.info(f"Tool execution via orchestrator complete: {tool_id_for_orchestrator}.{method_name_for_orchestrator} -> Status: {enhanced_result.status}") # Covered by "Adding tool result"
            span.end(status_message=f"tool_executed_via_orchestrator: {enhanced_result.status}", output=enhanced_result.result or enhanced_result.error)
            return enhanced_result
            
        except Exception as e:
            logger.error(f"Error calling ToolOrchestrator for {tool_id_for_orchestrator}.{method_name_for_orchestrator}: {str(e)}", exc_info=True)
            span.end(status_message="tool_orchestration_error", output=str(e), level="ERROR")
            # Construct a failed ToolResult
            return ToolResult(
                tool_id=tool_id_for_orchestrator or "unknown_tool",
                execution_id=str(uuid.uuid4()), # Generate new exec id for this failure event
                status="failed",
                error=f"Error during tool orchestration: {str(e)}"
            )

    async def _execute_tools(
        self, 
        tool_calls: List[Dict[str, Any]], 
        execution_strategy: ToolExecutionStrategy = "sequential"
    ) -> List[Tuple[Dict[str, Any], ToolResult]]: # Changed return type
        """Execute tool calls with the specified strategy using ToolOrchestrator.
        
        Args:
            tool_calls: List of tool calls to execute
            execution_strategy: Strategy for executing tools
                
        Returns:
            List of tuples containing the original tool call and its ToolResult
        """
        logger.info(f"Executing {len(tool_calls)} tools with strategy: {execution_strategy} via ToolOrchestrator")
        self.trace.event(name="executing_tools_via_orchestrator", level="DEFAULT",
                         status_message=(f"Count: {len(tool_calls)}, Strategy: {execution_strategy}"))
            
        if execution_strategy == "sequential":
            return await self._execute_tools_sequentially(tool_calls)
        elif execution_strategy == "parallel":
            return await self._execute_tools_in_parallel(tool_calls)
        else:
            logger.warning(f"Unknown execution strategy: {execution_strategy}, falling back to sequential")
            return await self._execute_tools_sequentially(tool_calls)

    async def _execute_tools_sequentially(self, tool_calls: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], ToolResult]]: # Changed return type
        """Execute tool calls sequentially using ToolOrchestrator."""
        if not tool_calls:
            return []
            
        results = []
        for index, tool_call in enumerate(tool_calls):
            tool_repr = tool_call.get('function_name') or f"{tool_call.get('tool_id')}__{tool_call.get('method_name')}"
            logger.debug(f"Executing tool {index+1}/{len(tool_calls)} (seq): {tool_repr}")
            
            try:
                result = await self._execute_tool(tool_call) # This now calls the orchestrator
                results.append((tool_call, result))
                logger.debug(f"Completed tool {tool_repr} with status: {result.status}")
            except Exception as e: # Should be caught by _execute_tool, but as a safeguard
                logger.error(f"Outer error executing tool {tool_repr} (seq): {str(e)}")
                error_result = ToolResult(
                    tool_id=tool_call.get('tool_id') or tool_call.get('function_name', 'unknown'),
                    execution_id=str(uuid.uuid4()), status="failed",
                    error=f"Sequential execution error: {str(e)}"
                )
                results.append((tool_call, error_result))
        return results

    async def _execute_tools_in_parallel(self, tool_calls: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], ToolResult]]: # Changed return type
        """Execute tool calls in parallel using ToolOrchestrator."""
        if not tool_calls:
            return []
            
        tasks = [self._execute_tool(tool_call) for tool_call in tool_calls]
        # Execute all tasks concurrently, exceptions are returned by gather
        gathered_results = await asyncio.gather(*tasks, return_exceptions=True)
            
        processed_results = []
        for i, (tool_call, result_or_exception) in enumerate(zip(tool_calls, gathered_results)):
            tool_repr = tool_call.get('function_name') or f"{tool_call.get('tool_id')}__{tool_call.get('method_name')}"
            if isinstance(result_or_exception, Exception):
                logger.error(f"Error executing tool {tool_repr} (parallel): {str(result_or_exception)}")
                error_result = ToolResult(
                    tool_id=tool_call.get('tool_id') or tool_call.get('function_name', 'unknown'),
                    execution_id=str(uuid.uuid4()), status="failed",
                    error=f"Parallel execution error: {str(result_or_exception)}"
                )
                processed_results.append((tool_call, error_result))
            else: # It's a ToolResult
                processed_results.append((tool_call, result_or_exception))
        return processed_results

    async def _add_tool_result(
        self, 
        thread_id: str, 
        tool_call: Dict[str, Any], 
        result: ToolResult,
        strategy: Union[XmlAddingStrategy, str] = "assistant_message",
        assistant_message_id: Optional[str] = None,
        parsing_details: Optional[Dict[str, Any]] = None
    ) -> Optional[Dict[str, Any]]: # Return the full saved message object or None
        """Add a tool result (ToolResult) to the conversation thread."""
        try:
            # message_id = None # Not used directly like this anymore
            metadata = {}
            if assistant_message_id:
                metadata["assistant_message_id"] = assistant_message_id
            if parsing_details:
                metadata["parsing_details"] = parsing_details
            
            # Native function calls have an 'id' in the original tool_call from the LLM
            is_native_call = "id" in tool_call

            tool_name_for_logging = result.tool_id # From ToolResult

            # Determine content for the message
            # For native calls, content is result.result or result.error
            # For XML, it's formatted string
            if is_native_call:
                content_to_store = result.result if result.status == "completed" else result.error
                if isinstance(content_to_store, (dict, list)):
                    content_to_store = json.dumps(content_to_store)
                else:
                    content_to_store = str(content_to_store) # Ensure string
            else: # XML call
                # _format_xml_tool_result expects the old ToolResult structure.
                # We need to adapt or make it use ToolResult.
                # For now, let's quickly adapt here.
                temp_legacy_result_obj = {"success": result.status == "completed", "output": result.result or result.error}
                # This is a simplification; ToolResult was a class. Let's assume _format_xml_tool_result just needs a string.
                content_to_store = self._format_xml_tool_result(tool_call, str(temp_legacy_result_obj['output']))

            logger.info(f"Adding tool result to history: ToolName='{tool_name_for_logging}', Status='{result.status}', AssistantMessageID='{assistant_message_id}'")
            logger.debug(f"Tool result content being added: {content_to_store}")


            if is_native_call:
                tool_message_content = {
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "name": tool_call.get("function_name", tool_name_for_logging), # function_name from original LLM req
                    "content": content_to_store
                }
                # logger.info(f"Adding native tool result for tool_call_id={tool_call['id']}") # Covered by general log
                msg_obj = await self.add_message(
                    thread_id=thread_id, type="tool", content=tool_message_content, # type="tool"
                    is_llm_message=True, metadata=metadata # Typically considered LLM-related
                )
                return msg_obj # Return the full message object

            # XML or other non-native tools (strategy applies here)
            result_role = "user" if strategy == "user_message" else "assistant"
            # For XML, the content_payload is the full XML string <tool_result>...</tool_result>
            # This is then wrapped in a standard message structure.
            # content_to_store here IS the <tool_result>...</tool_result> string for XML
            tool_result_message_content = {"role": result_role, "content": content_to_store}


            # The type of message for XML tool results might depend on the strategy
            # If it's "user_message", type might be "user". If "assistant_message", type "assistant".
            # Let's assume type="tool" is generic enough, or adjust if needed.
            # For now, keeping type="tool" as it represents the result of a tool.
            message_type_for_xml_result = "tool" # Or map based on strategy if desired.
            
            msg_obj = await self.add_message(
                thread_id=thread_id, type=message_type_for_xml_result,
                content=tool_result_message_content,
                is_llm_message=True, metadata=metadata # True if it's part of LLM flow
            )
            return msg_obj # Return the full message object

        except Exception as e:
            logger.error(f"Error adding tool result (ToolResult): {str(e)}", exc_info=True)
            # Fallback for safety, though less likely needed now
            try:
                error_content_str = str(result.result or result.error or "Unknown tool processing error")
                fallback_content_dict = {"role": "system", "content": f"Error processing tool result: {error_content_str[:500]}"}
                msg_obj = await self.add_message(
                    thread_id=thread_id, type="status", content=fallback_content_dict, # Save as a status message
                    is_llm_message=False, metadata=metadata
                )
                return msg_obj # Return the error status message object
            except Exception as e2:
                logger.error(f"Failed even with fallback message saving for tool result: {str(e2)}", exc_info=True)
                self.trace.event(name="failed_fallback_message_saving_for_tool_result", level="CRITICAL", status_message=(f"Failed fallback message saving: {str(e2)}"))
                return None

    def _format_xml_tool_result(self, tool_call: Dict[str, Any], result_str: str) -> str:
        """Format a tool result wrapped in a <tool_result> tag.

        Args:
            tool_call: The tool call that was executed
            result: The result of the tool execution

        Returns:
            String containing the formatted result wrapped in <tool_result> tag
        """
        """Format an XML tool result string wrapped in a <tool_result> tag."""
        # xml_tag_name should be present in tool_call for XML tools from _parse_xml_tool_call
        xml_tag_name = tool_call.get("xml_tag_name", tool_call.get("method_name", "unknown_tool"))
        return f"<tool_result> <{xml_tag_name}> {result_str} </{xml_tag_name}> </tool_result>"

    def _create_tool_context(self, tool_call: Dict[str, Any], tool_index: int, assistant_message_id: Optional[str] = None, parsing_details: Optional[Dict[str, Any]] = None) -> ToolExecutionContext:
        """Create a tool execution context with display name and parsing details populated."""
        context = ToolExecutionContext(
            tool_call=tool_call,
            tool_index=tool_index,
            assistant_message_id=assistant_message_id,
            parsing_details=parsing_details
        )
        
        # Set function_name and xml_tag_name fields
        if "xml_tag_name" in tool_call:
            context.xml_tag_name = tool_call["xml_tag_name"]
            context.function_name = tool_call.get("function_name", tool_call["xml_tag_name"])
        else:
            # For non-XML tools, use function name directly
            context.function_name = tool_call.get("function_name", "unknown")
            context.xml_tag_name = None
        
        return context
        
    async def _yield_and_save_tool_started(self, context: ToolExecutionContext, thread_id: str, thread_run_id: str) -> Optional[Dict[str, Any]]:
        """Formats, saves, and returns a tool started status message."""
        tool_name = context.xml_tag_name or context.function_name
        content = {
            "role": "assistant", "status_type": "tool_started",
            "function_name": context.function_name, "xml_tag_name": context.xml_tag_name,
            "message": f"Starting execution of {tool_name}", "tool_index": context.tool_index,
            "tool_call_id": context.tool_call.get("id") # Include tool_call ID if native
        }
        metadata = {"thread_run_id": thread_run_id}
        # If context.result is ToolResult, it might contain artifacts or other metadata
        if context.result and context.result.artifacts:
            metadata["artifacts"] = context.result.artifacts
        if context.result and context.result.warnings:
            metadata["warnings"] = context.result.warnings

        saved_message_obj = await self.add_message(
            thread_id=thread_id, type="status", content=content, is_llm_message=False, metadata=metadata
        )
        return saved_message_obj # Return the full object (or None if saving failed)

    async def _yield_and_save_tool_completed(self, context: ToolExecutionContext, tool_message_id: Optional[str], thread_id: str, thread_run_id: str) -> Optional[Dict[str, Any]]:
        """Formats, saves, and returns a tool completed/failed status message using ToolResult."""
        if not context.result: # context.result is a ToolResult
            return await self._yield_and_save_tool_error(context, thread_id, thread_run_id)

        tool_name = context.xml_tag_name or context.function_name
        # Use status from ToolResult
        is_success = context.result.status == "completed"
        status_type = "tool_completed" if is_success else "tool_failed"
        message_text = f"Tool {tool_name} {context.result.status}"
        if not is_success and context.result.error:
            message_text += f": {context.result.error}"


        content = {
            "role": "assistant", "status_type": status_type,
            "function_name": context.function_name, "xml_tag_name": context.xml_tag_name,
            "message": message_text, "tool_index": context.tool_index,
            "tool_call_id": context.tool_call.get("id") # Native tool_call id
        }
        metadata = {"thread_run_id": thread_run_id}
        if context.result.artifacts:
            metadata["artifacts"] = context.result.artifacts
        if context.result.warnings:
            metadata["warnings"] = context.result.warnings

        # Add the *actual* tool result message ID to the metadata if available and successful
        if is_success and tool_message_id:
            metadata["linked_tool_result_message_id"] = tool_message_id
            
        # <<< ADDED: Signal if this is a terminating tool >>>
        # This list might need to come from a config or be more dynamic
        if context.function_name in ['ask', 'complete', 'submit_subtask_report']: # Added submit_subtask_report
            metadata["agent_should_terminate"] = True
            logger.info(f"Marking tool status for '{context.function_name}' with termination signal.")
            self.trace.event(name="marking_tool_status_for_termination", level="DEFAULT", status_message=(f"Marking tool status for '{context.function_name}' with termination signal."))
        # <<< END ADDED >>>

        saved_message_obj = await self.add_message(
            thread_id=thread_id, type="status", content=content, is_llm_message=False, metadata=metadata
        )
        return saved_message_obj

    async def _yield_and_save_tool_error(self, context: ToolExecutionContext, thread_id: str, thread_run_id: str) -> Optional[Dict[str, Any]]:
        """Formats, saves, and returns a tool error status message."""
        error_msg = str(context.error) if context.error else "Unknown error during tool execution"
        tool_name = context.xml_tag_name or context.function_name
        content = {
            "role": "assistant", "status_type": "tool_error",
            "function_name": context.function_name, "xml_tag_name": context.xml_tag_name,
            "message": f"Error executing tool {tool_name}: {error_msg}",
            "tool_index": context.tool_index,
            "tool_call_id": context.tool_call.get("id")
        }
        metadata = {"thread_run_id": thread_run_id}
        # Save the status message with is_llm_message=False
        saved_message_obj = await self.add_message(
            thread_id=thread_id, type="status", content=content, is_llm_message=False, metadata=metadata
        )
        return saved_message_obj