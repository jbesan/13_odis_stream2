from unittest.mock import patch, MagicMock

from utils.auth import check_password, resolve_org_for_oidc, verify_credentials


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
    """A complete legacy-authenticated session remains valid."""
    mock_session = {
        "password_correct": True,
        "auth_method": "legacy",
        "user": MagicMock(),
        "org": MagicMock(),
    }
    with (
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.user", None, create=True),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
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
    """An explicitly authorized domain receives its configured organization."""
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = True
    mock_user.email = "contact@lahso.org"

    with (
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
        patch("config.OIDC_ALLOWED_DOMAINS", {"lahso.org"}),
        patch("config.OIDC_DOMAIN_ORG_MAPPING", {"lahso.org": "emile_aura"}),
        patch("config.OIDC_ALLOWED_EMAILS", set()),
        patch("config.OIDC_EMAIL_ORG_MAPPING", {}),
    ):
        assert check_password() is True
        assert mock_session["password_correct"] is True
        assert mock_session["auth_method"] == "oidc"
        assert mock_session["org"].id == "emile_aura"


def test_check_password_oidc_logged_in_resolves_exact_email():
    """An explicitly authorized email receives its configured organization."""
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = True
    mock_user.email = "user@example.com"

    with (
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
        patch("config.OIDC_ALLOWED_DOMAINS", set()),
        patch("config.OIDC_DOMAIN_ORG_MAPPING", {}),
        patch("config.OIDC_ALLOWED_EMAILS", {"user@example.com"}),
        patch("config.OIDC_EMAIL_ORG_MAPPING", {"user@example.com": "jaccueille"}),
    ):
        assert check_password() is True
        assert mock_session["password_correct"] is True
        assert mock_session["org"].id == "jaccueille"


def test_check_password_oidc_not_logged_in_shows_login_form():
    """A user who has not authenticated with OIDC sees the login form."""
    mock_session = {}
    mock_user = MagicMock()
    mock_user.is_logged_in = False

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


def test_oidc_unknown_domain_is_denied_and_clears_prior_context():
    """An authenticated but unapproved identity cannot retain a prior session."""
    mock_session = {
        "password_correct": True,
        "auth_method": "oidc",
        "user": MagicMock(),
        "org": MagicMock(),
        "search_results": MagicMock(),
    }
    mock_user = MagicMock()
    mock_user.is_logged_in = True
    mock_user.email = "outsider@example.net"

    with (
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.error"),
        patch("utils.auth.st.button", return_value=False),
        patch("utils.auth.os.environ", {"K_SERVICE": "test"}),
        patch("config.OIDC_ALLOWED_DOMAINS", {"lahso.org"}),
        patch("config.OIDC_DOMAIN_ORG_MAPPING", {"lahso.org": "emile_aura"}),
        patch("config.OIDC_ALLOWED_EMAILS", set()),
        patch("config.OIDC_EMAIL_ORG_MAPPING", {}),
    ):
        assert check_password() is False

    assert mock_session == {}


def test_oidc_authorization_requires_a_known_mapped_organization():
    with (
        patch("config.OIDC_ALLOWED_DOMAINS", {"lahso.org"}),
        patch("config.OIDC_DOMAIN_ORG_MAPPING", {"lahso.org": "missing_org"}),
        patch("config.OIDC_ALLOWED_EMAILS", set()),
        patch("config.OIDC_EMAIL_ORG_MAPPING", {}),
    ):
        assert resolve_org_for_oidc("CONTACT@LAHSO.ORG") is None


def test_oidc_exact_email_requires_explicit_allowlist_membership():
    with (
        patch("config.OIDC_ALLOWED_DOMAINS", set()),
        patch("config.OIDC_DOMAIN_ORG_MAPPING", {}),
        patch("config.OIDC_ALLOWED_EMAILS", set()),
        patch("config.OIDC_EMAIL_ORG_MAPPING", {"partner@example.org": "agir33"}),
    ):
        assert resolve_org_for_oidc("partner@example.org") is None
