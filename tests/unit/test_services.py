import pytest
from unittest.mock import MagicMock, patch
from services import telemetry, bq_logger
from ui import feedback
from typing import Any

@pytest.fixture
def mock_session_state():
    """Mock Streamlit session state with support for attribute and item access."""
    state = MagicMock()
    # Mock __contains__ so 'in st.session_state' works
    state.__contains__.side_effect = lambda k: k in state.__dict__
    return state

def test_interaction_id_persistence(mock_session_state):
    """Test that interaction_id is generated and remains stable."""
    with patch("streamlit.session_state", mock_session_state):
        # First call should generate it
        first_id = telemetry.get_interaction_id()
        assert len(first_id) == 8
        
        # Second call should return the same ID
        second_id = telemetry.get_interaction_id()
        assert second_id == first_id
        
        # Reset should change it
        reset_id = telemetry.reset_interaction_id()
        assert reset_id != first_id
        assert len(reset_id) == 8

def test_interaction_id_logic():
    """Test that interaction_id is generated and remains stable."""
    with patch("streamlit.session_state", MagicMock()) as mock_ss:
        # Mocking the attribute behavior
        mock_ss.interaction_id = "test-id"
        mock_ss.__contains__.side_effect = lambda k: k == "interaction_id"
        
        val = telemetry.get_interaction_id()
        assert val == "test-id"

@patch("services.bq_logger.bigquery.Client")
def test_log_agent_state_to_bq(mock_client_class):
    """Test that agent state is correctly formatted for BQ."""
    mock_client = mock_client_class.return_value
    mock_client.project = "test-project"
    mock_client.insert_rows_json.return_value = []
    
    with patch("streamlit.session_state", MagicMock()) as mock_ss:
        mock_ss.get.side_effect = lambda k, d=None: "test_user" if k == "username" else d
        mock_ss.interaction_id = "test-id"
        mock_ss.__contains__.side_effect = lambda k: k == "interaction_id"
        
        with patch("os.getenv", return_value="test-project"):
            agent_state = {
                "messages": [{"role": "user", "content": "hello"}],
                "usage": {"cost_usd": 0.01}
            }
            bq_logger.log_agent_state_to_bq("hello", agent_state)
            
            assert mock_client.insert_rows_json.called
            args, _ = mock_client.insert_rows_json.call_args
            row = args[1][0] # client.insert_rows_json(table_ref, [row])
            assert row["username"] == "test_user"
            assert row["last_user_message"] == "hello"

@patch("ui.feedback.bigquery.Client")
def test_feedback_submission(mock_client_class):
    """Test that feedback is sent to the correct BQ table."""
    mock_client = mock_client_class.return_value
    mock_client.project = "test-project"
    mock_client.insert_rows_json.return_value = []
    
    with patch("streamlit.session_state", MagicMock()) as mock_ss:
        mock_ss.get.side_effect = lambda k, d=None: "test_user" if k == "username" else d
        mock_ss.interaction_id = "test-id"
        mock_ss.__contains__.side_effect = lambda k: k == "interaction_id"
        
        with patch("os.getenv", return_value="test-project"):
            success = feedback._submit_to_bq("Bug", "It's broken")
            assert success is True
            assert mock_client.insert_rows_json.called
            args, _ = mock_client.insert_rows_json.call_args
            row = args[1][0]
            assert row["feedback_type"] == "Bug"
            assert row["comment"] == "It's broken"
