from unittest.mock import patch

from core.models import CommuneResult
from ui.results import render_refiner_panel


def _commune() -> CommuneResult:
    return CommuneResult(codgeo="69123", name="Lyon", global_score=0.85)


def test_refiner_panel_stays_processing_until_a_terminal_result():
    commune = _commune()
    with (
        patch("ui.results.st.session_state", {}),
        patch(
            "ui.results.odis_get_bg_result",
            return_value={"status_refiner": "running"},
        ),
        patch("ui.results.sync_background_data"),
        patch("ui.results.st.info") as info,
        patch("ui.results.st.caption"),
    ):
        assert render_refiner_panel(commune, "search-hash") is False

    info.assert_called_once_with("Analyse des points forts en cours...")


def test_refiner_panel_uses_static_top_three_after_a_terminal_failure():
    commune = _commune()
    with (
        patch("ui.results.st.session_state", {}),
        patch(
            "ui.results.odis_get_bg_result",
            return_value={"status_refiner": "error"},
        ),
        patch("ui.results.sync_background_data"),
        patch("ui.results.generate_static_pitch", return_value="- indicateur") as pitch,
        patch("ui.results.st.caption"),
        patch("ui.results.st.markdown") as markdown,
    ):
        assert render_refiner_panel(commune, "search-hash") is True

    pitch.assert_called_once_with(commune)
    markdown.assert_called_once_with("- indicateur")


def test_refiner_panel_keeps_the_completed_refiner_output():
    commune = _commune()
    commune.refiner_pitch = "Analyse personnalisée"
    with (
        patch("ui.results.st.session_state", {}),
        patch("ui.results.sync_background_data"),
        patch("ui.results.st.markdown") as markdown,
    ):
        assert render_refiner_panel(commune, "search-hash") is True

    markdown.assert_called_once_with("Analyse personnalisée")


def test_refiner_panel_shared_snapshot_uses_refiner_pitch_if_present():
    commune = _commune()
    commune.refiner_pitch = "Pitch partagé"
    with (
        patch("ui.results.st.session_state", {"immutable_shared_snapshot": True}),
        patch("ui.results.st.markdown") as markdown,
    ):
        assert render_refiner_panel(commune, "search-hash") is True

    markdown.assert_called_once_with("Pitch partagé")


def test_refiner_panel_shared_snapshot_falls_back_to_static_pitch():
    commune = _commune()
    with (
        patch("ui.results.st.session_state", {"immutable_shared_snapshot": True}),
        patch("ui.results.generate_static_pitch", return_value="- pitch statique") as pitch,
        patch("ui.results.st.markdown") as markdown,
    ):
        assert render_refiner_panel(commune, "search-hash") is True

    pitch.assert_called_once_with(commune)
    markdown.assert_called_once_with("- pitch statique")

