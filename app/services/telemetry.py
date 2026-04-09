import logging
import json
import uuid
import time
from datetime import datetime, timezone
try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo 
import streamlit as st
from google.cloud import bigquery
import os
import logging
from core.models import SearchCriterias, SearchResultsData

# Use root logger for critical visibility in background threads
logger = logging.getLogger(__name__)

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

def _safe_json_format(obj: Any) -> Any:
    """Recursively converts sets to lists for JSON serialization."""
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _safe_json_format(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_json_format(i) for i in obj]
    return obj

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
        
        try:
            paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
            timestamp_str = datetime.now(paris_tz).isoformat()
        except:
            timestamp_str = datetime.now().isoformat()
        
        # 1. Prepare Criteria & Weights
        # Handle both Pydantic models and dicts
        full_config = config.model_dump() if hasattr(config, "model_dump") else config
        if not isinstance(full_config, dict):
            full_config = {}

        criteria_keys = ['commune_actuelle', 'loc_search_area', 'situation_famille', 'nb_enfants', 'besoin_emploi', 'besoin_sante', 'inc_services_add_selection', 'freq_retour', 'active_criteria']
        search_criteria = {k: full_config.get(k) for k in criteria_keys if k in full_config}
        weights = {k: v for k, v in full_config.items() if k.startswith('poids_')}
        
        # 2. Prepare Results Summary
        top_5_results = []
        top_5_breakdown = {}
        
        # Handle search_results as model or dict
        results_list = search_results.results if hasattr(search_results, "results") else search_results.get("results", [])
        
        for commune in results_list:
            # Extract basic data
            c_codgeo = getattr(commune, "codgeo", commune.get("codgeo") if isinstance(commune, dict) else None)
            c_name = getattr(commune, "name", commune.get("name") if isinstance(commune, dict) else None)
            c_score = getattr(commune, "global_score", commune.get("global_score") if isinstance(commune, dict) else 0.0)
            c_scores = getattr(commune, "scores", commune.get("scores", {}) if isinstance(commune, dict) else {})
            c_pitch = getattr(commune, "scorer_pitch", commune.get("scorer_pitch", "") if isinstance(commune, dict) else "")
            c_expert = getattr(commune, "expert_analysis", commune.get("expert_analysis", {}) if isinstance(commune, dict) else {})

            top_5_results.append({
                "codgeo": c_codgeo,
                "libgeo": c_name,
                "score": c_score
            })

            commune_scores = {}
            for cat, items in c_scores.items():
                commune_scores[cat] = [s.model_dump() if hasattr(s, 'model_dump') else s for s in items]

            top_5_breakdown[str(c_codgeo)] = {
                "libgeo": c_name,
                "scores": commune_scores,
                "scorer_pitch": c_pitch,
                "expert_analysis": c_expert
            }
        
        # 3. BigQuery Insert
        client = bigquery.Client()
        table_ref = f"{client.project}.odis_logs.search_events"
        
        # Ensure sets are converted to lists BEFORE json.dumps
        row = {
            "interaction_id": interaction_id,
            "timestamp": timestamp_str,
            "username": username,
            "source_flow": source_flow,
            "search_criteria": json.dumps(_safe_json_format(search_criteria), default=str, ensure_ascii=False),
            "weights": json.dumps(_safe_json_format(weights), default=str, ensure_ascii=False),
            "top_results": json.dumps(_safe_json_format(top_5_results), default=str, ensure_ascii=False),
            "detailed_breakdown": json.dumps(_safe_json_format(top_5_breakdown), default=str, ensure_ascii=False)
        }
        
        errors = client.insert_rows_json(table_ref, [row], timeout=15)
        if errors:
            logger.error(f"❌ [TELEMETRY] BQ Insert Error for {interaction_id}: {errors}")
        else:
            _telemetry_logger.info(f"✅ Successfully logged search event to BQ (ID: {interaction_id})")
            
    except Exception as e:
        logger.error(f"❌ [TELEMETRY] Failed to log search event to BQ: {str(e)}", exc_info=True)
