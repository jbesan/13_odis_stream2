import pytest
from unittest.mock import patch

import pandas as pd
from shapely.geometry import Point
from google.api_core import exceptions as google_exceptions

from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem, Org
from services.share_service import (
    SharedSearchSnapshot,
    load_shared_search,
    load_shared_search_snapshot,
    load_shared_search_snapshot_outcome,
    restore_shared_search_from_query_params,
    restore_shared_search_to_session_state,
    save_shared_search,
    is_valid_share_id,
)
from services.service_outcomes import OutcomeStatus, ServiceOutcome


def test_is_valid_share_id():
    assert is_valid_share_id("12345678") is True
    assert is_valid_share_id("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4") is True
    assert is_valid_share_id("share_123-abc") is True
    assert is_valid_share_id("") is False
    assert is_valid_share_id("../searches/123") is False
    assert is_valid_share_id("abc/def") is False
    assert is_valid_share_id("abc") is False  # too short (<4)
    assert is_valid_share_id("a" * 65) is False  # too long (>64)


def test_save_and_load_shared_search_roundtrip(monkeypatch):
    """Verify that GCS persistence round-trips identical Pydantic objects for same org."""
    stored_objects = {}
    stored_preconditions = {}

    class FakeBlob:
        def __init__(self, name):
            self.name = name
            self.content_encoding = None

        def upload_from_string(self, data, content_type=None, if_generation_match=None):
            stored_objects[self.name] = data
            stored_preconditions[self.name] = if_generation_match

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
    assert stored_preconditions[f"searches/{share_id}.json"] == 0

    # 2. Load with matching org
    loaded_config, loaded_results = load_shared_search(share_id, caller_org_id="test_org")

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

    snapshot = load_shared_search_snapshot(share_id, caller_org_id="test_org")
    assert snapshot is not None
    assert snapshot.version == "2.0"
    assert snapshot.org_id == "test_org"
    assert snapshot.username == "test_user"
    assert snapshot.data_release == "release-2026-08-04"
    assert snapshot.map_view == {"center": [46.0, 3.0], "zoom": 8}
    assert snapshot.has_map_context
    assert snapshot.map_context[0]["codgeo"] == "69123"


def test_shared_search_cross_org_access_denied(monkeypatch):
    """Verify that a shared search from Org A is denied to Org B."""
    stored_objects = {}

    class FakeBlob:
        def __init__(self, name):
            self.name = name
            self.content_encoding = None

        def upload_from_string(self, data, content_type=None, if_generation_match=None):
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

    config = SearchCriterias(nb_adultes=1)
    current_geo = CommuneResult(codgeo="75056", name="Paris", global_score=70.0)
    search_results = SearchResultsData(
        search_hash="hash-1", results=[], current_geo=current_geo
    )

    share_id = save_shared_search(
        config=config,
        search_results=search_results,
        username="alice@jaccueille.fr",
        org_id="jaccueille",
    )

    # Attempt load with different org
    outcome = load_shared_search_snapshot_outcome(share_id, caller_org_id="other_org")
    assert outcome.status == OutcomeStatus.UNAUTHORIZED
    assert outcome.error_code == "SHARE-ORG-MISMATCH"
    assert outcome.value is None

    cfg, res = load_shared_search(share_id, caller_org_id="other_org")
    assert cfg is None
    assert res is None


def test_shared_search_unauthenticated_access_denied(monkeypatch):
    """Verify that a shared search is denied when caller is unauthenticated."""
    stored_objects = {}

    class FakeBlob:
        def __init__(self, name):
            self.name = name
            self.content_encoding = None

        def upload_from_string(self, data, content_type=None, if_generation_match=None):
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

    config = SearchCriterias(nb_adultes=1)
    current_geo = CommuneResult(codgeo="75056", name="Paris", global_score=70.0)
    search_results = SearchResultsData(
        search_hash="hash-1", results=[], current_geo=current_geo
    )

    share_id = save_shared_search(
        config=config,
        search_results=search_results,
        username="alice@jaccueille.fr",
        org_id="jaccueille",
    )

    with patch("streamlit.session_state", {}):
        outcome = load_shared_search_snapshot_outcome(share_id, caller_org_id=None)
        assert outcome.status == OutcomeStatus.UNAUTHORIZED
        assert outcome.error_code == "SHARE-UNAUTHENTICATED"
        assert outcome.value is None


