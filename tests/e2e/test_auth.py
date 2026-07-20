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
        patch("utils.auth.st.button") as mock_button,
        patch("utils.auth.st.markdown"),
        patch("utils.auth.st.rerun"),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
    ):
        mock_verify.return_value = False
        mock_submit.return_value = False
        mock_button.return_value = False

        assert check_password() is False
        assert mock_session["password_correct"] is False


def test_check_password_oidc_authorized_email():
    """Test that check_password returns True and sets session state when OIDC user is whitelisted by email."""
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = True
    mock_user.email = "jbesancon@gmail.com"

    with (
        patch("utils.auth.inject_idle_sleep"),
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.sidebar"),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
    ):
        assert check_password() is True
        assert mock_session["password_correct"] is True
        assert mock_session["username"] == "jbesancon@gmail.com"
        assert mock_session["user"].username == "jbesancon@gmail.com"
        assert mock_session["org"].id == "jaccueille"


def test_check_password_oidc_authorized_domain():
    """Test that check_password returns True and sets correct Org when OIDC domain is whitelisted."""
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = True
    mock_user.email = "contact@lahso.org"

    with (
        patch("utils.auth.inject_idle_sleep"),
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.sidebar"),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
    ):
        assert check_password() is True
        assert mock_session["password_correct"] is True
        assert mock_session["org"].id == "emile_aura"


def test_check_password_oidc_unauthorized():
    """Test that check_password returns False and shows error/logout for unauthorized OIDC user."""
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = True
    mock_user.email = "stranger@malicious.com"

    with (
        patch("utils.auth.inject_idle_sleep"),
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.container"),
        patch("utils.auth.st.subheader"),
        patch("utils.auth.st.error") as mock_error,
        patch("utils.auth.st.button") as mock_button,
        patch("utils.auth.st.logout") as mock_logout,
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
    ):
        mock_button.return_value = True  # Simulate clicking "Se déconnecter"
        assert check_password() is False
        mock_error.assert_called_once_with(
            "❌ Accès refusé : l'adresse email 'stranger@malicious.com' n'est pas autorisée à accéder à ODIS."
        )
        mock_logout.assert_called_once()
