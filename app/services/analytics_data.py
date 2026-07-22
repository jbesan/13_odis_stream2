import os
import logging
import pandas as pd
import streamlit as st
from google.cloud import bigquery

logger = logging.getLogger("services.analytics_data")
dataset_id = "odis_logs"


@st.cache_resource(ttl=300)
def get_bq_client():
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        return None
    try:
        return bigquery.Client()
    except Exception as e:
        logger.error(f"Failed to initialize BQ client: {e}")
        return None


@st.cache_data
def fetch_analytics_data(_client, days: int):
    if _client is None:
        return pd.DataFrame(), pd.DataFrame()

    query_searches = f"""
        SELECT 
            interaction_id,
            timestamp,
            username,
            IFNULL(org_id, 'défaut') AS org_id,
            IFNULL(search_hash, '') AS search_hash,
            source_flow,
            search_criteria,
            weights,
            top_results,
            detailed_breakdown
        FROM `{_client.project}.{dataset_id}.search_events`
        WHERE TIMESTAMP(timestamp) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY timestamp DESC
    """

    query_usage = f"""
        SELECT 
            interaction_id,
            login_session_id,
            timestamp,
            username,
            IFNULL(org_id, 'défaut') AS org_id,
            event_name,
            payload
        FROM `{_client.project}.{dataset_id}.usage_events`
        WHERE TIMESTAMP(timestamp) >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL {days} DAY)
        ORDER BY timestamp DESC
    """

    try:
        df_searches = _client.query(query_searches).to_dataframe(create_bqstorage_client=False)
    except Exception as e:
        logger.warning(f"Failed to query search_events: {e}")
        df_searches = pd.DataFrame()

    try:
        df_usage = _client.query(query_usage).to_dataframe(create_bqstorage_client=False)
    except Exception as e:
        logger.warning(f"Failed to query usage_events: {e}")
        df_usage = pd.DataFrame()

    return df_searches, df_usage
