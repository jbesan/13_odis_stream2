from unittest.mock import patch, MagicMock

# We don't want to poison the sys.modules globally.
# Instead, we patch st where it is used.

from utils.auth import verify_credentials, check_password


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
    mock_secrets = MagicMock()
    mock_session = {"password_correct": True}
    with (
        patch("utils.auth.st.secrets", mock_secrets),
        patch("utils.auth.st.session_state", mock_session),
    ):
        assert check_password() is True


def test_check_password_flow_unauthenticated():
    """Test that check_password returns False and shows form when not logged in."""
    mock_session = {}
    with (
        patch("utils.auth.verify_credentials") as mock_verify,
        patch("utils.auth.st.secrets", {}),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.container"),
        patch("utils.auth.st.form"),
        patch("utils.auth.st.subheader"),
        patch("utils.auth.st.text_input"),
        patch("utils.auth.st.form_submit_button") as mock_submit,
        patch("utils.auth.st.rerun"),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
    ):
        mock_verify.return_value = False
        mock_submit.return_value = False

        assert check_password() is False
        assert mock_session["password_correct"] is False
