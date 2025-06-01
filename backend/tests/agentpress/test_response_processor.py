import pytest
import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import json
import asyncio
from typing import List, Dict, Any, AsyncGenerator
import uuid # For mocking plan_execution_run_id if needed

from agentpress.response_processor import ResponseProcessor, ProcessorConfig, ToolExecutionContext
from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.plan_executor import PlanExecutor # Added
from agentpress.tool import ToolResult
from agentpress.utils.json_helpers import format_for_yield, to_json_string # Added
from utils.logger import logger # Assuming logger is configured

# Disable most logging for tests to keep output clean, can be enabled for debugging
# logger.setLevel("CRITICAL")

# --- Helper to create mock LLM stream chunks ---
async def mock_llm_stream_chunks(chunks_data: List[Dict[str, Any]]) -> AsyncGenerator[MagicMock, None]:
    for data in chunks_data:
        chunk_mock = MagicMock()
        choice_mock = MagicMock()
        delta_mock = MagicMock()

        if "content" in data:
            delta_mock.content = data["content"]
        else:
            delta_mock.content = None

        if "tool_calls" in data: # For native tool calls
            delta_mock.tool_calls = []
            for tc_chunk_data in data["tool_calls"]:
                tool_call_chunk_mock = MagicMock()
                tool_call_chunk_mock.index = tc_chunk_data.get("index")
                tool_call_chunk_mock.id = tc_chunk_data.get("id")
                tool_call_chunk_mock.type = tc_chunk_data.get("type", "function")

                function_mock = MagicMock()
                function_mock.name = tc_chunk_data.get("function_name")
                function_mock.arguments = tc_chunk_data.get("arguments_chunk")
                tool_call_chunk_mock.function = function_mock
                delta_mock.tool_calls.append(tool_call_chunk_mock)
        else:
            delta_mock.tool_calls = None

        choice_mock.delta = delta_mock
        choice_mock.finish_reason = data.get("finish_reason")
        chunk_mock.choices = [choice_mock]

        # Simulate a short delay as a real stream would have
        # await asyncio.sleep(0.001)
        yield chunk_mock

# --- Helper to create mock LLM non-streaming response ---
def mock_llm_non_stream_response(content: str = None, tool_calls_data: List[Dict[str, Any]] = None, finish_reason: str = "stop"):
    response_mock = MagicMock()
    choice_mock = MagicMock()
    message_mock = MagicMock()

    message_mock.content = content
    if tool_calls_data:
        message_mock.tool_calls = []
        for tc_data in tool_calls_data:
            tool_call_mock = MagicMock()
            tool_call_mock.id = tc_data["id"]
            tool_call_mock.type = tc_data.get("type", "function")
            function_mock = MagicMock()
            function_mock.name = tc_data["function_name"]
            function_mock.arguments = tc_data["arguments_json_string"] # Expects JSON string
            tool_call_mock.function = function_mock
            message_mock.tool_calls.append(tool_call_mock)
    else:
        message_mock.tool_calls = None

    choice_mock.message = message_mock
    choice_mock.finish_reason = finish_reason
    response_mock.choices = [choice_mock]
    return response_mock


