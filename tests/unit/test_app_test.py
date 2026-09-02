import os
from streamlit.testing.v1 import AppTest
from unittest.mock import patch
from core.models import User, Org

MAIN_PY = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../app/main.py"))


@patch("utils.data_loader.preload_scoring_datasets_async")
@patch("utils.data_loader.initialize_session_state")
@patch("services.telemetry.log_page_view")
@patch("utils.auth.check_password", return_value=True)
def test_main_app_redirect_authenticated(
    mock_auth, mock_page_view, mock_initialize, mock_preload
):
    at = AppTest.from_file(MAIN_PY, default_timeout=60)
    at.session_state["username"] = "user"
    at.session_state["user"] = User(username="user", org_id="test_org")
    at.session_state["org"] = Org(id="test_org", name="Test Org")

    # Run the AppTest with a safe timeout
    at.run(timeout=60)

    # Verify that it ran the redirect target and didn't crash
    assert len(at.exception) == 0


@patch("utils.data_loader.preload_scoring_datasets_async")
@patch("utils.data_loader.initialize_session_state")
@patch("services.telemetry.log_page_view")
@patch("utils.auth.check_password", return_value=False)
@patch("ui.page_shell.inject_idle_disconnect")
def test_main_app_blocks_unauthenticated(
    mock_idle_disconnect, mock_auth, mock_page_view, mock_initialize, mock_preload
):
    # Set Cloud Run env so check_password logic actually triggers the form
    with patch("os.environ", {"K_SERVICE": "yes"}):
        at = AppTest.from_file(MAIN_PY, default_timeout=60)
        at.run(timeout=60)

        # Verify the common shell stopped before initialization/navigation.
        assert len(at.exception) == 0
        mock_initialize.assert_not_called()
        mock_preload.assert_not_called()


@patch("utils.data_loader.preload_scoring_datasets_async")
@patch("utils.data_loader.initialize_session_state")
@patch("services.telemetry.log_page_view")
@patch("utils.auth.check_password", return_value=True)
def test_accueil_page_runs_authenticated(
    mock_auth, mock_page_view, mock_initialize, mock_preload
):
    accueil_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../app/pages/1_Accueil.py"))
    at = AppTest.from_file(accueil_py, default_timeout=60)
    at.session_state["username"] = "test-user"
    at.session_state["user"] = User(username="test-user", org_id="jaccueille")
    at.session_state["org"] = Org(id="jaccueille", name="J'accueille")

    at.run(timeout=60)
    assert len(at.exception) == 0


@patch("services.telemetry.log_page_view")
@patch("utils.auth.check_password", return_value=True)
@patch("utils.auth.is_admin", return_value=False)
def test_analytics_page_blocks_non_admin(mock_is_admin, mock_auth, mock_page_view):
    analytics_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../app/pages/4_Analytics.py"))
    at = AppTest.from_file(analytics_py, default_timeout=60)
    at.session_state["username"] = "user"
    at.session_state["user"] = User(username="user", org_id="jaccueille")
    at.session_state["org"] = Org(id="jaccueille", name="J'accueille")

    at.run(timeout=60)
    assert len(at.exception) == 0
    assert len(at.error) > 0


@patch("services.telemetry.log_page_view")
@patch("utils.auth.check_password", return_value=True)
@patch("utils.auth.is_admin", return_value=True)
@patch("services.analytics_data.get_bq_client", return_value=None)
def test_analytics_page_allows_admin(mock_bq, mock_is_admin, mock_auth, mock_page_view):
    analytics_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../app/pages/4_Analytics.py"))
    at = AppTest.from_file(analytics_py, default_timeout=60)
    at.session_state["username"] = "admin"
    at.session_state["user"] = User(username="admin", org_id="jaccueille")
    at.session_state["org"] = Org(id="jaccueille", name="J'accueille")

    at.run(timeout=60)
    assert len(at.exception) == 0

