import logging
import json
import uuid
import time
from datetime import datetime, timezone
import streamlit as st
from google.cloud import bigquery
import os

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
        st.session_state.interaction_id = str(uuid.uuid4())[:8]
    return st.session_state.interaction_id

def reset_interaction_id():
    """Generates a new interaction ID (e.g., on a new search)."""
    st.session_state.interaction_id = str(uuid.uuid4())[:8]
    return st.session_state.interaction_id

def log_event(event_name: str, payload: dict = None):
    """Logs a succint technical event to stderr (Cloud Logging)."""
    if payload is None:
        payload = {}
        
    username = st.session_state.get('username', 'unknown')
    
    event_data = {
        "event_name": event_name,
        "interaction_id": get_interaction_id(),
        "username": username,
        "data_summary": {k: str(v)[:100] for k, v in payload.items()} # Succint summary
    }
    
    _telemetry_logger.info(f"Telemetry Technical: {event_name}", extra={"json_payload": event_data})

def log_search_complete(criteria: dict, weights: dict, results: list, breakdown: dict = None, source_flow: str = 'classic'):
    """
    Consolidated logging of a search event directly to BigQuery search_events table.
    """
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        # Silently skip if no GCP project (local dev without config)
        return

    try:
        interaction_id = get_interaction_id()
        username = st.session_state.get('username', 'unknown')
        
        client = bigquery.Client()
        table_ref = f"{client.project}.odis_logs.search_events"
        
        row = {
            "interaction_id": interaction_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "username": username,
            "source_flow": source_flow,
            "search_criteria": json.dumps(criteria, default=str, ensure_ascii=False),
            "weights": json.dumps(weights, default=str, ensure_ascii=False),
            "top_results": json.dumps(results, default=str, ensure_ascii=False),
            "detailed_breakdown": json.dumps(breakdown or {}, default=str, ensure_ascii=False)
        }
        
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            _telemetry_logger.error(f"BQ Search Event Insert Error: {errors}")
        else:
            _telemetry_logger.info(f"Successfully logged search event to BQ (ID: {interaction_id})")
            
    except Exception as e:
        _telemetry_logger.error(f"Failed to log search event to BQ: {str(e)}")
