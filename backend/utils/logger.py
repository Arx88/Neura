"""
Centralized logging configuration for AgentPress.

This module provides a unified logging interface with:
- Structured JSON logging for better parsing
- Log levels for different environments
- Correlation IDs for request tracing
- Contextual information for debugging
"""

import logging
import json
import sys
import os
from datetime import datetime, timezone
from contextvars import ContextVar
from functools import wraps
import traceback
from logging.handlers import RotatingFileHandler

from .config import config, EnvMode

# --- Path Definitions ---
# Assuming this script is in backend/utils/logger.py
# PROJECT_ROOT should point to the directory containing 'backend', 'frontend', 'LOG'
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
GLOBAL_LOG_DIR_NAME = "TEMP_LOGS" # Name of the directory at project root
GLOBAL_LOG_DIR_PATH = "/app/TEMP_LOGS"
TEMP_LOG_FILENAME = "errors.log"
TEMP_LOG_FILE_PATH = os.path.join(GLOBAL_LOG_DIR_PATH, TEMP_LOG_FILENAME)

APP_SPECIFIC_LOG_DIR_NAME = "logs" # This is for the ./logs/agentpress_date.log
# --- End Path Definitions ---

# Context variable for request correlation ID
request_id: ContextVar[str] = ContextVar('request_id', default='')

# Global flag to ensure TEMP_LOG is truncated only once per session by the BACKEND logger
_temp_log_truncated_this_session = False

