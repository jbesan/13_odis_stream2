import pytest
import time
import streamlit as st
from unittest.mock import patch, MagicMock
from core.models import JobOfferDetail, EmploymentMetrics, CommuneResult
from agents.utils import get_odis_bg_store
from core.postscoring import launch_background_job_curation


@pytest.fixture(autouse=True)
def clean_streamlit_session():
    """Cleans up streamlit session state before and after each test."""
    st.session_state.clear()
    import os

    with patch.dict(os.environ, {"ODIS_AI_FREE_MODE": "False"}):
        yield
    st.session_state.clear()


def test_job_offer_detail_model_validation():
    """Validates that JobOfferDetail instantiates and validates correctly."""
    offer_data = {
        "id": "123ABCD",
        "title": "Ingénieur IA",
        "company": "ODIS Lab",
        "contract_type": "CDI",
        "contract_label": "Contrat à durée indéterminée",
        "description": "Développement d'agents intelligents",
        "location": "Bordeaux",
        "location_insee": "33063",
        "salary": "45K-50K €",
        "url": "https://example.com/apply",
    }

    offer = JobOfferDetail.model_validate(offer_data)
    assert offer.id == "123ABCD"
    assert offer.title == "Ingénieur IA"
    assert offer.location_insee == "33063"

    # Verify model dump
    dump = offer.model_dump(mode="json")
    assert dump["id"] == "123ABCD"
    assert dump["location_insee"] == "33063"


def test_employment_metrics_matching_job_offers():
    """Validates that EmploymentMetrics can house matching_job_offers as a list of lists."""
    offer1 = JobOfferDetail(
        id="OFFER1", title="Boulanger", contract_type="CDI", location_insee="33063"
    )
    offer2 = JobOfferDetail(
        id="OFFER2", title="Pâtissier", contract_type="CDD", location_insee="33063"
    )

    metrics = EmploymentMetrics(
        cat_score=0.85,
        standard_jobs_total=120,
        matching_job_offers=[
            [offer1],  # Adult 1 jobs
            [offer2],  # Adult 2 jobs
        ],
    )

    assert len(metrics.matching_job_offers) == 2
    assert metrics.matching_job_offers[0][0].id == "OFFER1"
    assert metrics.matching_job_offers[1][0].id == "OFFER2"

    # Verify serialization
    dump = metrics.model_dump(mode="json")
    assert len(dump["matching_job_offers"]) == 2
    assert dump["matching_job_offers"][0][0]["id"] == "OFFER1"


@patch("services.mcp_france_travail._search_job_offers_logic")
@patch("agents.job_curator.job_curator_agent.run")
def test_background_jobs_enrichment_success(mock_curator_run, mock_search):
    """Verifies that background jobs enrichment queries ROME codes, pools them, curates via LLM, and preserves order."""
    # 1. Mock search API response returning 10 offers per ROME code
    mock_search.return_value = {
        "offres": [
            {
                "id": f"J{k}",
                "intitule": f"Job {k}",
                "typeContrat": "CDI",
                "typeContratLibelle": "CDI",
                "description_sh": f"Desc {k}",
                "lieuTravail": {"libelle": "Bordeaux", "codeINSEE": "33063"},
                "entreprise": {"nom": "Company A"},
                "salaire": {"libelle": "A négocier"},
                "origineOffre": {"urlOrigine": f"https://ft.fr/J{k}"},
            }
            for k in range(10)
        ],
        "total": 10,
    }

    # 2. Mock LLM curation return value (selecting even numbered IDs in reverse)
    from agents.job_curator import JobCurationResult, CuratedJob
    from unittest.mock import AsyncMock

    mock_result = MagicMock()
    mock_result.output = JobCurationResult(
        selected_jobs=[
            CuratedJob(job_id=f"J{k}", job_brief=f"Brief for job J{k}")
            for k in [8, 6, 4, 2, 0]
        ]
    )
    mock_curator_run.side_effect = AsyncMock(return_value=mock_result)

    from core.models import CriteriaItem, SearchCriterias

    codes_metiers = [
        [
            CriteriaItem(code="M1805", label="Développement informatique")
        ],  # Adult 1: Dev
        [CriteriaItem(code="A1201", label="Boulangerie")],  # Adult 2: Boulanger
    ]

    config = SearchCriterias(
        codes_metiers=codes_metiers,
        odis_brief="Candidat motivé recherchant un emploi.",
        notes_qualitatives=["Permis B", "Maitrise du français"],
    )

    codgeos = ["33063"]
    hash_val = "stable_test_hash"

    # Setup SearchResultsData with CommuneResult
    from core.models import SearchResultsData

    bordeaux = CommuneResult(
        codgeo="33063", name="Bordeaux", population=250000, global_score=0.85
    )
    search_results = SearchResultsData(
        search_hash=hash_val, results=[bordeaux], current_geo=bordeaux
    )

    # Trigger background hydration
    launch_background_job_curation(codgeos, config, hash_val, search_results)

    # Wait briefly for background thread to complete
    timeout = 2.0
    start = time.time()
    store = get_odis_bg_store()
    while time.time() - start < timeout:
        if hash_val in store and "jobs_enrichment" in store[hash_val]:
            cities_data = store[hash_val]["jobs_enrichment"]
            if all(cities_data[cg]["status"] in ["done", "error"] for cg in codgeos):
                break
        time.sleep(0.1)

    assert hash_val in store
    jobs_enrichment = store[hash_val]["jobs_enrichment"]
    assert "33063" in jobs_enrichment

    city_data = jobs_enrichment["33063"]
    assert city_data["status"] == "done"
    assert len(city_data["jobs"]) == 2  # 2 adults

    # Verify curation: selected 5 jobs in LLM specified order
    assert len(city_data["jobs"][0]) == 5
    assert city_data["jobs"][0][0]["id"] == "J8"
    assert city_data["jobs"][0][0]["job_brief"] == "Brief for job J8"
    assert city_data["jobs"][0][1]["id"] == "J6"
    assert city_data["jobs"][0][4]["id"] == "J0"

    # Assert that mock_curator_run was called with prompt containing Bordeaux's context
    mock_curator_run.assert_called()
    called_prompt = mock_curator_run.call_args[0][0]
    called_deps = mock_curator_run.call_args[1].get("deps")

    assert "Voici la liste des offres d'emploi" in called_prompt
    assert "J8" in called_prompt
    assert called_deps is not None
    assert "Candidat motivé recherchant un emploi." in called_deps.state.odis_brief
    assert called_deps.state.focus_city.name == "Bordeaux"
    assert called_deps.state.focus_city.codgeo == "33063"
    assert called_deps.state.search_criteria.notes_qualitatives == [
        "Permis B",
        "Maitrise du français",
    ]

    # Assert correct parameters were sent: sort=2, distance=20, range_end=9 (10 jobs limit)
    mock_search.assert_any_call(
        rome="M1805", location="33063", distance=20, sort=2, range_start=0, range_end=9
    )
    mock_search.assert_any_call(
        rome="A1201", location="33063", distance=20, sort=2, range_start=0, range_end=9
    )


