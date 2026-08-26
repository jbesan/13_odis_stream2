import os
import json
import logging
import sys
from datetime import datetime

if sys.version_info >= (3, 9):
    import zoneinfo
else:
    from backports import zoneinfo as zoneinfo  # type: ignore
from google.cloud import bigquery
import streamlit as st
from typing import Any, Optional
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


def log_agent_state_to_bq(
    user_input: str,
    agent_state: dict,
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
):
    """
    Logs the structured agent state to BigQuery with dedicated columns.
    Uses the existing interaction_id for tracing.
    """
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        logger.warning(
            "No Google Cloud Project found. Skipping BQ Agent State logging."
        )
        return

    try:
        # Tier 1: Use explicit caller-supplied values (thread-safe)
        # Tier 2: Fall back to values baked into the agent_state dict (thread-safe)
        # Tier 3: Try Streamlit session_state (only safe on the main thread)
        if not interaction_id:
            interaction_id = (
                agent_state.get("interaction_id", "")
                if isinstance(agent_state, dict)
                else ""
            )
        if not username or username == "unknown":
            username = (
                agent_state.get("username", "") if isinstance(agent_state, dict) else ""
            )

        try:
            if not interaction_id:
                interaction_id = get_interaction_id()
            if not username:
                username = st.session_state.get("username", "unknown")
        except Exception:
            pass  # session_state is unavailable in background threads — that's expected

        interaction_id = interaction_id or "unknown"
        username = username or "unknown"

        paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
        timestamp_paris = datetime.now(paris_tz).isoformat()

        client = bigquery.Client()
        table_ref = f"{client.project}.{DATASET_ID}.{TABLE_STATE_LOGS}"

        messages = agent_state.get("messages", [])
        # Extract last agent response
        last_response = ""
        if messages:
            for msg in reversed(messages):
                if msg.get("role") == "assistant":
                    last_response = msg.get("content", "")
                    break

        usage = agent_state.get("usage", {})
        usage_value = lambda name, default=0: (
            usage.get(name, default)
            if isinstance(usage, dict)
            else getattr(usage, name, default)
        )
        cost_eur = usage_value("cost_eur", 0.0)

        # Map new search_results to old columns for BQ schema compatibility
        sr = agent_state.get("search_results")
        top_cities_data = []
        artifacts_data = {}
        if sr:
            # Handle both SearchResultsData object and dict
            results = (
                getattr(sr, "results", [])
                if not isinstance(sr, dict)
                else sr.get("results", [])
            )
            for r in results:
                if hasattr(r, "model_dump"):
                    top_cities_data.append(
                        r.model_dump(
                            exclude={"geometry", "centroid", "expert_analysis"}
                        )
                    )
                    artifacts_data[r.codgeo] = r.expert_analysis
                elif isinstance(r, dict):
                    top_cities_data.append(
                        {
                            k: v
                            for k, v in r.items()
                            if k not in ["geometry", "centroid", "expert_analysis"]
                        }
                    )
                    artifacts_data[r.get("codgeo", "")] = r.get("expert_analysis", {})

        # ``cost_eur`` is the first-class billing field in the migrated
        # ``agent_state_logs`` schema.  The detailed rate-card and grounding
        # breakdown remains in the existing JSON ``artifacts`` column.
        usage_summary = {
            "cost_eur": float(cost_eur or 0.0),
            "cost_eur_available": bool(usage_value("eur_priced", True)),
            "token_cost_eur": float(usage_value("token_cost_eur", 0.0) or 0.0),
            "input_tokens": int(usage_value("input_tokens", 0) or 0),
            "input_tokens_new": int(usage_value("input_tokens_new", 0) or 0),
            "input_tokens_cached": int(
                usage_value("cache_read_tokens", 0) or 0
            ),
            "output_tokens": int(usage_value("output_tokens", 0) or 0),
            "cache_write_tokens": int(
                usage_value("cache_write_tokens", 0) or 0
            ),
            "cache_hit_ratio": float(
                usage_value("cache_hit_ratio", 0.0) or 0.0
            ),
            "requests": int(usage_value("requests", 0) or 0),
            "tool_calls": int(usage_value("tool_calls", 0) or 0),
            "grounding_queries": int(
                usage_value("grounding_queries", 0) or 0
            ),
            "grounding_cost_eur": float(
                usage_value("grounding_cost_eur", 0.0) or 0.0
            ),
            "places_requests": int(usage_value("places_requests", 0) or 0),
            "places_cost_eur": float(
                usage_value("places_cost_eur", 0.0) or 0.0
            ),
            "unpriced_model_requests": int(
                usage_value("unpriced_model_requests", 0) or 0
            ),
        }
        breakdown = usage_value("breakdown", {})
        if isinstance(breakdown, dict):
            grounding_queries: list[str] = []
            grounding_sources: list[dict[str, Any]] = []
            grounding_supports: list[dict[str, Any]] = []
            google_usage_metadata: list[dict[str, Any]] = []
            pricing_cards: list[dict[str, Any]] = []
            seen_urls: set[str] = set()
            for entry in breakdown.values():
                if not isinstance(entry, dict):
                    continue
                for query in entry.get("grounding_queries", []) or []:
                    if isinstance(query, str) and query not in grounding_queries:
                        grounding_queries.append(query)
                for source in entry.get("grounding_sources", []) or []:
                    if not isinstance(source, dict):
                        continue
                    url = source.get("url")
                    if isinstance(url, str) and url not in seen_urls:
                        seen_urls.add(url)
                        grounding_sources.append(source)
                for support in entry.get("grounding_supports", []) or []:
                    if isinstance(support, dict):
                        grounding_supports.append(support)
                for usage_metadata in entry.get("google_usage_metadata", []) or []:
                    if isinstance(usage_metadata, dict):
                        google_usage_metadata.append(usage_metadata)
                pricing_card = {
                    key: entry.get(key)
                    for key in (
                        "model_id",
                        "model_family",
                        "pricing_status",
                        "pricing_source",
                        "rates_per_million",
                        "skus",
                    )
                    if entry.get(key) is not None
                }
                if pricing_card:
                    pricing_cards.append(pricing_card)
            usage_summary["grounding_query_values"] = grounding_queries
            usage_summary["grounding_sources"] = grounding_sources
            usage_summary["grounding_supports"] = grounding_supports
            usage_summary["google_usage_metadata"] = google_usage_metadata
            usage_summary["pricing_cards"] = pricing_cards
        artifacts_data["__usage__"] = usage_summary

        row = {
            "interaction_id": interaction_id,
            "timestamp": timestamp_paris,
            "username": username,
            "last_user_message": user_input[:2000] if user_input else "",
            "last_agent_response": last_response[:10000] if last_response else "",
            "search_criteria": json.dumps(
                _safe_json_format(agent_state.get("search_criteria", {})),
                default=str,
                ensure_ascii=False,
            ),
            "briefing": str(agent_state.get("odis_brief", "")),
            "top_cities": json.dumps(
                _safe_json_format(top_cities_data), default=str, ensure_ascii=False
            ),
            "artifacts": json.dumps(
                _safe_json_format(artifacts_data), default=str, ensure_ascii=False
            ),
            "execution_mode": str(agent_state.get("execution_mode", "full_analysis")),
            "cost_eur": float(cost_eur or 0.0),
        }

        # Fire to BigQuery
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(f"BQ Agent State Insert Errors: {errors}")
        else:
            logger.debug(
                "Successfully logged Agent State to BigQuery with granular fields."
            )

    except Exception as e:
        logger.error(f"Failed to log agent state to BQ: {str(e)}")
