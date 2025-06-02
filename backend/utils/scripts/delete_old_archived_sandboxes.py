import asyncio
import sys
import os
import argparse
from datetime import datetime, timedelta, timezone as dt_timezone # Renamed to avoid conflict

# Ensure the script can find a_backend modules
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from dotenv import load_dotenv
from sandbox.sandbox import daytona # Assuming daytona client is initialized here
from utils.logger import logger
from daytona_api_client.models.workspace_state import WorkspaceState # For state comparison
from daytona_sdk.sandbox import SandboxInfo # For type hinting if needed

def parse_datetime_string(datetime_str: str) -> datetime | None:
    """
    Parses a datetime string into a timezone-aware datetime object (UTC).
    Handles ISO format with or without 'Z' and potential fractional seconds.
    """
    if not datetime_str:
        return None
    try:
        if datetime_str.endswith('Z'):
            datetime_str = datetime_str[:-1] + '+00:00'
        dt_obj = datetime.fromisoformat(datetime_str)
        if dt_obj.tzinfo is None:
            dt_obj = dt_obj.replace(tzinfo=dt_timezone.utc) # Assume UTC if naive
        else:
            dt_obj = dt_obj.astimezone(dt_timezone.utc) # Convert to UTC
        return dt_obj
    except ValueError as e:
        logger.warning(f"Could not parse datetime string '{datetime_str}': {e}")
        return None

