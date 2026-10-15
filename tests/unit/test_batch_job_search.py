import pytest
from pydantic_ai.models.test import TestModel
from unittest.mock import AsyncMock, MagicMock, patch
import sys
import os

# Ensure app is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app")))

from agents.capability_registry import get_default_capability_registry
from agents.job_hunter import job_hunter_agent
from agents.state import GraphState, ODISDeps
from agents.tools import search_job_offers_batch, JobOfferSearchQuery

from core.models import CommuneResult


@pytest.fixture
def test_deps():
    state = GraphState(
        odis_brief="Projet: Boulanger et Pâtissier",
        focus_city=CommuneResult(name="Paris", codgeo="75056"),
        search_criteria={},
    )
    mock_client = MagicMock()
    return ODISDeps(state=state, client=mock_client)


@pytest.mark.asyncio
async def test_search_job_offers_batch_logic():
    """Verify the internal logic of search_job_offers_batch function in tools.py."""
    with patch(
        "agents.tools._search_job_offers_logic", new_callable=MagicMock
    ) as mock_logic:
        # Note: _search_job_offers_logic is called via to_thread, so it stays sync in logic
        mock_logic.return_value = {"offres": [], "total": 10}

        queries = [
            JobOfferSearchQuery(rome="D1102", city_code_insee="75056"),
            JobOfferSearchQuery(rome="D1104", city_code_insee="75056"),
        ]

        results = await search_job_offers_batch(queries)

        assert "D1102|75056|" in results
        assert "D1104|75056|" in results
        assert results["D1102|75056|"]["total"] == 10
        assert mock_logic.call_count == 2


def test_search_job_offers_invalid_rome_regex():
    """Verify that invalid ROME codes (like INSEE) return empty results without calling API."""
    from services.mcp_france_travail import _search_job_offers_logic

    # Passing an INSEE code instead of ROME
    results = _search_job_offers_logic(rome="45032", location="11069")
    assert results == {"offres": [], "total": 0}

    # Passing a non-string
    results = _search_job_offers_logic(rome=12345)
    assert results == {"offres": [], "total": 0}

    # Valid ROME should still work (it will try to call API, so we mock it if we wanted to check positive case)


@pytest.mark.asyncio
async def test_job_hunter_tool_registration(test_deps):
    """Verify that JobHunter capability has the batch tool and NOT the unitary one."""
    # In Phase 1 Étape 4, static tools are 0 on agent singletons; tools are provided via capabilities
    assert list(job_hunter_agent._function_toolset.tools.keys()) == []

    registry = get_default_capability_registry()
    caps = registry.build_capabilities_for_expert(
        ["job_adequation_territoire"], expert="job_hunter"
    )
    job_tools = {getattr(t, "__name__", str(t)) for cap in caps for t in cap.tools}

    assert "search_job_offers_batch_tool" in job_tools
    assert "search_job_offers_tool" not in job_tools


@pytest.mark.asyncio
async def test_job_hunter_execution_with_batch_mock(test_deps):
    """Verify the agent runs and handles batch tool responses."""
    mock_model = TestModel()

    with job_hunter_agent.override(model=mock_model):
        with (
            patch(
                "agents.tools.search_referentiels_batch", new_callable=AsyncMock
            ) as mock_ref,
            patch(
                "agents.tools.search_job_offers_batch", new_callable=AsyncMock
            ) as mock_jobs,
            patch("agents.tools.get_job_details", return_value={}),
        ):
            mock_ref.return_value = {
                "communes:Paris": [{"code": "75056", "label": "Paris"}]
            }
            mock_jobs.return_value = {
                "D1102|75056|": {
                    "offres": [{"id": "1", "intitule": "Boulanger"}],
                    "total": 1,
                },
                "D1104|75056|": {
                    "offres": [{"id": "2", "intitule": "Pâtissier"}],
                    "total": 1,
                },
            }

            caps = get_default_capability_registry().build_capabilities_for_expert(
                ["job_adequation_territoire"]
            )
            result = await job_hunter_agent.run(
                "Cherche des jobs de boulanger et pâtissier à Paris",
                deps=test_deps,
                capabilities=caps,
            )
            assert result.output is not None
            # The agent might not produce content if the mock model doesn't return anything,
            # but we want to ensure no crash and tool availability.
