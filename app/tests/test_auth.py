import pytest
from unittest.mock import patch, MagicMock
import sys

# We don't want to poison the sys.modules globally.
# Instead, we patch st where it is used.

from app.auth import verify_credentials, check_password

def test_verify_credentials_success():
    """Test verification with correct credentials."""
    secrets = {"passwords": {"alice": "secret123"}}
    assert verify_credentials("alice", "secret123", secrets) is True

def test_verify_credentials_failure():
    """Test verification with incorrect credentials."""
    secrets = {"passwords": {"alice": "secret123"}}
    assert verify_credentials("alice", "wrong", secrets) is False
    assert verify_credentials("bob", "secret123", secrets) is False

def test_verify_credentials_no_secrets():
    """Test behavior when secrets are missing."""
    secrets = {}
    assert verify_credentials("alice", "secret123", secrets) is False

def test_check_password_flow_authenticated():
    """Test that check_password returns True when already logged in."""
    with patch("app.auth.st") as mock_st:
        mock_st.session_state = {"password_correct": True}
        assert check_password() is True

def test_check_password_flow_unauthenticated():
    """Test that check_password returns False and shows form when not logged in."""
    with patch("app.auth.st") as mock_st:
        mock_st.session_state = {}
        # Simulate st.container() and st.form() context managers
        mock_st.container.return_value.__enter__.return_value = MagicMock()
        mock_st.form.return_value.__enter__.return_value = MagicMock()
        mock_st.text_input.return_value = "guest"
        mock_st.form_submit_button.return_value = False
        
        assert check_password() is False
        assert mock_st.session_state["password_correct"] is False
