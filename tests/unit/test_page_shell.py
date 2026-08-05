from unittest.mock import patch

from ui import page_shell


def test_enter_page_applies_auth_before_telemetry():
    events = []
    state = {"username": "user", "user": object(), "org": object()}

    with patch.object(
        page_shell.auth,
        "check_password",
        side_effect=lambda: events.append("auth") or True,
    ), patch.object(
        page_shell.telemetry,
        "log_page_view",
        side_effect=lambda page: events.append(f"telemetry:{page}"),
    ), patch.object(page_shell.st, "session_state", state), patch.object(
        page_shell.st, "query_params", {}
    ):
        page_shell.enter_page("Formulaire")

    assert events == ["auth", "telemetry:Formulaire"]


def test_enter_page_routes_shared_search_after_authentication():
    events = []
    state = {"username": "user", "user": object(), "org": object()}

    with patch.object(
        page_shell.auth,
        "check_password",
        side_effect=lambda: events.append("auth") or True,
    ), patch(
        "services.share_service.restore_shared_search_from_query_params",
        side_effect=lambda: events.append("restore") or True,
    ), patch.object(
        page_shell.st,
        "switch_page",
        side_effect=lambda page: events.append(f"switch:{page}"),
    ), patch.object(page_shell.st, "session_state", state), patch.object(
        page_shell.st, "query_params", {"search": "share-1"}
    ):
        page_shell.enter_page(
            None,
            handle_shared_search=True,
            redirect_shared_to_results=True,
        )

    assert events == ["auth", "restore", "switch:pages/3_Resultats.py"]
