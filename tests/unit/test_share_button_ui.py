from unittest.mock import MagicMock
import streamlit as st
from app.ui.results import render_share_search_button, render_export_pdf_button
from core.models import SearchResultsData, CommuneResult


def _create_mock_search_results(codgeo: str = "69123") -> SearchResultsData:
    commune = MagicMock(spec=CommuneResult)
    commune.codgeo = codgeo
    commune.siae_jobs = None
    commune.associations_details = None
    commune.inclusion = MagicMock()
    commune.inclusion.services_detailed = None
    commune.odis_synthesis = None

    search_results = MagicMock(spec=SearchResultsData)
    search_results.results = [commune]
    search_results.commune_pressentie = None
    search_results.search_hash = "hash_123"
    return search_results


def _call_fn(fn, *args, **kwargs):
    target = getattr(fn, "__wrapped__", fn)
    return target(*args, **kwargs)


def test_render_share_search_button_disabled_when_postscoring_not_done(monkeypatch):
    """Verify render_share_search_button renders a disabled preparation button when post-scoring is still running."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results()
    monkeypatch.setattr("app.ui.results.st.session_state", {"search_results": search_results})
    monkeypatch.setattr("app.ui.results.odis_get_bg_result", lambda h: None)

    _call_fn(render_share_search_button, h="hash_123", button_text="Partager")

    assert len(button_calls) == 1
    label, kwargs = button_calls[0]
    assert label == "Partager (Préparation...)"
    assert kwargs.get("disabled") is True


def test_render_share_search_button_enabled_when_postscoring_done(monkeypatch):
    """Verify render_share_search_button renders an active button when post-scoring is complete."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results("69123")
    monkeypatch.setattr("app.ui.results.st.session_state", {"search_results": search_results})
    mock_bg_res = {
        "status_refiner": "done",
        "jobs_enrichment": {"69123": {"status": "success_nonempty"}},
        "association_enrichment_status": {"69123": {"status": "success_nonempty"}},
        "inclusion_enrichment_status": {"69123": {"status": "success_nonempty"}},
    }
    monkeypatch.setattr("app.ui.results.odis_get_bg_result", lambda h: mock_bg_res)

    _call_fn(render_share_search_button, h="hash_123", button_text="Partager")

    assert len(button_calls) == 1
    label, kwargs = button_calls[0]
    assert label == "Partager"
    assert kwargs.get("disabled") is False


def test_render_export_pdf_button_states(monkeypatch):
    """Verify render_export_pdf_button toggles disabled/enabled based on post-scoring completion."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results("69123")
    monkeypatch.setattr("app.ui.results.st.session_state", {"search_results": search_results})

    # 1. Not done
    monkeypatch.setattr("app.ui.results.odis_get_bg_result", lambda h: None)
    _call_fn(render_export_pdf_button, h="hash_123")
    assert len(button_calls) == 1
    assert button_calls[0][0] == "Exporter résultats (Préparation...)"
    assert button_calls[0][1].get("disabled") is True

    # 2. Done
    mock_bg_res = {
        "status_refiner": "done",
        "jobs_enrichment": {"69123": {"status": "success_nonempty"}},
        "association_enrichment_status": {"69123": {"status": "success_nonempty"}},
        "inclusion_enrichment_status": {"69123": {"status": "success_nonempty"}},
    }
    monkeypatch.setattr("app.ui.results.odis_get_bg_result", lambda h: mock_bg_res)
    _call_fn(render_export_pdf_button, h="hash_123")
    assert len(button_calls) == 2
    assert button_calls[1][0] == "Exporter résultats"
    assert button_calls[1][1].get("disabled") is False


def test_render_details_trigger_button_states(monkeypatch):
    """Verify render_details_trigger_button enables as soon as hydrations are terminal, without waiting for refiner or AI analyses."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results("69123")
    commune = search_results.results[0]
    monkeypatch.setattr("app.ui.results.st.session_state", {"search_results": search_results})

    # 1. Hydration running
    monkeypatch.setattr("app.ui.results.odis_get_bg_result", lambda h: None)
    from app.ui.results import render_details_trigger_button
    _call_fn(render_details_trigger_button, commune=commune, h="hash_123")
    assert len(button_calls) == 1
    assert button_calls[0][0] == "En savoir plus (Préparation...)"
    assert button_calls[0][1].get("disabled") is True

    # 2. Hydration done, but Refiner still running -> "En savoir plus" MUST be enabled!
    mock_bg_res = {
        "status_refiner": "running",  # Refiner is NOT done yet
        "jobs_enrichment": {"69123": {"status": "success_nonempty"}},
        "association_enrichment_status": {"69123": {"status": "success_nonempty"}},
        "inclusion_enrichment_status": {"69123": {"status": "success_nonempty"}},
    }
    monkeypatch.setattr("app.ui.results.odis_get_bg_result", lambda h: mock_bg_res)
    _call_fn(render_details_trigger_button, commune=commune, h="hash_123")
    assert len(button_calls) == 2
    assert button_calls[1][0] == "En savoir plus"
    assert button_calls[1][1].get("disabled") is False
