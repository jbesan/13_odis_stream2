import pytest
import time
import streamlit as st
from unittest.mock import patch, MagicMock
from core.models import JobOfferDetail, EmploymentMetrics, CommuneResult
from agents.utils import launch_background_jobs_enrichment, get_odis_bg_store

@pytest.fixture(autouse=True)
def clean_streamlit_session():
    """Cleans up streamlit session state before and after each test."""
    st.session_state.clear()
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
        "url": "https://example.com/apply"
    }
    
    offer = JobOfferDetail.model_validate(offer_data)
    assert offer.id == "123ABCD"
    assert offer.title == "Ingénieur IA"
    assert offer.location_insee == "33063"
    
    # Verify model dump
    dump = offer.model_dump(mode='json')
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
            [offer1], # Adult 1 jobs
            [offer2]  # Adult 2 jobs
        ]
    )
    
    assert len(metrics.matching_job_offers) == 2
    assert metrics.matching_job_offers[0][0].id == "OFFER1"
    assert metrics.matching_job_offers[1][0].id == "OFFER2"
    
    # Verify serialization
    dump = metrics.model_dump(mode='json')
    assert len(dump["matching_job_offers"]) == 2
    assert dump["matching_job_offers"][0][0]["id"] == "OFFER1"

@patch("services.mcp_france_travail._search_job_offers_logic")
def test_background_jobs_enrichment_success(mock_search):
    """Verifies that background jobs enrichment queries ROME codes, groups by adult, and caps outputs."""
    # Mock search API response returning 10 offers
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
                "origineOffre": {"urlOrigine": f"https://ft.fr/J{k}"}
            } for k in range(10)
        ],
        "total": 10
    }
    
    from core.models import CriteriaItem
    codes_metiers = [
        [CriteriaItem(code="M1805", label="Développement informatique")], # Adult 1: Dev
        ["A1201"]  # Adult 2: Boulanger
    ]
    codgeos = ["33063"]
    hash_val = "stable_test_hash"
    
    # Trigger background hydration
    launch_background_jobs_enrichment(codgeos, codes_metiers, hash_val)
    
    # Wait briefly for background thread to complete
    timeout = 2.0
    start = time.time()
    store = get_odis_bg_store()
    while time.time() - start < timeout:
        if hash_val in store and "jobs_enrichment" in store[hash_val]:
            break
        time.sleep(0.1)
        
    assert hash_val in store
    jobs_enrichment = store[hash_val]["jobs_enrichment"]
    assert "33063" in jobs_enrichment
    
    city_data = jobs_enrichment["33063"]
    assert city_data["status"] == "done"
    assert len(city_data["jobs"]) == 2 # 2 adults
    
    # Verify strict slicing: capped at 5 offers per ROME code
    assert len(city_data["jobs"][0]) == 5
    assert len(city_data["jobs"][1]) == 5
    
    # Verify ROME code and label mapping
    assert city_data["jobs"][0][0]["rome_code"] == "M1805"
    assert city_data["jobs"][0][0]["rome_label"] == "Développement informatique"
    assert city_data["jobs"][1][0]["rome_code"] == "A1201"
    assert city_data["jobs"][1][0]["rome_label"] == "A1201"
    
    # Assert correct parameters were sent: sort=2, distance=20, range_end=4
    mock_search.assert_any_call(
        rome="M1805", location="33063", distance=20, sort=2, range_start=0, range_end=4
    )
    mock_search.assert_any_call(
        rome="A1201", location="33063", distance=20, sort=2, range_start=0, range_end=4
    )

@patch("services.mcp_france_travail._search_job_offers_logic")
def test_background_jobs_enrichment_graceful_fallback(mock_search):
    """Verifies that background task handles API exceptions gracefully and registers error status without crashing."""
    # Mock search API raising an error
    mock_search.side_effect = ValueError("Missing credentials or API down")
    
    codes_metiers = [["M1805"]]
    codgeos = ["33063"]
    hash_val = "error_test_hash"
    
    # Trigger background hydration (should NOT raise / crash)
    launch_background_jobs_enrichment(codgeos, codes_metiers, hash_val)
    
    # Wait briefly for background thread
    timeout = 2.0
    start = time.time()
    store = get_odis_bg_store()
    while time.time() - start < timeout:
        if hash_val in store and "jobs_enrichment" in store[hash_val]:
            break
        time.sleep(0.1)
        
    assert hash_val in store
    jobs_enrichment = store[hash_val]["jobs_enrichment"]
    assert "33063" in jobs_enrichment
    
    city_data = jobs_enrichment["33063"]
    assert city_data["status"] == "done"  # Indivisual queries fail gracefully
    assert city_data["jobs"] == [[]]      # Return empty list for failed queries