@patch("services.mcp_france_travail._search_job_offers_logic")
@patch("agents.job_curator.job_curator_agent.run")
def test_background_jobs_enrichment_bypass(mock_curator_run, mock_search):
    """Verifies that if retrieved jobs count <= 5, LLM curation is bypassed and jobs are returned directly."""
    # 1. Mock search API returning 3 offers
    mock_search.return_value = {
        "offres": [
            {
                "id": f"J{k}",
                "intitule": f"Job {k}",
                "typeContrat": "CDI",
                "typeContratLibelle": "CDI",
                "description_sh": f"Desc {k}",
                "lieuTravail": {"libelle": "Bordeaux", "codeINSEE": "33063"},
                "entreprise": {"nom": "Company A"},
            }
            for k in range(3)
        ],
        "total": 3,
    }

    from core.models import CriteriaItem, SearchCriterias

    config = SearchCriterias(
        codes_metiers=[
            [CriteriaItem(code="M1805", label="Développement informatique")]
        ],
        odis_brief="Candidat",
        notes_qualitatives=[],
    )

    codgeos = ["33063"]
    hash_val = "bypass_test_hash"

    # Trigger background hydration
    launch_background_job_curation(codgeos, config, hash_val)

    # Wait briefly for background thread to complete
    timeout = 2.0
    start = time.time()
    store = get_odis_bg_store()
    while time.time() - start < timeout:
        if hash_val in store and "jobs_enrichment" in store[hash_val]:
            cities_data = store[hash_val]["jobs_enrichment"]
            if all(cities_data[cg]["status"] in ["done", "error"] for cg in codgeos):
                break
        time.sleep(0.1)

    assert hash_val in store
    jobs_enrichment = store[hash_val]["jobs_enrichment"]
    city_data = jobs_enrichment["33063"]

    assert city_data["status"] == "done"
    assert len(city_data["jobs"][0]) == 3  # All 3 returned directly
    mock_curator_run.assert_not_called()  # LLM agent bypassed


@patch("services.mcp_france_travail._search_job_offers_logic")
@patch("agents.job_curator.job_curator_agent.run")
def test_background_jobs_enrichment_graceful_fallback(mock_curator_run, mock_search):
    """Verifies that background task handles API exceptions and LLM failures gracefully."""
    # 1. Mock search API returning 10 offers
    mock_search.return_value = {
        "offres": [
            {
                "id": f"J{k}",
                "intitule": f"Job {k}",
                "typeContrat": "CDI",
                "typeContratLibelle": "CDI",
                "entreprise": {"nom": "Company A"},
            }
            for k in range(10)
        ],
        "total": 10,
    }

    # 2. Mock LLM curation raising an exception
    mock_curator_run.side_effect = ValueError("LLM is down")

    from core.models import CriteriaItem, SearchCriterias

    config = SearchCriterias(
        codes_metiers=[
            [CriteriaItem(code="M1805", label="Développement informatique")]
        ],
        odis_brief="Candidat",
        notes_qualitatives=[],
    )

    codgeos = ["33063"]
    hash_val = "fallback_test_hash"

    # Trigger background hydration
    launch_background_job_curation(codgeos, config, hash_val)

    # Wait briefly for background thread to complete
    timeout = 2.0
    start = time.time()
    store = get_odis_bg_store()
    while time.time() - start < timeout:
        if hash_val in store and "jobs_enrichment" in store[hash_val]:
            cities_data = store[hash_val]["jobs_enrichment"]
            if all(cities_data[cg]["status"] in ["done", "error"] for cg in codgeos):
                break
        time.sleep(0.1)

    assert hash_val in store
    jobs_enrichment = store[hash_val]["jobs_enrichment"]
    city_data = jobs_enrichment["33063"]

    assert city_data["status"] == "done"
    # Fallback returned first 5 distance-sorted offers
    assert len(city_data["jobs"][0]) == 5
    assert city_data["jobs"][0][0]["id"] == "J0"
    assert city_data["jobs"][0][4]["id"] == "J4"
