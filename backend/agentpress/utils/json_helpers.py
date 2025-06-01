"""
JSON helper utilities for handling both legacy (string) and new (dict/list) formats.

These utilities help with the transition from storing JSON as strings to storing
them as proper JSONB objects in the database.
"""

import json
import logging # Added import
import re
from typing import Any, Union, Dict, List

logger = logging.getLogger(__name__)


def ensure_dict(value: Union[str, Dict[str, Any], None], default: Dict[str, Any] = None) -> Dict[str, Any]:
    """
    Ensure a value is a dictionary.
    
    Handles:
    - None -> returns default or {}
    - Dict -> returns as-is
    - JSON string -> parses and returns dict
    - Other -> returns default or {}
    
    Args:
        value: The value to ensure is a dict
        default: Default value if conversion fails
        
    Returns:
        A dictionary
    """
    if default is None:
        default = {}
        
    if value is None:
        return default
        
    if isinstance(value, dict):
        return value
        
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
            return default
        except (json.JSONDecodeError, TypeError):
            return default
            
    return default


def ensure_list(value: Union[str, List[Any], None], default: List[Any] = None) -> List[Any]:
    """
    Ensure a value is a list.
    
    Handles:
    - None -> returns default or []
    - List -> returns as-is
    - JSON string -> parses and returns list
    - Other -> returns default or []
    
    Args:
        value: The value to ensure is a list
        default: Default value if conversion fails
        
    Returns:
        A list
    """
    if default is None:
        default = []
        
    if value is None:
        return default
        
    if isinstance(value, list):
        return value
        
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
            return default
        except (json.JSONDecodeError, TypeError):
            return default
            
    return default


def safe_json_parse(value: Union[str, Dict, List, Any], default: Any = None) -> Any:
    """
    Safely parse a value that might be JSON string or already parsed.
    
    This handles the transition period where some data might be stored as
    JSON strings (old format) and some as proper objects (new format).
    
    Args:
        value: The value to parse
        default: Default value if parsing fails
        
    Returns:
        Parsed value or default
    """
    if value is None:
        return default
        
    # If it's already a dict or list, return as-is
    if isinstance(value, (dict, list)):
        return value
        
    # If it's a string, try to parse it
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            # If it's not valid JSON, return the string itself
            return value
            
    # For any other type, return as-is
    return value


def to_json_string(value: Any) -> str:
    """
    Convert a value to a JSON string if needed.
    
    This is used for backwards compatibility when yielding data that
    expects JSON strings.
    
    Args:
        value: The value to convert
        
    Returns:
        JSON string representation
    """
    if isinstance(value, str):
        # If it's already a string, check if it's valid JSON
        try:
            json.loads(value)
            return value  # It's already a JSON string
        except (json.JSONDecodeError, TypeError):
            # It's a plain string, encode it as JSON
            return json.dumps(value)
    
    # For all other types, convert to JSON
    return json.dumps(value)


def format_for_yield(message_object: Dict[str, Any]) -> Dict[str, Any]:
    """
    Format a message object for yielding, ensuring content and metadata are JSON strings.
    
    This maintains backward compatibility with clients expecting JSON strings
    while the database now stores proper objects.
    
    Args:
        message_object: The message object from the database
        
    Returns:
        Message object with content and metadata as JSON strings
    """
    if not message_object:
        return message_object
        
    # Create a copy to avoid modifying the original
    formatted = message_object.copy()
    
    # Ensure content is a JSON string
    if 'content' in formatted and not isinstance(formatted['content'], str):
        formatted['content'] = json.dumps(formatted['content'])
        
    # Ensure metadata is a JSON string
    if 'metadata' in formatted and not isinstance(formatted['metadata'], str):
        formatted['metadata'] = json.dumps(formatted['metadata'])
        
    return formatted

def extract_json_from_response(response_text: str) -> Union[Dict, List, None]:
    """
    Extrae un bloque de código JSON de una cadena de texto.
    Busca el primer objeto JSON completo y balanceado.
    También intenta manejar bloques JSON envueltos en markdown ```json ... ```.
    """
    if not response_text:
        logger.warning("Respuesta de texto vacía, no se puede extraer JSON.")
        return None

    # Intento 1: Extraer de un bloque de código Markdown ```json ... ```
    match_markdown_json = re.search(r"```json\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```", response_text, re.DOTALL)
    if match_markdown_json:
        json_str_markdown = match_markdown_json.group(1)
        try:
            # logger.debug(f"JSON extraído de bloque Markdown: {json_str_markdown[:200]}...")
            return json.loads(json_str_markdown)
        except json.JSONDecodeError as e:
            logger.warning(f"Error al decodificar JSON de bloque Markdown: {e}. Contenido: {json_str_markdown[:500]}...")
            # Continuar si este método específico falla, para probar otros.

    # Intento 2: Encontrar el primer '{' o '[' y buscar su delimitador de cierre correspondiente.
    # Esto es más robusto para extraer el primer objeto/array JSON completo.

    # Determinar si buscamos un objeto o un array basado en el primer carácter relevante
    first_brace = response_text.find('{')
    first_bracket = response_text.find('[')

    json_start_index = -1
    start_char = ''
    end_char = ''

    if first_brace != -1 and (first_bracket == -1 or first_brace < first_bracket):
        json_start_index = first_brace
        start_char = '{'
        end_char = '}'
    elif first_bracket != -1 and (first_brace == -1 or first_bracket < first_brace):
        json_start_index = first_bracket
        start_char = '['
        end_char = ']'
    else:
        logger.warning("No se encontró '{' o '[' inicial para extraer JSON de la respuesta.")
        return None

    open_delimiters = 0
    json_end_index = -1

    for i in range(json_start_index, len(response_text)):
        char = response_text[i]
        if char == start_char:
            open_delimiters += 1
        elif char == end_char:
            open_delimiters -= 1

        if open_delimiters == 0:
            json_end_index = i + 1
            break

    if json_end_index != -1:
        json_str_balanced = response_text[json_start_index:json_end_index]
        try:
            # logger.debug(f"JSON extraído (delimitadores balanceados '{start_char}{end_char}'): {json_str_balanced[:200]}...")
            return json.loads(json_str_balanced)
        except json.JSONDecodeError as e:
            logger.error(f"Error al decodificar JSON extraído con delimitadores balanceados ('{start_char}{end_char}'): {e}\nFragmento: {json_str_balanced[:500]}...\nInicio de respuesta: {response_text[:1000]}...")
            # Si la extracción balanceada falla, no intentar métodos más simples que podrían ser incorrectos.
            return None
    else:
        logger.warning(f"No se encontró un objeto/array JSON balanceado ('{start_char}{end_char}') comenzando desde el índice {json_start_index} en la respuesta: {response_text[:500]}...")
        return None