import os
from unittest.mock import patch, MagicMock
from utils.auth import check_password, logout
from core.models import User, Org
from utils.data_loader import apply_logged_in_org_defaults


def test_check_password_local_dev_autologin():
    """Verify that check_password bypasses authentication in local development and injects local user."""
    mock_session = {}
    with (
        patch("utils.auth.st.session_state", mock_session),
        patch("config.ORGANIZATION_PROFILES", {}),
        patch("config.OIDC_EMAIL_ORG_MAPPING", {}),
        patch.dict(os.environ, {}, clear=True),
    ):
        res = check_password()
        assert res is True
        assert "user" in mock_session
        assert isinstance(mock_session["user"], User)
        assert mock_session["user"].username == "jacques-local"
        assert mock_session.get("org") is None


def test_check_password_local_dev_autologin_with_org():
    """Verify that check_password injects org if mapped for local user."""
    mock_session = {}
    with (
        patch("utils.auth.st.session_state", mock_session),
        patch("config.ORGANIZATION_PROFILES", {"test_org": Org(id="test_org", name="Test Org")}),
        patch("config.OIDC_EMAIL_ORG_MAPPING", {"jacques-local": "test_org"}),
        patch.dict(os.environ, {}, clear=True),
    ):
        res = check_password()
        assert res is True
        assert mock_session["org"] is not None
        assert mock_session["org"].id == "test_org"


def test_check_password_local_dev_forced_auth():
    """Forced auth must not use the local-development identity bypass."""
    mock_session = {}
    with (
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.container"),
        patch("utils.auth.st.subheader"),
        patch("utils.auth.st.info"),
        patch("utils.auth.st.button", return_value=False),
    ):
        with patch.dict(os.environ, {"ODIS_FORCE_AUTH": "True"}, clear=True):
            res = check_password()
            assert res is False
            assert "user" not in mock_session


def test_check_password_rejects_partial_authenticated_cloud_run_session():
    """A reset must not leave Cloud Run with auth=True but no org context."""
    mock_session = {"password_correct": True, "username": "stale@example.com"}
    with (
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.user", None, create=True),
        patch("utils.auth.st.container"),
        patch("utils.auth.st.subheader"),
        patch("utils.auth.st.info"),
        patch("utils.auth.st.button", return_value=False),
    ):
        with patch.dict(
            os.environ,
            {"K_SERVICE": "odis-service", "ODIS_FORCE_AUTH": "True"},
            clear=True,
        ):
            assert check_password() is False

    assert mock_session == {}


def test_apply_logged_in_org_defaults():
    """Verify that apply_logged_in_org_defaults correctly applies Pydantic Org defaults to the options dictionary."""
    mock_org = Org(
        id="test_org",
        name="Test Org",
        zone_type="departement",
        default_zones=["33", "40"],
        defaults={"hebergement_cible": ["Chez l'habitant"], "target_population": 10000},
    )

    mock_session = {"org": mock_org}
    defaults = {
        "org_context": None,
        "org_strategic_locations": [],
        "org_strategic_locations_type": "departement",
        "hebergement_cible": ["Location avec Intermédiation"],
        "target_population": 50000,
    }

    with patch("utils.data_loader.st.session_state", mock_session):
        apply_logged_in_org_defaults(defaults)

        assert defaults["org_context"] == "test_org"
        assert defaults["org_strategic_locations"] == ["33", "40"]
        assert defaults["org_strategic_locations_type"] == "departement"
        # Test merging list items without duplicates
        assert set(defaults["hebergement_cible"]) == {
            "Location avec Intermédiation",
            "Chez l'habitant",
        }
        # Test scalar override
        assert defaults["target_population"] == 10000


def test_logout_direct_email():
    """Verify that logout clears session_state and switches page for email direct login."""
    mock_session = MagicMock()

    with (
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.user", None, create=True),
        patch("utils.auth.st.switch_page") as mock_switch,
    ):
        logout()
        mock_session.clear.assert_called_once()
        mock_switch.assert_called_once_with("pages/1_Accueil.py")


def test_logout_oidc():
    """Verify that logout calls st.logout() when user is logged in via OIDC."""
    mock_session = MagicMock()
    mock_user = MagicMock()
    mock_user.is_logged_in = True

    with (
        patch("utils.auth.st.session_state", mock_session),
        patch("utils.auth.st.user", mock_user, create=True),
        patch("utils.auth.st.logout") as mock_st_logout,
    ):
        logout()
        mock_session.clear.assert_called_once()
        mock_st_logout.assert_called_once()
