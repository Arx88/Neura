import json
from typing import Dict, Any, List # For type hinting

from agentpress.tool import Tool, openapi_schema, xml_schema # ToolResult removed
from agent.tools.data_providers.LinkedinProvider import LinkedinProvider
from agent.tools.data_providers.YahooFinanceProvider import YahooFinanceProvider
from agent.tools.data_providers.AmazonProvider import AmazonProvider
import logging # Added for logging
from agent.tools.data_providers.ZillowProvider import ZillowProvider
from agent.tools.data_providers.TwitterProvider import TwitterProvider

# Custom Exceptions
class DataProvidersToolError(Exception):
    """Base exception for DataProvidersTool errors."""
    pass

class DataProviderNotFoundError(DataProvidersToolError):
    """Exception for when a data provider is not found."""
    pass

class EndpointNotFoundError(DataProvidersToolError):
    """Exception for when an endpoint is not found for a provider."""
    pass

class DataProvidersTool(Tool):
    """Tool for making requests to various data providers."""

    def __init__(self):
        super().__init__()

        self.register_data_providers = {
            "linkedin": LinkedinProvider(),
            "yahoo_finance": YahooFinanceProvider(),
            "amazon": AmazonProvider(),
            "zillow": ZillowProvider(),
            "twitter": TwitterProvider()
        }

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "get_data_provider_endpoints",
            "description": "Get available endpoints for a specific data provider",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "The name of the data provider (e.g., 'linkedin', 'twitter', 'zillow', 'amazon', 'yahoo_finance')"
                    }
                },
                "required": ["service_name"]
            }
        }
    })
    @xml_schema(
        tag_name="get-data-provider-endpoints",
        mappings=[
            {"param_name": "service_name", "node_type": "attribute", "path": "."}
        ],
        example='''
<!-- 
The get-data-provider-endpoints tool returns available endpoints for a specific data provider.
Use this tool when you need to discover what endpoints are available.
-->

<!-- Example to get LinkedIn API endpoints -->
<get-data-provider-endpoints service_name="linkedin">
</get-data-provider-endpoints>
        '''
    )
    async def get_data_provider_endpoints(
        self,
        service_name: str
    ) -> Dict[str, Any]: # Return dict on success
        """
        Get available endpoints for a specific data provider.
        
        Parameters:
            service_name: The name of the data provider (e.g., 'linkedin')
        Returns:
            A dictionary of available endpoints.
        Raises:
            ValueError: If service_name is not provided.
            DataProviderNotFoundError: If the specified service_name is not found.
            DataProvidersToolError: For other errors.
        """
        if not service_name or not isinstance(service_name, str):
            raise ValueError("Data provider name (service_name) is required and must be a string.")

        if service_name not in self.register_data_providers:
            available_providers = list(self.register_data_providers.keys())
            raise DataProviderNotFoundError(f"Data provider '{service_name}' not found. Available: {available_providers}")

        try:
            # Assuming get_endpoints() itself returns a dict or raises an error
            endpoints = self.register_data_providers[service_name].get_endpoints()
            return endpoints # Return raw data
            
        except Exception as e:
            logging.error(f"Error getting endpoints for '{service_name}': {str(e)}", exc_info=True)
            raise DataProvidersToolError(f"Error getting data provider endpoints for '{service_name}': {str(e)}") from e

    @openapi_schema({
        "type": "function",
        "function": {
            "name": "execute_data_provider_call",
            "description": "Execute a call to a specific data provider endpoint",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_name": {
                        "type": "string",
                        "description": "The name of the API service (e.g., 'linkedin')"
                    },
                    "route": {
                        "type": "string",
                        "description": "The key of the endpoint to call"
                    },
                    "payload": {
                        "type": "object",
                        "description": "The payload to send with the API call"
                    }
                },
                "required": ["service_name", "route"]
            }
        }
    })
    @xml_schema(
        tag_name="execute-data-provider-call",
        mappings=[
            {"param_name": "service_name", "node_type": "attribute", "path": "service_name"},
            {"param_name": "route", "node_type": "attribute", "path": "route"},
            {"param_name": "payload", "node_type": "content", "path": "."}
        ],
        example='''
        <!-- 
        The execute-data-provider-call tool makes a request to a specific data provider endpoint.
        Use this tool when you need to call an data provider endpoint with specific parameters.
        The route must be a valid endpoint key obtained from get-data-provider-endpoints tool!!
        -->
        
        <!-- Example to call linkedIn service with the specific route person -->
        <execute-data-provider-call service_name="linkedin" route="person">
            {"link": "https://www.linkedin.com/in/johndoe/"}
        </execute-data-provider-call>
        '''
    )
    async def execute_data_provider_call(
        self,
        service_name: str,
        route: str,
        payload: str # This is a JSON string, will be parsed.
    ) -> Any: # Return type can be anything the specific provider endpoint returns
        """
        Execute a call to a specific data provider endpoint.
        
        Parameters:
            service_name: The name of the data provider (e.g., 'linkedin').
            route: The key of the endpoint to call.
            payload: A JSON string representing the payload for the API call.
        Returns:
            The result from the data provider's endpoint call.
        Raises:
            ValueError: For missing or invalid parameters, or invalid JSON payload.
            DataProviderNotFoundError: If service_name is not found.
            EndpointNotFoundError: If route is not found for the service.
            DataProvidersToolError: For other errors during execution.
        """
        if not service_name or not isinstance(service_name, str):
            raise ValueError("service_name is required and must be a string.")
        if not route or not isinstance(route, str):
            raise ValueError("route is required and must be a string.")
        if payload is None or not isinstance(payload, str): # Payload must be a string to be parsed
            raise ValueError("payload is required and must be a JSON string.")

        try:
            parsed_payload = json.loads(payload)
        except json.JSONDecodeError as e_json:
            raise ValueError(f"Invalid JSON payload provided: {str(e_json)}") from e_json

        if service_name not in self.register_data_providers:
            available_providers = list(self.register_data_providers.keys())
            raise DataProviderNotFoundError(f"Data provider '{service_name}' not found. Available: {available_providers}")

        data_provider = self.register_data_providers[service_name]

        # The "YOU FUCKING IDIOT!" message was unprofessional. Changed to a standard error.
        if route == service_name:
            raise ValueError(f"Invalid route: route ('{route}') cannot be the same as service_name ('{service_name}').")

        available_endpoints = data_provider.get_endpoints()
        if route not in available_endpoints:
            raise EndpointNotFoundError(f"Endpoint '{route}' not found in '{service_name}' data provider. Available endpoints: {list(available_endpoints.keys())}")

        try:
            # Assuming call_endpoint returns data directly or raises an error
            result = data_provider.call_endpoint(route, parsed_payload)
            return result # Return raw data
        except Exception as e_call: # Catch errors from the specific provider's call_endpoint
            logging.error(f"Error calling endpoint '{route}' for service '{service_name}': {str(e_call)}", exc_info=True)
            # It might be useful to wrap provider-specific errors if they are not already custom exceptions.
            raise DataProvidersToolError(f"Error during call to '{service_name}' endpoint '{route}': {str(e_call)}") from e_call
