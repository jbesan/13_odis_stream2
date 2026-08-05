from pathlib import Path
from unittest.mock import patch

import pytest

from ui import idle_sleep


IDLE_IMAGE = Path(idle_sleep.__file__).parents[1] / "static" / "idle.png"
DOCKERFILE = Path(idle_sleep.__file__).parents[1] / "Dockerfile"


def test_idle_disconnect_mounts_component_with_timeout_data():
    with patch.object(idle_sleep, "_idle_disconnect_component") as component:
        idle_sleep.inject_idle_disconnect(timeout_minutes=7)

    component.assert_called_once_with(
        data={"timeout_ms": 420_000},
        key="odis_idle_disconnect_monitor",
    )


def test_idle_disconnect_rejects_non_positive_timeout():
    with pytest.raises(ValueError, match="greater than zero"):
        idle_sleep.inject_idle_disconnect(timeout_minutes=0)


def test_idle_disconnect_uses_document_unload_and_supported_cleanup():
    script = idle_sleep._IDLE_DISCONNECT_JS

    assert "window.location.assign(idleUrl)" in script
    assert 'new URL("app/static/idle.png"' in script
    assert "return () =>" in script
    assert "removeEventListener" in script
    assert 'componentHost.dataset.idleDisconnect = "active"' in script
    assert "XMLHttpRequest" not in script
    assert "targetWin.fetch" not in script


def test_idle_disconnect_static_target_is_a_png_asset():
    assert IDLE_IMAGE.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert "--server.enableStaticServing=true" in DOCKERFILE.read_text()
