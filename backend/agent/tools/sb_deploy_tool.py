import os
from dotenv import load_dotenv
from agentpress.tool import openapi_schema, xml_schema # ToolResult removed
from sandbox.tool_base import SandboxToolsBase
from utils.files_utils import clean_path
from agentpress.thread_manager import ThreadManager

# Load environment variables
load_dotenv()

# Custom Exceptions
class DeployToolError(Exception):
    """Base exception for deployment tool errors."""
    pass

class SandboxDeployTool(SandboxToolsBase):
    """Tool for deploying static websites from a Daytona sandbox to Cloudflare Pages."""

    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self.workspace_path = "/workspace"  # Ensure we're always operating in /workspace
        self.cloudflare_api_token = os.getenv("CLOUDFLARE_API_TOKEN")

    def clean_path(self, path: str) -> str:
        """Clean and normalize a path to be relative to /workspace"""
        return clean_path(path, self.workspace_path)

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "deploy",
            "description": "Deploy a **static web application** (HTML, CSS, JavaScript files and assets) from a specified directory in the sandbox to Cloudflare Pages for public web hosting. This tool is **exclusively for static site deployments** and cannot be used for deploying backend servers, databases, virtual machines, or other infrastructure. Only use this tool when permanent deployment of static web content to a production environment is needed. The directory path must be relative to /workspace. The website will be deployed to {name}.kortix.cloud.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Name for the deployment, will be used in the URL as {name}.kortix.cloud"
                    },
                    "directory_path": {
                        "type": "string",
                        "description": "Path to the directory containing the static website files to deploy, relative to /workspace (e.g., 'build')"
                    }
                },
                "required": ["name", "directory_path"]
            }
        }
    })
    @xml_schema(
        tag_name="deploy",
        mappings=[
            {"param_name": "name", "node_type": "attribute", "path": "name"},
            {"param_name": "directory_path", "node_type": "attribute", "path": "directory_path"}
        ],
        example='''
        <!-- 
        IMPORTANT: Only use this tool when:
        1. The user explicitly requests permanent deployment to production
        2. You have a complete, ready-to-deploy directory 
        - The content is a static website (HTML/CSS/JS). This tool cannot deploy VMs or servers.
        
        NOTE: If the same name is used, it will redeploy to the same project as before
                -->

        <deploy name="my-site" directory_path="website">
        </deploy>
        '''
    )
    async def deploy(self, name: str, directory_path: str) -> dict: # Return dict on success
        """
        Deploy a static website (HTML+CSS+JS) from the sandbox to Cloudflare Pages.
        Only use this tool when permanent deployment to a production environment is needed.
        
        Args:
            name: Name for the deployment, will be used in the URL as {name}.kortix.cloud
            directory_path: Path to the directory to deploy, relative to /workspace
            
        Returns:
            A dictionary containing deployment information on success.
        Raises:
            ValueError: If inputs are invalid (e.g., path is not a directory).
            EnvironmentError: If required environment variables like CLOUDFLARE_API_TOKEN are missing.
            DeployToolError: For deployment-specific errors (e.g., command failure, Cloudflare API issues).
        """
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            if not name or not isinstance(name, str):
                raise ValueError("A valid deployment name is required.")
            if not directory_path or not isinstance(directory_path, str):
                raise ValueError("A valid directory path is required.")

            cleaned_directory_path = self.clean_path(directory_path) # Use a different variable name
            full_path = f"{self.workspace_path}/{cleaned_directory_path}"
            
            # Verify the directory exists
            try:
                dir_info = self.sandbox.fs.get_file_info(full_path)
                if not dir_info.is_dir:
                    raise ValueError(f"'{cleaned_directory_path}' is not a directory.")
            except Exception as e: # fs.get_file_info might raise if path doesn't exist
                raise ValueError(f"Directory '{cleaned_directory_path}' does not exist or is inaccessible: {str(e)}") from e
            
            # Deploy to Cloudflare Pages directly from the container
            # Get Cloudflare API token from environment
            if not self.cloudflare_api_token:
                raise EnvironmentError("CLOUDFLARE_API_TOKEN environment variable not set. Cannot deploy.")
                
            # Single command that creates the project if it doesn't exist and then deploys
            # Ensure project_name is URL-safe; Cloudflare might have restrictions.
            # Using sandbox_id ensures some level of uniqueness if multiple users deploy "my-site".
            project_name = f"{self.sandbox_id}-{name.lower().replace(' ', '-')}"

            # Ensure full_path is correctly quoted if it might contain spaces, though clean_path should handle some of this.
            # For robustness, consider ensuring paths passed to shell commands are safe.
            deploy_cmd = f'''cd {self.workspace_path} && export CLOUDFLARE_API_TOKEN={self.cloudflare_api_token} && \
                (npx wrangler pages deploy "{full_path}" --project-name "{project_name}" || \
                (npx wrangler pages project create "{project_name}" --production-branch production && \
                npx wrangler pages deploy "{full_path}" --project-name "{project_name}"))'''

            # Execute the command directly using the sandbox's process.exec method
            response = self.sandbox.process.exec(deploy_cmd, timeout=300) # process.exec is synchronous
            
            # print(f"Deployment command output: {response.result}") # For debugging, consider using logging

            if response.exit_code == 0:
                # Extract deployment URL or other relevant info from response.result if possible and add to dict
                # For now, just returning the raw output.
                # Example of what could be parsed: "✨ Deployment complete! Take a look at your site: https://project_name.pages.dev"
                # This parsing can be fragile.
                deployment_url = None
                if response.result:
                    for line in response.result.splitlines():
                        if "https://*.pages.dev".replace("*",project_name) in line or "Deployment complete!" in line: # crude search for URL
                             # Try to extract the URL more reliably if possible
                            url_parts = [part for part in line.split() if project_name in part and ".pages.dev" in part]
                            if url_parts:
                                deployment_url = url_parts[0]
                                break

                success_payload = {
                    "message": f"Website '{name}' deployed successfully.",
                    "project_name": project_name,
                    "output": response.result
                }
                if deployment_url:
                    success_payload["deployment_url"] = deployment_url
                return success_payload
            else:
                error_detail = f"Deployment failed with exit code {response.exit_code}: {response.result}"
                raise DeployToolError(error_detail)
        except (ValueError, EnvironmentError): # Re-raise specific errors
            raise
        except Exception as e: # Catch any other unexpected errors
            # Log the full error for debugging
            # logger.error(f"Unexpected error during deployment of '{name}': {str(e)}", exc_info=True)
            raise DeployToolError(f"An unexpected error occurred during deployment: {str(e)}") from e

if __name__ == "__main__":
    # The __main__ block needs to be updated to reflect synchronous nature
    # and direct call for testing if needed, or removed if not used for direct execution.
    # For now, I'll comment it out as it's not directly compatible with the refactor
    # and might require a running sandbox instance setup manually.
    # import asyncio
    #
    # async def test_deploy():
    #     # This test would need a live sandbox and proper project_id / thread_manager
    #     # For example:
    #     # project_id_for_test = "your_project_id"
    #     # thread_manager_for_test = ThreadManager(None) # Mock or real
    #     # deploy_tool = SandboxDeployTool(project_id_for_test, thread_manager_for_test)
    #     # try:
    #     #     # Setup: Ensure a 'website' directory with an index.html exists in the sandbox's /workspace
    #     #     result = await deploy_tool.deploy(
    #     #         name="my-test-deployment",
    #     #         directory_path="website"
    #     #     )
    #     #     print(f"Deployment successful: {result}")
    #     # except Exception as e:
    #     #     print(f"Deployment failed: {e}")
    #
    # if __name__ == "__main__":
    #     asyncio.run(test_deploy())
    pass