class TestResponseProcessor(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
        self.mock_add_message_callback = AsyncMock()

        # Default behavior for add_message_callback: return a mock message object
        async def default_add_message_impl(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
            mock_msg = {
                "thread_id": thread_id, "type": type, "content": content,
                "is_llm_message": is_llm_message, "metadata": metadata or {},
                "message_id": message_id or f"mock_msg_{type}_{len(self.mock_add_message_callback.mock_calls)}",
                "created_at": "now", "updated_at": "now"
            }
            if isinstance(content, dict) and "role" in content : # if content is a dict with role
                mock_msg["content"] = json.dumps(content)
            if isinstance(metadata, dict):
                 mock_msg["metadata"] = json.dumps(metadata)
            return mock_msg
        self.mock_add_message_callback.side_effect = default_add_message_impl

        self.mock_plan_executor = MagicMock(spec=PlanExecutor) # Added
        self.processor = ResponseProcessor(
            tool_orchestrator=self.mock_tool_orchestrator,
            add_message_callback=self.mock_add_message_callback,
            plan_executor=self.mock_plan_executor, # Added
            trace=None # Added for consistency
        )
        self.thread_id = "test_thread_123"
        self.prompt_messages = [{"role": "user", "content": "Hello"}]
        self.llm_model = "test_model"

        # Mocking the ToolOrchestrator's schema methods for XML parsing
        # Mocking for ToolOrchestrator's get_xml_examples and get_tool_schemas_for_llm
        # These are called by ResponseProcessor.
        # For XML parsing, ResponseProcessor._extract_xml_chunks calls self.tool_orchestrator.tools[tool_id].get_schemas()
        # to get registered XML tags. We need to mock this.
        self.mock_tool_orchestrator.tools = {
            "MockPythonTool": MagicMock(), # Simpler mock for now
            "MockWebSearchTool": MagicMock()
        }
        # Simulate that 'execute_python_code' and 'web_search' are registered XML tags
        # The actual schema structure for XMLTagSchema is complex, so we simplify the mock.
        # ResponseProcessor._extract_xml_chunks iterates registered tools and their schemas
        # to find schema_obj.xml_schema.tag_name.

        # Mock get_schemas to return something that _extract_xml_chunks can use
        # to find 'tag_name'.
        mock_python_xml_schema = MagicMock()
        mock_python_xml_schema.tag_name = "execute_python_code"
        mock_python_tool_schema_obj = MagicMock()
        mock_python_tool_schema_obj.xml_schema = mock_python_xml_schema

        self.mock_tool_orchestrator.tools["MockPythonTool"].get_schemas.return_value = {
            "execute_python_code": [mock_python_tool_schema_obj]
        }

        mock_web_xml_schema = MagicMock()
        mock_web_xml_schema.tag_name = "web_search"
        mock_web_tool_schema_obj = MagicMock()
        mock_web_tool_schema_obj.xml_schema = mock_web_xml_schema

        self.mock_tool_orchestrator.tools["MockWebSearchTool"].get_schemas.return_value = {
            "web_search": [mock_web_tool_schema_obj]
        }


    async def consume_async_generator(self, gen: AsyncGenerator):
        items = []
        async for item in gen:
            items.append(item)
        return items

# --- Helper for streaming input for new tests ---
async def _stream_input_chunks(chunks_content: List[Optional[str]], finish_reason: Optional[str] = "stop") -> AsyncGenerator[Dict[str, Any], None]:
    for content in chunks_content:
        delta = {"content": content}
        # For LiteLLM, a chunk that signifies the end will have finish_reason, content might be None or last bit.
        # If content is explicitly None in chunks_content, it's likely meant to be a finish signal chunk.
        current_finish_reason = None
        if content is None:
            delta = {"content": None}
            current_finish_reason = finish_reason

        yield {"choices": [{"delta": delta, "finish_reason": current_finish_reason}]}

    # If the loop finished and the last content was not None (i.e., not a finish chunk)
    # and a finish_reason is provided, yield a final finish chunk.
    if chunks_content and chunks_content[-1] is not None and finish_reason:
         yield {"choices": [{"delta": {"content": None}, "finish_reason": finish_reason}]}
    elif not chunks_content and finish_reason: # Handle empty content list, just yield finish
         yield {"choices": [{"delta": {"content": None}, "finish_reason": finish_reason}]}


    # --- Native Tool Call Tests ---

    async def test_process_streaming_native_tool_call_success(self):
        config = ProcessorConfig(native_tool_calling=True, xml_tool_calling=False, execute_tools=True, execute_on_stream=True)
        # Ensure this test uses the updated processor from setUp
        self.processor.mock_plan_executor.execute_json_plan = AsyncMock(return_value=[]) # Ensure plan executor is benign for this test

        tool_call_id = "call_python_123"
        function_name = "MockPythonTool__execute_python_code"
        arguments_chunks = ['{"co', 'de": "', 'print(\'hello\')"}']

        llm_chunks_data = [
            {"content": "Okay, I will run python: "},
            {"tool_calls": [{"index": 0, "id": tool_call_id, "function_name": function_name, "arguments_chunk": arguments_chunks[0]}]},
            {"tool_calls": [{"index": 0, "id": None, "function_name": None, "arguments_chunk": arguments_chunks[1]}]},
            {"tool_calls": [{"index": 0, "id": None, "function_name": None, "arguments_chunk": arguments_chunks[2]}]},
            {"content": "The code is running.", "finish_reason": "tool_calls"}
        ]

        mock_tool_result = ToolResult(
            tool_id="MockPythonTool", execution_id="exec_123", status="completed",
            result={"output": "hello world"},
            start_time=0, end_time=1
        )
        self.mock_tool_orchestrator.execute_tool = AsyncMock(return_value=mock_tool_result)

        response_generator = self.processor.process_streaming_response(
            mock_llm_stream_chunks(llm_chunks_data), self.thread_id, self.prompt_messages, self.llm_model, config
        )
        results = await self.consume_async_generator(response_generator)

        # Assertions
        self.mock_tool_orchestrator.execute_tool.assert_called_once_with(
            tool_id="MockPythonTool",
            method_name="execute_python_code",
            params={"code": "print('hello')"}
        )

        # Check for assistant message parts
        self.assertTrue(any(r.get('type') == 'assistant' and json.loads(r['content']).get('content') == "Okay, I will run python: " for r in results))
        self.assertTrue(any(r.get('type') == 'assistant' and json.loads(r['content']).get('content') == "The code is running." for r in results))

        # Check for status messages (tool_started, tool_completed)
        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_started' for r in results))
        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_completed' for r in results))

        # Check for the final tool result message
        tool_result_msg = next((r for r in results if r.get('type') == 'tool'), None)
        self.assertIsNotNone(tool_result_msg)
        tool_result_content = json.loads(tool_result_msg['content'])
        self.assertEqual(tool_result_content['tool_call_id'], tool_call_id)
        self.assertEqual(tool_result_content['name'], function_name) # Check if it's MockPythonTool__execute_python_code
        self.assertEqual(json.loads(tool_result_content['content']), {"output": "hello world"})


    async def test_process_non_streaming_native_tool_call_success(self):
        config = ProcessorConfig(native_tool_calling=True, xml_tool_calling=False, execute_tools=True)

        tool_call_id = "call_python_456"
        function_name = "MockPythonTool__execute_python_code"
        arguments_json_string = '{"code": "print(\'non-stream\')"}'

        llm_response_mock = mock_llm_non_stream_response(
            content="I will run this python code for you.",
            tool_calls_data=[{
                "id": tool_call_id,
                "function_name": function_name,
                "arguments_json_string": arguments_json_string
            }],
            finish_reason="tool_calls"
        )

        mock_tool_result = ToolResult(
            tool_id="MockPythonTool", execution_id="exec_456", status="completed",
            result={"output": "non-stream output"}, start_time=0, end_time=1
        )
        self.mock_tool_orchestrator.execute_tool = AsyncMock(return_value=mock_tool_result)

        response_generator = self.processor.process_non_streaming_response(
            llm_response_mock, self.thread_id, self.prompt_messages, self.llm_model, config
        )
        results = await self.consume_async_generator(response_generator)

        self.mock_tool_orchestrator.execute_tool.assert_called_once_with(
            tool_id="MockPythonTool",
            method_name="execute_python_code",
            params={"code": "print('non-stream')"}
        )

        # Check for assistant message (should be one complete message)
        assistant_msg = next((r for r in results if r.get('type') == 'assistant'), None)
        self.assertIsNotNone(assistant_msg)
        assistant_content = json.loads(assistant_msg['content'])
        self.assertEqual(assistant_content['content'], "I will run this python code for you.")
        # Also check if the tool_calls part is in the assistant message's content if applicable by your design
        self.assertTrue(any(tc['id'] == tool_call_id for tc in assistant_content.get('tool_calls', [])))


        # Check for status messages
        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_started' for r in results))
        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_completed' for r in results))

        # Check for the tool result message
        tool_result_msg = next((r for r in results if r.get('type') == 'tool'), None)
        self.assertIsNotNone(tool_result_msg)
        tool_result_content = json.loads(tool_result_msg['content'])
        self.assertEqual(tool_result_content['tool_call_id'], tool_call_id)
        self.assertEqual(json.loads(tool_result_content['content']), {"output": "non-stream output"})

    # --- XML Tool Call Tests ---
    async def test_process_streaming_xml_tool_call_success(self):
        config = ProcessorConfig(native_tool_calling=False, xml_tool_calling=True, execute_tools=True, execute_on_stream=True)

        xml_code = "<execute_python_code code='print(\"xml_stream\")'/>"

        llm_chunks_data = [
            {"content": "Okay, running XML: "},
            {"content": xml_code[:15]},
            {"content": xml_code[15:]},
            {"content": " Done.", "finish_reason": "stop"}
        ]

        mock_tool_result = ToolResult(
            tool_id="MockPythonTool", execution_id="exec_xml_stream", status="completed",
            result={"output": "xml_stream_output"}, start_time=0, end_time=1
        )
        self.mock_tool_orchestrator.execute_tool = AsyncMock(return_value=mock_tool_result)

        response_generator = self.processor.process_streaming_response(
            mock_llm_stream_chunks(llm_chunks_data), self.thread_id, self.prompt_messages, self.llm_model, config
        )
        results = await self.consume_async_generator(response_generator)

        self.mock_tool_orchestrator.execute_tool.assert_called_once_with(
            tool_id="MockPythonTool",
            method_name="execute_python_code",
            params={"code": 'print("xml_stream")'} # Note: attribute value
        )

        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_started' for r in results))
        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_completed' for r in results))

        tool_result_msg = next((r for r in results if r.get('type') == 'tool'), None)
        self.assertIsNotNone(tool_result_msg)
        # For XML, the content is often the raw XML result string or a JSON representation
        # depending on _add_tool_result and xml_adding_strategy. Default is assistant_message.
        # The ToolResult's result field is what we check against.
        # The actual message content will be like <tool_result><execute_python_code>...</execute_python_code></tool_result>
        # Let's check if the original tool's output is somewhere within the message content.
        self.assertIn("xml_stream_output", json.loads(tool_result_msg['content'])['content'])


    async def test_process_non_streaming_xml_tool_call_success(self):
        config = ProcessorConfig(native_tool_calling=False, xml_tool_calling=True, execute_tools=True)

        xml_code = "<web_search><query>non_stream_xml</query></web_search>"
        llm_response_mock = mock_llm_non_stream_response(
            content=f"Here are the search results: {xml_code}",
            finish_reason="stop"
        )

        mock_tool_result = ToolResult(
            tool_id="MockWebSearchTool", execution_id="exec_xml_nonstream", status="completed",
            result={"results": "results for non_stream_xml"}, start_time=0, end_time=1
        )
        self.mock_tool_orchestrator.execute_tool = AsyncMock(return_value=mock_tool_result)

        response_generator = self.processor.process_non_streaming_response(
            llm_response_mock, self.thread_id, self.prompt_messages, self.llm_model, config
        )
        results = await self.consume_async_generator(response_generator)

        self.mock_tool_orchestrator.execute_tool.assert_called_once_with(
            tool_id="MockWebSearchTool",
            method_name="web_search",
            params={"query": "non_stream_xml"}
        )

        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_started' for r in results))
        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_completed' for r in results))

        tool_result_msg = next((r for r in results if r.get('type') == 'tool'), None)
        self.assertIsNotNone(tool_result_msg)
        self.assertIn("results for non_stream_xml", json.loads(tool_result_msg['content'])['content'])


    # --- Config Toggle Tests ---
    async def test_native_tool_calling_disabled(self):
        config = ProcessorConfig(native_tool_calling=False, xml_tool_calling=True, execute_tools=True)
        # Use the same non-streaming setup as test_process_non_streaming_native_tool_call_success
        tool_call_id = "call_python_789"
        function_name = "MockPythonTool__execute_python_code"
        arguments_json_string = '{"code": "print(\'disabled_native\')"}'
        llm_response_mock = mock_llm_non_stream_response(
            content="Native call with native disabled.",
            tool_calls_data=[{"id": tool_call_id, "function_name": function_name, "arguments_json_string": arguments_json_string}],
            finish_reason="tool_calls"
        )
        self.mock_tool_orchestrator.execute_tool = AsyncMock()

        response_generator = self.processor.process_non_streaming_response(
            llm_response_mock, self.thread_id, self.prompt_messages, self.llm_model, config
        )
        await self.consume_async_generator(response_generator)

        self.mock_tool_orchestrator.execute_tool.assert_not_called()

    async def test_xml_tool_calling_disabled(self):
        config = ProcessorConfig(native_tool_calling=True, xml_tool_calling=False, execute_tools=True)
        xml_code = "<execute_python_code code='print(\"disabled_xml\")'/>"
        llm_response_mock = mock_llm_non_stream_response(
            content=f"XML call with XML disabled: {xml_code}",
            finish_reason="stop"
        )
        self.mock_tool_orchestrator.execute_tool = AsyncMock()

        response_generator = self.processor.process_non_streaming_response(
            llm_response_mock, self.thread_id, self.prompt_messages, self.llm_model, config
        )
        await self.consume_async_generator(response_generator)

        self.mock_tool_orchestrator.execute_tool.assert_not_called()

    # --- Error Handling Tests ---
    async def test_tool_execution_failure_streaming(self):
        config = ProcessorConfig(native_tool_calling=True, xml_tool_calling=False, execute_tools=True, execute_on_stream=True)
        tool_call_id = "call_fail_stream"
        function_name = "MockPythonTool__execute_python_code"
        arguments_chunks = ['{"co', 'de": "', 'error_code"}']
        llm_chunks_data = [
            {"tool_calls": [{"index": 0, "id": tool_call_id, "function_name": function_name, "arguments_chunk": arguments_chunks[0]}]},
            {"tool_calls": [{"index": 0, "arguments_chunk": arguments_chunks[1]}]},
            {"tool_calls": [{"index": 0, "arguments_chunk": arguments_chunks[2]}]},
            {"finish_reason": "tool_calls"}
        ]

        failed_tool_result = ToolResult(
            tool_id="MockPythonTool", execution_id="exec_fail_stream", status="failed",
            error="Simulated tool execution error", start_time=0, end_time=1
        )
        self.mock_tool_orchestrator.execute_tool = AsyncMock(return_value=failed_tool_result)

        response_generator = self.processor.process_streaming_response(
            mock_llm_stream_chunks(llm_chunks_data), self.thread_id, self.prompt_messages, self.llm_model, config
        )
        results = await self.consume_async_generator(response_generator)

        self.mock_tool_orchestrator.execute_tool.assert_called_once()
        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_started' for r in results))

        failed_status_msg = next((r for r in results if r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_failed'), None)
        self.assertIsNotNone(failed_status_msg)
        self.assertIn("Simulated tool execution error", json.loads(failed_status_msg['content'])['message'])

        tool_result_msg = next((r for r in results if r.get('type') == 'tool'), None)
        self.assertIsNotNone(tool_result_msg)
        self.assertIn("Simulated tool execution error", json.loads(tool_result_msg['content'])['content'])


    async def test_tool_execution_exception_non_streaming(self):
        config = ProcessorConfig(native_tool_calling=True, xml_tool_calling=False, execute_tools=True)
        tool_call_id = "call_exception_nonstream"
        function_name = "MockPythonTool__execute_python_code"
        arguments_json_string = '{"code": "trigger_exception_in_orchestrator"}'
        llm_response_mock = mock_llm_non_stream_response(
            tool_calls_data=[{"id": tool_call_id, "function_name": function_name, "arguments_json_string": arguments_json_string}],
            finish_reason="tool_calls"
        )

        # Simulate execute_tool itself raising an exception
        self.mock_tool_orchestrator.execute_tool = AsyncMock(side_effect=RuntimeError("Orchestrator crashed"))

        response_generator = self.processor.process_non_streaming_response(
            llm_response_mock, self.thread_id, self.prompt_messages, self.llm_model, config
        )
        results = await self.consume_async_generator(response_generator)

        self.mock_tool_orchestrator.execute_tool.assert_called_once()

        # Check for tool_started (should still be yielded before crash)
        self.assertTrue(any(r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_started' for r in results))

        # Check for tool_failed status
        failed_status_msg = next((r for r in results if r.get('type') == 'status' and json.loads(r['content']).get('status_type') == 'tool_failed'), None)
        self.assertIsNotNone(failed_status_msg)
        self.assertIn("Orchestrator crashed", json.loads(failed_status_msg['content'])['message'])

        # Check for the tool result message (which should also indicate failure)
        tool_result_msg = next((r for r in results if r.get('type') == 'tool'), None)
        self.assertIsNotNone(tool_result_msg)
        self.assertIn("Orchestrator crashed", json.loads(tool_result_msg['content'])['content'])


if __name__ == '__main__':
    unittest.main()

# --- New tests for Plan Detection and Execution ---

@pytest.mark.asyncio
async def test_plan_detection_and_execution(mocker):
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    # Configure add_message_callback to return a dict suitable for format_for_yield
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        # Ensure content and metadata are json strings if they are dicts/lists
        processed_content = content if isinstance(content, str) else to_json_string(content)
        processed_metadata = metadata if isinstance(metadata, str) else to_json_string(metadata or {})
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": processed_content, "metadata": processed_metadata, "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    mock_plan_executor = MagicMock(spec=PlanExecutor)
    # Mock execute_json_plan to be an async generator
    async def mock_execute_plan_gen(*args, **kwargs):
        # Simulate plan executor yielding some status messages or results
        yield format_for_yield(await mock_add_message_side_effect("thread1", "status", {"status_type": "plan_tool_started"}, False, {"thread_run_id": "plan_run_id"}))
        yield format_for_yield(await mock_add_message_side_effect("thread1", "status", {"status_type": "plan_tool_completed"}, False, {"thread_run_id": "plan_run_id"}))
    mock_plan_executor.execute_json_plan = mock_execute_plan_gen # Assign the async generator function

    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=mock_plan_executor,
        trace=None
    )

    plan_dict = {"plan": [{"tool_name": "TestTool__action", "parameters": {"param": "value"}}]}
    plan_json_str = json.dumps(plan_dict) # Use json.dumps for proper JSON string

    # Input stream: plan arrives in two chunks
    input_chunks = [plan_json_str[:15], plan_json_str[15:]]
    llm_response_stream = _stream_input_chunks(input_chunks)

    results = []
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", ProcessorConfig()):
        results.append(result)

    # Assert execute_json_plan was called
    # Since it's an async generator, checking if it was called requires a bit more nuance if it's directly assigned
    # For a MagicMock wrapping an async generator, call_args might not be populated in the same way.
    # Instead, we can check if the mock object itself was called if it's a callable mock.
    # If execute_json_plan is a direct method on a MagicMock instance:
    assert mock_plan_executor.execute_json_plan.call_count == 1

    called_args = mock_plan_executor.execute_json_plan.call_args
    assert called_args is not None
    called_plan_data = called_args[0][0]
    assert called_plan_data == plan_dict

    # Assert that "plan_execution_start" and "plan_execution_end" statuses were yielded by ResponseProcessor
    status_contents = [json.loads(r['content']) for r in results if r['type'] == 'status']
    assert any(s.get("status_type") == "plan_execution_start" for s in status_contents)
    assert any(s.get("status_type") == "plan_execution_end" for s in status_contents)

    # Assert that the plan executor's yielded items are in the results
    assert any(json.loads(r['content']).get("status_type") == "plan_tool_started" for r in results)
    assert any(json.loads(r['content']).get("status_type") == "plan_tool_completed" for r in results)

    # Assert that the original plan JSON string was NOT yielded directly as assistant content
    assistant_content_messages = [json.loads(r['content']).get('content') for r in results if r['type'] == 'assistant' and json.loads(r['content']).get('role') == 'assistant']
    assert plan_json_str not in "".join(str(acm) for acm in assistant_content_messages)
    # Also check that no part of the plan string is in assistant messages
    assert plan_json_str[:15] not in "".join(str(acm) for acm in assistant_content_messages)
    assert plan_json_str[15:] not in "".join(str(acm) for acm in assistant_content_messages)


@pytest.mark.asyncio
async def test_plan_buffering_and_execution(mocker):
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": to_json_string(content), "metadata": to_json_string(metadata or {}), "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    mock_plan_executor = MagicMock(spec=PlanExecutor)
    async def mock_execute_plan_gen_buffer(*args, **kwargs):
        yield format_for_yield(await mock_add_message_side_effect("thread1", "status", {"status_type": "buffered_plan_executed"}, False, {"thread_run_id": "plan_run_id"}))
        # Must be an empty list or generator, not None
    mock_plan_executor.execute_json_plan = mock_execute_plan_gen_buffer

    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=mock_plan_executor,
        trace=None
    )

    plan_dict = {"plan": [{"tool_name": "BufferedTool__action", "parameters": {"p1": "v1"}}]}
    plan_json_str = json.dumps(plan_dict)

    # Simulate plan arriving in multiple small chunks
    chunks = [plan_json_str[i:i+5] for i in range(0, len(plan_json_str), 5)]
    llm_response_stream = _stream_input_chunks(chunks)

    results = []
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", ProcessorConfig()):
        results.append(result)

    assert mock_plan_executor.execute_json_plan.call_count == 1
    called_plan_data = mock_plan_executor.execute_json_plan.call_args[0][0]
    assert called_plan_data == plan_dict

    status_contents = [json.loads(r['content']) for r in results if r['type'] == 'status']
    assert any(s.get("status_type") == "plan_execution_start" for s in status_contents)
    assert any(s.get("status_type") == "plan_execution_end" for s in status_contents)
    assert any(s.get("status_type") == "buffered_plan_executed" for s in status_contents)

    assistant_content_messages = [json.loads(r['content']).get('content') for r in results if r['type'] == 'assistant' and json.loads(r['content']).get('role') == 'assistant']
    assert not any(c in "".join(str(acm) for acm in assistant_content_messages) for c in chunks), "Plan chunks should not appear in assistant text output"


@pytest.mark.asyncio
async def test_non_plan_message_processing(mocker):
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": to_json_string(content), "metadata": to_json_string(metadata or {}), "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    mock_plan_executor = MagicMock(spec=PlanExecutor)
    mock_plan_executor.execute_json_plan = AsyncMock(return_value=[]) # Should not be called

    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=mock_plan_executor,
        trace=None
    )

    text_chunks = ["This is ", "a regular ", "text message."]
    full_text = "".join(text_chunks)
    llm_response_stream = _stream_input_chunks(text_chunks)

    results = []
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", ProcessorConfig()):
        results.append(result)

    mock_plan_executor.execute_json_plan.assert_not_called()

    # Check for assistant message chunks
    text_chunk_messages = [r for r in results if r['type'] == 'assistant' and json.loads(r['metadata']).get('stream_status') == 'chunk']
    assert len(text_chunk_messages) == len(text_chunks)
    for i, chunk_msg in enumerate(text_chunk_messages):
        assert json.loads(chunk_msg['content']).get('content') == text_chunks[i]

    # Check for final assistant message
    final_assistant_message = next((r for r in results if r['type'] == 'assistant' and json.loads(r['metadata']).get('stream_status') == 'complete'), None)
    assert final_assistant_message is not None
    assert json.loads(final_assistant_message['content']).get('content') == full_text


@pytest.mark.asyncio
async def test_mixed_content_then_plan(mocker):
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": to_json_string(content), "metadata": to_json_string(metadata or {}), "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    mock_plan_executor = MagicMock(spec=PlanExecutor)
    async def mock_execute_mixed_plan_gen(*args, **kwargs):
        yield format_for_yield(await mock_add_message_side_effect("thread1", "status", {"status_type": "mixed_plan_tool_started"}, False, {"thread_run_id": "plan_run_id"}))
    mock_plan_executor.execute_json_plan = mock_execute_mixed_plan_gen

    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=mock_plan_executor,
        trace=None
    )

    initial_text = "Regular text. "
    plan_dict = {"plan": [{"tool_name": "MixedTool__action"}]}
    plan_json_str = json.dumps(plan_dict)

    # Stream: initial text, then plan marker, then rest of plan
    # The plan marker itself will be part of the plan buffer.
    input_stream_chunks_content = [
        initial_text,
        '{"plan": [{"tool_name": "MixedTool__action"}]}' # Entire plan in one chunk after initial text
    ]
    llm_response_stream = _stream_input_chunks(input_stream_chunks_content)

    results = []
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", ProcessorConfig()):
        results.append(result)

    # Assert plan execution
    assert mock_plan_executor.execute_json_plan.call_count == 1
    called_plan_data = mock_plan_executor.execute_json_plan.call_args[0][0]
    # The plan_marker containing chunk is also part of the buffer, so plan_data will be just the plan.
    assert called_plan_data == plan_dict

    # Assert initial text was yielded
    initial_text_chunk = next((r for r in results if r['type'] == 'assistant' and json.loads(r['metadata']).get('stream_status') == 'chunk' and json.loads(r['content']).get('content') == initial_text), None)
    assert initial_text_chunk is not None

    # Assert plan status messages
    status_contents = [json.loads(r['content']) for r in results if r['type'] == 'status']
    assert any(s.get("status_type") == "plan_execution_start" for s in status_contents)
    assert any(s.get("status_type") == "plan_execution_end" for s in status_contents)
    assert any(s.get("status_type") == "mixed_plan_tool_started" for s in status_contents)

    # Assert plan content (e.g., '{"plan": ...') was NOT part of any final assistant message
    # The initial_text should be in the final assistant message if no other text followed the plan.
    final_assistant_message = next((r for r in results if r['type'] == 'assistant' and json.loads(r['metadata']).get('stream_status') == 'complete'), None)
    assert final_assistant_message is not None
    final_assistant_content = json.loads(final_assistant_message['content']).get('content')
    assert initial_text.strip() in final_assistant_content # Initial text should be there
    assert plan_json_str not in final_assistant_content # Plan JSON should not


@pytest.mark.asyncio
async def test_plan_message_with_native_tool_calls_in_plan_content(mocker):
    """
    Tests that if a plan's content (actions) *looks* like native tool calls,
    it's still treated as plan data and not separately processed by ResponseProcessor's
    native tool call logic.
    """
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": to_json_string(content), "metadata": to_json_string(metadata or {}), "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    mock_plan_executor = MagicMock(spec=PlanExecutor)
    async def mock_execute_plan_gen_native_like(*args, **kwargs):
        # Plan executor should receive the plan_data as is
        yield format_for_yield(await mock_add_message_side_effect("thread1", "status", {"status_type": "plan_with_native_like_actions_executed"}, False, {"thread_run_id": "plan_run_id"}))
    mock_plan_executor.execute_json_plan = mock_execute_plan_gen_native_like

    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=mock_plan_executor,
        trace=None
    )

    # Plan data where actions resemble native tool calls
    plan_dict = {
        "plan": [
            {"id": "tc1", "tool_name": "SomeTool__tool_method", "type": "function", "function": {"name": "SomeTool__tool_method", "arguments": '{"arg1": "val1"}'}},
            {"id": "tc2", "tool_name": "AnotherTool__other_method", "type": "function", "function": {"name": "AnotherTool__other_method", "arguments": '{"p": "q"}'}}
        ]
    }
    plan_json_str = json.dumps(plan_dict)

    # Simulate stream with this plan data
    llm_response_stream = _stream_input_chunks([plan_json_str])

    results = []
    # Use a config that *would* enable native tool calling, to ensure it's bypassed for plan content
    config = ProcessorConfig(native_tool_calling=True, xml_tool_calling=False)
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", config):
        results.append(result)

    # Assert PlanExecutor was called with the correct plan data
    assert mock_plan_executor.execute_json_plan.call_count == 1
    called_plan_data = mock_plan_executor.execute_json_plan.call_args[0][0]
    assert called_plan_data == plan_dict

    # Assert no direct native tool call processing by ResponseProcessor itself
    # This means mock_tool_orchestrator.execute_tool should NOT have been called directly by ResponseProcessor
    # (it would be called by PlanExecutor, but we are testing ResponseProcessor's behavior here)
    # This is implicitly tested by ensuring execute_json_plan was called.
    # If ResponseProcessor tried to parse native tools from the plan string, it might call orchestrator or buffer differently.

    status_contents = [json.loads(r['content']) for r in results if r['type'] == 'status']
    assert any(s.get("status_type") == "plan_with_native_like_actions_executed" for s in status_contents)


@pytest.mark.asyncio
async def test_plan_execution_yields_results(mocker):
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": to_json_string(content), "metadata": to_json_string(metadata or {}), "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    mock_plan_executor = MagicMock(spec=PlanExecutor)

    # Mock PlanExecutor to yield specific items
    plan_yield_item1 = {"type": "status", "content": to_json_string({"status_type": "plan_step_1"}), "metadata": to_json_string({"thread_run_id": "plan_run_id"})}
    plan_yield_item2 = {"type": "tool", "content": to_json_string({"tool_name": "tool_in_plan"}), "metadata": to_json_string({"thread_run_id": "plan_run_id"})}

    async def mock_yielding_plan_executor(*args, **kwargs):
        yield plan_yield_item1 # Already formatted for yield
        yield plan_yield_item2
    mock_plan_executor.execute_json_plan = mock_yielding_plan_executor

    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=mock_plan_executor,
        trace=None
    )

    plan_dict = {"plan": [{"tool_name": "YieldTestTool__action"}]}
    plan_json_str = json.dumps(plan_dict)
    llm_response_stream = _stream_input_chunks([plan_json_str])

    results = []
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", ProcessorConfig()):
        results.append(result)

    # Check that items yielded by plan_executor are in the results from ResponseProcessor
    # Note: ResponseProcessor also yields plan_execution_start/end.
    assert plan_yield_item1 in results
    assert plan_yield_item2 in results


