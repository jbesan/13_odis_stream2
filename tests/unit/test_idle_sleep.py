from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ui import idle_sleep


IDLE_HTML = Path(idle_sleep.__file__).parents[1] / "static" / "idle.html"
DOCKERFILE = Path(idle_sleep.__file__).parents[1] / "Dockerfile"


def test_idle_disconnect_mounts_component_with_timeout_data():
    with (
        patch.object(idle_sleep, "_idle_disconnect_component") as component,
        patch.object(idle_sleep, "is_server_idle", return_value=False),
        patch.object(idle_sleep, "get_last_server_activity_ts", return_value=1000.0),
        patch("ui.idle_sleep.time.time", return_value=1005.0),
    ):
        idle_sleep.inject_idle_disconnect(timeout_minutes=7)

    component.assert_called_once_with(
        data={
            "timeout_ms": 420_000,
            "server_last_activity_ms": 1_000_000,
            "server_idle_age_ms": 5_000,
            "should_disconnect": False,
        },
        key="odis_idle_disconnect_monitor",
        on_check_idle_change=idle_sleep._mark_idle_check_rerun,
    )


def test_idle_disconnect_rejects_non_positive_timeout():
    with pytest.raises(ValueError, match="greater than zero"):
        idle_sleep.inject_idle_disconnect(timeout_minutes=0)


def test_idle_disconnect_uses_document_unload_and_supported_cleanup():
    script = idle_sleep._IDLE_DISCONNECT_JS

    assert "window.location.assign(idleUrl)" in script
    assert 'new URL("/app/static/idle.html"' in script
    assert 'setTriggerValue("check_idle"' in script
    assert "return () =>" in script
    assert "removeEventListener" in script
    assert 'componentHost.dataset.idleDisconnect = "active"' in script
    assert "XMLHttpRequest" not in script
    assert "targetWin.fetch" not in script


def test_idle_disconnect_static_target_is_an_html_asset():
    html = IDLE_HTML.read_text(encoding="utf-8")
    assert "Reprendre ma session" in html
    assert 'rawReturn.startsWith("/")' in html
    assert '!rawReturn.startsWith("//")' in html
    assert "--server.enableStaticServing=true" in DOCKERFILE.read_text()


def test_server_idle_tracking_respects_timeout_and_background_work(monkeypatch):
    from services import server_activity

    monkeypatch.setattr(server_activity, "_last_server_activity_ts", 1000.0)
    with patch("agents.utils.get_odis_bg_store", return_value={}):
        assert idle_sleep.is_server_idle(timeout_minutes=30, now=1000.0 + 1799) is False
        assert idle_sleep.is_server_idle(timeout_minutes=30, now=1000.0 + 1800) is True

        idle_sleep.record_server_activity(2500.0)
        assert idle_sleep.get_last_server_activity_ts() == 2500.0
        assert idle_sleep.is_server_idle(timeout_minutes=30, now=2500.0 + 60) is False

    # Running AI task prevents idle disconnect
    with patch(
        "agents.utils.get_odis_bg_store",
        return_value={"analysis_h_33063": {"status": "running"}},
    ):
        assert idle_sleep.has_active_background_work() is True
        assert idle_sleep.is_server_idle(timeout_minutes=30, now=99999.0) is False

    # Unready active hydration_run prevents idle disconnect
    fake_run = MagicMock()
    fake_run._cancelled = False
    fake_run.plan.codgeos = ("33063",)
    fake_run.ready.return_value = False
    with patch(
        "agents.utils.get_odis_bg_store",
        return_value={"h": {"hydration_run": fake_run}},
    ):
        assert idle_sleep.has_active_background_work() is True

    # Completed dispatched auto_analysis must NOT block idle forever
    with patch(
        "agents.utils.get_odis_bg_store",
        return_value={
            "h": {
                "status_refiner": "done",
                "auto_analysis_steps": {
                    "33063": {"status": "dispatched", "task_key": "analysis_h_33063"}
                },
            },
            "analysis_h_33063": {"status": "done"},
        },
    ):
        assert idle_sleep.has_active_background_work() is False
