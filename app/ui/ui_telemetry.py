"""UI-layer telemetry bridge.

Extracts user session metadata and interaction IDs from Streamlit session state
and forwards them to backend telemetry services (services.telemetry).
Pure Python services never access Streamlit state directly.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import streamlit as st
from services import telemetry
from utils import auth

logger = logging.getLogger(__name__)


def get_ui_interaction_id() -> str:
    """Retrieve or generate a unique interaction ID for the active Streamlit session."""
    val = telemetry.normalize_interaction_id(st.session_state.get("interaction_id"))
    if val is None:
        new_id = telemetry.generate_interaction_id()
        st.session_state["interaction_id"] = new_id
        return new_id
    return str(val)


def reset_ui_interaction_id() -> str:
    """Generate and store a fresh interaction ID for the active Streamlit session."""
    new_id = telemetry.generate_interaction_id()
    st.session_state["interaction_id"] = new_id
    return new_id


def track_ui_event(
    event_name: str,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    """Extract session metadata from Streamlit and log an event to BigQuery."""
    try:
        interaction_id = get_ui_interaction_id()
        username = st.session_state.get("username", "unknown")
        org = st.session_state.get("org")
        org_id = org.id if org and hasattr(org, "id") else "unknown"
        login_session_id = auth.get_login_session_id()

        telemetry.log_usage_event(
            event_name=event_name,
            payload=payload,
            interaction_id=interaction_id,
            username=username,
            org_id=org_id,
            login_session_id=login_session_id,
        )
    except Exception as exc:
        logger.warning(f"Failed to track UI event {event_name}: {exc}")


def log_page_view(page_name: str) -> None:
    """Log page navigation event to BQ, deduplicating consecutive re-runs on the same page."""
    try:
        current_page = st.session_state.get("current_page")
        if current_page != page_name:
            previous_page = current_page
            st.session_state["previous_page"] = previous_page
            st.session_state["current_page"] = page_name

            track_ui_event(
                "page_view",
                {
                    "page": page_name,
                    "origin": previous_page or "direct_entry",
                },
            )
    except Exception as exc:
        logger.warning(f"Failed to log page view: {exc}")
