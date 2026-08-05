from unittest.mock import patch, MagicMock

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


def test_check_password_oidc_logged_in_resolves_org_by_domain():
    """Test that check_password sets correct org when Streamlit confirms OIDC login (domain match).

    Streamlit enforces allowed_domains natively via secrets.toml.
    Our code only needs to handle org resolution after is_logged_in is True.
    """
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = True
    mock_user.email = "contact@lahso.org"

    mock_secrets = MagicMock()
    mock_secrets.get.return_value = {}
    mock_secrets.__contains__ = lambda self, key: False

    with (
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.secrets", mock_secrets),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
        patch("config.OIDC_DOMAIN_ORG_MAPPING", {"lahso.org": "emile_aura"}),
    ):
        assert check_password() is True
        assert mock_session["password_correct"] is True
        assert mock_session["org"].id == "emile_aura"


def test_check_password_oidc_logged_in_resolves_org_from_secrets():
    """Test that check_password reads org mapping from st.secrets when present."""
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = True
    mock_user.email = "user@example.com"

    mock_auth_section = {
        "email_org_mapping": {"user@example.com": "jaccueille"},
        "domain_org_mapping": {},
    }
    mock_secrets = MagicMock()
    mock_secrets.get.side_effect = lambda key, default=None: (
        mock_auth_section if key == "auth" else default
    )

    with (
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.secrets", mock_secrets),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
    ):
        assert check_password() is True
        assert mock_session["password_correct"] is True
        assert mock_session["org"].id == "jaccueille"


def test_check_password_oidc_not_logged_in_shows_login_form():
    """Test that when st.user.is_logged_in is False (unauthorized or not yet authenticated),
    the login form is shown and check_password returns False.

    Streamlit sets is_logged_in=False for unauthorized users — we never need to check the email ourselves.
    """
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = False  # Streamlit rejected the user (not in allowed_emails/domains)

    with (
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.container"),
        patch("utils.auth.st.subheader"),
        patch("utils.auth.st.info"),
        patch("utils.auth.st.button") as mock_button,
        patch("utils.auth.st.form"),
        patch("utils.auth.st.text_input"),
        patch("utils.auth.st.form_submit_button") as mock_submit,
        patch("utils.auth.st.markdown"),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
    ):
        mock_button.return_value = False
        mock_submit.return_value = False
        assert check_password() is False
