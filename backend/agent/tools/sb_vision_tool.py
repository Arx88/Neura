import os
import base64
import mimetypes
from typing import Optional, Tuple
from io import BytesIO
from PIL import Image

from ...agentpress.tool import openapi_schema, xml_schema # ToolResult removed
from ...sandbox.tool_base import SandboxToolsBase
from ...agentpress.thread_manager import ThreadManager
# import json # Not used directly in this file after refactor
import logging # Added for logging

# Add common image MIME types if mimetypes module is limited
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/jpeg", ".jpg")
mimetypes.add_type("image/jpeg", ".jpeg")
mimetypes.add_type("image/png", ".png")
mimetypes.add_type("image/gif", ".gif")

# Maximum file size in bytes (e.g., 10MB for original, 5MB for compressed)
MAX_IMAGE_SIZE = 10 * 1024 * 1024
MAX_COMPRESSED_SIZE = 5 * 1024 * 1024

# Compression settings
DEFAULT_MAX_WIDTH = 1920 # Pixels
DEFAULT_MAX_HEIGHT = 1080 # Pixels
DEFAULT_JPEG_QUALITY = 85 # 0-100
DEFAULT_PNG_COMPRESS_LEVEL = 6 # 0-9

# Custom Exceptions
class VisionToolError(Exception):
    """Base exception for vision tool errors."""
    pass

