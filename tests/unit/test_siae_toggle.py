"""Unit tests for the SIAE search option toggle."""

import pandas as pd
import pytest

from core.models import CriteriaItem, SearchCriterias, SearchResultsData
from core import scoring
from ui.form_state import FormState


def test_search_criterias_recherche_siae_default():
    """Verify that recherche_siae defaults to True in SearchCriterias."""
    criteria = SearchCriterias()
    assert criteria.recherche_siae is True


def test_search_criterias_recherche_siae_explicit():
    """Verify that recherche_siae can be set to False explicitly."""
    criteria = SearchCriterias(recherche_siae=False)
    assert criteria.recherche_siae is False


def test_form_state_collect_recherche_siae():
    """Verify that FormState.collect correctly collects recherche_siae."""
    # When ui_recherche_siae is missing, defaults to True
    state = {}
    fs = FormState(state)
    collected = fs.collect({})
    assert collected.recherche_siae is True

    # When ui_recherche_siae is True
    state["ui_recherche_siae"] = True
    collected = fs.collect({})
    assert collected.recherche_siae is True

    # When ui_recherche_siae is False
    state["ui_recherche_siae"] = False
    collected = fs.collect({})
    assert collected.recherche_siae is False


def test_form_state_hydrate_recherche_siae():
    """Verify that FormState.hydrate correctly populates ui_recherche_siae."""
    state = {}
    fs = FormState(state)

    criteria = SearchCriterias(recherche_siae=False)
    fs.hydrate(criteria)
    assert state.get("ui_recherche_siae") is False

    criteria_true = SearchCriterias(recherche_siae=True)
    fs.hydrate(criteria_true)
    assert state.get("ui_recherche_siae") is True


@pytest.fixture
def base_sample_data():
    """Simple 3-row dataset for fast isolated unit tests."""
    data = {
        "codgeo": ["13055", "13001", "64445"],
        "libgeo": ["Marseille", "Aix", "Pau"],
        "dep_code": ["13", "13", "64"],
        "reg_code": ["93", "93", "75"],
        "bassin_de_vie": ["13055", "13001", "64445"],
        "population": [870000, 140000, 77000],
        "epci_code": ["200054807", "200054807", "246401722"],
        "nb_stops_total": [100, 20, 5],
        "sante_hopital_scaled": [0.8, 0.4, 0.1],
    }
    return pd.DataFrame(data).set_index("codgeo")


def test_scoring_active_criteria_with_siae_true(base_sample_data, live_scores_cat):
    """Verify that when recherche_siae is True, SIAE job and density criteria are active."""
    engine = scoring.ScoringEngine(
        df_all_communes=base_sample_data,
        df_bv_geo=pd.DataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        live_jobs_data=pd.DataFrame(),
        siae_jobs_data=pd.DataFrame(),
    )

    config = SearchCriterias(
        nb_adultes=1,
        codes_metiers=[[CriteriaItem(code="K1302", label="Aide domicile")]],
        recherche_siae=True,
    )

    active = engine._get_active_criteria(config)
    assert "met_siae_match_adult1_scaled" in active
    assert "inc_siae_density_scaled" in active


def test_scoring_active_criteria_with_siae_false(base_sample_data, live_scores_cat):
    """Verify that when recherche_siae is False, both met_siae_match and inc_siae_density are deactivated."""
    engine = scoring.ScoringEngine(
        df_all_communes=base_sample_data,
        df_bv_geo=pd.DataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        live_jobs_data=pd.DataFrame(),
        siae_jobs_data=pd.DataFrame(),
    )

    config = SearchCriterias(
        nb_adultes=1,
        codes_metiers=[[CriteriaItem(code="K1302", label="Aide domicile")]],
        recherche_siae=False,
    )

    active = engine._get_active_criteria(config)
    assert "met_siae_match_adult1_scaled" not in active
    assert "inc_siae_density_scaled" not in active


def test_evaluate_employment_features_siae_disabled(base_sample_data, live_scores_cat):
    """Verify that _evaluate_employment_features disables SIAE data when recherche_siae is False."""
    sample_siae_jobs = pd.DataFrame(
        [
            {"codgeo": "13055", "rome": "K1302", "rome_label": "Aide domicile"},
        ]
    )

    engine = scoring.ScoringEngine(
        df_all_communes=base_sample_data,
        df_bv_geo=pd.DataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        live_jobs_data=pd.DataFrame(),
        siae_jobs_data=sample_siae_jobs,
    )

    # With recherche_siae=False
    config_disabled = SearchCriterias(
        nb_adultes=1,
        codes_metiers=[[CriteriaItem(code="K1302", label="Aide domicile")]],
        recherche_siae=False,
    )

    res_disabled = engine.format_city_details(base_sample_data.loc["13055"], config_disabled)
    assert res_disabled.employment.source_availability.get("emplois_inclusion") == "disabled"
    assert res_disabled.employment.inclusive_jobs_total == 0
    assert res_disabled.employment.inclusive_jobs_matching_total == 0
    assert res_disabled.employment.inclusive_jobs_summary == {}
    assert res_disabled.employment.inclusive_jobs_matching_summary == {}

    # Ensure SearchResultsData and Pydantic validation succeed with 'disabled' availability
    results_data = SearchResultsData(
        search_hash="test_siae_disabled",
        results=[res_disabled],
        current_geo=res_disabled,
    )
    assert (
        results_data.results[0].employment.source_availability.get("emplois_inclusion")
        == "disabled"
    )
    dumped = results_data.model_dump()
    revalidated = SearchResultsData.model_validate(dumped)
    assert (
        revalidated.results[0].employment.source_availability.get("emplois_inclusion")
        == "disabled"
    )

    # With recherche_siae=True
    config_enabled = SearchCriterias(
        nb_adultes=1,
        codes_metiers=[[CriteriaItem(code="K1302", label="Aide domicile")]],
        recherche_siae=True,
    )

    res_enabled = engine.format_city_details(base_sample_data.loc["13055"], config_enabled)
    assert res_enabled.employment.source_availability.get("emplois_inclusion") == "available"
    assert res_enabled.employment.inclusive_jobs_total == 1
    assert res_enabled.employment.inclusive_jobs_matching_total == 1