@pytest.mark.asyncio
async def test_malformed_plan_json_incomplete(mocker):
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": to_json_string(content), "metadata": to_json_string(metadata or {}), "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    mock_plan_executor = MagicMock(spec=PlanExecutor)
    mock_plan_executor.execute_json_plan = AsyncMock() # Should not be called

    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=mock_plan_executor,
        trace=None
    )

    # Incomplete JSON: missing closing brackets and brace
    incomplete_plan_str = '{"plan": [{"tool_name": "IncompleteTool__action", "parameters": {"param1": "val1"}'
    # This is not a valid JSON on its own, but ResponseProcessor buffers until is_complete_json is true
    # or the stream ends. If the stream ends with this, it's still incomplete.

    llm_response_stream = _stream_input_chunks([incomplete_plan_str, None]) # Stream ends after this chunk

    results = []
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", ProcessorConfig()):
        results.append(result)

    mock_plan_executor.execute_json_plan.assert_not_called()

    # The processor should not hang. It should process the incomplete JSON as simple text
    # because is_complete_json will keep returning false, and eventually the stream ends.
    # The buffered content (incomplete_plan_str) will be treated as regular assistant content.
    final_assistant_message = next((r for r in results if r['type'] == 'assistant' and json.loads(r['metadata']).get('stream_status') == 'complete'), None)
    assert final_assistant_message is not None
    final_content_dict = json.loads(final_assistant_message['content'])
    assert final_content_dict.get('content') == incomplete_plan_str

    # No specific error status for "malformed plan JSON that never completed" is defined to be yielded.
    # It just falls through to text processing if never completed and stream ends.
    status_messages = [r for r in results if r['type'] == 'status' and json.loads(r['content']).get('status_type') == 'error']
    assert not status_messages # No error status message should be yielded for this case.


