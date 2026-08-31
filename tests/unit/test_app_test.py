from streamlit.testing.v1 import AppTest
from unittest.mock import patch
from core.models import User, Org


@patch("utils.data_loader.preload_scoring_datasets_async")
@patch("utils.data_loader.initialize_session_state")
@patch("services.telemetry.log_page_view")
@patch("utils.auth.check_password", return_value=True)
def test_main_app_redirect_authenticated(
    mock_auth, mock_page_view, mock_initialize, mock_preload
):
    at = AppTest.from_file("app/main.py", default_timeout=60)
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
        at = AppTest.from_file("app/main.py", default_timeout=60)
        at.run(timeout=60)

        # Verify the common shell stopped before initialization/navigation.
        assert len(at.exception) == 0
        mock_initialize.assert_not_called()
        mock_preload.assert_not_called()
