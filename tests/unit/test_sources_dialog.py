import pandas as pd
import pytest
from unittest.mock import patch

from services.telemetry import get_manifest_version
from services.service_outcomes import OutcomeStatus, ServiceOutcome
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
        fn = getattr(load_manifest, "__wrapped__", load_manifest)
        outcome = fn()
        assert outcome.status == OutcomeStatus.SUCCESS
        assert outcome.value is not None
        assert outcome.value["manifest_version"] == "v2-abc123"
        assert len(outcome.value["sources"]) == 1


def test_load_manifest_marks_provider_failure_unavailable():
    with patch(
        "ui.sources_dialog.load_active_data_manifest",
        side_effect=RuntimeError("GCS unavailable"),
    ):
        fn = getattr(load_manifest, "__wrapped__", load_manifest)
        outcome = fn()
        assert outcome.status == OutcomeStatus.UNAVAILABLE
        assert outcome.error_code == "DATA-MANIFEST-UNAVAILABLE"


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

    with patch(
        "ui.sources_dialog.load_manifest",
        return_value=ServiceOutcome(OutcomeStatus.SUCCESS, value=mock_manifest),
    ), \
         patch("streamlit.dataframe") as mock_st_dataframe, \
         patch("streamlit.markdown"), \
         patch("streamlit.caption"):
        from ui.sources_dialog import show_sources_dialog

        show_sources_dialog.__wrapped__()

        assert mock_st_dataframe.called
        # The sources dataframe is the first call to st.dataframe
        df_passed = mock_st_dataframe.call_args_list[0][0][0]
        assert df_passed.loc[0, "Documentation"] == "https://www.data.gouv.fr/datasets/contours-administratifs/"
        assert pd.isna(df_passed.loc[1, "Documentation"])


def test_show_about_dialog_empty_sources_shows_info():
    mock_manifest = {
        "manifest_version": "v1.0",
        "created_at": "2026-07-22T10:00:00Z",
        "sources": [],
    }

    with patch(
        "ui.sources_dialog.load_manifest",
        return_value=ServiceOutcome(OutcomeStatus.SUCCESS, value=mock_manifest),
    ), \
         patch("streamlit.info") as mock_st_info, \
         patch("streamlit.markdown"), \
         patch("streamlit.dataframe"):
        from ui.sources_dialog import show_about_dialog

        show_about_dialog.__wrapped__()

        assert mock_st_info.called
        assert "Aucune source détaillée" in mock_st_info.call_args[0][0]


def test_render_about_sidebar_link():
    with patch("streamlit.button", return_value=True) as mock_button, \
         patch("ui.sources_dialog.show_about_dialog") as mock_show_dialog:
        from ui.components import render_about_sidebar_link

        render_about_sidebar_link()

        assert mock_button.called
        assert mock_button.call_args[0][0] == "À propos"
        assert mock_show_dialog.called


def test_format_age_and_ttl_dynamic():
    from datetime import datetime, timezone, timedelta
    from ui.sources_dialog import _format_age_and_ttl

    now = datetime.now(timezone.utc)
    # 1. Normal age under TTL
    acquired_10d = (now - timedelta(days=10)).isoformat()
    res1 = _format_age_and_ttl({"acquired_at": acquired_10d, "ttl_days": 30})
    assert res1 == "10 j / 30 j"

    # 2. Exceeded TTL triggers warning icon
    acquired_45d = (now - timedelta(days=45)).isoformat()
    res2 = _format_age_and_ttl({"acquired_at": acquired_45d, "ttl_days": 30})
    assert res2 == "⚠️ 45 j / 30 j"

    # 3. Fallback used
    res3 = _format_age_and_ttl({
        "acquired_at": acquired_10d,
        "ttl_days": 30,
        "fallback_used": True,
    })
    assert res3 == "10 j / 30 j (fallback)"

    # 4. Unknown date falls back to precalculated or inconnu
    res4 = _format_age_and_ttl({"ttl_days": 15})
    assert "inconnu / 15 j" in res4


def test_render_legal_terms_tab():
    with patch("streamlit.markdown") as mock_md, \
         patch("streamlit.expander") as mock_expander:
        from ui.sources_dialog import _render_legal_terms_tab

        _render_legal_terms_tab()

        assert mock_md.called
        assert mock_expander.call_count == 4
        # Verify expander titles match expected legal topics
        expander_titles = [call[0][0] for call in mock_expander.call_args_list]
        assert any("Aide à la décision" in t for t in expander_titles)
        assert any("données publiques" in t for t in expander_titles)
        assert any("RGPD" in t for t in expander_titles)
        assert any("Intelligence Artificielle" in t for t in expander_titles)