class JSONFormatter(logging.Formatter):
    """Custom JSON formatter for structured logging."""
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON with contextual information."""
        log_data = {
            'timestamp': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
            'level': record.levelname,
            'message': record.getMessage(),
            'module': record.module,
            'function': record.funcName,
            'line': record.lineno,
            'request_id': request_id.get(),
            'thread_id': getattr(record, 'thread_id', None),
            'correlation_id': getattr(record, 'correlation_id', None)
        }
        
        # Add extra fields if present
        if hasattr(record, 'extra'):
            log_data.update(record.extra)
            
        # Add exception info if present
        if record.exc_info:
            log_data['exception'] = {
                'type': str(record.exc_info[0].__name__),
                'message': str(record.exc_info[1]),
                'traceback': traceback.format_exception(*record.exc_info)
            }
            
        return json.dumps(log_data)

def setup_logger(name: str = 'BACKEND') -> logging.Logger:
    """
    Set up a centralized logger with console, app-specific rotating file,
    and a global TEMP_LOG file handler.
    
    Args:
        name: The name of the logger (e.g., 'BACKEND', 'WORKER').
        
    Returns:
        logging.Logger: Configured logger instance.
    """
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Set lowest level for logger, handlers control their own levels
    
    # --- App-specific Rotating File Handler ---
    app_specific_log_dir_path = "/app/runtime_logs/" # Explicit absolute path
    app_log_file_setup_success = False
    try:
        os.makedirs(app_specific_log_dir_path, exist_ok=True)
        # Check if directory is writable
        if not os.access(app_specific_log_dir_path, os.W_OK):
            # If not writable, raise an error to be caught by the except block
            raise PermissionError(f"Directory {app_specific_log_dir_path} exists but is not writable by the application.")

        app_log_file = os.path.join(app_specific_log_dir_path, f'{name}_{datetime.now().strftime("%Y%m%d_%H%M%S_%f")}.log')
        
        rotating_file_handler = RotatingFileHandler(
            app_log_file,
            maxBytes=10*1024*1024,
            backupCount=0, 
            encoding='utf-8'
        )
        rotating_file_handler.setLevel(logging.DEBUG)
        
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s - %(message)s'
        )
        rotating_file_handler.setFormatter(file_formatter)
        logger.addHandler(rotating_file_handler)
        # This print is useful for seeing if setup was attempted. Keep it.
        print(f"Attempted to add app-specific rotating file handler for: {app_log_file}")
        app_log_file_setup_success = True # Mark as success
    except Exception as e:
        # IMPORTANT: Log this error using the logger itself, so it appears in the JSON console output if possible.
        # This assumes the console handler might already be added or will be added.
        # As a fallback, the print to stderr is kept.
        error_message = f"CRITICAL_ERROR_LOGGER_SETUP: Failed to set up app-specific file handler for {name} at {app_specific_log_dir_path}. Error: {type(e).__name__} - {str(e)}"
        # Check if logger has handlers, particularly the console one we expect for JSON output
        if logger.hasHandlers(): # Check if any handlers exist
            # Attempt to find a console handler, or just log if any handler exists
            console_handler_exists = any(isinstance(h, logging.StreamHandler) for h in logger.handlers)
            if console_handler_exists :
                 logger.error(error_message, exc_info=False) # Set exc_info=False to avoid huge traceback in this specific log, stderr has it.
            else:
                # If no console handler yet, print to stdout as a structured-like message
                print(json.dumps({
                    'timestamp': datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                    'level': 'ERROR',
                    'message': error_message,
                    'module': __name__, # or record.module if available
                    'function': 'setup_logger_app_specific_handler_exception',
                    'type': str(type(e).__name__),
                    'detail': str(e)
                }))
        else:
            # Fallback if logger has no handlers at all yet (e.g. console handler failed too)
             print(f"Fallback print: {error_message}", file=sys.stderr)
        print(f"Original stderr print: Error setting up app-specific rotating file handler: {e}", file=sys.stderr) # Keep original stderr print

    # After the try-except for app_specific_log_dir_path, add a log message indicating success or failure
    if app_log_file_setup_success:
        logger.info(f"Successfully set up app-specific file logging to: {app_log_file}")
    else:
        logger.warning(f"App-specific file logging setup FAILED for logger '{name}'. Check previous errors for details.")

    # --- Global TEMP_LOG File Handler (PROJECT_ROOT/LOG/TEMP_LOG) ---
    try:
        os.makedirs(GLOBAL_LOG_DIR_PATH, exist_ok=True)

        global _temp_log_truncated_this_session
        log_mode = 'a'  # Default to append mode

        current_temp_log_file_path = ''
        if name == 'FRONTEND':
            current_temp_log_file_path = os.path.join(GLOBAL_LOG_DIR_PATH, "frontend_errors.log")
            # Frontend logs will always append. If truncation is needed, it would be specific.
            log_mode = 'a'
        else: # BACKEND, WORKER, etc.
            current_temp_log_file_path = TEMP_LOG_FILE_PATH # /app/TEMP_LOGS/errors.log
            if name == 'BACKEND' and not _temp_log_truncated_this_session:
                log_mode = 'w'  # Truncate if it's the BACKEND logger and not yet truncated
                _temp_log_truncated_this_session = True
                print(f"Truncating {current_temp_log_file_path} for new session by {name} logger.")

        # Common handler setup
        temp_log_handler = logging.FileHandler(current_temp_log_file_path, mode=log_mode, encoding='utf-8')
        temp_log_handler.setLevel(logging.ERROR) # Only ERROR level and above
        
        temp_log_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] [%(name)s] %(message)s (%(filename)s:%(lineno)d - %(funcName)s)'
        )
        temp_log_handler.setFormatter(temp_log_formatter)
        logger.addHandler(temp_log_handler)
        print(f"Added TEMP_LOG file handler for {name} at: {current_temp_log_file_path}")
    except Exception as e:
        print(f"Error setting up TEMP_LOG handler for {name}: {e}", file=sys.stderr)

    # --- Console Handler (JSON Formatter) ---
    try:
        console_handler = logging.StreamHandler(sys.stdout)
        if config.ENV_MODE == EnvMode.PRODUCTION:
            console_handler.setLevel(logging.WARNING)
        else:
            console_handler.setLevel(logging.DEBUG)
        
        console_formatter = JSONFormatter()
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
        # Avoid logging with the logger instance itself during setup if it's not fully configured
        print(f"Added console handler with level: {logging.getLevelName(console_handler.level)}")
    except Exception as e:
        print(f"Error setting up console handler: {e}", file=sys.stderr)
    
    # # Example test logging (can be uncommented for quick verification)
    # logger.debug("Logger setup complete - DEBUG test")
    # logger.info("Logger setup complete - INFO test")
    # logger.warning("Logger setup complete - WARNING test")
    
    return logger

# Create default logger instance
logger = setup_logger() 