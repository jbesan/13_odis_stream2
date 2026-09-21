"""Unit tests for the single-active-dialog session state contract."""

import pytest

from ui.dialog_state import clear_dialog, keep_only_dialog, request_dialog


def test_request_dialog_clears_competing_requests() -> None:
    """A new request leaves exactly one dialog payload active."""
    state = {
        "active_details_index": "33063",
        "active_ia_city_index": "75056",
    }

    request_dialog(state, "active_pdf_modal")

    assert state["active_pdf_modal"] is True
    assert state["active_details_index"] is None
    assert state["active_ia_city_index"] is None


def test_keep_only_dialog_removes_stale_requests() -> None:
    """Dispatcher normalization prevents legacy state from opening two dialogs."""
    state = {
        "active_details_index": "33063",
        "active_ccas_index": "33063",
        "active_ia_city_index": "75056",
    }

    keep_only_dialog(state, "active_ia_city_index")

    assert state["active_ia_city_index"] == "75056"
    assert state["active_details_index"] is None
    assert state["active_ccas_index"] is None


def test_clear_dialog_removes_one_request() -> None:
    """A dismissal callback clears its own request without raising."""
    state = {"active_about_dialog": True}

    clear_dialog(state, "active_about_dialog")

    assert state["active_about_dialog"] is None


def test_unknown_dialog_key_fails_loudly() -> None:
    """New dialog keys must be registered explicitly."""
    with pytest.raises(ValueError, match="Unknown dialog state key"):
        request_dialog({}, "active_unknown_dialog")
