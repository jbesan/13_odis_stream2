import logging
import json
import uuid
import time
from datetime import datetime, timezone
# try:
#     import zoneinfo
# except ImportError:
#     from backports import zoneinfo # For Python < 3.9 if needed, though 1.55+ streamlit usually means 3.9+
import streamlit as st
from google.cloud import bigquery
import os
from core.models import SearchCriterias, SearchResultsData

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

def log_event(event_name: str, payload: dict = None, interaction_id: str = None, username: str = None):
    """Logs a succint technical event to stderr (Cloud Logging)."""
    if payload is None:
        payload = {}
        
    try:
        if not username:
            username = st.session_state.get('username', 'unknown')
        if not interaction_id:
            interaction_id = get_interaction_id()
    except:
        username = username or 'unknown'
        interaction_id = interaction_id or 'unknown'
    
    event_data = {
        "event_name": event_name,
        "interaction_id": interaction_id,
        "username": username,
        "data_summary": {k: str(v)[:100] for k, v in payload.items()} # Succint summary
    }
    
    _telemetry_logger.info(f"Telemetry Technical: {event_name}", extra={"json_payload": event_data})

def log_search_complete(config: SearchCriterias, search_results: SearchResultsData, source_flow: str = 'classic', interaction_id: str = None, username: str = None):
    """
    Consolidated logging of a search event directly to BigQuery search_events table.
    """
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        return

    try:
        # Resolve metadata
        try:
            if not interaction_id:
                interaction_id = get_interaction_id()
            if not username:
                username = st.session_state.get('username', 'unknown')
        except:
             interaction_id = interaction_id or "unknown"
             username = username or "unknown"
        
        # paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
        # timestamp_paris = datetime.now(paris_tz).isoformat()
        
        # 1. Prepare Criteria & Weights
        _telemetry_logger.info(f"🔍 [TELEMETRY] config type: {type(config)}")
        if isinstance(config, dict):
            _telemetry_logger.warning(f"⚠️ [TELEMETRY] config is a dict, not a model! Keys: {list(config.keys())}")
        
        full_config = config.model_dump()
        criteria_keys = ['commune_actuelle', 'loc_search_area', 'situation_famille', 'nb_enfants', 'besoin_emploi', 'besoin_sante', 'inc_services_add_selection', 'freq_retour', 'active_criteria']
        search_criteria = {k: full_config.get(k) for k in criteria_keys if k in full_config}
        weights = {k: v for k, v in full_config.items() if k.startswith('poids_')}
        
        # 2. Prepare Results Summary
        top_5_results = []
        top_5_breakdown = {}
        for commune in search_results.results:
            top_5_results.append({
                "codgeo": commune.codgeo,
                "libgeo": commune.name,
                "score": commune.global_score
            })
            # Log score items type
            commune_scores = {}
            for cat, items in commune.scores.items():
                if items and not hasattr(items[0], 'model_dump'):
                    _telemetry_logger.warning(f"⚠️ [TELEMETRY] Score items in {cat} are not models! Type: {type(items[0])}")
                commune_scores[cat] = [s.model_dump() if hasattr(s, 'model_dump') else s for s in items]

            top_5_breakdown[str(commune.codgeo)] = {
                "libgeo": commune.name,
                "scores": commune_scores,
                "scorer_pitch": commune.scorer_pitch,
                "expert_analysis": commune.expert_analysis
            }
        
        # 3. BigQuery Insert
        client = bigquery.Client()
        table_ref = f"{client.project}.odis_logs.search_events"
        
        row = {
            "interaction_id": interaction_id,
            "timestamp": datetime.now().isoformat(),
            "username": username,
            "source_flow": source_flow,
            "search_criteria": json.dumps(search_criteria, default=str, ensure_ascii=False),
            "weights": json.dumps(weights, default=str, ensure_ascii=False),
            "top_results": json.dumps(top_5_results, default=str, ensure_ascii=False),
            "detailed_breakdown": json.dumps(top_5_breakdown, default=str, ensure_ascii=False)
        }
        
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            _telemetry_logger.error(f"BQ Search Event Insert Error: {errors}")
        else:
            _telemetry_logger.info(f"Successfully logged search event to BQ (ID: {interaction_id})")
            
    except Exception as e:
        _telemetry_logger.error(f"Failed to log search event to BQ: {str(e)}")
