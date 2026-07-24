import pytest
import os
import shutil
from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem
from services.share_service import save_shared_search, load_shared_search, _get_local_filepath


def test_save_and_load_shared_search_roundtrip(tmp_path, monkeypatch):
    """Verify that saving a search snapshot and loading it back yields identical Pydantic objects."""
    # Mock local storage directory to a temporary path
    test_dir = str(tmp_path / "shared_searches")
    monkeypatch.setattr("services.share_service.LOCAL_STORAGE_DIR", test_dir)

    config = SearchCriterias(
        commune_actuelle=CriteriaItem(code="75056", label="Paris"),
        nb_adultes=2,
        nb_enfants=1,
    )

    city1 = CommuneResult(
        codgeo="69123",
        name="Lyon",
        global_score=85.5,
        refiner_pitch="Excellente opportunité d'emploi et de logement.",
    )

    current_city = CommuneResult(
        codgeo="75056",
        name="Paris",
        global_score=70.0,
    )

    search_results = SearchResultsData(
        search_hash="test_hash_12345",
        results=[city1],
        current_geo=current_city,
        global_pitch="Analyse globale du projet de vie",
    )

    # 1. Save
    share_id = save_shared_search(
        config=config,
        search_results=search_results,
        username="test_user",
        org_id="test_org",
    )

    assert isinstance(share_id, str)
    assert len(share_id) == 8

    local_path = os.path.join(test_dir, f"{share_id}.json")
    assert os.path.exists(local_path)

    # 2. Load
    loaded_config, loaded_results = load_shared_search(share_id)

    assert loaded_config is not None
    assert loaded_results is not None
    assert loaded_config.commune_actuelle.code == "75056"
    assert loaded_config.nb_adultes == 2
    assert loaded_config.nb_enfants == 1
    assert loaded_results.search_hash == "test_hash_12345"
    assert len(loaded_results.results) == 1
    assert loaded_results.results[0].codgeo == "69123"
    assert loaded_results.results[0].name == "Lyon"
    assert loaded_results.results[0].global_score == 85.5
    assert loaded_results.results[0].refiner_pitch == "Excellente opportunité d'emploi et de logement."


def test_load_shared_search_invalid_id():
    """Verify that non-existent share_ids safely return (None, None)."""
    cfg, res = load_shared_search("non_existent_id_99999")
    assert cfg is None
    assert res is None
