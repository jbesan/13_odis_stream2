"""E2E AppTest scenarios testing UI form transitions, validation blocking, and error resilience."""

from pathlib import Path
from unittest.mock import patch
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core.models import User, SearchCriterias
from ui.results_actions import _is_postscoring_ready_for_search
from agents.utils import get_odis_bg_store

pytestmark = pytest.mark.e2e


@pytest.fixture
def base_app_test():
    """Initializes AppTest with authenticated session state at main.py."""
    root_path = Path(__file__).resolve().parent.parent.parent
    at = AppTest.from_file(str(root_path / "app" / "main.py"), default_timeout=60)

    at.session_state["password_correct"] = True
    at.session_state["auth_method"] = "local"
    at.session_state["username"] = "test"
    at.session_state["user"] = User(username="test")
    at.session_state["org"] = None
    at.session_state["demo_data"] = {}
    return at


@patch("ui.page_shell.inject_idle_disconnect")
@patch("core.postscoring.launch_post_scoring_tasks")
@patch("utils.data_loader.fetch_salesforce_jaccueille_bdv")
@patch("services.rna_rag.RNARagService")
def test_location_validation_blocks_progression(
    mock_rna_rag,
    mock_fetch_salesforce,
    mock_launch_post_scoring_tasks,
    mock_inject_idle_disconnect,
    base_app_test,
):
    """Scenario 1: Mandatory location validation blocks next button and displays warning."""
    at = base_app_test

    # 1. Run main -> 1_Accueil
    at.run(timeout=60)
    assert len(at.exception) == 0

    # 2. Switch to 2_Formulaire.py
    at.switch_page("pages/2_Formulaire.py").run(timeout=60)
    assert len(at.exception) == 0
    assert at.session_state.form_page == "localisation"

    # 3. Try to click 'Suivant' without selecting a commune
    next_btn = next(b for b in at.button if b.label == "Suivant")
    next_btn.click().run(timeout=60)

    # Progression must be blocked at 'localisation' step
    assert at.session_state.form_page == "localisation"
    assert "location_validation_warning" in at.session_state
    assert len(at.warning) > 0
    assert "Pas si vite" in at.warning[0].value

    # 4. Try to click sidebar 'Passer aux résultats' without location
    sidebar_btn = next(b for b in at.button if b.label == "Passer aux résultats")
    sidebar_btn.click().run(timeout=60)
    assert at.session_state.form_page == "localisation"
    assert "location_validation_warning" in at.session_state

    # 5. Now select valid commune AND mobility dept and verify progression unblocks
    at.selectbox(key="ui_commune").select("33063").run()
    at.multiselect(key="ui_mobility_dept").select("33").run()
    next_btn = next(b for b in at.button if b.label == "Suivant")
    next_btn.click().run(timeout=60)

    assert at.session_state.form_page == "family"
    assert "location_validation_warning" not in at.session_state


@patch("ui.page_shell.inject_idle_disconnect")
@patch("core.postscoring.launch_post_scoring_tasks")
@patch("utils.data_loader.fetch_salesforce_jaccueille_bdv")
@patch("services.rna_rag.RNARagService")
def test_search_modification_replaces_prior_results(
    mock_rna_rag,
    mock_fetch_salesforce,
    mock_launch_post_scoring_tasks,
    mock_inject_idle_disconnect,
    base_app_test,
):
    """Scenario 2: Modifying criteria cleanly replaces prior search results."""
    at = base_app_test
    mock_fetch_salesforce.return_value = pd.DataFrame(
        columns=["bassin_de_vie", "contact_count", "lead_count"]
    )

    def fake_launch(engine, config, search_results, h):
        store = get_odis_bg_store()
        store[h] = {
            "status_refiner": "done",
            "pitches": {c.codgeo: f"Pitch for {c.name}" for c in search_results.results},
            "enrichment": {c.codgeo: {} for c in search_results.results},
            "jobs": {c.codgeo: [] for c in search_results.results},
        }

    mock_launch_post_scoring_tasks.side_effect = fake_launch

    # Run main -> Formulaire
    at.run(timeout=60)
    at.switch_page("pages/2_Formulaire.py").run(timeout=60)

    # Fill minimum required for Search A on localisation page
    at.selectbox(key="ui_commune").select("33063").run()
    at.multiselect(key="ui_mobility_dept").select("33").run()

    # Move to family page and set nb_adultes = 1
    next_btn = next(b for b in at.button if b.label == "Suivant")
    next_btn.click().run(timeout=60)
    assert at.session_state.form_page == "family"
    at.radio(key="ui_nb_adultes").set_value(1).run()

    # Submit Search A via sidebar button
    sidebar_btn = next(b for b in at.button if b.label == "Passer aux résultats")
    sidebar_btn.click().run(timeout=60)

    # Verify Search A completed and produced results
    assert len(at.exception) == 0
    assert "config" in at.session_state
    config_a: SearchCriterias = at.session_state["config"]
    hash_a = at.session_state.search_results.search_hash
    assert config_a.nb_adultes == 1

    # Modify criteria: return to form (which preserves form_page == 'family') and change nb_adultes to 2
    at.switch_page("pages/2_Formulaire.py").run(timeout=60)
    assert len(at.exception) == 0
    assert at.session_state.form_page == "family"
    at.radio(key="ui_nb_adultes").set_value(2).run()

    # Re-submit Search B via sidebar button
    sidebar_btn = next(b for b in at.button if b.label == "Passer aux résultats")
    sidebar_btn.click().run(timeout=60)
    assert len(at.exception) == 0

    # Verify that Search B cleanly replaced Search A
    config_b: SearchCriterias = at.session_state["config"]
    hash_b = at.session_state.search_results.search_hash

    assert config_b.nb_adultes == 2
    assert hash_b != hash_a
    assert at.session_state.search_results.search_hash == hash_b


def test_enrichment_timeout_and_error_graceful_unlock():
    """Scenario 3: Failed/timed-out background enrichments reach terminal state and unblock actions."""
    import streamlit as st
    from core.models import SearchResultsData, CommuneResult
    from core.enrichment_status import EnrichmentStatus

    commune_1 = CommuneResult(codgeo="33063", name="Bordeaux", global_score=0.85)
    commune_2 = CommuneResult(codgeo="64445", name="Pau", global_score=0.75)
    current_geo = CommuneResult(codgeo="75056", name="Paris", global_score=0.5)
    search_results = SearchResultsData(
        search_hash="test_hash_timeout_123",
        results=[commune_1, commune_2],
        current_geo=current_geo,
    )

    st.session_state["search_results"] = search_results
    st.session_state["immutable_shared_snapshot"] = False

    store = get_odis_bg_store()
    # Simulate failed and timed-out post-scoring workers
    store["test_hash_timeout_123"] = {
        "status_refiner": "timeout",
        "jobs_enrichment": {
            "33063": {"status": EnrichmentStatus.ERROR.value},
            "64445": {"status": EnrichmentStatus.TIMEOUT.value},
        },
        "association_enrichment_status": {
            "33063": {"status": EnrichmentStatus.TIMEOUT.value},
            "64445": {"status": EnrichmentStatus.ERROR.value},
        },
        "inclusion_enrichment_status": {
            "33063": {"status": EnrichmentStatus.NOT_CONFIGURED.value},
            "64445": {"status": EnrichmentStatus.TIMEOUT.value},
        },
    }

    # Verify that terminal failure states are recognized as ready (unblocking export & share)
    ready = _is_postscoring_ready_for_search("test_hash_timeout_123")
    assert ready is True, "Terminal error/timeout status did not unlock post-scoring readiness"
