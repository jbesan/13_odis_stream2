import json
import pytest
from unittest.mock import patch

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


def test_show_sources_dialog_dataframe_urls():
    mock_manifest = {
        "manifest_version": "v1.0",
        "created_at": "2026-07-22T10:00:00Z",
        "sources": [
            {
                "source_key": "communes",
                "name": "Contours communes",
                "doc_url": "https://www.data.gouv.fr/datasets/contours-administratifs/",
            },
            {
                "source_key": "no_doc",
                "name": "No Doc Dataset",
                "doc_url": None,
            },
        ],
    }

    with patch("ui.sources_dialog.load_manifest", return_value=mock_manifest), \
         patch("streamlit.dataframe") as mock_st_dataframe, \
         patch("streamlit.markdown"), \
         patch("streamlit.caption"):
        from ui.sources_dialog import show_sources_dialog

        show_sources_dialog.__wrapped__()

        assert mock_st_dataframe.called
        df_passed = mock_st_dataframe.call_args[0][0]
        assert df_passed.loc[0, "Documentation"] == "https://www.data.gouv.fr/datasets/contours-administratifs/"
        assert df_passed.loc[1, "Documentation"] is None