@pytest.mark.asyncio
async def test_invalid_plan_structure_after_json_parse(mocker):
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": to_json_string(content), "metadata": to_json_string(metadata or {}), "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    mock_plan_executor = MagicMock(spec=PlanExecutor)
    mock_plan_executor.execute_json_plan = AsyncMock() # Should not be called for invalid structure

    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=mock_plan_executor,
        trace=None
    )

    # Valid JSON, but not a valid plan structure (e.g. "plan" value is not a list)
    # The plan marker is present, so it will initially be treated as a plan.
    invalid_plan_structure_str = '{"plan": "this should be a list of actions"}'
    llm_response_stream = _stream_input_chunks([invalid_plan_structure_str])

    results = []
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", ProcessorConfig()):
        results.append(result)

    mock_plan_executor.execute_json_plan.assert_not_called()

    # According to ResponseProcessor logic, if JSON is valid but structure is not a plan,
    # it logs a warning, resets is_plan, and processed_as_plan_chunk becomes False.
    # The original chunk content then falls through to regular processing.
    final_assistant_message = next((r for r in results if r['type'] == 'assistant' and json.loads(r['metadata']).get('stream_status') == 'complete'), None)
    assert final_assistant_message is not None
    final_content_dict = json.loads(final_assistant_message['content'])
    assert final_content_dict.get('content') == invalid_plan_structure_str

    # No error status message should be yielded by ResponseProcessor itself for this case,
    # as it falls through to text. PlanExecutor might yield errors if it were called.
    error_status_messages = [r for r in results if r['type'] == 'status' and json.loads(r['content']).get('status_type') == 'error']
    assert not error_status_messages