async def main():
    parser = argparse.ArgumentParser(description="Delete old archived Daytona sandboxes.")
    parser.add_argument(
        "--days-archived",
        type=int,
        default=7,
        help="Number of days a sandbox must have been archived to be eligible for deletion (default: 7)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate deletion without making actual changes."
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Bypass interactive confirmation for deletion (for automated runs)."
    )
    args = parser.parse_args()

    # Initialization
    logger.info("Starting deletion script for old archived sandboxes.")
    load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env')) # Load .env from root

    daytona_server_url = os.getenv("DAYTONA_SERVER_URL", "Not configured")
    logger.info(f"Daytona Server URL: {daytona_server_url}")
    if args.dry_run:
        logger.info("DRY RUN mode enabled. No actual deletions will occur.")

    # Counters for summary
    sandboxes_checked = 0
    archived_sandboxes_count = 0
    eligible_for_deletion_count = 0
    deleted_count = 0
    failed_deletion_count = 0

    try:
        logger.info("Fetching all sandboxes from Daytona...")
        # Ensure daytona client is ready (it's initialized globally in sandbox.py)
        all_sandboxes: list[SandboxInfo] = await daytona.list_all_sandboxes()
        sandboxes_checked = len(all_sandboxes)
        logger.info(f"Fetched {sandboxes_checked} sandboxes in total.")

        if not all_sandboxes:
            logger.info("No sandboxes found. Exiting.")
            return

        for sandbox_info in all_sandboxes:
            project_name = sandbox_info.project_name or "Unknown Project"
            sandbox_id = sandbox_info.id

            if str(sandbox_info.state) == str(WorkspaceState.ARCHIVED): # Compare string representations
                archived_sandboxes_count += 1
                logger.debug(f"Sandbox '{sandbox_id}' (Project: '{project_name}') is archived.")

                updated_at_str = getattr(sandbox_info, 'updated_at', None)
                if not updated_at_str:
                    # Daytona SDK might store it in info.updated_at or directly
                    # For SandboxInfo, it's directly updated_at
                    # If it's an instance of Sandbox, it's sandbox.info().updated_at
                    # list_all_sandboxes returns SandboxInfo
                    logger.warning(f"Archived sandbox '{sandbox_id}' (Project: '{project_name}') has no 'updated_at' timestamp. Skipping.")
                    continue
                
                # The updated_at from SandboxInfo is already a datetime object
                if isinstance(updated_at_str, str): # Just in case it's a string in some context
                    updated_at_datetime = parse_datetime_string(updated_at_str)
                elif isinstance(updated_at_str, datetime):
                    updated_at_datetime = updated_at_str
                    if updated_at_datetime.tzinfo is None: # Ensure timezone aware
                        updated_at_datetime = updated_at_datetime.replace(tzinfo=dt_timezone.utc)
                    else:
                        updated_at_datetime = updated_at_datetime.astimezone(dt_timezone.utc)
                else:
                    logger.warning(f"Archived sandbox '{sandbox_id}' (Project: '{project_name}') 'updated_at' has unknown type: {type(updated_at_str)}. Skipping.")
                    continue
                    
                if not updated_at_datetime:
                    logger.warning(f"Could not parse 'updated_at' for archived sandbox '{sandbox_id}' (Project: '{project_name}'). Skipping.")
                    continue

                age_archived = datetime.now(dt_timezone.utc) - updated_at_datetime
                logger.debug(f"Sandbox '{sandbox_id}' (Project: '{project_name}') archived for {age_archived.days} days (updated_at: {updated_at_datetime.isoformat()}).")

                if age_archived.days >= args.days_archived:
                    eligible_for_deletion_count += 1
                    logger.info(f"Sandbox '{sandbox_id}' (Project: '{project_name}') is eligible for deletion (archived for {age_archived.days} days).")

                    if args.dry_run:
                        logger.info(f"DRY RUN: Would delete sandbox '{sandbox_id}' (Project: '{project_name}').")
                        # In dry run, we simulate a successful deletion for reporting
                        deleted_count +=1 
                    else:
                        proceed_with_deletion = False
                        if args.confirm:
                            proceed_with_deletion = True
                        else:
                            try:
                                confirm_input = input(f"Delete sandbox '{sandbox_id}' for project '{project_name}' (archived {age_archived.days} days)? (y/N): ").strip().lower()
                                if confirm_input == 'y':
                                    proceed_with_deletion = True
                            except KeyboardInterrupt:
                                logger.info("\nDeletion process interrupted by user. Exiting.")
                                sys.exit(0)
                        
                        if proceed_with_deletion:
                            try:
                                logger.info(f"Attempting to delete sandbox '{sandbox_id}' (Project: '{project_name}').")
                                # daytona.delete() expects a Sandbox object, not just ID or SandboxInfo.
                                # We need to get the full Sandbox object first.
                                sandbox_to_delete = await daytona.get_current_sandbox(sandbox_id)
                                if sandbox_to_delete:
                                    await daytona.delete(sandbox_to_delete)
                                    logger.info(f"Successfully deleted sandbox '{sandbox_id}' (Project: '{project_name}').")
                                    deleted_count += 1
                                else:
                                    logger.error(f"Could not retrieve full sandbox object for ID '{sandbox_id}' (Project: '{project_name}'). Deletion skipped.")
                                    failed_deletion_count += 1
                            except Exception as e:
                                logger.error(f"Failed to delete sandbox '{sandbox_id}' (Project: '{project_name}'): {e}", exc_info=True)
                                failed_deletion_count += 1
                        else:
                            logger.info(f"Skipped deletion of sandbox '{sandbox_id}' (Project: '{project_name}') by user confirmation.")
                else:
                    logger.debug(f"Sandbox '{sandbox_id}' (Project: '{project_name}') not old enough for deletion (archived for {age_archived.days} days).")
            else:
                logger.debug(f"Sandbox '{sandbox_id}' (Project: '{project_name}') is not archived (state: {sandbox_info.state}). Skipping.")

    except Exception as e:
        logger.error(f"An error occurred during the script execution: {e}", exc_info=True)
    finally:
        logger.info("--- Summary ---")
        logger.info(f"Total sandboxes checked: {sandboxes_checked}")
        logger.info(f"Total archived sandboxes: {archived_sandboxes_count}")
        logger.info(f"Sandboxes eligible for deletion (>= {args.days_archived} days archived): {eligible_for_deletion_count}")
        if args.dry_run:
            logger.info(f"Sandboxes that would be deleted (DRY RUN): {deleted_count}")
        else:
            logger.info(f"Sandboxes successfully deleted: {deleted_count}")
            logger.info(f"Sandboxes failed to delete: {failed_deletion_count}")
        logger.info("Script finished.")

if __name__ == "__main__":
    asyncio.run(main())
