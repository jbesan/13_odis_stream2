from unittest.mock import patch
import streamlit as st
from app.utils.memory import reset_app_state, perform_garbage_collection


def test_reset_app_state():
    # Mock streamlit session state as a dict-like object
    mock_state = {
        "app_data": "heavy_cache",
        "password_correct": True,
        "username": "user1",
        "user": {"username": "user1", "org_id": "test_org"},
        "org": {"id": "test_org"},
        "login_session_id": "login-123",
        "org_defaults_applied": "test_org",
        "temp_key_1": "to_be_deleted",
        "temp_key_2": 123,
    }

    with patch.object(st, "session_state", mock_state):
        reset_app_state()

        # Verify heavy caches are preserved
        assert "app_data" in st.session_state
        assert "password_correct" in st.session_state
        assert "username" in st.session_state
        assert "user" in st.session_state
        assert "org" in st.session_state
        assert "login_session_id" in st.session_state
        assert "org_defaults_applied" in st.session_state

        # Verify temporary keys are removed
        assert "temp_key_1" not in st.session_state
        assert "temp_key_2" not in st.session_state


def test_perform_garbage_collection():
    with patch("gc.collect") as mock_collect:
        perform_garbage_collection()
        mock_collect.assert_called_once()
