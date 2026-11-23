"""Unit tests for the pre-warmed Streamlit boot launcher."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

import server_boot


def test_warmup_runtime_populates_caches_and_mcp_context():
    """warmup_runtime loads datasets, warms tag index, and injects MCP DATA_CONTEXT."""
    fake_bundle = {
        "_release_id": "gcs:run-test-123",
        "asso_tags_ref": pd.DataFrame({"tag_canonique": ["sport"]}),
    }
    with (
        patch.object(
            server_boot.data_loader, "get_app_data", return_value=fake_bundle
        ) as mock_get_app_data,
        patch.object(
            server_boot.data_loader, "get_tag_search_index"
        ) as mock_get_tag_index,
        patch.object(server_boot.mcp_server, "set_data_context") as mock_set_ctx,
    ):
        result = server_boot.warmup_runtime()

    assert result is fake_bundle
    mock_get_app_data.assert_called_once_with()
    mock_get_tag_index.assert_called_once_with(fake_bundle)
    mock_set_ctx.assert_called_once_with(fake_bundle)


def test_server_boot_does_not_eagerly_import_v2_component_modules():
    """Verify server_boot avoids importing ui.idle_sleep or ui.page_shell before Runtime exists."""
    import ast

    boot_source = Path(server_boot.__file__).read_text(encoding="utf-8")
    tree = ast.parse(boot_source)
    imported_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported_names.add(mod)
            imported_names.update(f"{mod}.{alias.name}" if mod else alias.name for alias in node.names)

    assert not any("idle_sleep" in name or "page_shell" in name for name in imported_names)
    assert sys.modules.get("geopandas") is None


def test_main_runs_warmup_then_delegates_to_streamlit_cli():
    """main() runs warmup_runtime() before invoking streamlit run with forwarded args."""
    call_order: list[str] = []
    mock_cli_main = MagicMock(side_effect=lambda **kwargs: call_order.append("cli"))

    with (
        patch.object(
            server_boot,
            "warmup_runtime",
            side_effect=lambda: call_order.append("warmup") or {},
        ),
        patch.object(server_boot.st_cli.main, "main", mock_cli_main),
    ):
        server_boot.main(["main.py", "--server.port=8080"])

    assert call_order == ["warmup", "cli"]
    mock_cli_main.assert_called_once_with(
        args=["run", "main.py", "--server.port=8080"],
        prog_name="streamlit",
        standalone_mode=False,
    )
