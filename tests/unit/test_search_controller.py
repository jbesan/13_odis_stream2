from unittest.mock import Mock, patch

import pandas as pd

from core.models import CommuneResult, SearchCriterias, SearchResultsData
from services.app_session import AppSession
from services.search_controller import SearchController


def test_execute_owns_search_transition_and_background_launch():
    state = {"form_completed": True}
    session = AppSession(state)
    controller = SearchController(session)
    config = SearchCriterias(loc_search_area="france")
    results = SearchResultsData(
        search_hash="run-hash",
        results=[],
        current_geo=CommuneResult(codgeo="33063", name="Bordeaux", global_score=0),
    )
    processed = pd.DataFrame({"weighted_score": [0.8]}, index=["33063"])
    engine = Mock()
    engine.run_optimized.return_value = (results, processed)
    app_data = {
        "odis": pd.DataFrame(index=["33063"]),
        "odis_geo": pd.DataFrame(),
    }

    with patch.object(controller, "_build_engine", return_value=engine), patch(
        "services.search_controller.data_loader.get_data_mtime",
        return_value="release-3",
    ), patch(
        "services.search_controller.telemetry.reset_interaction_id"
    ) as reset_telemetry, patch(
        "services.search_controller.odis_get_bg_result", return_value=None
    ), patch(
        "services.search_controller.launch_post_scoring_tasks"
    ) as launch_tasks:
        returned = controller.execute(config, app_data)

    assert returned is results
    assert state["config"] is config
    assert state["active_data_release"] == "release-3"
    assert state["search_results"] is results
    assert state["active_search_hash"] == "run-hash"
    assert state["form_completed"] is False
    reset_telemetry.assert_called_once_with()
    launch_tasks.assert_called_once_with(engine, config, results, "run-hash")


def test_execute_does_not_duplicate_existing_background_run():
    state = {}
    controller = SearchController(AppSession(state))
    config = SearchCriterias(loc_search_area="france")
    results = SearchResultsData(
        search_hash="run-hash",
        results=[],
        current_geo=CommuneResult(codgeo="33063", name="Bordeaux", global_score=0),
    )
    engine = Mock()
    engine.run_optimized.return_value = (results, pd.DataFrame())
    app_data = {"odis": pd.DataFrame(), "odis_geo": pd.DataFrame()}

    with patch.object(controller, "_build_engine", return_value=engine), patch(
        "services.search_controller.data_loader.get_data_mtime",
        return_value="release-3",
    ), patch(
        "services.search_controller.telemetry.reset_interaction_id"
    ), patch(
        "services.search_controller.odis_get_bg_result",
        return_value={"status": "running"},
    ), patch(
        "services.search_controller.launch_post_scoring_tasks"
    ) as launch_tasks:
        controller.execute(config, app_data)

    launch_tasks.assert_not_called()


def test_center_map_sets_initial_center_and_unhighlight_restores():
    from ui.results import _result_highlight_callback

    state = {}
    session = AppSession(state)
    controller = SearchController(session)
    config = SearchCriterias(loc_search_area="france")
    c1 = CommuneResult(codgeo="33063", name="Bordeaux", global_score=0.9)
    results = SearchResultsData(
        search_hash="hash-1",
        results=[c1],
        current_geo=c1,
    )
    app_data = {"odis": pd.DataFrame(), "odis_geo": pd.DataFrame()}

    controller._center_map(config, results, app_data)

    assert "initial_center" in state
    assert state["initial_center"] == state["center"]

    # Emulate Streamlit session
    with patch("ui.results.st.session_state", state), patch(
        "ui.results.maps._get_geom", return_value=Mock(x=-0.58, y=44.84)
    ):
        state["search_results"] = results
        state["highlighted_result"] = [False, None]
        state["processed_gdf"] = pd.DataFrame()

        # 1. Highlight
        _result_highlight_callback(0)
        assert state["highlighted_result"] == [True, 0]
        assert state["center"] == [44.84, -0.58]
        assert state["zoom"] == 11

        # 2. Un-highlight (same click)
        _result_highlight_callback(0)
        assert state["highlighted_result"] == [False, None]
        assert state["zoom"] is None
        assert state["center"] == state["initial_center"]

