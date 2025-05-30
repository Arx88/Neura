import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime, timedelta, timezone as dt_timezone
import argparse
import sys
import os

# Adjust path for script import if necessary.
# This assumes the test runner is initiated from the project root.
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from backend.utils.scripts.delete_old_archived_sandboxes import main as delete_script_main
from daytona_api_client.models.workspace_state import WorkspaceState
from daytona_sdk.sandbox import SandboxInfo, Sandbox # For creating mock objects

# Mock logger before it's imported by the script
mock_logger = MagicMock()
# Patch the specific logger instance used in the script module
# This needs to target where 'logger' is *used*, not where it's defined, if they differ.
# Assuming 'backend.utils.logger.logger' is the path if the script does 'from backend.utils.logger import logger'
# Or if the script does 'from utils.logger import logger' and utils is in sys.path
# The script uses 'from backend.utils.logger import logger'
logger_patch_path = 'backend.utils.scripts.delete_old_archived_sandboxes.logger'

@patch(logger_patch_path, mock_logger) # Patch logger for all tests in this class
class TestDeleteOldArchivedSandboxes(unittest.IsolatedAsyncioTestCase):

    def setUp(self):
        # Reset mocks for each test to ensure isolation
        mock_logger.reset_mock()

        self.mock_daytona_client = AsyncMock()
        self.patch_daytona_client = patch('backend.utils.scripts.delete_old_archived_sandboxes.daytona', self.mock_daytona_client)
        self.patch_daytona_client.start() # Start patcher

        self.fixed_now = datetime(2023, 10, 31, 12, 0, 0, tzinfo=dt_timezone.utc)
        self.patch_datetime_now = patch('backend.utils.scripts.delete_old_archived_sandboxes.datetime')
        self.mock_datetime = self.patch_datetime_now.start()
        self.mock_datetime.now.return_value = self.fixed_now
        self.mock_datetime.fromisoformat.side_effect = datetime.fromisoformat # Use real fromisoformat
        self.mock_datetime.strptime.side_effect = datetime.strptime # Use real strptime


    def tearDown(self):
        self.patch_daytona_client.stop()
        self.patch_datetime_now.stop()
        mock_logger.reset_mock() # Ensure logger mocks are cleared

    async def run_script_with_args(self, args_list):
        """Helper to run the script's main function with mocked sys.argv."""
        with patch('sys.argv', ['delete_old_archived_sandboxes.py'] + args_list):
            await delete_script_main()

    def _create_mock_sandbox_info(self, id, name, state, updated_at_dt):
        si = SandboxInfo(id=id, name=name, project_name=name, state=state, updated_at=updated_at_dt, target="test-target")
        # Ensure updated_at is timezone-aware if it's a datetime object
        if isinstance(updated_at_dt, datetime) and updated_at_dt.tzinfo is None:
            si.updated_at = updated_at_dt.replace(tzinfo=dt_timezone.utc)
        return si

    # 1. Argument Parsing Tests (implicitly tested by running with args)
    # We can also test argparse directly if needed, but running the script is more integrated.

    async def test_arg_parsing_defaults(self):
        self.mock_daytona_client.list_all_sandboxes.return_value = []
        await self.run_script_with_args([]) # No args, should use defaults
        # Check if logger was called with messages indicating default days (e.g. in eligibility)
        # This is an indirect check; direct argparse testing is also an option.
        # For now, we assume argparse works and focus on script logic.
        # The script logs the 'days_archived' value in the summary, which implies parsing.
        # We'll check for this in logging tests.

    # 2. Sandbox Fetching and Filtering
    async def test_no_sandboxes_found(self):
        self.mock_daytona_client.list_all_sandboxes.return_value = []
        await self.run_script_with_args(['--days-archived', '7'])
        mock_logger.info.assert_any_call("No sandboxes found. Exiting.")

    async def test_sandboxes_various_states_and_ages(self):
        days_archived_threshold = 7
        sandboxes_data = [
            self._create_mock_sandbox_info("s1", "running_sb", WorkspaceState.RUNNING, self.fixed_now - timedelta(days=10)),
            self._create_mock_sandbox_info("s2", "arch_new_sb", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=3)), # Archived, too new
            self._create_mock_sandbox_info("s3", "arch_old_sb", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=10)), # Archived, old enough
            self._create_mock_sandbox_info("s4", "stopped_sb", WorkspaceState.STOPPED, self.fixed_now - timedelta(days=10)),
            self._create_mock_sandbox_info("s5", "arch_exact_age_sb", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=days_archived_threshold)), # Archived, exactly old enough
        ]
        self.mock_daytona_client.list_all_sandboxes.return_value = sandboxes_data
        
        # Mock get_current_sandbox and delete for the deletable ones
        mock_s3_full = MagicMock(spec=Sandbox)
        mock_s5_full = MagicMock(spec=Sandbox)
        self.mock_daytona_client.get_current_sandbox.side_effect = [mock_s3_full, mock_s5_full]
        self.mock_daytona_client.delete.return_value = None # Simulate successful deletion

        with patch('builtins.input', return_value='y'): # Auto-confirm 'y' for interactive
            await self.run_script_with_args(['--days-archived', str(days_archived_threshold)])
        
        # Verify filtering
        mock_logger.info.assert_any_call(f"Sandbox 's3' (Project: 'arch_old_sb') is eligible for deletion (archived for 10 days).")
        mock_logger.info.assert_any_call(f"Sandbox 's5' (Project: 'arch_exact_age_sb') is eligible for deletion (archived for {days_archived_threshold} days).")
        
        mock_logger.debug.assert_any_call("Sandbox 's1' (Project: 'running_sb') is not archived (state: RUNNING). Skipping.")
        mock_logger.debug.assert_any_call("Sandbox 's2' (Project: 'arch_new_sb') not old enough for deletion (archived for 3 days).")

        # Verify deletion calls
        self.assertEqual(self.mock_daytona_client.delete.call_count, 2)
        self.mock_daytona_client.delete.assert_any_call(mock_s3_full)
        self.mock_daytona_client.delete.assert_any_call(mock_s5_full)
        
        # Check summary
        mock_logger.info.assert_any_call(f"Total sandboxes checked: {len(sandboxes_data)}")
        mock_logger.info.assert_any_call(f"Total archived sandboxes: 3") # s2, s3, s5
        mock_logger.info.assert_any_call(f"Sandboxes eligible for deletion (>= {days_archived_threshold} days archived): 2") # s3, s5
        mock_logger.info.assert_any_call(f"Sandboxes successfully deleted: 2")


    # 3. Deletion Logic
    async def test_dry_run_mode(self):
        days_archived_threshold = 5
        sandboxes_data = [
            self._create_mock_sandbox_info("s_dry1", "arch_dry_old", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=10)),
        ]
        self.mock_daytona_client.list_all_sandboxes.return_value = sandboxes_data

        await self.run_script_with_args(['--days-archived', str(days_archived_threshold), '--dry-run'])

        mock_logger.info.assert_any_call("DRY RUN mode enabled. No actual deletions will occur.")
        mock_logger.info.assert_any_call("DRY RUN: Would delete sandbox 's_dry1' (Project: 'arch_dry_old').")
        self.mock_daytona_client.delete.assert_not_called()
        # Check summary for dry run
        mock_logger.info.assert_any_call(f"Sandboxes that would be deleted (DRY RUN): 1")


    async def test_live_run_interactive_confirm_yes(self):
        days_archived_threshold = 5
        s_live_yes = self._create_mock_sandbox_info("s_live_yes", "arch_live_yes", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=10))
        self.mock_daytona_client.list_all_sandboxes.return_value = [s_live_yes]
        
        mock_s_live_yes_full = MagicMock(spec=Sandbox)
        self.mock_daytona_client.get_current_sandbox.return_value = mock_s_live_yes_full
        self.mock_daytona_client.delete.return_value = None

        with patch('builtins.input', return_value='y') as mock_input:
            await self.run_script_with_args(['--days-archived', str(days_archived_threshold)])
        
        mock_input.assert_called_once()
        self.mock_daytona_client.get_current_sandbox.assert_called_once_with("s_live_yes")
        self.mock_daytona_client.delete.assert_called_once_with(mock_s_live_yes_full)
        mock_logger.info.assert_any_call("Successfully deleted sandbox 's_live_yes' (Project: 'arch_live_yes').")
        mock_logger.info.assert_any_call(f"Sandboxes successfully deleted: 1")

    async def test_live_run_interactive_confirm_no(self):
        days_archived_threshold = 5
        s_live_no = self._create_mock_sandbox_info("s_live_no", "arch_live_no", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=10))
        self.mock_daytona_client.list_all_sandboxes.return_value = [s_live_no]

        with patch('builtins.input', return_value='n') as mock_input:
            await self.run_script_with_args(['--days-archived', str(days_archived_threshold)])
        
        mock_input.assert_called_once()
        self.mock_daytona_client.get_current_sandbox.assert_not_called()
        self.mock_daytona_client.delete.assert_not_called()
        mock_logger.info.assert_any_call("Skipped deletion of sandbox 's_live_no' (Project: 'arch_live_no') by user confirmation.")
        mock_logger.info.assert_any_call(f"Sandboxes successfully deleted: 0")


    async def test_live_run_bypass_confirmation(self):
        days_archived_threshold = 5
        s_bypass = self._create_mock_sandbox_info("s_bypass", "arch_bypass", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=10))
        self.mock_daytona_client.list_all_sandboxes.return_value = [s_bypass]

        mock_s_bypass_full = MagicMock(spec=Sandbox)
        self.mock_daytona_client.get_current_sandbox.return_value = mock_s_bypass_full
        self.mock_daytona_client.delete.return_value = None

        with patch('builtins.input') as mock_input: # Ensure input is not called
            await self.run_script_with_args(['--days-archived', str(days_archived_threshold), '--confirm'])
        
        mock_input.assert_not_called()
        self.mock_daytona_client.get_current_sandbox.assert_called_once_with("s_bypass")
        self.mock_daytona_client.delete.assert_called_once_with(mock_s_bypass_full)
        mock_logger.info.assert_any_call("Successfully deleted sandbox 's_bypass' (Project: 'arch_bypass').")

    async def test_failed_deletion(self):
        days_archived_threshold = 5
        s_fail_del = self._create_mock_sandbox_info("s_fail_del", "arch_fail_del", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=10))
        self.mock_daytona_client.list_all_sandboxes.return_value = [s_fail_del]

        mock_s_fail_del_full = MagicMock(spec=Sandbox)
        self.mock_daytona_client.get_current_sandbox.return_value = mock_s_fail_del_full
        self.mock_daytona_client.delete.side_effect = Exception("Daytona API error")

        await self.run_script_with_args(['--days-archived', str(days_archived_threshold), '--confirm'])
        
        mock_logger.error.assert_any_call("Failed to delete sandbox 's_fail_del' (Project: 'arch_fail_del'): Daytona API error", exc_info=True)
        mock_logger.info.assert_any_call(f"Sandboxes failed to delete: 1")

    # 4. Logging and Reporting (covered implicitly in other tests by checking logger calls)

    # 5. Edge Cases
    async def test_archived_sandbox_missing_updated_at(self):
        # The SandboxInfo model requires updated_at, so this mock needs careful construction
        # or we assume the script handles if it were None despite type hints.
        # The current script checks for `getattr(sandbox_info, 'updated_at', None)`
        # and SandboxInfo has updated_at as a required field.
        # To truly test this, one might need to mock SandboxInfo itself if it could return None for updated_at
        # For now, let's assume `updated_at` is always present due to SDK typing.
        # If we were to simulate it being None:
        mock_sandbox_info_no_ts = self._create_mock_sandbox_info("s_no_ts", "arch_no_ts", WorkspaceState.ARCHIVED, self.fixed_now - timedelta(days=10))
        mock_sandbox_info_no_ts.updated_at = None # Force it to None for test
        
        self.mock_daytona_client.list_all_sandboxes.return_value = [mock_sandbox_info_no_ts]
        await self.run_script_with_args(['--days-archived', '7'])
        mock_logger.warning.assert_any_call("Archived sandbox 's_no_ts' (Project: 'arch_no_ts') has no 'updated_at' timestamp. Skipping.")


    async def test_error_during_list_all_sandboxes(self):
        self.mock_daytona_client.list_all_sandboxes.side_effect = Exception("API connection error")
        await self.run_script_with_args(['--days-archived', '7'])
        mock_logger.error.assert_any_call("An error occurred during the script execution: API connection error", exc_info=True)

    async def test_datetime_parsing_logic(self):
        # The script now relies on SandboxInfo.updated_at being a datetime object.
        # The parse_datetime_string helper is robust.
        # If SandboxInfo could return strings, this test would be more direct.
        # For now, we trust the SDK and our type handling for datetime objects.
        # We can ensure our mock_datetime handles timezone correctly for age calculation.
        
        # Scenario: an archived sandbox, updated_at is naive, should be treated as UTC
        naive_dt = datetime(2023, 10, 1, 0, 0, 0) # Naive datetime
        s_naive_dt = self._create_mock_sandbox_info("s_naive", "arch_naive", WorkspaceState.ARCHIVED, naive_dt)
        self.mock_daytona_client.list_all_sandboxes.return_value = [s_naive_dt]
        
        # Mock get_current_sandbox and delete
        mock_s_naive_full = MagicMock(spec=Sandbox)
        self.mock_daytona_client.get_current_sandbox.return_value = mock_s_naive_full
        self.mock_daytona_client.delete.return_value = None

        # fixed_now is 2023-10-31. Age should be 30 days.
        with patch('builtins.input', return_value='y'):
             await self.run_script_with_args(['--days-archived', '30'])

        mock_logger.info.assert_any_call(f"Sandbox 's_naive' (Project: 'arch_naive') is eligible for deletion (archived for 30 days).")
        self.mock_daytona_client.delete.assert_called_once()


if __name__ == "__main__":
    unittest.main()
