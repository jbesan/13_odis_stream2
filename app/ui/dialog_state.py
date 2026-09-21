"""Shared state helpers for dialogs requested by Streamlit widgets."""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any


ACTIVE_DIALOG_STATE_KEYS = (
    "active_ia_city_index",
    "active_details_index",
    "active_ccas_index",
    "active_pdf_modal",
    "active_share_dialog",
    "active_about_dialog",
)


def request_dialog(
    state: MutableMapping[str, Any], state_key: str, value: Any = True
) -> None:
    """Request exactly one dialog for the next full application rerun.

    Args:
        state: Session-like mutable mapping holding the UI state.
        state_key: State key identifying the dialog to open.
        value: Payload stored for the dialog, such as a commune code.

    Raises:
        ValueError: If ``state_key`` is not a registered dialog state key.
    """
    _validate_dialog_state_key(state_key)
    for active_key in ACTIVE_DIALOG_STATE_KEYS:
        state[active_key] = None
    state[state_key] = value


def clear_dialog(state: MutableMapping[str, Any], state_key: str) -> None:
    """Clear one dialog request after the dialog has been dismissed.

    Args:
        state: Session-like mutable mapping holding the UI state.
        state_key: State key identifying the dialog to close.

    Raises:
        ValueError: If ``state_key`` is not a registered dialog state key.
    """
    _validate_dialog_state_key(state_key)
    state[state_key] = None


def keep_only_dialog(state: MutableMapping[str, Any], state_key: str) -> None:
    """Remove stale competing dialog requests while preserving one request.

    Args:
        state: Session-like mutable mapping holding the UI state.
        state_key: State key of the dialog being dispatched.

    Raises:
        ValueError: If ``state_key`` is not a registered dialog state key.
    """
    _validate_dialog_state_key(state_key)
    for active_key in ACTIVE_DIALOG_STATE_KEYS:
        if active_key != state_key:
            state[active_key] = None


def _validate_dialog_state_key(state_key: str) -> None:
    """Validate a dialog state key before mutating session state."""
    if state_key not in ACTIVE_DIALOG_STATE_KEYS:
        raise ValueError(f"Unknown dialog state key: {state_key}")
