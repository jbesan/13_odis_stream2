import pytest
from unittest.mock import MagicMock, patch
import os
import streamlit as st
from app.ui.feedback import _submit_to_bq
from typing import Any


@patch("app.ui.feedback.bigquery.Client")
@patch("app.ui.feedback.get_interaction_id")
def test_submit_to_bq_with_context(mock_get_id, mock_bq_client):
    # Setup mocks
    mock_get_id.return_value = "test-interaction-123"
    mock_client_instance = mock_bq_client.return_value
    mock_client_instance.project = "test-project"
    mock_client_instance.insert_rows_json.return_value = [] # No errors
    
    # Mock session state
    st.session_state.username = "test-user"
    
    # Set environment variable to bypass the check
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": "test-project"}):
        # Call the function
        success = _submit_to_bq("Result Relevance", "5", context='{"city": "Paris"}')
        
    assert success is True
    
    # Verify the row content
    args, kwargs = mock_client_instance.insert_rows_json.call_args
    rows = args[1]
    assert len(rows) == 1
    assert rows[0]["feedback_type"] == "Result Relevance"
    assert rows[0]["comment"] == "5"
    assert rows[0]["context"] == '{"city": "Paris"}'
    assert rows[0]["username"] == "test-user"
    assert rows[0]["interaction_id"] == "test-interaction-123"

@patch("app.ui.feedback.bigquery.Client")
def test_submit_to_bq_no_project(mock_bq_client):
    # Set environment variable to None or empty
    with patch.dict(os.environ, {"GOOGLE_CLOUD_PROJECT": ""}, clear=True):
        if "GCP_PROJECT" in os.environ:
             del os.environ["GCP_PROJECT"]
        
        success = _submit_to_bq("Bug", "It broke")
        
    assert success is True # Should return True to not block UI
    assert not mock_bq_client.called
