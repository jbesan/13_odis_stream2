import os
from unittest.mock import patch

from ui import page_shell


def test_idle_disconnect_is_mounted_on_cloud_run_by_default():
    with patch.object(page_shell, "inject_idle_disconnect") as mount:
        with patch.dict(
            os.environ,
            {"K_SERVICE": "odis-service"},
            clear=True,
        ):
            page_shell._mount_idle_disconnect_when_configured()

        mount.assert_called_once_with(
            timeout_minutes=page_shell.IDLE_DISCONNECT_TIMEOUT_MINUTES
        )


def test_idle_disconnect_is_not_mounted_outside_cloud_run():
    with patch.object(page_shell, "inject_idle_disconnect") as mount:
        with patch.dict(os.environ, {}, clear=True):
            page_shell._mount_idle_disconnect_when_configured()

        mount.assert_not_called()


def test_idle_disconnect_has_a_cloud_run_kill_switch():
    with patch.object(page_shell, "inject_idle_disconnect") as mount:
        with patch.dict(
            os.environ,
            {
                "K_SERVICE": "odis-service",
                "ODIS_IDLE_DISCONNECT_ENABLED": "False",
            },
            clear=True,
        ):
            page_shell._mount_idle_disconnect_when_configured()

        mount.assert_not_called()


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
