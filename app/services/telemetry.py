import logging
import json
import uuid
import time
from datetime import datetime
import sys

if sys.version_info >= (3, 9):
    import zoneinfo
else:
    from backports import zoneinfo as zoneinfo  # type: ignore
import streamlit as st
import os
from google.cloud import bigquery
from core.models import SearchCriterias, SearchResultsData
from typing import Any, Optional
from utils import auth

# Use root logger for critical visibility in background threads
logger = logging.getLogger(__name__)


class JsonFormatter(logging.Formatter):
    """Formatter that outputs JSON strings for Google Cloud Logging."""

    def format(self, record):
        log_record = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": time.time(),
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

import atexit
from concurrent.futures import ThreadPoolExecutor

_TELEMETRY_EXECUTOR = ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="odis-telemetry-worker"
)
atexit.register(_TELEMETRY_EXECUTOR.shutdown, wait=False)


def _execute_bq_insert(table_name_or_ref: str, row: dict) -> None:
    """Execute BigQuery streaming insertion in the background."""
    try:
        client = bigquery.Client()
        if "." in table_name_or_ref:
            table_ref = table_name_or_ref
        else:
            table_ref = f"{client.project}.odis_logs.{table_name_or_ref}"
        errors = client.insert_rows_json(table_ref, [row], timeout=15)
        if errors:
            logger.error(f"❌ [TELEMETRY] BQ Insert Error for {table_ref}: {errors}")
        else:
            _telemetry_logger.debug(
                f"✅ [TELEMETRY] Successfully logged event to {table_ref}"
            )
    except Exception as e:
        logger.error(
            f"❌ [TELEMETRY] Failed to log event to BQ ({table_name_or_ref}): {e}"
        )


def _submit_bq_insert(table_name_or_ref: str, row: dict):
    """Submit BigQuery insertion task asynchronously (fire-and-forget).

    When running inside pytest, wait synchronously so mock assertions succeed.
    """
    future = _TELEMETRY_EXECUTOR.submit(_execute_bq_insert, table_name_or_ref, row)
    if "PYTEST_CURRENT_TEST" in os.environ:
        try:
            future.result(timeout=5)
        except Exception:
            pass
    return future


_INVALID_INTERACTION_IDS = frozenset({"", "unknown", "none", "null"})


