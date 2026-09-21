from unittest.mock import MagicMock
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
    commune.name = "Bordeaux"
    commune.codgeo = codgeo
    commune.employment = MagicMock()
    commune.employment.matching_job_offers = []
    commune.employment.standard_jobs_matching_total = 0
    commune.inclusion = MagicMock()
    commune.inclusion.asso_inclusion_list_by_cat = {}
    commune.inclusion.asso_refugee_list = []
    commune.inclusion.services_detailed = {}
    commune.commune_results_hydrated = False
    commune.odis_synthesis = None
    commune.refiner_pitch = ""

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
    monkeypatch.setattr(
        "ui.results_actions.st.session_state", {"search_results": search_results}
    )
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
    monkeypatch.setattr(
        "ui.results_actions.st.session_state", {"search_results": search_results}
    )
    mock_bg_res = {
        "status_refiner": "done",
        "jobs_enrichment": {"69123": {"status": "success_nonempty"}},
        "association_enrichment_status": {"69123": {"status": "success_nonempty"}},
        "inclusion_services_status": {"69123": {"status": "success_nonempty"}},
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
    monkeypatch.setattr(
        "ui.results_actions.st.session_state", {"search_results": search_results}
    )

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
        "inclusion_services_status": {"69123": {"status": "success_nonempty"}},
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
    monkeypatch.setattr(
        "ui.results.st.session_state", {"search_results": search_results}
    )

    # 1. Hydration running
    monkeypatch.setattr("ui.results_actions.odis_get_bg_result", lambda h: None)
    _call_fn(render_details_trigger_button, commune=commune, h="hash_123")
    assert len(button_calls) == 1
    assert button_calls[0][0] == "En savoir plus (Préparation...)"
    assert button_calls[0][1].get("disabled") is True

    # 2. Hydration done, but Refiner still running -> "En savoir plus" MUST be enabled!
    mock_bg_res = {
        "status_refiner": "running",  # Refiner is NOT done yet
        "jobs_enrichment": {"69123": {"status": "success_nonempty"}},
        "association_enrichment_status": {"69123": {"status": "success_nonempty"}},
        "inclusion_services_status": {"69123": {"status": "success_nonempty"}},
    }
    monkeypatch.setattr("ui.results_actions.odis_get_bg_result", lambda h: mock_bg_res)
    _call_fn(render_details_trigger_button, commune=commune, h="hash_123")
    assert len(button_calls) == 2
    assert button_calls[1][0] == "En savoir plus"
    assert button_calls[1][1].get("disabled") is False


def test_buttons_enabled_when_commune_results_hydrated_flag_is_true(monkeypatch):
    """Verify all actions unlock immediately when commune_results_hydrated is True, even with empty bg store."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results("69123")
    commune = search_results.results[0]
    commune.commune_results_hydrated = True
    monkeypatch.setattr(
        "ui.results_actions.st.session_state", {"search_results": search_results}
    )
    monkeypatch.setattr(
        "ui.results.st.session_state", {"search_results": search_results}
    )
    # volatile bg store is completely empty / None
    monkeypatch.setattr("ui.results_actions.odis_get_bg_result", lambda h: None)

    # 1. En savoir plus
    _call_fn(render_details_trigger_button, commune=commune, h="hash_123")
    assert button_calls[-1][0] == "En savoir plus"
    assert button_calls[-1][1].get("disabled") is False

    # 2. Export PDF
    _call_fn(render_export_pdf_button, h="hash_123")
    assert button_calls[-1][0] == "Exporter résultats"
    assert button_calls[-1][1].get("disabled") is False

    # 3. Share results
    _call_fn(render_share_search_button, h="hash_123", button_text="Partager")
    assert button_calls[-1][0] == "Partager"
    assert button_calls[-1][1].get("disabled") is False


def test_render_active_dialogs_dispatches_only_one_dialog(monkeypatch):
    calls = []

    session_state = {
        "active_details_index": "33009",
        "active_ccas_index": "33009",
        "active_ia_city_index": "33009",
    }
    monkeypatch.setattr("ui.results.st.session_state", session_state)
    monkeypatch.setattr(
        "ui.results.show_details_dialog", lambda index: calls.append(("details", index))
    )
    monkeypatch.setattr(
        "ui.results.show_ccas_dialog", lambda index: calls.append(("ccas", index))
    )
    monkeypatch.setattr(
        "ui.results.show_ia_analysis_dialog", lambda index: calls.append(("ia", index))
    )

    render_active_dialogs()

    assert calls == [("ia", "33009")]
    assert session_state["active_details_index"] is None
    assert session_state["active_ccas_index"] is None


def test_render_active_dialogs_dispatches_action_dialogs(monkeypatch):
    """Verify export and share requests use the same root dispatcher."""
    for state_key, dialog_name in (
        ("active_pdf_modal", "pdf"),
        ("active_share_dialog", "share"),
    ):
        calls = []
        session_state = {state_key: True}
        monkeypatch.setattr("ui.results.st.session_state", session_state)
        monkeypatch.setattr("ui.results.pdf_modal", lambda: calls.append("pdf"))
        monkeypatch.setattr(
            "ui.results.share_search_modal", lambda: calls.append("share")
        )

        render_active_dialogs()

        assert calls == [dialog_name]


def test_render_details_trigger_button_requests_root_dispatch(monkeypatch):
    """Verify a fragment click requests, rather than opens, the details dialog."""
    reruns = []
    search_results = _create_mock_search_results("69123")
    commune = search_results.results[0]
    session_state = {"search_results": search_results}

    monkeypatch.setattr("ui.results.st.session_state", session_state)
    monkeypatch.setattr("ui.results._is_hydration_ready_for_city", lambda c, h: True)
    monkeypatch.setattr(st, "button", lambda label, **kwargs: True)
    monkeypatch.setattr(st, "rerun", lambda **kwargs: reruns.append(kwargs))
    monkeypatch.setattr(
        "ui.results.show_details_dialog",
        lambda codgeo: (_ for _ in ()).throw(
            AssertionError("dialog opened in fragment")
        ),
    )

    _call_fn(render_details_trigger_button, commune=commune, h="hash_123")

    assert session_state["active_details_index"] == "69123"
    assert reruns == [{"scope": "app"}]


def test_render_ai_trigger_button_in_immutable_snapshot_with_existing_analysis(
    monkeypatch,
):
    """Verify that in immutable snapshot mode, if an analysis already exists, the button is enabled."""
    button_calls = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    commune.odis_synthesis = [{"role": "assistant", "content": "Synthèse sauvegardée"}]
    monkeypatch.setattr(
        "ui.results.st.session_state",
        {
            "search_results": search_results,
            "immutable_shared_snapshot": True,
        },
    )

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")

    assert len(button_calls) == 1
    label, kwargs = button_calls[0]
    assert label == "Consulter l'analyse"
    assert kwargs.get("disabled") is False


def test_render_ai_trigger_button_snapshot_click_requests_app_rerun(monkeypatch):
    """Verify a stored analysis also opens through the root dispatcher."""
    reruns = []
    monkeypatch.setattr(st, "button", lambda label, **kwargs: True)
    monkeypatch.setattr(
        st,
        "rerun",
        lambda **kwargs: reruns.append(kwargs),
    )

    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    commune.odis_synthesis = [{"role": "assistant", "content": "Synthèse"}]
    session_state = {
        "search_results": search_results,
        "immutable_shared_snapshot": True,
    }
    monkeypatch.setattr("ui.results.st.session_state", session_state)

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")

    assert session_state["active_ia_city_index"] == "33063"
    assert reruns == [{"scope": "app"}]


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
    monkeypatch.setattr(
        "ui.results.st.session_state",
        {
            "search_results": search_results,
            "immutable_shared_snapshot": True,
        },
    )

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")

    assert len(button_calls) == 1
    label, kwargs = button_calls[0]
    assert label == "Analyse Avancée (non réalisée)"
    assert kwargs.get("disabled") is True


def test_render_ai_trigger_button_in_live_mode(monkeypatch):
    """Verify manual and in-progress live analysis states."""
    button_calls = []
    reruns = []

    def mock_button(label, **kwargs):
        button_calls.append((label, kwargs))
        return False

    monkeypatch.setattr(st, "button", mock_button)
    monkeypatch.setattr(
        st,
        "rerun",
        lambda **kwargs: reruns.append(kwargs),
    )
    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    commune.odis_synthesis = None
    commune.analysis_report = None
    monkeypatch.setattr(
        "ui.results.st.session_state",
        {
            "search_results": search_results,
            "immutable_shared_snapshot": False,
        },
    )
    monkeypatch.setattr(
        "ui.results.cfg.is_auto_analyse_top_cities_enabled", lambda: False
    )
    # 1. Postscoring not ready -> Lancement (disabled)
    monkeypatch.setattr("ui.results._is_postscoring_ready_for_city", lambda c, h: False)
    monkeypatch.setattr("ui.results.odis_get_bg_result", lambda k: None)

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert button_calls[0][0] == "Analyse Avancée [Préparation...]"
    assert button_calls[0][1].get("disabled") is True

    # 2. Postscoring ready -> stays a manual enabled trigger; no auto-launch.
    launched = []
    toasts = []
    monkeypatch.setattr(st, "toast", lambda msg, **kwargs: toasts.append(msg))
    monkeypatch.setattr(
        "ui.results.launch_background_city_analysis",
        lambda **kwargs: launched.append(kwargs),
    )
    monkeypatch.setattr("ui.results._is_postscoring_ready_for_city", lambda c, h: True)
    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert launched == []
    assert toasts == []
    assert button_calls[1][0] == "Analyse Avancée"
    assert button_calls[1][1].get("disabled") is False

    # 3. Running state (> 1s) -> En cours... (disabled)
    monkeypatch.setattr(
        "ui.results.odis_get_bg_result",
        lambda k: {"status": "running", "start_time": 100.0},
    )
    import time

    monkeypatch.setattr(time, "time", lambda: 105.0)
    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert button_calls[2][0] == "Analyse Avancée [En cours...]"
    assert button_calls[2][1].get("disabled") is True

    # 4. Error state -> Échec - Réessayer ? (enabled)
    monkeypatch.setattr(
        "ui.results.odis_get_bg_result",
        lambda k: {"status": "error", "error": "timeout"},
    )
    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert button_calls[3][0] == "Analyse Avancée [Échec - Réessayer ?]"
    assert button_calls[3][1].get("disabled") in (False, None)

    # 5. Done state -> Analyse Avancée (enabled) + toast emitted
    toasts = []
    monkeypatch.setattr(st, "toast", lambda msg, **kwargs: toasts.append(msg))
    monkeypatch.setattr(
        "ui.results.odis_get_bg_result",
        lambda k: {"status": "done", "result": {}},
    )
    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert button_calls[4][0] == "Consulter l'analyse"
    assert button_calls[4][1].get("disabled") in (False, None)
    assert len(toasts) == 1
    assert "disponible" in toasts[0]
    assert reruns == []


def test_render_ai_trigger_button_waits_for_planned_auto_stage(monkeypatch):
    """A planned top-five city cannot be auto-launched by the card renderer."""
    button_calls = []
    launched = []

    monkeypatch.setattr(
        st,
        "button",
        lambda label, **kwargs: button_calls.append((label, kwargs)) or False,
    )
    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    monkeypatch.setattr(
        "ui.results.st.session_state",
        {
            "search_results": search_results,
            "immutable_shared_snapshot": False,
        },
    )
    monkeypatch.setattr(
        "ui.results.cfg.is_auto_analyse_top_cities_enabled", lambda: True
    )
    monkeypatch.setattr("ui.results._is_postscoring_ready_for_city", lambda c, h: True)
    monkeypatch.setattr(
        "ui.results.odis_get_bg_result",
        lambda key: (
            {"auto_analysis_steps": {"33063": {"status": "waiting"}}}
            if key == "hash_123"
            else None
        ),
    )
    monkeypatch.setattr(
        "ui.results.launch_background_city_analysis",
        lambda **kwargs: launched.append(kwargs),
    )

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")

    assert launched == []
    assert button_calls[0][0] == "Analyse Avancée [Lancement...]"
    assert button_calls[0][1]["disabled"] is True


def test_render_ai_trigger_button_manual_click_launches_when_auto_disabled(monkeypatch):
    """A ready city starts analysis only after the user presses its button."""
    launched = []
    reruns = []
    monkeypatch.setattr(st, "button", lambda label, **kwargs: True)
    monkeypatch.setattr(st, "toast", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        st,
        "rerun",
        lambda **kwargs: reruns.append(kwargs),
    )
    monkeypatch.setattr(
        "ui.results.launch_background_city_analysis",
        lambda **kwargs: launched.append(kwargs),
    )
    monkeypatch.setattr("ui.results.ui_telemetry.track_ui_event", lambda *args: None)
    monkeypatch.setattr(
        "ui.results.cfg.is_auto_analyse_top_cities_enabled", lambda: False
    )
    monkeypatch.setattr("ui.results._is_postscoring_ready_for_city", lambda c, h: True)
    monkeypatch.setattr("ui.results.odis_get_bg_result", lambda key: None)

    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    monkeypatch.setattr(
        "ui.results.st.session_state",
        {
            "search_results": search_results,
            "config": object(),
            "immutable_shared_snapshot": False,
        },
    )

    _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")

    assert len(launched) == 1
    assert launched[0]["trigger"] == "user_modal"
    assert reruns == [{"scope": "fragment"}]
    assert "active_ia_city_index" not in st.session_state


def test_render_ai_trigger_button_retry_action(monkeypatch):
    """Verify retry re-launches analysis without opening a dialog."""
    launched = []
    toasts = []
    reruns = []

    monkeypatch.setattr(st, "button", lambda label, **kwargs: True)
    monkeypatch.setattr(st, "toast", lambda msg, **kwargs: toasts.append(msg))
    monkeypatch.setattr(
        st,
        "rerun",
        lambda **kwargs: reruns.append(kwargs),
    )
    monkeypatch.setattr(
        "ui.results.launch_background_city_analysis",
        lambda **kwargs: launched.append(kwargs),
    )
    monkeypatch.setattr(
        "ui.results.odis_get_bg_result",
        lambda k: {"status": "error", "error": "timeout"},
    )

    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    monkeypatch.setattr(
        "ui.results.st.session_state",
        {
            "search_results": search_results,
            "config": MagicMock(),
            "immutable_shared_snapshot": False,
            "ia_analysis_launch_toasted": {"33063"},
            "ia_analysis_toasted": {"33063"},
        },
    )

    res = _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert res is False
    assert len(launched) == 1
    assert launched[0]["retry"] is True
    assert launched[0]["trigger"] == "city_card_retry"
    assert len(toasts) == 1
    assert "lancée..." in toasts[0]
    assert reruns == [{"scope": "fragment"}]


def test_render_ai_trigger_button_requests_dialog_on_app_rerun(monkeypatch):
    """Verify the completed button defers dialog opening to the root dispatcher."""
    dialog_calls = []
    telemetry_calls = []
    reruns = []

    monkeypatch.setattr(st, "button", lambda label, **kwargs: True)
    monkeypatch.setattr(
        "ui.results.show_ia_analysis_dialog", lambda codgeo: dialog_calls.append(codgeo)
    )
    monkeypatch.setattr(
        st,
        "rerun",
        lambda **kwargs: reruns.append(kwargs),
    )
    monkeypatch.setattr(
        "ui.results.ui_telemetry.track_ui_event",
        lambda event, data: telemetry_calls.append((event, data)),
    )
    monkeypatch.setattr(
        "ui.results.odis_get_bg_result",
        lambda k: {"status": "done", "result": {}},
    )

    search_results = _create_mock_search_results("33063")
    commune = search_results.results[0]
    session_state = {
        "search_results": search_results,
        "immutable_shared_snapshot": False,
    }
    monkeypatch.setattr("ui.results.st.session_state", session_state)

    res = _call_fn(render_ai_trigger_button, commune=commune, h="hash_123")
    assert res is True
    assert dialog_calls == []
    assert session_state.get("active_ia_city_index") == "33063"
    assert reruns == [{"scope": "app"}]
    assert len(telemetry_calls) == 1
    assert telemetry_calls[0][0] == "run_ia_analysis"


def test_ia_analysis_content_does_not_start_missing_analysis(monkeypatch):
    """Verify the modal only presents a status if an analysis is unavailable."""
    from ui.ai_analysis_dialog import ia_analysis_content

    info_calls = []
    launched = []
    search_results = _create_mock_search_results("69123")
    commune = search_results.results[0]
    commune.analysis_report = None
    search_results.get_by_code.return_value = commune

    monkeypatch.setattr(
        "ui.ai_analysis_dialog.st.session_state",
        {
            "search_results": search_results,
            "active_search_hash": "hash_123",
            "immutable_shared_snapshot": False,
        },
    )
    monkeypatch.setattr(
        "ui.ai_analysis_dialog.st.info",
        lambda text: info_calls.append(text),
    )
    monkeypatch.setattr(
        "ui.ai_analysis_dialog.launch_background_city_analysis",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )

    _call_fn(ia_analysis_content, "Lyon", "69123", MagicMock())

    assert launched == []
    assert len(info_calls) == 1
    assert "pas encore disponible" in info_calls[0]


def test_share_search_modal_renders_unified_actions(monkeypatch):
    """Verify share_search_modal invokes unified _share_actions_component with slack msg and mailto url."""
    from ui.results_actions import (
        share_search_modal,
        _share_actions_component,
        _slack_share_component,
    )

    assert _slack_share_component == _share_actions_component

    rendered_components = []

    def mock_component(*args, **kwargs):
        rendered_components.append(kwargs)

    monkeypatch.setattr("ui.results_actions._share_actions_component", mock_component)
    monkeypatch.setattr("ui.results_actions.st.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr("ui.results_actions.st.code", lambda *args, **kwargs: None)

    search_results = _create_mock_search_results("69123")
    monkeypatch.setattr(
        "ui.results_actions.st.session_state",
        {
            "search_results": search_results,
            "config": MagicMock(),
            "active_share_id": "share_abc123",
        },
    )

    _call_fn(share_search_modal)

    assert len(rendered_components) == 1
    data = rendered_components[0]["data"]
    assert "mailto_url" in data
    assert "mailto:?subject=" in data["mailto_url"]
    assert "search=share_abc123" in data["mailto_url"]
    assert "msg" in data
    assert "search=share_abc123" in data["msg"]
    assert rendered_components[0]["key"] == "share_actions_share_abc123"


def test_decode_idna_host():
    """Verify _decode_idna_host decodes punycode domains and preserves ports."""
    from ui.results_actions import _decode_idna_host

    assert (
        _decode_idna_host("xn--mobilits-h1a.jaccueille.fr") == "mobilités.jaccueille.fr"
    )
    assert (
        _decode_idna_host("xn--mobilits-h1a.jaccueille.fr:443")
        == "mobilités.jaccueille.fr:443"
    )
    assert _decode_idna_host("localhost:8501") == "localhost:8501"
    assert _decode_idna_host("odis.jaccueille.fr") == "odis.jaccueille.fr"
    assert _decode_idna_host("mobilités.jaccueille.fr") == "mobilités.jaccueille.fr"
    assert _decode_idna_host("") == ""


def test_share_search_modal_decodes_punycode_host(monkeypatch):
    """Verify share_search_modal formats URL using decoded unicode domain instead of punycode."""
    from ui.results_actions import share_search_modal

    rendered_components = []
    code_outputs = []

    monkeypatch.setattr(
        "ui.results_actions._share_actions_component",
        lambda *args, **kwargs: rendered_components.append(kwargs),
    )
    monkeypatch.setattr("ui.results_actions.st.markdown", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "ui.results_actions.st.code", lambda code, **kwargs: code_outputs.append(code)
    )

    # Mock st.context.headers with punycode host
    class MockContext:
        headers = {"host": "xn--mobilits-h1a.jaccueille.fr"}

    monkeypatch.setattr("ui.results_actions.st.context", MockContext(), raising=False)

    search_results = _create_mock_search_results("69123")
    monkeypatch.setattr(
        "ui.results_actions.st.session_state",
        {
            "search_results": search_results,
            "config": MagicMock(),
            "active_share_id": "share_xyz789",
        },
    )

    _call_fn(share_search_modal)

    expected_url = "https://mobilités.jaccueille.fr/?search=share_xyz789"
    assert len(code_outputs) == 1
    assert code_outputs[0] == expected_url
    assert len(rendered_components) == 1
    assert expected_url in rendered_components[0]["data"]["msg"]