@pytest.mark.asyncio
async def test_plan_executor_not_available(mocker):
    mock_tool_orchestrator = MagicMock(spec=ToolOrchestrator)
    mock_add_message_callback = AsyncMock()
    async def mock_add_message_side_effect(thread_id, type, content, is_llm_message, metadata=None, message_id=None):
        return {"message_id": message_id or f"mock_msg_{uuid.uuid4()}", "type": type, "content": to_json_string(content), "metadata": to_json_string(metadata or {}), "is_llm_message": is_llm_message, "created_at": "ts", "updated_at": "ts"}
    mock_add_message_callback.side_effect = mock_add_message_side_effect

    # PlanExecutor is None
    processor = ResponseProcessor(
        tool_orchestrator=mock_tool_orchestrator,
        add_message_callback=mock_add_message_callback,
        plan_executor=None, # Explicitly None
        trace=None
    )

    plan_dict = {"plan": [{"tool_name": "SomeTool__action"}]}
    plan_json_str = json.dumps(plan_dict)
    llm_response_stream = _stream_input_chunks([plan_json_str])

    results = []
    async for result in processor.process_streaming_response(llm_response_stream, "thread1", [], "gpt-4", ProcessorConfig()):
        results.append(result)

    # Assert an error status message is yielded
    error_message = next((r for r in results if r['type'] == 'status' and json.loads(r['content']).get('status_type') == 'error'), None)
    assert error_message is not None
    error_content = json.loads(error_message['content'])
    assert "PlanExecutor not available" in error_content.get("message", "")

    # Assert that the plan content itself is not processed as regular assistant message
    assistant_messages = [r for r in results if r['type'] == 'assistant']
    assert not assistant_messages
