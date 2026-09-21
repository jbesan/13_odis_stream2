"""Unit tests for pure services.telemetry and ui.ui_telemetry bridge."""

from unittest.mock import MagicMock, patch
import pytest
from services import telemetry
from ui import ui_telemetry


def test_generate_interaction_id():
    id1 = telemetry.generate_interaction_id()
    id2 = telemetry.generate_interaction_id()
    assert len(id1) == 8
    assert len(id2) == 8
    assert id1 != id2


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("abc12345", "abc12345"),
        ("  xyz98765  ", "xyz98765"),
        ("", None),
        ("   ", None),
        ("unknown", None),
        ("UNKNOWN", None),
        ("none", None),
        ("null", None),
        (None, None),
        (12345, None),
    ],
)
def test_normalize_interaction_id(raw, expected):
    assert telemetry.normalize_interaction_id(raw) == expected


def test_resolve_interaction_id():
    assert telemetry.resolve_interaction_id("custom_id") == "custom_id"
    generated = telemetry.resolve_interaction_id(None)
    assert len(generated) == 8
    assert generated != "unknown"


def test_reset_interaction_id_with_dict():
    session = {"interaction_id": "old_id"}
    new_id = telemetry.reset_interaction_id(session)
    assert len(new_id) == 8
    assert new_id != "old_id"
    assert session["interaction_id"] == new_id


def test_get_interaction_id_with_dict():
    session = {}
    id1 = telemetry.get_interaction_id(session)
    assert len(id1) == 8
    assert session["interaction_id"] == id1

    id2 = telemetry.get_interaction_id(session)
    assert id2 == id1


@patch("services.telemetry._submit_bq_insert")
def test_log_usage_event_pure_python(mock_submit):
    with patch("os.getenv", return_value="test-project"):
        telemetry.log_usage_event(
            "test_event",
            payload={"key": "val"},
            interaction_id="inter_12",
            username="test_user",
            org_id="test_org",
            login_session_id="log_123",
        )
        assert mock_submit.called
        table_name, row = mock_submit.call_args[0]
        assert table_name == "usage_events"
        assert row["event_name"] == "test_event"
        assert row["interaction_id"] == "inter_12"
        assert row["username"] == "test_user"
        assert row["org_id"] == "test_org"
        assert row["login_session_id"] == "log_123"


@patch("services.telemetry._submit_bq_insert")
def test_log_saved_search_event_pure_python(mock_submit):
    with patch("os.getenv", return_value="test-project"):
        telemetry.log_saved_search_event(
            event_type="create",
            share_id="share_99",
            status="success",
            username="alice",
            org_id="jaccueille",
            interaction_id="inter_99",
        )
        assert mock_submit.called
        table_name, row = mock_submit.call_args[0]
        assert table_name == "saved_searches"
        assert row["share_id"] == "share_99"
        assert row["username"] == "alice"
        assert row["org_id"] == "jaccueille"


def test_ui_telemetry_interaction_id_lifecycle():
    fake_session = {}
    with patch("streamlit.session_state", fake_session):
        id1 = ui_telemetry.get_ui_interaction_id()
        assert len(id1) == 8
        assert fake_session["interaction_id"] == id1

        id2 = ui_telemetry.get_ui_interaction_id()
        assert id2 == id1

        id3 = ui_telemetry.reset_ui_interaction_id()
        assert id3 != id1
        assert fake_session["interaction_id"] == id3


@patch("services.telemetry.log_usage_event")
@patch("utils.auth.get_login_session_id", return_value="sess_123")
def test_ui_telemetry_track_ui_event(mock_get_sess, mock_log_usage):
    fake_org = MagicMock()
    fake_org.id = "org_xyz"
    fake_session = {
        "interaction_id": "inter_abc",
        "username": "bob",
        "org": fake_org,
    }
    with patch("streamlit.session_state", fake_session):
        ui_telemetry.track_ui_event("button_click", {"target": "details"})
        assert mock_log_usage.called
        kwargs = mock_log_usage.call_args.kwargs
        assert kwargs["event_name"] == "button_click"
        assert kwargs["payload"] == {"target": "details"}
        assert kwargs["interaction_id"] == "inter_abc"
        assert kwargs["username"] == "bob"
        assert kwargs["org_id"] == "org_xyz"
        assert kwargs["login_session_id"] == "sess_123"
