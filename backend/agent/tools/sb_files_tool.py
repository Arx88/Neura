from backend.agentpress.tool import ToolResult, openapi_schema, xml_schema
from backend.sandbox.tool_base import SandboxToolsBase
from backend.utils.files_utils import should_exclude_file, clean_path
from backend.agentpress.thread_manager import ThreadManager
from backend.utils.logger import logger
import os
import logging # Added for logging

# Custom Exceptions
class FilesToolError(Exception):
    """Base exception for file tool errors."""
    pass

class SandboxFilesTool(SandboxToolsBase):
    """Tool for executing file system operations in a Daytona sandbox. All operations are performed relative to the /workspace directory."""

    def __init__(self, project_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self.SNIPPET_LINES = 4  # Number of context lines to show around edits
        self.workspace_path = "/workspace"  # Ensure we're always operating in /workspace

    def clean_path(self, path: str) -> str:
        """Clean and normalize a path to be relative to /workspace"""
        return clean_path(path, self.workspace_path)

    def _should_exclude_file(self, rel_path: str) -> bool:
        """Check if a file should be excluded based on path, name, or extension"""
        return should_exclude_file(rel_path)

    def _file_exists(self, path: str) -> bool:
        """Check if a file exists in the sandbox"""
        try:
            self.sandbox.fs.get_file_info(path)
            return True
        except Exception:
            return False

    async def get_workspace_state(self) -> dict:
        """Get the current workspace state by reading all files"""
        files_state = {}
        try:
            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            files = self.sandbox.fs.list_files(self.workspace_path)
            for file_info in files:
                rel_path = file_info.name
                
                # Skip excluded files and directories
                if self._should_exclude_file(rel_path) or file_info.is_dir:
                    continue

                try:
                    full_path = f"{self.workspace_path}/{rel_path}"
                    content = self.sandbox.fs.download_file(full_path).decode()
                    files_state[rel_path] = {
                        "content": content,
                        "is_dir": file_info.is_dir,
                        "size": file_info.size,
                        "modified": file_info.mod_time
                    }
                except Exception as e:
                    print(f"Error reading file {rel_path}: {e}")
                except UnicodeDecodeError:
                    print(f"Skipping binary file: {rel_path}")

            return files_state
        
        except Exception as e:
            print(f"Error getting workspace state: {str(e)}")
            return {}


    # def _get_preview_url(self, file_path: str) -> Optional[str]:
    #     """Get the preview URL for a file if it's an HTML file."""
    #     if file_path.lower().endswith('.html') and self._sandbox_url:
    #         return f"{self._sandbox_url}/{(file_path.replace('/workspace/', ''))}"
    #     return None

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "create_file",
            "description": "Create a new file with the provided contents at a given path in the workspace. The path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py)",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to be created, relative to /workspace (e.g., 'src/main.py')"
                    },
                    "file_contents": {
                        "type": "string",
                        "description": "The content to write to the file"
                    },
                    "permissions": {
                        "type": "string",
                        "description": "File permissions in octal format (e.g., '644')",
                        "default": "644"
                    }
                },
                "required": ["file_path", "file_contents"]
            }
        }
    })
    @xml_schema(
        tag_name="create-file",
        mappings=[
            {"param_name": "file_path", "node_type": "attribute", "path": "."},
            {"param_name": "file_contents", "node_type": "content", "path": "."}
        ],
        example='''
        <create-file file_path="src/main.py">
        File contents go here
        </create-file>
        '''
    )
    async def create_file(self, file_path: str, file_contents: str, permissions: str = "644") -> dict:
        try:
            if not file_path or not isinstance(file_path, str):
                raise ValueError("A valid file path is required.")
            if file_contents is None: # Allow empty string, but not None
                raise ValueError("File contents must be provided (can be an empty string).")

            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            cleaned_fp = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{cleaned_fp}"

            if self._file_exists(full_path):
                raise FilesToolError(f"File '{cleaned_fp}' already exists. Use full_file_rewrite or str_replace to modify existing files.")
            
            # Create parent directories if needed
            parent_dir_path = os.path.dirname(full_path)
            if parent_dir_path and parent_dir_path != self.workspace_path: # Avoid creating /workspace itself if path is at root
                # self.sandbox.fs.create_folder might need to be async if it involves I/O with Daytona SDK
                # Assuming it's synchronous for now based on current usage. If it's async, add await.
                self.sandbox.fs.create_folder(parent_dir_path, "755")
            
            # Write the file content
            # self.sandbox.fs.upload_file might need to be async
            self.sandbox.fs.upload_file(full_path, file_contents.encode('utf-8'))
            self.sandbox.fs.set_file_permissions(full_path, permissions)
            
            message = f"File '{cleaned_fp}' created successfully."
            return {"message": message, "file_path": cleaned_fp}
        except ValueError:
            raise
        except Exception as e:
            logging.error(f"Error creating file '{file_path}': {str(e)}", exc_info=True)
            raise FilesToolError(f"Error creating file '{file_path}': {str(e)}") from e

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "str_replace",
            "description": "Replace specific text in a file. The file path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py). Use this when you need to replace a unique string that appears exactly once in the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the target file, relative to /workspace (e.g., 'src/main.py')"
                    },
                    "old_str": {
                        "type": "string",
                        "description": "Text to be replaced (must appear exactly once)"
                    },
                    "new_str": {
                        "type": "string",
                        "description": "Replacement text"
                    }
                },
                "required": ["file_path", "old_str", "new_str"]
            }
        }
    })
    @xml_schema(
        tag_name="str-replace",
        mappings=[
            {"param_name": "file_path", "node_type": "attribute", "path": "."},
            {"param_name": "old_str", "node_type": "element", "path": "old_str"},
            {"param_name": "new_str", "node_type": "element", "path": "new_str"}
        ],
        example='''
        <str-replace file_path="src/main.py">
            <old_str>text to replace (must appear exactly once in the file)</old_str>
            <new_str>replacement text that will be inserted instead</new_str>
        </str-replace>
        '''
    )
    async def str_replace(self, file_path: str, old_str: str, new_str: str) -> dict:
        try:
            if not file_path or not isinstance(file_path, str):
                raise ValueError("A valid file path is required.")
            if old_str is None: # new_str can be empty
                 raise ValueError("The 'old_str' to be replaced must be provided.")

            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            cleaned_fp = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{cleaned_fp}"
            if not self._file_exists(full_path):
                raise FileNotFoundError(f"File '{cleaned_fp}' does not exist.")

            # download_file might need await if Daytona SDK is async
            content = self.sandbox.fs.download_file(full_path).decode('utf-8')
            
            # Expand tabs consistently for reliable counting and replacement
            # Note: This changes the file content if it has tabs. Consider if this is desired.
            # If not, count and replace without expandtabs, but be aware of tab inconsistencies.
            old_str_expanded = old_str.expandtabs()
            new_str_expanded = new_str.expandtabs()
            content_expanded_for_count = content.expandtabs() # Count on expanded version
            
            occurrences = content_expanded_for_count.count(old_str_expanded)
            if occurrences == 0:
                raise ValueError(f"String '{old_str}' not found in file '{cleaned_fp}'.")
            if occurrences > 1:
                lines = [i+1 for i, line in enumerate(content_expanded_for_count.split('\n')) if old_str_expanded in line]
                raise ValueError(f"Multiple occurrences of '{old_str}' found in lines {lines} of file '{cleaned_fp}'. Please ensure the string is unique for replacement.")
            
            # Perform replacement on original content if no tabs in old/new, or on expanded if tabs matter for matching
            # For simplicity and to match original logic of replacing expanded strings:
            new_content = content_expanded_for_count.replace(old_str_expanded, new_str_expanded)
            
            # upload_file might need await
            self.sandbox.fs.upload_file(full_path, new_content.encode('utf-8'))
            
            # Snippet generation can be kept if it's considered part of the "raw data" result
            # replacement_line_idx = -1
            # temp_content_lines = content_expanded_for_count.split('\n')
            # for i, line_text in enumerate(temp_content_lines):
            #     if old_str_expanded in line_text:
            #         replacement_line_idx = i
            #         break
            
            # snippet_str = ""
            # if replacement_line_idx != -1:
            #     start_line = max(0, replacement_line_idx - self.SNIPPET_LINES)
            #     # Calculate end_line based on new content's line structure around the replacement
            #     # This is tricky if new_str_expanded has different line count than old_str_expanded
            #     # For simplicity, let's use a fixed window around the start of the replacement in the new content
            #     new_content_lines = new_content.split('\n')
            #     # Find where the new_str starts effectively
            #     # This is an approximation
            #     new_str_start_line_idx = content_expanded_for_count[:content_expanded_for_count.find(old_str_expanded)].count('\n')

            #     end_line = min(len(new_content_lines) -1, new_str_start_line_idx + new_str_expanded.count('\n') + self.SNIPPET_LINES)
            #     snippet_str = '\n'.join(new_content_lines[start_line : end_line + 1])

            message = f"Replacement successful in file '{cleaned_fp}'."
            return {"message": message, "file_path": cleaned_fp} # "snippet": snippet_str if snippet_str else "Snippet not generated."
            
        except (ValueError, FileNotFoundError):
            raise
        except Exception as e:
            logging.error(f"Error replacing string in '{file_path}': {str(e)}", exc_info=True)
            raise FilesToolError(f"Error replacing string in '{file_path}': {str(e)}") from e

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "full_file_rewrite",
            "description": "Completely rewrite an existing file with new content. The file path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py). Use this when you need to replace the entire file content or make extensive changes throughout the file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to be rewritten, relative to /workspace (e.g., 'src/main.py')"
                    },
                    "file_contents": {
                        "type": "string",
                        "description": "The new content to write to the file, replacing all existing content"
                    },
                    "permissions": {
                        "type": "string",
                        "description": "File permissions in octal format (e.g., '644')",
                        "default": "644"
                    }
                },
                "required": ["file_path", "file_contents"]
            }
        }
    })
    @xml_schema(
        tag_name="full-file-rewrite",
        mappings=[
            {"param_name": "file_path", "node_type": "attribute", "path": "."},
            {"param_name": "file_contents", "node_type": "content", "path": "."}
        ],
        example='''
        <full-file-rewrite file_path="src/main.py">
        This completely replaces the entire file content.
        Use when making major changes to a file or when the changes
        are too extensive for str-replace.
        All previous content will be lost and replaced with this text.
        </full-file-rewrite>
        '''
    )
    async def full_file_rewrite(self, file_path: str, file_contents: str, permissions: str = "644") -> dict:
        try:
            if not file_path or not isinstance(file_path, str):
                raise ValueError("A valid file path is required.")
            if file_contents is None: # Allow empty string
                raise ValueError("File contents must be provided.")

            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            cleaned_fp = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{cleaned_fp}"
            if not self._file_exists(full_path):
                # Consider if this should be create_file or an error.
                # The description implies it rewrites an *existing* file.
                raise FileNotFoundError(f"File '{cleaned_fp}' does not exist. Use create_file to create a new file.")
            
            # upload_file might need await
            self.sandbox.fs.upload_file(full_path, file_contents.encode('utf-8'))
            self.sandbox.fs.set_file_permissions(full_path, permissions)
            
            message = f"File '{cleaned_fp}' completely rewritten successfully."
            return {"message": message, "file_path": cleaned_fp}
        except (ValueError, FileNotFoundError):
            raise
        except Exception as e:
            logging.error(f"Error rewriting file '{file_path}': {str(e)}", exc_info=True)
            raise FilesToolError(f"Error rewriting file '{file_path}': {str(e)}") from e

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "Delete a file at the given path. The path must be relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py)",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to the file to be deleted, relative to /workspace (e.g., 'src/main.py')"
                    }
                },
                "required": ["file_path"]
            }
        }
    })
    @xml_schema(
        tag_name="delete-file",
        mappings=[
            {"param_name": "file_path", "node_type": "attribute", "path": "."}
        ],
        example='''
        <delete-file file_path="src/main.py">
        </delete-file>
        '''
    )
    async def delete_file(self, file_path: str) -> dict:
        try:
            if not file_path or not isinstance(file_path, str):
                raise ValueError("A valid file path is required.")

            # Ensure sandbox is initialized
            await self._ensure_sandbox()
            
            cleaned_fp = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{cleaned_fp}"
            if not self._file_exists(full_path):
                raise FileNotFoundError(f"File '{cleaned_fp}' does not exist, cannot delete.")
            
            # delete_file might need await
            self.sandbox.fs.delete_file(full_path)
            return {"message": f"File '{cleaned_fp}' deleted successfully.", "file_path": cleaned_fp}
        except (ValueError, FileNotFoundError):
            raise
        except Exception as e:
            logging.error(f"Error deleting file '{file_path}': {str(e)}", exc_info=True)
            raise FilesToolError(f"Error deleting file '{file_path}': {str(e)}") from e

    # @openapi_schema({
    #     "type": "function",
    #     "function": {
    #         "name": "read_file",
    #         "description": "Read and return the contents of a file. This tool is essential for verifying data, checking file contents, and analyzing information. Always use this tool to read file contents before processing or analyzing data. The file path must be relative to /workspace.",
    #         "parameters": {
    #             "type": "object",
    #             "properties": {
    #                 "file_path": {
    #                     "type": "string",
    #                     "description": "Path to the file to read, relative to /workspace (e.g., 'src/main.py' for /workspace/src/main.py). Must be a valid file path within the workspace."
    #                 },
    #                 "start_line": {
    #                     "type": "integer",
    #                     "description": "Optional starting line number (1-based). Use this to read specific sections of large files. If not specified, reads from the beginning of the file.",
    #                     "default": 1
    #                 },
    #                 "end_line": {
    #                     "type": "integer",
    #                     "description": "Optional ending line number (inclusive). Use this to read specific sections of large files. If not specified, reads to the end of the file.",
    #                     "default": None
    #                 }
    #             },
    #             "required": ["file_path"]
    #         }
    #     }
    # })
    # @xml_schema(
    #     tag_name="read-file",
    #     mappings=[
    #         {"param_name": "file_path", "node_type": "attribute", "path": "."},
    #         {"param_name": "start_line", "node_type": "attribute", "path": ".", "required": False},
    #         {"param_name": "end_line", "node_type": "attribute", "path": ".", "required": False}
    #     ],
    #     example='''
    #     <!-- Example 1: Read entire file -->
    #     <read-file file_path="src/main.py">
    #     </read-file>

    #     <!-- Example 2: Read specific lines (lines 10-20) -->
    #     <read-file file_path="src/main.py" start_line="10" end_line="20">
    #     </read-file>

    #     <!-- Example 3: Read from line 5 to end -->
    #     <read-file file_path="config.json" start_line="5">
    #     </read-file>

    #     <!-- Example 4: Read last 10 lines -->
    #     <read-file file_path="logs/app.log" start_line="-10">
    #     </read-file>
    #     '''
    # )
    # async def read_file(self, file_path: str, start_line: int = 1, end_line: Optional[int] = None) -> ToolResult:
    #     """Read file content with optional line range specification.
        
    #     Args:
    #         file_path: Path to the file relative to /workspace
    #         start_line: Starting line number (1-based), defaults to 1
    #         end_line: Ending line number (inclusive), defaults to None (end of file)
            
    #     Returns:
    #         ToolResult containing:
    #         - Success: File content and metadata
    #         - Failure: Error message if file doesn't exist or is binary
    #     """
    #     try:
    #         file_path = self.clean_path(file_path)
    #         full_path = f"{self.workspace_path}/{file_path}"
            
    #         if not self._file_exists(full_path):
    #             return self.fail_response(f"File '{file_path}' does not exist")
            
    #         # Download and decode file content
    #         content = self.sandbox.fs.download_file(full_path).decode()
            
    #         # Split content into lines
    #         lines = content.split('\n')
    #         total_lines = len(lines)
            
    #         # Handle line range if specified
    #         if start_line > 1 or end_line is not None:
    #             # Convert to 0-based indices
    #             start_idx = max(0, start_line - 1)
    #             end_idx = end_line if end_line is not None else total_lines
    #             end_idx = min(end_idx, total_lines)  # Ensure we don't exceed file length
                
    #             # Extract the requested lines
    #             content = '\n'.join(lines[start_idx:end_idx])
            
    #         return self.success_response({
    #             "content": content,
    #             "file_path": file_path,
    #             "start_line": start_line,
    #             "end_line": end_line if end_line is not None else total_lines,
    #             "total_lines": total_lines
    #         })
            
    #     except UnicodeDecodeError:
    #         return self.fail_response(f"File '{file_path}' appears to be binary and cannot be read as text")
    #     except Exception as e:
    #         return self.fail_response(f"Error reading file: {str(e)}")

