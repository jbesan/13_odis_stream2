from unittest.mock import MagicMock, patch
import pytest

import agents.agent_config as _agents_agent_config  # noqa: F401
import agents.graph as _agents_graph  # noqa: F401
from config import is_auto_analyse_top_cities_enabled
from core.models import CommuneResult, SearchResultsData
from core.postscoring import launch_post_scoring_tasks
from agents.utils import launch_background_city_analysis, run_logic
from agents.state import GraphState


def test_is_auto_analyse_top_cities_enabled_env(monkeypatch):
    """Verify that ODIS_AUTO_ANALYSE_TOP_CITIES parses standard truthy/falsy values."""
    monkeypatch.delenv("ODIS_AUTO_ANALYSE_TOP_CITIES", raising=False)
    monkeypatch.setenv("ODIS_AI_FREE_MODE", "False")
    assert not is_auto_analyse_top_cities_enabled()

    for val in ("true", "True", "1", "yes", "YES"):
        monkeypatch.setenv("ODIS_AUTO_ANALYSE_TOP_CITIES", val)
        assert is_auto_analyse_top_cities_enabled()

    for val in ("false", "False", "0", "no", "", "random"):
        monkeypatch.setenv("ODIS_AUTO_ANALYSE_TOP_CITIES", val)
        assert not is_auto_analyse_top_cities_enabled()


def test_is_auto_analyse_top_cities_disabled_when_ai_free_mode(monkeypatch):
    """Verify that auto-analysis is strictly disabled when AI-free mode is active."""
    monkeypatch.setenv("ODIS_AUTO_ANALYSE_TOP_CITIES", "true")
    monkeypatch.setenv("ODIS_AI_FREE_MODE", "True")
    assert not is_auto_analyse_top_cities_enabled()


def test_launch_post_scoring_tasks_triggers_auto_analysis_for_top_5_cities(monkeypatch):
    """Verify that post-scoring automatically triggers launch_background_city_analysis for top 5 cities."""
    monkeypatch.setenv("ODIS_AUTO_ANALYSE_TOP_CITIES", "true")
    monkeypatch.setenv("ODIS_AI_FREE_MODE", "False")

    cities = [
        CommuneResult(name=f"City_{i}", codgeo=f"7500{i}", total_score=100 - i)
        for i in range(1, 8)
    ]
    pressentie = CommuneResult(name="Pressentie", codgeo="99999", total_score=50)
    current_geo = CommuneResult(name="Current", codgeo="00000", total_score=0)
    search_results = SearchResultsData(
        search_hash="hash_123",
        results=cities,
        current_geo=current_geo,
        commune_pressentie=pressentie,
    )
    config = MagicMock()
    config.inc_services_selection = []
    engine = MagicMock()

    with (
        patch("core.postscoring.get_odis_bg_store", return_value={}),
        patch("core.postscoring.launch_background_refining"),
        patch("core.postscoring.launch_background_association_enrichment"),
        patch("core.postscoring.launch_background_inclusion_enrichment"),
        patch("core.postscoring.launch_background_job_curation"),
        patch("core.postscoring.launch_background_audit_log"),
        patch("core.postscoring.launch_background_city_analysis") as mock_launch_analysis,
    ):
        launch_post_scoring_tasks(engine, config, search_results, "hash_123")

        # Must be called exactly 5 times (for the top 5 recommendation cities only)
        assert mock_launch_analysis.call_count == 5
        called_codgeos = [
            call.kwargs["codgeo"] for call in mock_launch_analysis.call_args_list
        ]
        assert called_codgeos == ["75001", "75002", "75003", "75004", "75005"]
        assert "99999" not in called_codgeos

        # Verify trigger tag is post_scoring_auto
        for call in mock_launch_analysis.call_args_list:
            assert call.kwargs["trigger"] == "post_scoring_auto"


def test_launch_post_scoring_tasks_bypasses_auto_analysis_when_disabled(monkeypatch):
    """Verify that launch_background_city_analysis is not called when flag is False."""
    monkeypatch.setenv("ODIS_AUTO_ANALYSE_TOP_CITIES", "false")
    monkeypatch.setenv("ODIS_AI_FREE_MODE", "False")

    cities = [CommuneResult(name="Paris", codgeo="75056", total_score=90)]
    current_geo = CommuneResult(name="Current", codgeo="00000", total_score=0)
    search_results = SearchResultsData(
        search_hash="hash_123",
        results=cities,
        current_geo=current_geo,
    )
    config = MagicMock()
    config.inc_services_selection = []
    engine = MagicMock()

    with (
        patch("core.postscoring.get_odis_bg_store", return_value={}),
        patch("core.postscoring.launch_background_refining"),
        patch("core.postscoring.launch_background_association_enrichment"),
        patch("core.postscoring.launch_background_inclusion_enrichment"),
        patch("core.postscoring.launch_background_job_curation"),
        patch("core.postscoring.launch_background_audit_log"),
        patch("core.postscoring.launch_background_city_analysis") as mock_launch_analysis,
    ):
        launch_post_scoring_tasks(engine, config, search_results, "hash_123")
        assert mock_launch_analysis.call_count == 0


def test_launch_background_city_analysis_records_trigger_attribute():
    """Verify launch_background_city_analysis preserves the trigger tag in the store record."""
    store = {}

    async def fake_run_logic(_input_data):
        return {"search_results": {}}

    with (
        patch("agents.utils.get_odis_bg_store", return_value=store),
        patch("agents.utils.run_logic", side_effect=fake_run_logic),
    ):
        record = launch_background_city_analysis(
            nom="Lyon",
            codgeo="69123",
            search_criterias={},
            search_results={"results": []},
            h="hash_abc",
            trigger="post_scoring_auto",
        )

        assert record["trigger"] == "post_scoring_auto"
        assert store["analysis_hash_abc_69123"]["trigger"] == "post_scoring_auto"


@pytest.mark.asyncio
async def test_run_logic_passes_custom_trigger_to_span():
    """Verify run_logic passes custom trigger tag to Logfire span attributes."""
    captured = {}
    span = object()

    class FakeGraph:
        async def run(self, **kwargs):
            captured["run_kwargs"] = kwargs

    def fake_span(name, **attributes):
        captured["span_name"] = name
        captured["span_attributes"] = attributes
        return span

    with (
        patch("agents.utils.rehydrate_graph_state", return_value=GraphState()),
        patch("agents.utils.logfire.span", side_effect=fake_span),
        patch("agents.agent_config.get_gemini_client", return_value=object()),
        patch("agents.graph.create_odis_graph", return_value=FakeGraph()),
    ):
        await run_logic(
            {
                "criteria_hash": "criteria-hash",
                "interaction_id": "interaction-123",
                "run_id": "run-123",
                "run_attempt": 1,
                "trigger": "post_scoring_auto",
                "run_timeout_seconds": 1.0,
            }
        )

    assert captured["span_attributes"]["trigger"] == "post_scoring_auto"

