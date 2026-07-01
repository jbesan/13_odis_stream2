from streamlit.testing.v1 import AppTest
from unittest.mock import patch


@patch("utils.data_loader.ensure_data_initialized")
@patch("utils.auth.inject_idle_sleep")
def test_main_app_redirect_authenticated(mock_idle, mock_ensure_init):
    at = AppTest.from_file("app/main.py")
    # Simulate already authenticated user
    at.session_state["password_correct"] = True
    # Seed required session state dictionary for demo data
    at.session_state["demo_data"] = {}

    # Run the AppTest with a safe timeout
    at.run(timeout=10)

    # Verify that it ran the redirect target and didn't crash
    assert len(at.exception) == 0


@patch("utils.data_loader.ensure_data_initialized")
@patch("utils.auth.inject_idle_sleep")
def test_main_app_blocks_unauthenticated(mock_idle, mock_ensure_init):
    # Set Cloud Run env so check_password logic actually triggers the form
    with patch("os.environ", {"K_SERVICE": "yes"}):
        at = AppTest.from_file("app/main.py")
        at.session_state["password_correct"] = False

        at.run(timeout=10)

        # Verify it didn't switch page, and rendered the login header/form instead
        assert len(at.exception) == 0
        # Since it blocks and shows the login container, it will render "Accès ODIS" subheader
        subheaders = [s.value for s in at.subheader]
        assert "Accès ODIS" in subheaders
