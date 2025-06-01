import unittest
from unittest.mock import MagicMock, AsyncMock
import asyncio

from agentpress.tool_orchestrator import ToolOrchestrator
from agentpress.tool import Tool, ToolResult, openapi_schema, xml_schema, ToolSchema, XMLTagSchema, SchemaType

# --- Mock Tools ---

class MockPythonTool(Tool):
    PLUGIN_TOOL_ID = "MockPythonTool"

    @openapi_schema({
        "name": "execute_python_code",
        "description": "Executes Python code.",
        "parameters": {
            "type": "object",
            "properties": {"code": {"type": "string"}},
            "required": ["code"],
        },
    })
    @xml_schema(
        tag_name="execute_python_code",
        mappings=[{"param_name": "code", "node_type": "attribute"}],
        example="<execute_python_code code='print(1)'/>"
    )
    async def execute_python_code(self, code: str):
        if code == "raise_exception":
            raise ValueError("Test Exception")
        return {"output": f"executed: {code}"}

class MockWebSearchTool(Tool):
    PLUGIN_TOOL_ID = "MockWebSearchTool"

    @openapi_schema({
        "name": "web_search",
        "description": "Performs a web search.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    })
    @xml_schema(
        tag_name="web_search",
        mappings=[
            {"param_name": "query", "node_type": "element", "path": "query"}
        ],
        example="<web_search><query>test</query></web_search>"
    )
    async def web_search(self, query: str):
        return {"results": f"results for {query}"}

class TestToolOrchestrator(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.orchestrator = ToolOrchestrator()
        self.mock_python_tool = MockPythonTool()
        self.mock_web_search_tool = MockWebSearchTool()

        # Mock the actual methods to spy on them if needed,
        # but the tool methods themselves will be called by the orchestrator.
        # We are testing if the orchestrator correctly calls them and handles results.
        self.mock_python_tool.execute_python_code = AsyncMock(wraps=self.mock_python_tool.execute_python_code)
        self.mock_web_search_tool.web_search = AsyncMock(wraps=self.mock_web_search_tool.web_search)

        self.orchestrator.register_tool(self.mock_python_tool)
        self.orchestrator.register_tool(self.mock_web_search_tool)

    async def test_execute_tool_openapi_success(self):
        tool_id = "MockPythonTool"
        method_name = "execute_python_code"
        params = {"code": "print('hello')"}

        result = await self.orchestrator.execute_tool(tool_id, method_name, params)

        self.mock_python_tool.execute_python_code.assert_called_once_with(code="print('hello')")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_id, tool_id)
        self.assertIsNotNone(result.execution_id)
        self.assertEqual(result.result, {"output": "executed: print('hello')"})
        self.assertIsNone(result.error)

    async def test_execute_tool_openapi_exception(self):
        tool_id = "MockPythonTool"
        method_name = "execute_python_code"
        params = {"code": "raise_exception"}

        result = await self.orchestrator.execute_tool(tool_id, method_name, params)

        self.mock_python_tool.execute_python_code.assert_called_once_with(code="raise_exception")
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.tool_id, tool_id)
        self.assertIsNotNone(result.execution_id)
        self.assertIsNone(result.result)
        self.assertIn("Test Exception", result.error)

    async def test_execute_tool_xml_success(self):
        tool_id = "MockWebSearchTool"
        method_name = "web_search" # This should be the Python method name
        params = {"query": "test query"}

        result = await self.orchestrator.execute_tool(tool_id, method_name, params)

        self.mock_web_search_tool.web_search.assert_called_once_with(query="test query")
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.tool_id, tool_id)
        self.assertEqual(result.result, {"results": "results for test query"})

    async def test_get_tool_schemas_for_llm(self):
        schemas = self.orchestrator.get_tool_schemas_for_llm()

        self.assertEqual(len(schemas), 2) # One method for each tool

        python_schema_found = False
        web_search_schema_found = False

        for schema in schemas:
            if schema['name'] == "MockPythonTool__execute_python_code":
                python_schema_found = True
                self.assertEqual(schema['description'], "Executes Python code.")
                self.assertIn("code", schema['parameters']['properties'])
            elif schema['name'] == "MockWebSearchTool__web_search":
                web_search_schema_found = True
                self.assertEqual(schema['description'], "Performs a web search.")
                self.assertIn("query", schema['parameters']['properties'])

        self.assertTrue(python_schema_found, "Python tool schema not found or name incorrect")
        self.assertTrue(web_search_schema_found, "Web search tool schema not found or name incorrect")

    async def test_get_xml_schemas_for_llm(self):
        xml_schemas_str = self.orchestrator.get_xml_schemas_for_llm()

        self.assertIn("<execute_python_code code='print(1)'/>", xml_schemas_str)
        self.assertIn("Tool Name: MockPythonTool", xml_schemas_str)
        self.assertIn("Method: execute_python_code", xml_schemas_str)
        self.assertIn("XML Tag: <execute_python_code>", xml_schemas_str)

        self.assertIn("<web_search><query>test</query></web_search>", xml_schemas_str)
        self.assertIn("Tool Name: MockWebSearchTool", xml_schemas_str)
        self.assertIn("Method: web_search", xml_schemas_str)
        self.assertIn("XML Tag: <web_search>", xml_schemas_str)

    async def test_execute_tool_not_found(self):
        result = await self.orchestrator.execute_tool("NonExistentTool", "some_method", {})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.tool_id, "NonExistentTool")
        self.assertIn("Tool with ID 'NonExistentTool' not found", result.error)

    async def test_execute_method_not_found(self):
        result = await self.orchestrator.execute_tool("MockPythonTool", "non_existent_method", {})
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.tool_id, "MockPythonTool")
        self.assertIn("Method 'non_existent_method' not found on tool 'MockPythonTool'", result.error)

if __name__ == '__main__':
    unittest.main()
