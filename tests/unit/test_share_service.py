from unittest.mock import patch

import pandas as pd
from shapely.geometry import Point

from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem
from services.share_service import (
    load_shared_search,
    load_shared_search_snapshot,
    load_shared_search_snapshot_outcome,
    restore_shared_search_to_session_state,
    save_shared_search,
)
from services.service_outcomes import OutcomeStatus


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
    monkeypatch.setattr(
        "services.telemetry.log_usage_event", lambda *args, **kwargs: None
    )

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

    processed_gdf = pd.DataFrame(
        {
            "libgeo": ["Lyon"],
            "weighted_score": [0.855],
            "polygon": [Point(4.8357, 45.7640).wkb],
        },
        index=pd.Index(["69123"], name="codgeo"),
    )
    selected_geo = pd.DataFrame(
        {
            "libgeo": ["Paris"],
            "polygon": [Point(2.3522, 48.8566).wkb],
        },
        index=pd.Index(["75056"], name="codgeo"),
    )

    # 1. Save
    share_id = save_shared_search(
        config=config,
        search_results=search_results,
        username="test_user",
        org_id="test_org",
        processed_gdf=processed_gdf,
        selected_geo=selected_geo,
        data_release="release-2026-08-04",
        map_center=[46.0, 3.0],
        map_zoom=8,
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
    assert (
        loaded_results.results[0].refiner_pitch
        == "Excellente opportunité d'emploi et de logement."
    )

    snapshot = load_shared_search_snapshot(share_id)
    assert snapshot is not None
    assert snapshot.version == "2.0"
    assert snapshot.data_release == "release-2026-08-04"
    assert snapshot.map_view == {"center": [46.0, 3.0], "zoom": 8}
    assert snapshot.has_map_context
    assert snapshot.map_context[0]["codgeo"] == "69123"


def test_restore_snapshot_hydrates_ui_without_rescoring():
    """Restoration uses the saved display payload and never rebuilds an engine."""
    config = SearchCriterias(
        commune_actuelle=CriteriaItem(code="75056", label="Paris"), nb_adultes=2
    )
    results = SearchResultsData(
        search_hash="snapshot-hash",
        results=[CommuneResult(codgeo="69123", name="Lyon", global_score=0.855)],
        current_geo=CommuneResult(codgeo="75056", name="Paris", global_score=0.7),
    )

    from services.share_service import SharedSearchSnapshot

    point_wkb_b64 = "AQEAAAAAAAAAAACAAAAAAAAAAAA="
    snapshot = SharedSearchSnapshot(
        share_id="deadbeef",
        version="2.0",
        created_at="2026-08-04T12:00:00+02:00",
        data_release="release-2026-08-04",
        config=config,
        search_results=results,
        map_context=[
            {
                "codgeo": "69123",
                "libgeo": "Lyon",
                "weighted_score": 0.855,
                "polygon_wkb_b64": point_wkb_b64,
            }
        ],
        current_map_context=[
            {
                "codgeo": "75056",
                "libgeo": "Paris",
                "weighted_score": 0.0,
                "polygon_wkb_b64": point_wkb_b64,
            }
        ],
        map_view={"center": [46.0, 3.0], "zoom": 8},
    )
    state = {
        "engine": object(),
        "odis_bg_store": {
            "snapshot-hash": {"status_refiner": "done"},
            "analysis_snapshot-hash_69123": {"status": "done"},
        },
    }
    applied = []

    with (
        patch("streamlit.session_state", state),
        patch("utils.data_loader.initialize_session_state") as initialize_state,
        patch(
            "utils.data_loader.apply_search_criteria_to_ui",
            side_effect=lambda criteria: applied.append(criteria),
        ),
    ):
        restore_shared_search_to_session_state(config, results, "deadbeef", snapshot)

    initialize_state.assert_called_once_with()
    assert applied == [config]
    assert state["immutable_shared_snapshot"] is True
    assert state["shared_snapshot_data_release"] == "release-2026-08-04"
    assert state["processed_gdf"].loc["69123", "weighted_score"] == 0.855
    assert "engine" not in state
    assert "snapshot-hash" not in state["odis_bg_store"]
    assert "analysis_snapshot-hash_69123" not in state["odis_bg_store"]


def test_load_shared_search_invalid_id(monkeypatch):
    """Verify that non-existent share_ids safely return (None, None)."""
    monkeypatch.setattr("services.share_service._get_gcs_client", lambda: None)
    cfg, res = load_shared_search("non_existent_id_99999")
    assert cfg is None
    assert res is None


def test_shared_search_outcome_distinguishes_missing_from_unavailable(monkeypatch):
    class MissingBlob:
        def exists(self):
            return False

    class MissingBucket:
        def blob(self, name):
            return MissingBlob()

    class MissingClient:
        def bucket(self, name):
            return MissingBucket()

    monkeypatch.setattr(
        "services.share_service._get_gcs_client", lambda: MissingClient()
    )
    missing = load_shared_search_snapshot_outcome("deadbeef")
    assert missing.status == OutcomeStatus.NOT_FOUND
    assert missing.value is None

    monkeypatch.setattr("services.share_service._get_gcs_client", lambda: None)
    unavailable = load_shared_search_snapshot_outcome("deadbeef")
    assert unavailable.status == OutcomeStatus.UNAVAILABLE
    assert unavailable.error_code == "SHARE-GCS-UNAVAILABLE"


def test_shared_search_outcome_marks_corrupt_payload(monkeypatch):
    class CorruptBlob:
        def exists(self):
            return True

        def download_as_bytes(self):
            return b"this is not JSON"

    class CorruptBucket:
        def blob(self, name):
            return CorruptBlob()

    class CorruptClient:
        def bucket(self, name):
            return CorruptBucket()

    monkeypatch.setattr(
        "services.share_service._get_gcs_client", lambda: CorruptClient()
    )
    outcome = load_shared_search_snapshot_outcome("deadbeef")

    assert outcome.status == OutcomeStatus.INVALID_PAYLOAD
    assert outcome.error_code == "SHARE-PAYLOAD-INVALID"
