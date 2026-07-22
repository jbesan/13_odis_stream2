import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from services.telemetry import get_manifest_version
from ui.sources_dialog import load_manifest, format_iso_date


def test_get_manifest_version_raises_error_if_missing():
    with patch("pathlib.Path.exists", return_value=False):
        with pytest.raises(RuntimeError, match="manifest_version"):
            get_manifest_version()


def test_format_iso_date():
    iso_sample = "2026-07-22T10:30:00.000000+00:00"
    formatted = format_iso_date(iso_sample)
    assert "22/07/2026" in formatted
    assert format_iso_date(None) == "-"


def test_load_manifest_with_mock_file(tmp_path):
    manifest_file = tmp_path / "data_manifest.json"
    manifest_file.write_text(
        json.dumps(
            {
                "manifest_version": "v2026.07.22-abc123",
                "created_at": "2026-07-22T10:00:00Z",
                "total_sources": 1,
                "sources": [
                    {
                        "source_key": "test_src",
                        "name": "Test Dataset",
                        "method": "Data Platform Odace",
                        "row_count": 100,
                    }
                ],
            }
        )
    )

    with patch("ui.sources_dialog.MANIFEST_PATH", manifest_file):
        data = load_manifest()
        assert data is not None
        assert data["manifest_version"] == "v2026.07.22-abc123"
        assert len(data["sources"]) == 1
