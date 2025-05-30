import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import json
from uuid import uuid4

from agent.tools.visualization_tool import DataVisualizationTool
from sandbox.sandbox import SessionExecuteRequest
from agentpress.tool import ToolResult

class TestDataVisualizationTool(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        self.project_id = "test_project_id"
        self.thread_manager_mock = AsyncMock()
        self.tool = DataVisualizationTool(project_id=self.project_id, thread_manager=self.thread_manager_mock)
        
        # Mock sandbox and its process
        self.sandbox_mock = AsyncMock()
        self.sandbox_mock.process = AsyncMock()
        self.tool._sandbox = self.sandbox_mock # Inject mock sandbox

        # Common responses for sandbox process
        self.sandbox_mock.process.create_session = AsyncMock()
        self.sandbox_mock.process.delete_session = AsyncMock()
        
        self.execute_command_success_mock = AsyncMock(return_value=MagicMock(exit_code=0, cmd_id="cmd123"))
        self.execute_command_failure_mock = AsyncMock(return_value=MagicMock(exit_code=1, cmd_id="cmd456"))
        self.get_logs_mock = AsyncMock(return_value="Log output")

        self.tool._ensure_sandbox = AsyncMock() # Mock this helper

    async def test_create_bar_chart_success(self):
        self.sandbox_mock.process.execute_session_command = self.execute_command_success_mock
        self.sandbox_mock.process.get_session_command_logs = self.get_logs_mock

        title = "Test Chart"
        categories = ["A", "B", "C"]
        values = [10, 20, 30]
        output_file = "test_chart"
        
        result = await self.tool.create_bar_chart(title, categories, values, output_file, "X-Axis", "Y-Axis")

        self.assertTrue(result.is_success)
        self.assertIn("output_file", result.data)
        self.assertEqual(result.data["output_file"], f"/workspace/visualizations/{output_file}.png")
        
        # Check script content passed to execute_session_command for writing script
        # First call to execute_session_command is mkdir, second is writing script
        write_script_call_args = self.sandbox_mock.process.execute_session_command.call_args_list[1].kwargs['req']
        self.assertIsInstance(write_script_call_args, SessionExecuteRequest)
        self.assertIn(f"plt.title(\"{title}\")", write_script_call_args.command)
        self.assertIn(f"categories = {categories}", write_script_call_args.command)
        self.assertIn(f"values = {values}", write_script_call_args.command)
        self.assertIn(f"plt.savefig(\"/workspace/visualizations/{output_file}.png\")", write_script_call_args.command)

        # Check python execution call
        python_exec_call_args = self.sandbox_mock.process.execute_session_command.call_args_list[2].kwargs['req']
        self.assertIsInstance(python_exec_call_args, SessionExecuteRequest)
        self.assertTrue(python_exec_call_args.command.startswith("python /workspace/temp_viz_script_"))

        self.sandbox_mock.process.delete_session.assert_called_once()

    async def test_create_bar_chart_script_failure(self):
        # Simulate mkdir success, then script write success, then script exec failure
        self.sandbox_mock.process.execute_session_command = AsyncMock(
            side_effect=[
                MagicMock(exit_code=0, cmd_id="cmd_mkdir"), # mkdir
                MagicMock(exit_code=0, cmd_id="cmd_write"), # write script
                MagicMock(exit_code=1, cmd_id="cmd_exec"),  # execute script fails
                MagicMock(exit_code=0, cmd_id="cmd_rm") # remove script
            ]
        )
        self.sandbox_mock.process.get_session_command_logs = self.get_logs_mock
        
        result = await self.tool.create_bar_chart("Fail Chart", ["X"], [1], "fail_chart")
        
        self.assertFalse(result.is_success)
        self.assertIn("Failed to create bar chart", result.error_message)
        self.sandbox_mock.process.delete_session.assert_called_once()

    async def test_view_visualization_success(self):
        self.sandbox_mock.process.execute_session_command = self.execute_command_success_mock
        self.sandbox_mock.process.get_session_command_logs = AsyncMock(return_value="exists") # Simulate file exists
        
        image_path = "my_viz.png"
        expected_path = f"/workspace/{image_path}"
        
        result = await self.tool.view_visualization(image_path)
        
        self.assertTrue(result.is_success)
        self.assertEqual(result.data["image_path"], expected_path)
        
        # Check that test -f command was called correctly
        exec_call_args = self.sandbox_mock.process.execute_session_command.call_args_list[0].kwargs['req']
        self.assertEqual(exec_call_args.command, f"test -f {expected_path} && echo 'exists' || echo 'not_exists'")
        self.sandbox_mock.process.delete_session.assert_called_once()

    async def test_view_visualization_file_not_found(self):
        self.sandbox_mock.process.execute_session_command = self.execute_command_success_mock # Command executes fine
        self.sandbox_mock.process.get_session_command_logs = AsyncMock(return_value="not_exists") # but log says not_exists
        
        image_path = "non_existent.png"
        result = await self.tool.view_visualization(image_path)
        
        self.assertFalse(result.is_success)
        self.assertIn("Visualization file not found", result.error_message)
        self.sandbox_mock.process.delete_session.assert_called_once()

    async def test_view_visualization_path_cleaning(self):
        self.sandbox_mock.process.execute_session_command = self.execute_command_success_mock
        self.sandbox_mock.process.get_session_command_logs = AsyncMock(return_value="exists")
        
        # Path that needs cleaning and relative path
        await self.tool.view_visualization("../outside_workspace/img.png")
        cleaned_path_call1 = self.sandbox_mock.process.execute_session_command.call_args_list[0].kwargs['req'].command
        # clean_path in tool_base.py resolves `../` relative to `/workspace`
        self.assertIn(f"test -f /workspace/outside_workspace/img.png", cleaned_path_call1)

        await self.tool.view_visualization("subdir/my_image.png")
        cleaned_path_call2 = self.sandbox_mock.process.execute_session_command.call_args_list[1].kwargs['req'].command
        self.assertIn(f"test -f /workspace/subdir/my_image.png", cleaned_path_call2)
        
    async def test_display_visualization_in_browser_self_contained_html_success(self):
        mock_base64_data = "SGVsbG8gV29ybGQh" # "Hello World!" base64 encoded
        
        # Side effect for execute_session_command:
        # 1. Call for `cat | base64` (reading image)
        # 2. Call for `mkdir && cat > html_file` (writing HTML)
        self.sandbox_mock.process.execute_session_command = AsyncMock(
            side_effect=[
                MagicMock(exit_code=0, cmd_id="cmd_read_img"), # read image
                MagicMock(exit_code=0, cmd_id="cmd_write_html") # write html
            ]
        )
        # Side effect for get_session_command_logs:
        # 1. Return base64 data for the image read
        self.sandbox_mock.process.get_session_command_logs = AsyncMock(return_value=mock_base64_data)

        image_path = "chart.png" # Assumed to be PNG by default for mime type
        result = await self.tool.display_visualization_in_browser(image_path)

        self.assertTrue(result.is_success, msg=f"Failed with: {result.error_message}")
        self.assertIn("html_content", result.data)
        self.assertIn("html_path", result.data)
        self.assertIn(f"data:image/png;base64,{mock_base64_data}", result.data["html_content"])
        self.assertTrue(result.data["html_path"].startswith("/workspace/visualizations/visualization_display_"))
        self.assertTrue(result.data["html_path"].endswith(".html"))

        # Check the command for reading and base64 encoding the image
        read_img_call_args = self.sandbox_mock.process.execute_session_command.call_args_list[0].kwargs['req']
        self.assertEqual(read_img_call_args.command, f"cat /workspace/{image_path} | base64 --wrap=0")

        # Check the command for writing the HTML content
        write_html_call_args = self.sandbox_mock.process.execute_session_command.call_args_list[1].kwargs['req']
        self.assertIn(f"cat > {result.data['html_path']} << 'EOL'", write_html_call_args.command)
        self.assertIn(mock_base64_data, write_html_call_args.command) # Check if base64 data is in the written script

        self.sandbox_mock.process.delete_session.assert_called_once()

    async def test_display_visualization_in_browser_image_read_failure(self):
        # Simulate failure when trying to read and base64 encode the image
        self.sandbox_mock.process.execute_session_command = AsyncMock(
             return_value=MagicMock(exit_code=1, cmd_id="cmd_read_fail") # read image fails
        )
        self.sandbox_mock.process.get_session_command_logs = AsyncMock(return_value="Error reading file")

        image_path = "non_existent_chart.png"
        result = await self.tool.display_visualization_in_browser(image_path)

        self.assertFalse(result.is_success)
        self.assertIn("Failed to read and encode image file", result.error_message)
        self.sandbox_mock.process.delete_session.assert_called_once()

    async def test_display_visualization_in_browser_html_write_failure(self):
        mock_base64_data = "SGVsbG8="
         # Side effect for execute_session_command:
        # 1. Call for `cat | base64` (reading image) - SUCCEEDS
        # 2. Call for `mkdir && cat > html_file` (writing HTML) - FAILS
        self.sandbox_mock.process.execute_session_command = AsyncMock(
            side_effect=[
                MagicMock(exit_code=0, cmd_id="cmd_read_img"), 
                MagicMock(exit_code=1, cmd_id="cmd_write_html_fail")
            ]
        )
        # Side effect for get_session_command_logs:
        # 1. Return base64 data for the image read
        # 2. Return error log for HTML write failure
        self.sandbox_mock.process.get_session_command_logs = AsyncMock(
            side_effect=[
                mock_base64_data, # Log for successful image read
                "Error writing HTML file" # Log for failed HTML write
            ]
        )
        
        image_path = "chart.png"
        result = await self.tool.display_visualization_in_browser(image_path)

        self.assertFalse(result.is_success)
        self.assertIn("Failed to write self-contained HTML for visualization", result.error_message)
        self.sandbox_mock.process.delete_session.assert_called_once()

    async def test_session_cleanup_on_exception_in_create_bar_chart(self):
        self.tool._ensure_sandbox = AsyncMock(side_effect=Exception("Initial connection failed"))
        
        with self.assertRaises(Exception): # Check if the original exception is raised
             await self.tool.create_bar_chart("Title", ["A"], [1], "file")
        
        # Even if _ensure_sandbox fails before session creation, 
        # the finally block in the tool attempts to delete if sandbox.process exists.
        # In this specific mock setup, create_session is NOT called if _ensure_sandbox fails early.
        # The delete_session would be called if the session was created and then an error occurred.
        # Let's refine this test to simulate failure *after* session creation.

        self.tool._ensure_sandbox = AsyncMock() # Reset to success
        self.sandbox_mock.process.create_session = AsyncMock() # Session created
        self.sandbox_mock.process.execute_session_command = AsyncMock(side_effect=Exception("Exec error")) # Error during exec

        with self.assertRaises(Exception):
            await self.tool.create_bar_chart("Title", ["A"], [1], "file")
        
        self.sandbox_mock.process.delete_session.assert_called_once()


if __name__ == '__main__':
    unittest.main()
