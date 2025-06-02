"""
Core tool system providing the foundation for creating and managing tools.

This module defines the base classes and decorators for creating tools in AgentPress:
- Tool base class for implementing tool functionality
- Schema decorators for OpenAPI and XML tool definitions
- Result containers for standardized tool outputs
"""

from typing import Dict, Any, Union, Optional, List
from dataclasses import dataclass, field
from abc import ABC
import json
import inspect
import time
from enum import Enum
from backend.utils.logger import logger # Changed import path

class SchemaType(Enum):
    """Enumeration of supported schema types for tool definitions."""
    OPENAPI = "openapi"
    XML = "xml"
    CUSTOM = "custom"

@dataclass
class XMLNodeMapping:
    """Maps an XML node to a function parameter.
    
    Attributes:
        param_name (str): Name of the function parameter
        node_type (str): Type of node ("element", "attribute", or "content")
        path (str): XPath-like path to the node ("." means root element)
        required (bool): Whether the parameter is required (defaults to True)
    """
    param_name: str
    node_type: str = "element"
    path: str = "."
    required: bool = True

@dataclass
class XMLTagSchema:
    """Schema definition for XML tool tags.
    
    Attributes:
        tag_name (str): Root tag name for the tool
        mappings (List[XMLNodeMapping]): Parameter mappings for the tag
        example (str, optional): Example showing tag usage
        
    Methods:
        add_mapping: Add a new parameter mapping to the schema
    """
    tag_name: str
    mappings: List[XMLNodeMapping] = field(default_factory=list)
    example: Optional[str] = None
    
    def add_mapping(self, param_name: str, node_type: str = "element", path: str = ".", required: bool = True) -> None:
        """Add a new node mapping to the schema.
        
        Args:
            param_name: Name of the function parameter
            node_type: Type of node ("element", "attribute", or "content")
            path: XPath-like path to the node
            required: Whether the parameter is required
        """
        self.mappings.append(XMLNodeMapping(
            param_name=param_name,
            node_type=node_type, 
            path=path,
            required=required
        ))
        logger.debug(f"Added XML mapping for parameter '{param_name}' with type '{node_type}' at path '{path}', required={required}")

@dataclass
class ToolSchema:
    """Container for tool schemas with type information.
    
    Attributes:
        schema_type (SchemaType): Type of schema (OpenAPI, XML, or Custom)
        schema (Dict[str, Any]): The actual schema definition
        xml_schema (XMLTagSchema, optional): XML-specific schema if applicable
    """
    schema_type: SchemaType
    schema: Dict[str, Any]
    xml_schema: Optional[XMLTagSchema] = None

@dataclass
class ToolResult:
    """Enhanced container for tool execution results."""
    tool_id: str
    execution_id: str
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "running"  # running, completed, failed, cancelled
    progress: float = 0.0  # 0.0 to 1.0
    result: Optional[Any] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    artifacts: List[str] = field(default_factory=list) # List of file paths or URLs

    def update_progress(self, progress: float, status: Optional[str] = None):
        """Update the progress and optionally the status."""
        self.progress = progress
        if status:
            self.status = status
        logger.debug(f"Tool {self.tool_id} (Exec ID: {self.execution_id}) progress: {self.progress}%, status: {self.status}")

    def complete(self, result: Optional[Any] = None, error: Optional[str] = None, warnings: Optional[List[str]] = None):
        """Mark the tool execution as completed or failed."""
        self.end_time = time.time()
        if error:
            self.status = "failed"
            self.error = error
            logger.error(f"Tool {self.tool_id} (Exec ID: {self.execution_id}) failed: {self.error}")
        else:
            self.status = "completed"
            self.result = result
            logger.info(f"Tool {self.tool_id} (Exec ID: {self.execution_id}) completed successfully.")
        if warnings:
            self.warnings.extend(warnings)