def test_save_shared_search_collision_precondition(monkeypatch):
    """Verify that PreconditionFailed (collision) raises a clean RuntimeError."""
    class CollisionBlob:
        def __init__(self, name):
            self.content_encoding = None

        def upload_from_string(self, data, content_type=None, if_generation_match=None):
            raise google_exceptions.PreconditionFailed("Collision: object exists")

    class FakeBucket:
        def blob(self, name):
            return CollisionBlob(name)

    class FakeGcsClient:
        def bucket(self, name):
            return FakeBucket()

    monkeypatch.setattr(
        "services.share_service._get_gcs_client", lambda: FakeGcsClient()
    )

    config = SearchCriterias(nb_adultes=1)
    current_geo = CommuneResult(codgeo="75056", name="Paris", global_score=70.0)
    search_results = SearchResultsData(
        search_hash="hash-1", results=[], current_geo=current_geo
    )

    with pytest.raises(RuntimeError, match="Un identifiant de recherche partagée identique existe déjà"):
        save_shared_search(
            config=config,
            search_results=search_results,
            org_id="test_org",
        )


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
        org_id="jaccueille",
        username="user1",
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
    """Verify that non-existent or malformed share_ids safely return (None, None)."""
    monkeypatch.setattr("services.share_service._get_gcs_client", lambda: None)
    cfg, res = load_shared_search("non_existent_id_99999", caller_org_id="test_org")
    assert cfg is None
    assert res is None

    # Malformed ID
    cfg2, res2 = load_shared_search("../evil_path", caller_org_id="test_org")
    assert cfg2 is None
    assert res2 is None


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
    missing = load_shared_search_snapshot_outcome("deadbeef", caller_org_id="test_org")
    assert missing.status == OutcomeStatus.NOT_FOUND
    assert missing.value is None

    monkeypatch.setattr("services.share_service._get_gcs_client", lambda: None)
    unavailable = load_shared_search_snapshot_outcome("deadbeef", caller_org_id="test_org")
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
    outcome = load_shared_search_snapshot_outcome("deadbeef", caller_org_id="test_org")

    assert outcome.status == OutcomeStatus.INVALID_PAYLOAD
    assert outcome.error_code == "SHARE-PAYLOAD-INVALID"


def test_restore_shared_search_from_query_params_org_scenarios(monkeypatch):
    """Verify query parameter restoration for matching org, cross org, and unauthenticated."""
    test_org = Org(id="jaccueille", name="J'accueille", zone_type="departement", default_zones=[])

    # 1. Matching org scenario
    config = SearchCriterias(nb_adultes=1)
    current_geo = CommuneResult(codgeo="75056", name="Paris", global_score=70.0)
    results = SearchResultsData(
        search_hash="hash-match", results=[], current_geo=current_geo
    )
    matching_snapshot = SharedSearchSnapshot(
        share_id="share123",
        version="2.0",
        created_at=None,
        data_release=None,
        config=config,
        search_results=results,
        map_context=[],
        current_map_context=[],
        map_view={},
        org_id="jaccueille",
        username="alice",
    )

    state_match = {"org": test_org}
    with (
        patch("streamlit.query_params", {"search": "share123"}),
        patch("streamlit.session_state", state_match),
        patch(
            "services.share_service.load_shared_search_snapshot_outcome",
            return_value=ServiceOutcome(status=OutcomeStatus.SUCCESS, value=matching_snapshot),
        ),
        patch("services.share_service.restore_shared_search_to_session_state") as restore_mock,
    ):
        success = restore_shared_search_from_query_params()
        assert success is True
        restore_mock.assert_called_once()

    # 2. Cross org scenario
    state_cross = {"org": Org(id="other_org", name="Other", zone_type="departement", default_zones=[])}
    with (
        patch("streamlit.query_params", {"search": "share123"}),
        patch("streamlit.session_state", state_cross),
        patch(
            "services.share_service.load_shared_search_snapshot_outcome",
            return_value=ServiceOutcome(status=OutcomeStatus.UNAUTHORIZED, error_code="SHARE-ORG-MISMATCH"),
        ),
    ):
        fail_cross = restore_shared_search_from_query_params()
        assert fail_cross is False
        assert "SHARE-FORBIDDEN" in state_cross.get("share_error", "")

    # 3. Unauthenticated scenario
    state_unauth = {}
    with (
        patch("streamlit.query_params", {"search": "share123"}),
        patch("streamlit.session_state", state_unauth),
        patch(
            "services.share_service.load_shared_search_snapshot_outcome",
            return_value=ServiceOutcome(status=OutcomeStatus.UNAUTHORIZED, error_code="SHARE-UNAUTHENTICATED"),
        ),
    ):
        fail_unauth = restore_shared_search_from_query_params()
        assert fail_unauth is False
        assert "SHARE-UNAUTHORIZED" in state_unauth.get("share_error", "")

