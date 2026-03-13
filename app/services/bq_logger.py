import os
import json
import logging
from datetime import datetime, timezone
from google.cloud import bigquery
import streamlit as st
from services.telemetry import get_interaction_id

logger = logging.getLogger(__name__)

DATASET_ID = "odis_logs"
TABLE_ID = "agent_state_logs"

def log_agent_state_to_bq(user_input: str, agent_state: dict):
    """
    Logs the structured agent state to BigQuery with dedicated columns.
    Uses the existing interaction_id for tracing.
    """
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
         logger.warning("No Google Cloud Project found. Skipping BQ Agent State logging.")
         return

    try:
        interaction_id = get_interaction_id()
        username = st.session_state.get('username', 'unknown')
        
        client = bigquery.Client()
        table_ref = f"{client.project}.{DATASET_ID}.{TABLE_ID}"
        
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
        
        row = {
            "interaction_id": interaction_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "username": username,
            "last_user_message": user_input[:2000] if user_input else "",
            "last_agent_response": last_response[:10000] if last_response else "",
            "search_criteria": json.dumps(agent_state.get('search_criteria', {}), default=str, ensure_ascii=False),
            "briefing": str(agent_state.get('briefing', '')),
            "top_cities": json.dumps(agent_state.get('top_cities', []), default=str, ensure_ascii=False),
            "artifacts": json.dumps(agent_state.get('commune_artifacts', {}), default=str, ensure_ascii=False),
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