class Tool(ABC):
    """Abstract base class for all tools.
    
    Provides the foundation for implementing tools with schema registration
    and result handling capabilities.
    
    Attributes:
        _schemas (Dict[str, List[ToolSchema]]): Registered schemas for tool methods
        
    Methods:
        get_schemas: Get all registered tool schemas
        success_response: Create a successful result
        fail_response: Create a failed result
    """
    
    def __init__(self):
        """Initialize tool with empty schema registry."""
        self._schemas: Dict[str, List[ToolSchema]] = {}
        logger.debug(f"Initializing tool class: {self.__class__.__name__}")
        self._register_schemas()

    def _register_schemas(self):
        """Register schemas from all decorated methods."""
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, 'tool_schemas'):
                self._schemas[name] = method.tool_schemas
                logger.debug(f"Registered schemas for method '{name}' in {self.__class__.__name__}")

    def get_schemas(self) -> Dict[str, List[ToolSchema]]:
        """Get all registered tool schemas.
        
        Returns:
            Dict mapping method names to their schema definitions
        """
        return self._schemas

    def success_response(self, tool_id: str, execution_id: str, data: Union[Dict[str, Any], str]) -> ToolResult:
        """Create a successful tool result.
        
        Args:
            tool_id: The ID of the tool.
            execution_id: The ID of this execution instance.
            data: Result data (dictionary or string)
            
        Returns:
            ToolResult with status='completed' and formatted result
        """
        if isinstance(data, str):
            text = data
        else:
            text = json.dumps(data, indent=2)
        logger.debug(f"Created success response for {self.__class__.__name__}")
        return ToolResult(
            tool_id=tool_id,
            execution_id=execution_id,
            status="completed",
            result=text,
            progress=1.0,
            end_time=time.time()
        )

    def fail_response(self, tool_id: str, execution_id: str, msg: str) -> ToolResult:
        """Create a failed tool result.
        
        Args:
            tool_id: The ID of the tool.
            execution_id: The ID of this execution instance.
            msg: Error message describing the failure
            
        Returns:
            ToolResult with status='failed' and error message
        """
        logger.debug(f"Tool {self.__class__.__name__} returned failed result: {msg}")
        return ToolResult(
            tool_id=tool_id,
            execution_id=execution_id,
            status="failed",
            error=msg,
            progress=1.0, # Or should this be the progress it failed at? For now, 1.0
            end_time=time.time()
        )

def _add_schema(func, schema: ToolSchema):
    """Helper to add schema to a function."""
    if not hasattr(func, 'tool_schemas'):
        func.tool_schemas = []
    func.tool_schemas.append(schema)
    logger.debug(f"Added {schema.schema_type.value} schema to function {func.__name__}")
    return func

def openapi_schema(schema: Dict[str, Any]):
    """Decorator for OpenAPI schema tools."""
    def decorator(func):
        logger.debug(f"Applying OpenAPI schema to function {func.__name__}")
        return _add_schema(func, ToolSchema(
            schema_type=SchemaType.OPENAPI,
            schema=schema
        ))
    return decorator

def xml_schema(
    tag_name: str,
    mappings: List[Dict[str, Any]] = None,
    example: str = None
):
    """
    Decorator for XML schema tools with improved node mapping.
    
    Args:
        tag_name: Name of the root XML tag
        mappings: List of mapping definitions, each containing:
            - param_name: Name of the function parameter
            - node_type: "element", "attribute", or "content" 
            - path: Path to the node (default "." for root)
            - required: Whether the parameter is required (default True)
        example: Optional example showing how to use the XML tag
    
    Example:
        @xml_schema(
            tag_name="str-replace",
            mappings=[
                {"param_name": "file_path", "node_type": "attribute", "path": "."},
                {"param_name": "old_str", "node_type": "element", "path": "old_str"},
                {"param_name": "new_str", "node_type": "element", "path": "new_str"}
            ],
            example='''
            <str-replace file_path="path/to/file">
                <old_str>text to replace</old_str>
                <new_str>replacement text</new_str>
            </str-replace>
            '''
        )
    """
    def decorator(func):
        logger.debug(f"Applying XML schema with tag '{tag_name}' to function {func.__name__}")
        xml_schema = XMLTagSchema(tag_name=tag_name, example=example)
        
        # Add mappings
        if mappings:
            for mapping in mappings:
                xml_schema.add_mapping(
                    param_name=mapping["param_name"],
                    node_type=mapping.get("node_type", "element"),
                    path=mapping.get("path", "."),
                    required=mapping.get("required", True)
                )
                
        return _add_schema(func, ToolSchema(
            schema_type=SchemaType.XML,
            schema={},  # OpenAPI schema could be added here if needed
            xml_schema=xml_schema
        ))
    return decorator

def custom_schema(schema: Dict[str, Any]):
    """Decorator for custom schema tools."""
    def decorator(func):
        logger.debug(f"Applying custom schema to function {func.__name__}")
        return _add_schema(func, ToolSchema(
            schema_type=SchemaType.CUSTOM,
            schema=schema
        ))
    return decorator
