import os
import json
import logging
from datetime import datetime, timezone
try:
    import zoneinfo
except ImportError:
    from backports import zoneinfo
from google.cloud import bigquery
import streamlit as st
from typing import Any
from services.telemetry import get_interaction_id

logger = logging.getLogger(__name__)

DATASET_ID = "odis_logs"
TABLE_STATE_LOGS = "agent_state_logs"

def _safe_json_format(obj: Any) -> Any:
    """Recursively converts sets to lists for JSON serialization."""
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _safe_json_format(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_json_format(i) for i in obj]
    return obj

def log_agent_state_to_bq(user_input: str, agent_state: dict, interaction_id: str = None, username: str = None):
    """
    Logs the structured agent state to BigQuery with dedicated columns.
    Uses the existing interaction_id for tracing.
    """
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
         logger.warning("No Google Cloud Project found. Skipping BQ Agent State logging.")
         return

    try:
        try:
            if not interaction_id:
                interaction_id = get_interaction_id()
            if not username:
                username = st.session_state.get('username', 'unknown')
        except:
            interaction_id = interaction_id or "unknown"
            username = username or "unknown"
        
        paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
        timestamp_paris = datetime.now(paris_tz).isoformat()
        
        client = bigquery.Client()
        table_ref = f"{client.project}.{DATASET_ID}.{TABLE_STATE_LOGS}"
        
        messages = agent_state.get('messages', [])
        # Extract last agent response
        last_response = ""
        if messages:
             for msg in reversed(messages):
                  if msg.get('role') == 'assistant':
                       last_response = msg.get('content', '')
                       break

        usage = agent_state.get('usage', {})
        cost = usage.get('cost_usd', 0.0) if isinstance(usage, dict) else getattr(usage, 'cost_usd', 0.0)
        
        # Map new search_results to old columns for BQ schema compatibility
        sr = agent_state.get('search_results')
        top_cities_data = []
        artifacts_data = {}
        if sr:
             # Handle both SearchResultsData object and dict
             results = getattr(sr, 'results', []) if not isinstance(sr, dict) else sr.get('results', [])
             for r in results:
                  if hasattr(r, 'model_dump'):
                       top_cities_data.append(r.model_dump(exclude={'geometry', 'centroid', 'expert_analysis'}))
                       artifacts_data[r.codgeo] = r.expert_analysis
                  elif isinstance(r, dict):
                       top_cities_data.append({k: v for k, v in r.items() if k not in ['geometry', 'centroid', 'expert_analysis']})
                       artifacts_data[r.get('codgeo', '')] = r.get('expert_analysis', {})

        row = {
            "interaction_id": interaction_id,
            "timestamp": timestamp_paris,
            "username": username,
            "last_user_message": user_input[:2000] if user_input else "",
            "last_agent_response": last_response[:10000] if last_response else "",
            "search_criteria": json.dumps(_safe_json_format(agent_state.get('search_criteria', {})), default=str, ensure_ascii=False),
            "briefing": str(agent_state.get('odis_brief', '')),
            "top_cities": json.dumps(_safe_json_format(top_cities_data), default=str, ensure_ascii=False),
            "artifacts": json.dumps(_safe_json_format(artifacts_data), default=str, ensure_ascii=False),
            "execution_mode": str(agent_state.get('execution_mode', 'full_analysis')),
            "cost_usd": float(cost)
        }
        
        # Fire to BigQuery
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(f"BQ Agent State Insert Errors: {errors}")
        else:
            logger.info("Successfully logged Agent State to BigQuery with granular fields.")
            
    except Exception as e:
        logger.error(f"Failed to log agent state to BQ: {str(e)}")
