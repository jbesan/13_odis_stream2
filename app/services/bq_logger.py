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
        focus_city = agent_state.get('focus_city', {})
        usage = agent_state.get('usage', {})
        
        # Safely extract
        city_name = focus_city.get('name', '') if isinstance(focus_city, dict) else getattr(focus_city, 'name', '')
        cost = usage.get('cost_usd', 0.0) if isinstance(usage, dict) else getattr(usage, 'cost_usd', 0.0)
        
        row = {
            "interaction_id": interaction_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "username": username,
            "user_input": user_input[:1000] if user_input else "",
            "criteria_hash": str(agent_state.get('criteria_hash', '')),
            "focus_city": str(city_name),
            "cost_usd": float(cost),
            "agent_messages_json": json.dumps(messages),
            "full_state_json": json.dumps(agent_state, default=str)
        }
        
        # Fire to BigQuery
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(f"BQ Agent State Insert Errors: {errors}")
        else:
            logger.info("Successfully logged Agent State to BigQuery.")
            
    except Exception as e:
        logger.error(f"Failed to log agent state to BQ: {str(e)}")
