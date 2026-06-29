import pytest
import time
from unittest.mock import MagicMock
from agents.utils import get_odis_bg_store
from core.postscoring import launch_background_association_enrichment

def test_launch_background_association_enrichment():
    """Tests that launch_background_association_enrichment correctly calls prefetch_associations

    and updates the background store and engine's cache.
    """
    # 1. Setup Mock Engine
    mock_engine = MagicMock()
    mock_engine._associations_cache = {}
    mock_engine.rna_rag_service = MagicMock()
    
    # Mock associations returned by BQ/RAG service
    mock_associations = [
        {
            "id": "asso1",
            "codgeo": "33063",
            "name": "ASSOCIATION TEST REFUGEE",
            "description": "Refugee support association",
            "code_waldec": "W1234567",
            "categorie": "Action Sociale",
            "primary_category": "Social",
            "is_refugee_focused": True
        },
        {
            "id": "asso2",
            "codgeo": "33063",
            "name": "ASSOCIATION TEST INCLUSION",
            "description": "Inclusion support",
            "code_waldec": "W7654321",
            "categorie": "Action Sociale",
            "primary_category": "Inclusion",
            "is_refugee_focused": False
        }
    ]
    mock_engine.rna_rag_service.get_associations_by_codgeo.return_value = mock_associations

    # 2. Parameters
    codgeos = ["33063"]
    hash_val = "test_enrichment_hash"

    # Clean store before test
    store = get_odis_bg_store()
    if hash_val in store:
        del store[hash_val]

    # 3. Act
    launch_background_association_enrichment(mock_engine, codgeos, hash_val)

    # 4. Wait for background thread (with timeout)
    timeout = 2.0
    start = time.time()
    while time.time() - start < timeout:
        if hash_val in store and "enrichment" in store[hash_val]:
            break
        time.sleep(0.1)

    # 5. Assertions
    assert hash_val in store
    enrichment_data = store[hash_val]["enrichment"]
    assert "33063" in enrichment_data
    
    city_data = enrichment_data["33063"]
    assert len(city_data["refugee"]) == 1
    assert city_data["refugee"][0]["id"] == "asso1"
    assert city_data["refugee"][0]["name"] == "Association Test Refugee" # capitalized
    
    assert "Inclusion" in city_data["inclusion"]
    assert len(city_data["inclusion"]["Inclusion"]) == 1
    assert city_data["inclusion"]["Inclusion"][0]["id"] == "asso2"
    assert city_data["inclusion"]["Inclusion"][0]["name"] == "Association Test Inclusion"

    # Verify cache update on the engine
    assert "33063" in mock_engine._associations_cache
    assert mock_engine._associations_cache["33063"] == city_data
