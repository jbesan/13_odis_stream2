from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem
from services.share_service import save_shared_search, load_shared_search


def test_save_and_load_shared_search_roundtrip(monkeypatch):
    """Verify that GCS persistence round-trips identical Pydantic objects."""
    stored_objects = {}

    class FakeBlob:
        def __init__(self, name):
            self.name = name
            self.content_encoding = None

        def upload_from_string(self, data, content_type=None):
            stored_objects[self.name] = data

        def exists(self):
            return self.name in stored_objects

        def download_as_bytes(self):
            return stored_objects[self.name]

    class FakeBucket:
        def blob(self, name):
            return FakeBlob(name)

    class FakeGcsClient:
        def bucket(self, name):
            return FakeBucket()

    monkeypatch.setattr(
        "services.share_service._get_gcs_client", lambda: FakeGcsClient()
    )
    monkeypatch.setattr("services.telemetry.log_usage_event", lambda *args, **kwargs: None)

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

    assert f"searches/{share_id}.json" in stored_objects

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


def test_load_shared_search_invalid_id(monkeypatch):
    """Verify that non-existent share_ids safely return (None, None)."""
    monkeypatch.setattr("services.share_service._get_gcs_client", lambda: None)
    cfg, res = load_shared_search("non_existent_id_99999")
    assert cfg is None
    assert res is None
