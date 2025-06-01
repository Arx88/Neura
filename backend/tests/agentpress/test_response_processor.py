import unittest
from unittest.mock import MagicMock, AsyncMock, patch
import json
import asyncio
from typing import List, Dict, Any, AsyncGenerator

from agentpress.response_processor import ResponseProcessor, ProcessorConfig, ToolExecutionContext
from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.tool import ToolResult
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

        self.processor = ResponseProcessor(
            tool_orchestrator=self.mock_tool_orchestrator,
            add_message_callback=self.mock_add_message_callback
        )
        self.thread_id = "test_thread_123"
        self.prompt_messages = [{"role": "user", "content": "Hello"}]
        self.llm_model = "test_model"

        # Mocking the ToolOrchestrator's schema methods for XML parsing
        # This is a simplified mock. In a real scenario, you might need more detailed schema objects.
        self.mock_tool_orchestrator.tools = {
            "MockPythonTool": MagicMock(spec=Tool), # Add spec for type hinting if using strict type checkers
            "MockWebSearchTool": MagicMock(spec=Tool)
        }

        # Setup get_schemas for MockPythonTool
        python_tool_schemas_mock = {
            "execute_python_code": [
                ToolSchema(
                    schema_type=SchemaType.XML,
                    xml_schema=XMLTagSchema(
                        tag_name="execute_python_code",
                        mappings=[{"param_name": "code", "node_type": "attribute"}],
                        example="<execute_python_code code='print(1)'/>"
                    )
                )
            ]
        }
        self.mock_tool_orchestrator.tools["MockPythonTool"].get_schemas.return_value = python_tool_schemas_mock

        # Setup get_schemas for MockWebSearchTool (if needed for XML tests)
        web_tool_schemas_mock = {
            "web_search": [
                ToolSchema(
                    schema_type=SchemaType.XML,
                    xml_schema=XMLTagSchema(
                        tag_name="web_search",
                        mappings=[{"param_name": "query", "node_type": "element", "path":"query"}],
                        example="<web_search><query>test</query></web_search>"
                    )
                )
            ]
        }
        self.mock_tool_orchestrator.tools["MockWebSearchTool"].get_schemas.return_value = web_tool_schemas_mock


    async def consume_async_generator(self, gen: AsyncGenerator):
        items = []
        async for item in gen:
            items.append(item)
        return items

    # --- Native Tool Call Tests ---

    async def test_process_streaming_native_tool_call_success(self):
        config = ProcessorConfig(native_tool_calling=True, xml_tool_calling=False, execute_tools=True, execute_on_stream=True)

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
