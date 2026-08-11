import pandas as pd

from core.models import CommuneResult, SearchCriterias, SearchResultsData
from services.app_session import AppSession


def test_search_lifecycle_replaces_previous_run_atomically():
    state = {
        "search_results": "old",
        "processed_gdf": "old-map",
        "immutable_shared_snapshot": True,
        "shared_snapshot_version": "2.0",
    }
    session = AppSession(state)
    config = SearchCriterias()

    session.begin_search(config, "release-2")

    assert state["config"] is config
    assert state["active_data_release"] == "release-2"
    assert state["search_results"] is None
    assert state["immutable_shared_snapshot"] is False
    assert "shared_snapshot_version" not in state

    results = SearchResultsData(
        search_hash="hash-2",
        results=[],
        current_geo=CommuneResult(codgeo="33063", name="Bordeaux", global_score=0),
    )
    frame = pd.DataFrame({"score": [1.0]}, index=["33063"])
    engine = object()
    session.complete_search(
        engine=engine, search_results=results, processed_gdf=frame
    )

    assert state["engine"] is engine
    assert state["search_results"] is results
    assert state["processed_gdf"] is frame
    assert state["active_search_hash"] == "hash-2"


def test_restore_snapshot_clears_only_workers_for_restored_hash():
    state = {
        "engine": object(),
        "odis_bg_store": {
            "shared-hash": {"status": "done"},
            "analysis_shared-hash_33063": {"status": "done"},
            "other-hash": {"status": "running"},
        },
    }
    session = AppSession(state)
    results = SearchResultsData(
        search_hash="shared-hash",
        results=[],
        current_geo=CommuneResult(codgeo="33063", name="Bordeaux", global_score=0),
    )

    session.restore_snapshot(
        config=SearchCriterias(),
        search_results=results,
        share_id="share-1",
        processed_gdf=pd.DataFrame(),
        current_map_context=pd.DataFrame(),
        version="2.0",
        data_release="release-1",
        created_at=None,
        has_map=False,
        center=[46.5, 2.5],
        zoom=6,
    )

    assert "engine" not in state
    assert "shared-hash" not in state["odis_bg_store"]
    assert "analysis_shared-hash_33063" not in state["odis_bg_store"]
    assert "other-hash" in state["odis_bg_store"]
    assert state["immutable_shared_snapshot"] is True


def test_reset_for_home_preserves_identity_and_resources_only():
    state = {
        "app_data": object(),
        "password_correct": True,
        "username": "user@example.org",
        "user": object(),
        "org": object(),
        "search_results": object(),
        "ui_notes_qualitatives": "Draft",
    }

    removed = AppSession(state).reset_for_home()

    assert removed == 2
    assert "app_data" in state
    assert "user" in state
    assert "org" in state
    assert "search_results" not in state
    assert "ui_notes_qualitatives" not in state


def test_identity_rejects_partial_authenticated_state():
    session = AppSession({"username": "user@example.org", "user": object()})

    try:
        session.identity()
    except RuntimeError as exc:
        assert "incomplete identity" in str(exc)
    else:
        raise AssertionError("A partial authenticated identity must be rejected")
