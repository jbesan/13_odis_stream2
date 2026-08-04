import pytest
from unittest.mock import patch

from services.telemetry import get_manifest_version
from ui.sources_dialog import load_manifest, format_iso_date


def test_get_manifest_version_raises_error_if_missing():
    with patch(
        "utils.data_loader.load_active_data_manifest",
        side_effect=RuntimeError("missing manifest"),
    ):
        with pytest.raises(RuntimeError, match="manifest_version"):
            get_manifest_version()


def test_format_iso_date():
    iso_sample = "2026-07-22T10:30:00.000000+00:00"
    formatted = format_iso_date(iso_sample)
    assert "22/07/2026" in formatted
    assert format_iso_date(None) == "-"


def test_load_manifest_reads_the_active_release():
    active_manifest = {
        "manifest_version": "v2-abc123",
        "pipeline_run_id": "run-abc",
        "sources": [{"source_key": "test_src"}],
    }
    with patch(
        "ui.sources_dialog.load_active_data_manifest", return_value=active_manifest
    ):
        data = load_manifest()
        assert data is not None
        assert data["manifest_version"] == "v2-abc123"
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
