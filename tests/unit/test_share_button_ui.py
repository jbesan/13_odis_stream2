from unittest.mock import MagicMock
import pytest
import streamlit as st
from ui.results import (
    render_share_search_button,
    render_export_pdf_button,
    render_details_trigger_button,
    render_active_dialogs,
    render_ai_trigger_button,
)
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
    monkeypatch.setattr("ui.results_actions.st.session_state", {"search_results": search_results})
    monkeypatch.setattr("ui.results_actions.odis_get_bg_result", lambda h: None)

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
    monkeypatch.setattr("ui.results_actions.st.session_state", {"search_results": search_results})
    mock_bg_res = {
        "status_refiner": "done",
        "jobs_enrichment": {"69123": {"status": "success_nonempty"}},
        "association_enrichment_status": {"69123": {"status": "success_nonempty"}},
        "inclusion_enrichment_status": {"69123": {"status": "success_nonempty"}},
    }
    monkeypatch.setattr("ui.results_actions.odis_get_bg_result", lambda h: mock_bg_res)

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
    monkeypatch.setattr("ui.results_actions.st.session_state", {"search_results": search_results})

    # 1. Not done
    monkeypatch.setattr("ui.results_actions.odis_get_bg_result", lambda h: None)
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
    monkeypatch.setattr("ui.results_actions.odis_get_bg_result", lambda h: mock_bg_res)
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
    monkeypatch.setattr("ui.results.st.session_state", {"search_results": search_results})

    # 1. Hydration running
    monkeypatch.setattr("ui.results.odis_get_bg_result", lambda h: None)
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
    monkeypatch.setattr("ui.results.odis_get_bg_result", lambda h: mock_bg_res)
    _call_fn(render_details_trigger_button, commune=commune, h="hash_123")
    assert len(button_calls) == 2
    assert button_calls[1][0] == "En savoir plus"
    assert button_calls[1][1].get("disabled") is False


def test_render_active_dialogs_dispatches_all_result_dialogs(monkeypatch):
    calls = []

    monkeypatch.setattr("ui.results.st.session_state", {
        "active_details_index": "33009",
        "active_ccas_index": "33009",
        "active_ia_city_index": "33009",
    })
    monkeypatch.setattr("ui.results.show_details_dialog", lambda index: calls.append(("details", index)))
    monkeypatch.setattr("ui.results.show_ccas_dialog", lambda index: calls.append(("ccas", index)))
    monkeypatch.setattr("ui.results.show_ia_analysis_dialog", lambda index: calls.append(("ia", index)))

    render_active_dialogs()

    assert calls == [
        ("ia", "33009"),
        ("details", "33009"),
        ("ccas", "33009"),
    ]


def test_render_ai_trigger_button_in_immutable_snapshot_with_existing_analysis(monkeypatch):
    """Verify that in immutable snapshot mode, if an analysis already exists, the button is enabled."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    commune.odis_synthesis = [{"role": "assistant", "content": "Synthèse sauvegardée"}]
    monkeypatch.setattr("ui.results.st.session_state", {
        "search_results": search_results,
        "immutable_shared_snapshot": True,
    })

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")

    assert len(button_calls) == 1
    label, kwargs = button_calls[0]
    assert label == "Consulter l'Analyse Avancée"
    assert kwargs.get("disabled") is False


def test_render_ai_trigger_button_in_immutable_snapshot_without_analysis(monkeypatch):
    """Verify that in immutable snapshot mode, if no analysis exists, the button is disabled."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    commune.odis_synthesis = None
    commune.analysis_report = None
    monkeypatch.setattr("ui.results.st.session_state", {
        "search_results": search_results,
        "immutable_shared_snapshot": True,
    })

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")

    assert len(button_calls) == 1
    label, kwargs = button_calls[0]
    assert label == "Analyse Avancée (non réalisée)"
    assert kwargs.get("disabled") is True


