import logging
import json
import uuid
import time
import streamlit as st

class JsonFormatter(logging.Formatter):
    """Formatter that outputs JSON strings for Google Cloud Logging."""
    def format(self, record):
        log_record = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": time.time()
        }
        if hasattr(record, "json_payload"):
            log_record.update(record.json_payload)
        return json.dumps(log_record)

# Singleton logger setup for telemetry
_telemetry_logger = logging.getLogger("odis_telemetry")
if not _telemetry_logger.handlers:
    _telemetry_logger.setLevel(logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    _telemetry_logger.addHandler(handler)

def get_interaction_id():
    """Retrieves or generates a unique interaction ID for the current session state."""
    if "interaction_id" not in st.session_state:
        st.session_state.interaction_id = str(uuid.uuid4())
    return st.session_state.interaction_id

def reset_interaction_id():
    """Generates a new interaction ID (e.g., on a new search)."""
    st.session_state.interaction_id = str(uuid.uuid4())
    return st.session_state.interaction_id

def log_event(event_name: str, payload: dict = None):
    """
    Logs a structured event intended for Cloud Logging -> BigQuery.
    
    Args:
        event_name (str): Identifier for the event (e.g., 'RUN_SEARCH', 'SEARCH_RESULTS_RETURNED').
        payload (dict): Additional context data for the event.
    """
    if payload is None:
        payload = {}
        
    username = st.session_state.get('username', 'unknown') # If auth provides username
    # try to get from secrets login if saved
    
    event_data = {
        "event_name": event_name,
        "interaction_id": get_interaction_id(),
        "username": username,
        "data": payload
    }
    
    # Pass as extra so the formatter can pick it up
    _telemetry_logger.info(f"Telemetry Event: {event_name}", extra={"json_payload": event_data})
