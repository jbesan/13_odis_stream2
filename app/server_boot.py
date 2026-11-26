"""Pre-warmed Streamlit server launcher for Cloud Run container startup.

Syncs active GCS datasets, pre-imports heavy runtime modules, and populates
process-global ``@st.cache_resource`` caches and ``mcp_server.DATA_CONTEXT``
before handing execution to Streamlit in the same Python process.
"""

from __future__ import annotations

import logging
import sys
import time
from typing import Any, Dict, Sequence

# Runtime isolation: mask geopandas before any transitive imports (matches main.py).
sys.modules.setdefault("geopandas", None)

import utils.logger  # noqa: F401  # Configure structured JSON logging before warmup.
from core import scoring  # noqa: F401
from services import (
    app_session,  # noqa: F401
    mcp_server,
    search_controller,  # noqa: F401
    server_activity,  # noqa: F401
    telemetry,  # noqa: F401
)
from streamlit.web import cli as st_cli
from utils import auth, common, data_loader  # noqa: F401

logger = logging.getLogger(__name__)


def warmup_runtime() -> Dict[str, Any]:
    """Synchronize datasets and warm process-global caches before serving.

    Intentionally avoids importing Streamlit V2 component modules (such as
    ``ui.idle_sleep`` or ``ui.page_shell``) before ``Server`` creates the
    active ``Runtime``, because V2 components register against the current
    ``Runtime.instance().bidi_component_registry``.

    Returns:
        Dict[str, Any]: The warmed dataset bundle stored in ``@st.cache_resource``.
    """
    started = time.perf_counter()
    logger.info("🔥 [BOOT] Pre-warming ODIS runtime and dataset caches...")

    app_data = data_loader.get_app_data()
    data_loader.get_tag_search_index(app_data)
    mcp_server.set_data_context(app_data)

    elapsed = time.perf_counter() - started
    logger.info(
        "✅ [BOOT] Warmup completed in %.2fs (release=%s).",
        elapsed,
        app_data.get("_release_id", "unknown"),
    )
    return app_data


def main(argv: Sequence[str] | None = None) -> None:
    """Warm caches in-process, then launch the Streamlit HTTP/WebSocket server.

    Args:
        argv: Command-line arguments forwarded to ``streamlit run`` (defaults to
            ``sys.argv[1:]``, falling back to ``["main.py"]`` when empty).
    """
    warmup_runtime()
    cli_args = list(argv) if argv is not None else list(sys.argv[1:])
    if not cli_args:
        cli_args = ["main.py"]
    st_cli.main.main(
        args=["run", *cli_args],
        prog_name="streamlit",
        standalone_mode=False,
    )


if __name__ == "__main__":
    main()