def test_render_ai_trigger_button_in_live_mode(monkeypatch):
    """Verify that in live mode, button displays standard labels depending on readiness."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    commune.odis_synthesis = None
    commune.analysis_report = None
    monkeypatch.setattr("ui.results.st.session_state", {
        "search_results": search_results,
        "immutable_shared_snapshot": False,
    })
    # Postscoring not ready
    monkeypatch.setattr("ui.results._is_postscoring_ready_for_city", lambda c, h: False)

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert button_calls[0][0] == "Analyse Avancée (Préparation...)"
    assert button_calls[0][1].get("disabled") is True

    # Postscoring ready
    monkeypatch.setattr("ui.results._is_postscoring_ready_for_city", lambda c, h: True)
    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert button_calls[1][0] == "Analyse Avancée"
    assert button_calls[1][1].get("disabled") is False


from ui.ai_analysis_dialog import polling_synthesis_fragment


class _MockStatus:
    def __init__(self, label: str, expanded: bool = True):
        self.label = label
        self.expanded = expanded

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_polling_synthesis_fragment_renders_status_and_progress_on_first_turn(monkeypatch):
    """Verify that on initial launch (status_data is None), background analysis is started
    and st.status + st.progress are immediately rendered rather than stalling on a caption.
    """
    status_calls = []
    markdown_calls = []
    caption_calls = []
    progress_calls = []
    button_calls = []

    monkeypatch.setattr(
        "ui.ai_analysis_dialog.st.status",
        lambda label, **kwargs: (status_calls.append((label, kwargs)) or _MockStatus(label, **kwargs)),
    )
    monkeypatch.setattr("ui.ai_analysis_dialog.st.markdown", lambda text: markdown_calls.append(text))
    monkeypatch.setattr("ui.ai_analysis_dialog.st.caption", lambda text: caption_calls.append(text))
    monkeypatch.setattr(
        "ui.ai_analysis_dialog.st.progress",
        lambda val, text=None: progress_calls.append((val, text)),
    )
    monkeypatch.setattr(
        "ui.ai_analysis_dialog.st.button",
        lambda label, **kwargs: (button_calls.append((label, kwargs)) or False),
    )

    search_results = _create_mock_search_results("69123")
    commune = search_results.results[0]
    mock_state = type("MockState", (dict,), {"__getattr__": dict.__getitem__})({"search_results": search_results})
    monkeypatch.setattr("ui.ai_analysis_dialog.st.session_state", mock_state)

    # Initially no bg result, then transitions to done inside the in-place polling loop
    call_count = [0]

    def mock_get_bg(task_key):
        call_count[0] += 1
        if call_count[0] == 1:
            return None
        return {"status": "done", "result": {}}

    monkeypatch.setattr("ui.ai_analysis_dialog.odis_get_bg_result", mock_get_bg)

    # launch_background_city_analysis starts the task and returns running state
    launched = []

    def mock_launch(nom, codgeo, search_criterias, results, h):
        launched.append((nom, codgeo))
        return {"status": "running", "start_time": 100.0, "deadline_at": 160.0}

    monkeypatch.setattr("ui.ai_analysis_dialog.launch_background_city_analysis", mock_launch)
    import time

    monkeypatch.setattr(time, "time", lambda: 102.0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    rerun_calls = []
    monkeypatch.setattr("ui.ai_analysis_dialog.st.rerun", lambda: rerun_calls.append(True))

    _call_fn(
        polling_synthesis_fragment,
        task_key="analysis_69123_hash_123",
        nom="Lyon",
        codgeo="69123",
        search_criterias=MagicMock(),
        commune=commune,
        h="hash_123",
    )

    assert len(launched) == 1
    assert launched[0] == ("Lyon", "69123")

    # Status and progress must be immediately visible on turn 1
    assert len(status_calls) == 1
    assert status_calls[0][0] == "🧠 Analyse stratégique en cours..."
    assert status_calls[0][1].get("expanded") is True

    # At elapsed = 2s, step 1 is active, experts are pending
    assert any("Étape 1" in m for m in markdown_calls)
    assert any("En attente des experts" in c for c in caption_calls)

    assert len(progress_calls) == 1
    progress_val, progress_text = progress_calls[0]
    assert pytest.approx(progress_val, 0.001) == 2.0 / 60.0
    assert "Préparation de la synthèse" in progress_text

    # Cancel button is displayed
    assert any("Annuler l'analyse" in b[0] for b in button_calls)

    # st.rerun must be called to schedule the next tick
    assert len(rerun_calls) == 1


def test_polling_synthesis_fragment_step_progression_with_elapsed_time(monkeypatch):
    """Verify that as time elapses, st.status reveals steps 2 and 3 progressively."""
    status_calls = []
    markdown_calls = []
    caption_calls = []
    progress_calls = []

    monkeypatch.setattr(
        "ui.ai_analysis_dialog.st.status",
        lambda label, **kwargs: (status_calls.append((label, kwargs)) or _MockStatus(label, **kwargs)),
    )
    monkeypatch.setattr("ui.ai_analysis_dialog.st.markdown", lambda text: markdown_calls.append(text))
    monkeypatch.setattr("ui.ai_analysis_dialog.st.caption", lambda text: caption_calls.append(text))
    monkeypatch.setattr(
        "ui.ai_analysis_dialog.st.progress",
        lambda val, text=None: progress_calls.append((val, text)),
    )
    monkeypatch.setattr("ui.ai_analysis_dialog.st.button", lambda label, **kwargs: False)

    search_results = _create_mock_search_results("69123")
    commune = search_results.results[0]
    monkeypatch.setattr("ui.ai_analysis_dialog.st.session_state", {"search_results": search_results})

    # Already running, elapsed = 20s, then transitions to done inside in-place polling loop
    call_count = [0]

    def mock_get_bg(task_key):
        call_count[0] += 1
        if call_count[0] == 1:
            return {"status": "running", "start_time": 100.0, "deadline_at": 160.0}
        return {"status": "done", "result": {}}

    monkeypatch.setattr("ui.ai_analysis_dialog.odis_get_bg_result", mock_get_bg)
    import time

    monkeypatch.setattr(time, "time", lambda: 120.0)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    rerun_calls = []
    monkeypatch.setattr("ui.ai_analysis_dialog.st.rerun", lambda: rerun_calls.append(True))

    _call_fn(
        polling_synthesis_fragment,
        task_key="analysis_69123_hash_123",
        nom="Lyon",
        codgeo="69123",
        search_criterias=MagicMock(),
        commune=commune,
        h="hash_123",
    )

    # At elapsed = 20s, all 3 steps should be displayed in markdown
    assert any("Étape 1" in m for m in markdown_calls)
    assert any("Étape 2" in m for m in markdown_calls)
    assert any("Étape 3" in m for m in markdown_calls)
    assert len(caption_calls) == 0
    assert len(rerun_calls) == 1