class SandboxVisionTool(SandboxToolsBase):
    """Tool for allowing the agent to 'see' images within the sandbox."""

    def __init__(self, project_id: str, thread_id: str, thread_manager: ThreadManager):
        super().__init__(project_id, thread_manager)
        self.thread_id = thread_id
        # Make thread_manager accessible within the tool instance
        self.thread_manager = thread_manager

    def compress_image(self, image_bytes: bytes, mime_type: str, file_path: str) -> Tuple[bytes, str]:
        """Compress an image to reduce its size while maintaining reasonable quality.
        
        Args:
            image_bytes: Original image bytes
            mime_type: MIME type of the image
            file_path: Path to the image file (for logging)
            
        Returns:
            Tuple of (compressed_bytes, new_mime_type)
        """
        try:
            # Open image from bytes
            img = Image.open(BytesIO(image_bytes))
            
            # Convert RGBA to RGB if necessary (for JPEG)
            if img.mode in ('RGBA', 'LA', 'P') and mime_type != 'image/png': # Keep PNGs as RGBA if they are
                # Create a white background
                background = Image.new('RGB', img.size, (255, 255, 255))
                # If mode is 'P' (Palette), convert to RGBA first to ensure alpha channel is handled if present
                if img.mode == 'P':
                    img = img.convert('RGBA')

                # Paste using alpha channel as mask if available
                if img.mode == 'RGBA':
                    background.paste(img, mask=img.split()[-1])
                else: # For 'LA' or other modes that might not have alpha in the same way after conversion
                    background.paste(img)
                img = background
            
            # Calculate new dimensions while maintaining aspect ratio
            original_width, original_height = img.size
            resized_width, resized_height = original_width, original_height

            if original_width > DEFAULT_MAX_WIDTH or original_height > DEFAULT_MAX_HEIGHT:
                ratio = min(DEFAULT_MAX_WIDTH / original_width, DEFAULT_MAX_HEIGHT / original_height)
                resized_width = int(original_width * ratio)
                resized_height = int(original_height * ratio)
                img = img.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
                logging.info(f"[SeeImage] Resized image from {original_width}x{original_height} to {resized_width}x{resized_height} for '{file_path}'")
            
            output_buffer = BytesIO()
            output_mime = mime_type # Default to original, change if format changes
            
            if mime_type == 'image/gif':
                img.save(output_buffer, format='GIF', optimize=True) # optimize for GIFs
            elif mime_type == 'image/png':
                img.save(output_buffer, format='PNG', optimize=True, compress_level=DEFAULT_PNG_COMPRESS_LEVEL)
            elif mime_type in ('image/jpeg', 'image/jpg'):
                img.save(output_buffer, format='JPEG', quality=DEFAULT_JPEG_QUALITY, optimize=True)
            else: # For WEBP or other types, try to save as JPEG as a common compressed format
                logging.info(f"[SeeImage] Converting '{file_path}' from {mime_type} to JPEG for compression.")
                # Ensure image is in RGB mode for JPEG saving if it's not already handled
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                img.save(output_buffer, format='JPEG', quality=DEFAULT_JPEG_QUALITY, optimize=True)
                output_mime = 'image/jpeg' # Mime type changed
            
            compressed_bytes = output_buffer.getvalue()
            
            original_size_kb = len(image_bytes) / 1024
            compressed_size_kb = len(compressed_bytes) / 1024
            if original_size_kb > 0: # Avoid division by zero for empty files
                compression_ratio = (1 - compressed_size_kb / original_size_kb) * 100
                logging.info(f"[SeeImage] Compressed '{file_path}': {original_size_kb:.1f}KB -> {compressed_size_kb:.1f}KB ({compression_ratio:.1f}% reduction). New MIME: {output_mime}")
            else:
                logging.info(f"[SeeImage] Processed '{file_path}': {original_size_kb:.1f}KB -> {compressed_size_kb:.1f}KB. New MIME: {output_mime}")

            return compressed_bytes, output_mime
            
        except Exception as e:
            logging.error(f"[SeeImage] Failed to compress image '{file_path}': {str(e)}", exc_info=True)
            return image_bytes, mime_type # Return original if compression fails

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "see_image",
            "description": "Allows the agent to 'see' an image file located in the /workspace directory. Provide the relative path to the image. The image will be compressed before sending to reduce token usage. The image content will be made available in the next turn's context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "The relative path to the image file within the /workspace directory (e.g., 'screenshots/image.png'). Supported formats: JPG, PNG, GIF, WEBP. Max size: 10MB."
                    }
                },
                "required": ["file_path"]
            }
        }
    })
    @xml_schema(
        tag_name="see-image",
        mappings=[
            {"param_name": "file_path", "node_type": "attribute", "path": "."}
        ],
        example='''
        <!-- Example: Request to see an image named 'diagram.png' inside the 'docs' folder -->
        <see-image file_path="docs/diagram.png"></see-image>
        '''
    )
    async def see_image(self, file_path: str) -> dict:
        """Reads an image file, compresses it, converts it to base64, and adds it as a temporary message."""
        if not file_path or not isinstance(file_path, str):
            raise ValueError("A valid file path is required.")

        try:
            await self._ensure_sandbox()

            cleaned_path = self.clean_path(file_path)
            full_path = f"{self.workspace_path}/{cleaned_path}"

            try:
                file_info = self.sandbox.fs.get_file_info(full_path)
                if file_info.is_dir:
                    raise IsADirectoryError(f"Path '{cleaned_path}' is a directory, not an image file.")
            except FileNotFoundError: # fs.get_file_info should raise this or similar
                raise FileNotFoundError(f"Image file not found at path: '{cleaned_path}'")
            except Exception as e_info: # Catch other fs.get_file_info errors
                raise VisionToolError(f"Could not get file info for '{cleaned_path}': {str(e_info)}") from e_info


            if file_info.size > MAX_IMAGE_SIZE:
                raise ValueError(f"Image file '{cleaned_path}' is too large ({file_info.size / (1024*1024):.2f}MB). Max original size: {MAX_IMAGE_SIZE / (1024*1024)}MB.")

            try:
                image_bytes = self.sandbox.fs.download_file(full_path) # Assuming this is synchronous or handled by Daytona SDK
            except Exception as e_download:
                raise VisionToolError(f"Could not read image file '{cleaned_path}': {str(e_download)}") from e_download

            mime_type, _ = mimetypes.guess_type(full_path)
            if not mime_type or not mime_type.startswith('image/'):
                ext = os.path.splitext(cleaned_path)[1].lower()
                mime_type_map = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp'}
                mime_type = mime_type_map.get(ext)
                if not mime_type:
                    raise ValueError(f"Unsupported or unknown image format for file: '{cleaned_path}'. Supported: JPG, PNG, GIF, WEBP.")

            compressed_bytes, compressed_mime_type = self.compress_image(image_bytes, mime_type, cleaned_path)
            
            if len(compressed_bytes) > MAX_COMPRESSED_SIZE:
                raise ValueError(f"Image '{cleaned_path}' is too large after compression ({len(compressed_bytes)/(1024*1024):.2f}MB). Max compressed: {MAX_COMPRESSED_SIZE/(1024*1024)}MB.")

            base64_image = base64.b64encode(compressed_bytes).decode('utf-8')

            image_context_data = {
                "mime_type": compressed_mime_type,
                "base64": base64_image,
                "file_path": cleaned_path,
                "original_size_bytes": file_info.size,
                "compressed_size_bytes": len(compressed_bytes)
            }

            # This part is crucial for the tool's purpose: making image available to LLM
            await self.thread_manager.add_message(
                thread_id=self.thread_id,
                type="image_context",
                content=image_context_data,
                is_llm_message=False
            )

            success_message = (
                f"Successfully processed image '{cleaned_path}'. "
                f"Original: {file_info.size / 1024:.1f}KB, Compressed: {len(compressed_bytes) / 1024:.1f}KB. "
                f"It's now available in context."
            )
            return {
                "message": success_message,
                "file_path": cleaned_path,
                "original_size_bytes": file_info.size,
                "compressed_size_bytes": len(compressed_bytes),
                "mime_type": compressed_mime_type
            }
        except (ValueError, FileNotFoundError, IsADirectoryError):
            raise
        except Exception as e:
            logging.error(f"An unexpected error occurred while processing image '{file_path}': {str(e)}", exc_info=True)
            raise VisionToolError(f"An unexpected error occurred while trying to see the image '{file_path}': {str(e)}") from e