def normalize_interaction_id(value: Any) -> Optional[str]:
    """Return a usable interaction ID, rejecting missing-value sentinels."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or normalized.casefold() in _INVALID_INTERACTION_IDS:
        return None
    return normalized


def get_interaction_id() -> str:
    """Retrieves or generates a unique interaction ID for the current session state."""
    val = normalize_interaction_id(getattr(st.session_state, "interaction_id", None))
    if val is None:
        try:
            val = normalize_interaction_id(st.session_state.get("interaction_id"))
        except (AttributeError, RuntimeError) as exc:
            _telemetry_logger.debug(
                "st.session_state is unavailable in get_interaction_id: %s", exc
            )
            val = None
        except Exception as exc:
            _telemetry_logger.warning(
                "Error reading interaction_id from st.session_state: %s", exc
            )
            val = None

    if val is None:
        new_id = str(uuid.uuid4())[:8]
        try:
            st.session_state["interaction_id"] = new_id
        except (AttributeError, RuntimeError) as exc:
            _telemetry_logger.debug(
                "st.session_state unavailable when storing interaction_id: %s", exc
            )
        except Exception as exc:
            _telemetry_logger.warning(
                "Failed to store interaction_id in session_state: %s", exc
            )
        return new_id

    return str(val)


def resolve_interaction_id(value: Any = None) -> str:
    """Prefer an explicit ID, otherwise return the current/generated session ID."""
    explicit = normalize_interaction_id(value)
    return explicit or get_interaction_id()


def reset_interaction_id() -> str:
    """Generates a new interaction ID (e.g., on a new search)."""
    new_id = str(uuid.uuid4())[:8]
    try:
        st.session_state["interaction_id"] = new_id
    except (AttributeError, RuntimeError) as exc:
        _telemetry_logger.debug(
            "st.session_state unavailable in reset_interaction_id: %s", exc
        )
    except Exception as exc:
        _telemetry_logger.warning(
            "Failed to set interaction_id in reset_interaction_id: %s", exc
        )
    return new_id


def log_event(
    event_name: str,
    payload: Optional[dict] = None,
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
):
    """Logs a succint technical event to stderr (Cloud Logging)."""
    if payload is None:
        payload = {}

    try:
        if not username:
            username = st.session_state.get("username", "unknown")
        if not interaction_id:
            interaction_id = get_interaction_id()
    except (AttributeError, RuntimeError) as exc:
        _telemetry_logger.debug("st.session_state unavailable in log_event: %s", exc)
        username = username or "unknown"
        interaction_id = interaction_id or "unknown"
    except Exception as exc:
        _telemetry_logger.warning(
            "Failed to resolve session state in log_event: %s", exc
        )
        username = username or "unknown"
        interaction_id = interaction_id or "unknown"

    event_data = {
        "event_name": event_name,
        "interaction_id": interaction_id,
        "username": username,
        "data_summary": {
            k: str(v)[:100] for k, v in payload.items()
        },  # Succint summary
    }

    _telemetry_logger.info(
        f"Telemetry Technical: {event_name}", extra={"json_payload": event_data}
    )


def log_usage_event(
    event_name: str,
    payload: Optional[dict] = None,
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
    org_id: Optional[str] = None,
):
    """Logs a functional usage event (page view, feature click, etc.) to BigQuery usage_events table."""
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
        return

    if payload is None:
        payload = {}

    try:
        try:
            if not username:
                username = st.session_state.get("username", "unknown")
            if not interaction_id:
                interaction_id = get_interaction_id()
            if not org_id:
                org = st.session_state.get("org")
                org_id = org.id if org and hasattr(org, "id") else "unknown"
            login_session_id = auth.get_login_session_id()
        except (AttributeError, RuntimeError) as exc:
            _telemetry_logger.debug(
                "st.session_state unavailable in log_usage_event: %s", exc
            )
            username = username or "unknown"
            interaction_id = interaction_id or "unknown"
            org_id = org_id or "unknown"
            login_session_id = "unknown"
        except Exception as exc:
            _telemetry_logger.warning(
                "Error resolving session metadata in log_usage_event: %s", exc
            )
            username = username or "unknown"
            interaction_id = interaction_id or "unknown"
            org_id = org_id or "unknown"
            login_session_id = "unknown"

        try:
            paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
            timestamp_str = datetime.now(paris_tz).isoformat()
        except Exception as exc:
            _telemetry_logger.debug(
                "Failed to obtain Europe/Paris time in log_usage_event: %s", exc
            )
            timestamp_str = datetime.now().isoformat()

        row = {
            "interaction_id": interaction_id,
            "login_session_id": login_session_id,
            "timestamp": timestamp_str,
            "username": username,
            "org_id": org_id,
            "event_name": event_name,
            "payload": json.dumps(
                _safe_json_format(payload), default=str, ensure_ascii=False
            ),
        }

        _submit_bq_insert("usage_events", row)
    except Exception as e:
        logger.error(f"❌ [TELEMETRY] Failed to queue usage event to BQ: {str(e)}")


def log_page_view(page_name: str):
    """Logs page navigation event to BQ, deduplicating consecutive re-runs on the same page."""
    try:
        current_page = st.session_state.get("current_page")
        if current_page != page_name:
            previous_page = current_page
            st.session_state["previous_page"] = previous_page
            st.session_state["current_page"] = page_name

            log_usage_event(
                "page_view",
                {
                    "page": page_name,
                    "origin": previous_page or "direct_entry",
                },
            )
    except Exception as e:
        logger.error(f"Failed to log page view: {e}")


def _safe_json_format(obj: Any) -> Any:
    """Recursively converts sets to lists for JSON serialization."""
    if isinstance(obj, set):
        return list(obj)
    if isinstance(obj, dict):
        return {k: _safe_json_format(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_safe_json_format(i) for i in obj]
    return obj


def get_manifest_version() -> str:
    """Return the manifest version belonging to the active dataset release."""
    from utils.data_loader import load_active_data_manifest

    try:
        data = load_active_data_manifest()
        version = (
            data.get("manifest_version")
            or data.get("pipeline_run_id")
            or data.get("active_release_version")
        )
        if not version or version == "unknown":
            raise RuntimeError(
                "❌ Invalid or missing 'manifest_version' in the active data release."
            )
        return str(version)
    except Exception as e:
        raise RuntimeError(f"❌ Failed to load active manifest_version: {e}") from e


def log_search_complete(
    config: SearchCriterias,
    search_results: SearchResultsData,
    source_flow: str = "classic",
    interaction_id: Optional[str] = None,
    username: Optional[str] = None,
    org_id: Optional[str] = None,
):
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
                username = st.session_state.get("username", "unknown")
            if not org_id:
                org = st.session_state.get("org")
                org_id = org.id if org and hasattr(org, "id") else "unknown"
        except (AttributeError, RuntimeError) as exc:
            _telemetry_logger.debug(
                "st.session_state unavailable in log_search_event: %s", exc
            )
            interaction_id = interaction_id or "unknown"
            username = username or "unknown"
            org_id = org_id or "unknown"
        except Exception as exc:
            _telemetry_logger.warning(
                "Error resolving session metadata in log_search_event: %s", exc
            )
            interaction_id = interaction_id or "unknown"
            username = username or "unknown"
            org_id = org_id or "unknown"

        try:
            paris_tz = zoneinfo.ZoneInfo("Europe/Paris")
            timestamp_str = datetime.now(paris_tz).isoformat()
        except Exception as exc:
            _telemetry_logger.debug(
                "Failed to obtain Europe/Paris time in log_search_event: %s", exc
            )
            timestamp_str = datetime.now().isoformat()

        # Compute search hash
        search_hash = ""
        if hasattr(config, "compute_hash"):
            search_hash = config.compute_hash()
        elif hasattr(search_results, "search_hash"):
            search_hash = search_results.search_hash

        # 1. Prepare Criteria & Weights
        # Handle both Pydantic models and dicts
        full_config = config.model_dump() if hasattr(config, "model_dump") else config
        if not isinstance(full_config, dict):
            full_config = {}

        # Dynamically extract all criteria fields declared in SearchCriterias model
        from core.models import SearchCriterias

        criteria_keys = set(SearchCriterias.model_fields.keys())
        search_criteria = {
            k: full_config.get(k) for k in criteria_keys if k in full_config
        }
        weights = {k: v for k, v in full_config.items() if k.startswith("poids_")}

        # 2. Prepare Results Summary
        top_5_results = []
        top_5_breakdown = {}

        # Handle search_results as model or dict
        results_list = (
            search_results.results
            if hasattr(search_results, "results")
            else search_results.get("results", [])
        )

        for commune in results_list:
            # Extract basic data
            c_codgeo = getattr(
                commune,
                "codgeo",
                commune.get("codgeo") if isinstance(commune, dict) else None,
            )
            c_name = getattr(
                commune,
                "name",
                commune.get("name") if isinstance(commune, dict) else None,
            )
            c_score = getattr(
                commune,
                "global_score",
                commune.get("global_score") if isinstance(commune, dict) else 0.0,
            )
            c_scores = getattr(
                commune,
                "scores",
                commune.get("scores", {}) if isinstance(commune, dict) else {},
            )
            c_pitch = getattr(
                commune,
                "refiner_pitch",
                commune.get("refiner_pitch", "") if isinstance(commune, dict) else "",
            )
            c_expert = getattr(
                commune,
                "expert_analysis",
                commune.get("expert_analysis", {}) if isinstance(commune, dict) else {},
            )

            top_5_results.append(
                {"codgeo": c_codgeo, "libgeo": c_name, "score": c_score}
            )

            commune_scores = {}
            for cat, items in c_scores.items():
                commune_scores[cat] = [
                    s.model_dump() if hasattr(s, "model_dump") else s for s in items
                ]

            top_5_breakdown[str(c_codgeo)] = {
                "libgeo": c_name,
                "scores": commune_scores,
                "refiner_pitch": c_pitch,
                "expert_analysis": c_expert,
            }

        row = {
            "interaction_id": interaction_id,
            "timestamp": timestamp_str,
            "username": username,
            "org_id": org_id,
            "manifest_version": get_manifest_version(),
            "search_hash": search_hash,
            "source_flow": source_flow,
            "search_criteria": json.dumps(
                _safe_json_format(search_criteria), default=str, ensure_ascii=False
            ),
            "weights": json.dumps(
                _safe_json_format(weights), default=str, ensure_ascii=False
            ),
            "top_results": json.dumps(
                _safe_json_format(top_5_results), default=str, ensure_ascii=False
            ),
            "detailed_breakdown": json.dumps(
                _safe_json_format(top_5_breakdown), default=str, ensure_ascii=False
            ),
        }

        _submit_bq_insert("search_events", row)

    except Exception as e:
        logger.error(
            f"❌ [TELEMETRY] Failed to log search event to BQ: {str(e)}", exc_info=True
        )
