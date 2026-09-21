import os
import json
import logging
from datetime import datetime

import zoneinfo
from google.cloud import bigquery
from typing import Any, Optional

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


def _async_bq_insert(row: dict) -> None:
    try:
        client = bigquery.Client()
        table_ref = f"{client.project}.{DATASET_ID}.{TABLE_STATE_LOGS}"
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            if any("cost_details" in str(err) for err in errors):
                logger.warning(
                    "BigQuery table %s lacks cost_details column; retrying without it: %s",
                    table_ref,
                    errors,
                )
                fallback_row = {k: v for k, v in row.items() if k != "cost_details"}
                retry_errors = client.insert_rows_json(table_ref, [fallback_row])
                if retry_errors:
                    logger.error(f"BQ Agent State Insert Errors (fallback): {retry_errors}")
                else:
                    logger.debug(
                        "Successfully logged Agent State to BigQuery without cost_details."
                    )
            else:
                logger.error(f"BQ Agent State Insert Errors: {errors}")
        else:
            logger.debug(
                "Successfully logged Agent State to BigQuery with granular fields."
            )
    except Exception as e:
        logger.error(f"Failed to log agent state to BQ: {e}")


def _submit_agent_state_insert(row: dict):
    from services.telemetry import _TELEMETRY_EXECUTOR

    future = _TELEMETRY_EXECUTOR.submit(_async_bq_insert, row)
    if "PYTEST_CURRENT_TEST" in os.environ:
        try:
            future.result(timeout=5)
        except Exception:
            pass
    return future


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
        # Tier 1: Use explicit caller-supplied values
        # Tier 2: Fall back to values baked into the agent_state dict
        if not interaction_id and isinstance(agent_state, dict):
            interaction_id = agent_state.get("interaction_id", "")
        if (not username or username == "unknown") and isinstance(agent_state, dict):
            username = agent_state.get("username", "")

        interaction_id = interaction_id or "unknown"
        username = username or "unknown"

        paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
        timestamp_paris = datetime.now(paris_tz).isoformat()

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

        # Granular execution telemetry stored in dedicated ``cost_details`` column
        cost_details = {
            "cost_eur": float(cost_eur or 0.0),
            "cost_eur_available": bool(usage_value("eur_priced", True)),
            "token_cost_eur": float(usage_value("token_cost_eur", 0.0) or 0.0),
            "input_tokens": int(usage_value("input_tokens", 0) or 0),
            "input_tokens_new": int(usage_value("input_tokens_new", 0) or 0),
            "input_tokens_cached": int(usage_value("cache_read_tokens", 0) or 0),
            "output_tokens": int(usage_value("output_tokens", 0) or 0),
            "cache_hit_ratio": float(usage_value("cache_hit_ratio", 0.0) or 0.0),
            "grounding_queries": int(usage_value("grounding_queries", 0) or 0),
            "grounding_cost_eur": float(usage_value("grounding_cost_eur", 0.0) or 0.0),
            "places_requests": int(usage_value("places_requests", 0) or 0),
            "places_cost_eur": float(usage_value("places_cost_eur", 0.0) or 0.0),
            "unpriced_model_requests": int(
                usage_value("unpriced_model_requests", 0) or 0
            ),
            "cost_basis": "EUR rate-card estimate; free-tier/account aggregation may differ from invoice",
        }

        row = {
            "interaction_id": interaction_id,
            "timestamp": timestamp_paris,
            "env": os.getenv("ODIS_DEPLOYMENT_ENV", "local"),
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
            "cost_details": json.dumps(cost_details, ensure_ascii=False),
        }

        # Fire to BigQuery asynchronously
        _submit_agent_state_insert(row)

    except Exception as e:
        logger.error(f"Failed to log agent state to BQ: {str(e)}")
