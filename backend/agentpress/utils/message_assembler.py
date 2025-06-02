import json
import time

# Try to import the project's logger, fall back to a basic one if not found during subtask execution.
try:
    from ...utils.logger import logger
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    logger.info("Falling back to standard logging for MessageAssembler.")

class MessageAssembler:
    def __init__(self):
        self.buffer = {}  # type: dict[str, str] # thread_id -> buffer string
        self.buffer_timestamps = {}  # type: dict[str, float] # thread_id -> last_update_timestamp

    def process_chunk(self, chunk: dict) -> dict | None:
        """
        Processes a chunk of a potentially fragmented JSON message.
        The 'content' field of the chunk is expected to be a string fragment of a larger JSON string.
        If a complete JSON message is assembled, it returns the parsed message (as a dict).
        Otherwise, it returns None.
        """
        thread_id = chunk.get('thread_id')

        content_str_fragment = chunk.get('content')

        if not thread_id:
            logger.warn("Chunk received without thread_id. Cannot assemble. Content: %s", str(content_str_fragment)[:200])
            if isinstance(content_str_fragment, str):
                try:
                    if (content_str_fragment.strip().startswith('{') and content_str_fragment.strip().endswith('}')) or \
                       (content_str_fragment.strip().startswith('[') and content_str_fragment.strip().endswith(']')):
                        return json.loads(content_str_fragment)
                    else:
                        logger.debug("Fragment without thread_id is not a complete JSON object/array.")
                        return None
                except json.JSONDecodeError:
                    logger.debug("Failed to parse content for chunk without thread_id: %s", str(content_str_fragment)[:200])
                    return None
            elif isinstance(content_str_fragment, dict):
                 return content_str_fragment
            return None

        if not isinstance(content_str_fragment, str):
            logger.warn(f"Chunk content for thread_id {thread_id} is not a string: {type(content_str_fragment)}. Value: {str(content_str_fragment)[:200]}. Skipping append.")
            return None

        if thread_id not in self.buffer:
            self.buffer[thread_id] = ""
            logger.debug(f"Initialized buffer for thread_id: {thread_id}")

        self.buffer[thread_id] += content_str_fragment
        self.buffer_timestamps[thread_id] = time.time()
        logger.debug(f"Appended to buffer for thread_id {thread_id}. Fragment: '{content_str_fragment[:100]}...'. Buffer size: {len(self.buffer[thread_id])}")

        current_buffer_content = self.buffer[thread_id]
        try:
            stripped_buffer = current_buffer_content.strip()
            if (stripped_buffer.startswith('{') and stripped_buffer.endswith('}')) or \
               (stripped_buffer.startswith('[') and stripped_buffer.endswith(']')):
                json_obj = json.loads(current_buffer_content)
                logger.info(f"Successfully parsed complete JSON message for thread_id: {thread_id}. Size: {len(current_buffer_content)}")
                self.buffer[thread_id] = ""
                if thread_id in self.buffer_timestamps:
                    del self.buffer_timestamps[thread_id]
                return json_obj
            else:
                logger.debug(f"Buffer for thread_id {thread_id} does not form a complete JSON object/array structure yet. Content: '{current_buffer_content[:200]}...'")
                return None
        except json.JSONDecodeError:
            logger.debug(f"JSONDecodeError for thread_id {thread_id}. Buffer content likely incomplete or malformed: '{current_buffer_content[:200]}...'")
            return None
        except Exception as e:
            logger.error(f"Unexpected error while parsing buffer for thread_id {thread_id}: {e}. Buffer: '{current_buffer_content[:200]}...'", exc_info=True)
            return None

    def cleanup_stale_buffers(self, max_age_seconds: int = 60):
        current_time = time.time()
        stale_threads = []
        for thread_id in list(self.buffer_timestamps.keys()):
            last_update_time = self.buffer_timestamps.get(thread_id)
            if last_update_time is None:
                if thread_id in self.buffer: del self.buffer[thread_id]
                if thread_id in self.buffer_timestamps: del self.buffer_timestamps[thread_id]
                continue
            if current_time - last_update_time > max_age_seconds:
                stale_threads.append(thread_id)
        cleaned_count = 0
        for thread_id in stale_threads:
            if thread_id in self.buffer:
                logger.warn(f"Cleaning up stale buffer for thread_id {thread_id}. Last update: {self.buffer_timestamps.get(thread_id)}. Buffer size: {len(self.buffer[thread_id])}")
                del self.buffer[thread_id]
                cleaned_count +=1
            if thread_id in self.buffer_timestamps:
                del self.buffer_timestamps[thread_id]
        if cleaned_count > 0:
            logger.info(f"Cleaned up stale buffers for {cleaned_count} threads.")
