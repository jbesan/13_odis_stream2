import pytest
from pydantic_ai.models.test import TestModel
from unittest.mock import MagicMock, patch
from agents.job_hunter import job_hunter_agent
from agents.state import GraphState, ODISDeps

from core.models import CommuneResult


@pytest.fixture
def test_deps():
    state = GraphState(
        odis_brief="Projet: Boulanger",
        focus_city=CommuneResult(name="Paris", codgeo="75056"),
    )
    # We mock search_referentiels to return a dummy INSEE for Paris
    mock_client = MagicMock()
    return ODISDeps(state=state, client=mock_client)


@pytest.mark.asyncio
async def test_job_hunter_search_intent(test_deps):
    """Verify that the job hunter agent can be run with a mock model."""
    mock_model = TestModel()

    with job_hunter_agent.override(model=mock_model):
        from unittest.mock import AsyncMock

        with (
            patch(
                "agents.job_hunter.search_referentiels_batch", new_callable=AsyncMock
            ) as mock_ref,
            patch(
                "agents.job_hunter.search_job_offers_batch", new_callable=AsyncMock
            ) as mock_jobs,
            patch("agents.job_hunter.get_job_details", return_value={}),
        ):
            mock_ref.return_value = {
                "communes:Paris": [{"code": "75056", "label": "Paris"}]
            }
            mock_jobs.return_value = {}

            result = await job_hunter_agent.run(
                "Trouve moi des jobs de boulanger", deps=test_deps
            )
            assert result.output is not None
            # The result is now a structured JobHunterResult, not just string output
            from agents.job_hunter import JobHunterResult

            assert isinstance(result.output, JobHunterResult)


@pytest.mark.asyncio
async def test_job_hunter_tool_calls(test_deps):
    """Verify that the agent can call tools using TestModel's call_tools flag."""
    mock_model = TestModel()

    with job_hunter_agent.override(model=mock_model):
        from unittest.mock import AsyncMock

        with (
            patch(
                "agents.job_hunter.search_referentiels_batch", new_callable=AsyncMock
            ) as mock_ref,
            patch(
                "agents.job_hunter.search_job_offers_batch", new_callable=AsyncMock
            ) as mock_jobs,
            patch(
                "agents.job_hunter.get_job_details",
                return_value={"id": "1234567A", "intitule": "Boulanger"},
            ),
        ):
            mock_ref.return_value = {
                "communes:Paris": [{"code": "75056", "label": "Paris"}]
            }
            mock_jobs.return_value = {}

            result = await job_hunter_agent.run(
                "Donne moi plus d'infos sur l'offre 1234567A", deps=test_deps
            )
            assert result.output is not None
