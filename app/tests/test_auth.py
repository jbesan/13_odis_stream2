import pytest
from unittest.mock import MagicMock
import sys

# Mock streamlit
sys.modules["streamlit"] = MagicMock()
import streamlit as st

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

def test_check_password_flow_unauthenticated():
    """Test that check_password returns False and inits state when not logged in."""
    st.session_state = {}
    assert check_password() is False
    assert st.session_state["password_correct"] is False

def test_check_password_flow_authenticated():
    """Test that check_password returns True when already logged in."""
    st.session_state = {"password_correct": True}
    assert check_password() is True
