import pytest
from pydantic_ai.models.test import TestModel
from unittest.mock import MagicMock, patch
from agents.job_hunter import job_hunter_agent
from agents.state import ODISGraphState, ODISDeps

@pytest.fixture
def test_deps():
    state = ODISGraphState(
        briefing="Projet: Boulanger",
        focus_city="Paris"
    )
    # We mock search_referentiels to return a dummy INSEE for Paris
    mock_client = MagicMock()
    return ODISDeps(state=state, client=mock_client)

@pytest.mark.asyncio
async def test_job_hunter_search_intent(test_deps):
    """Verify that the job hunter agent can be run with a mock model."""
    mock_model = TestModel()
    
    with job_hunter_agent.override(model=mock_model):
        # Patching agents.tools which is where the agent imports from
        with patch('agents.job_hunter.search_referentiels', return_value=[{"code": "75056", "label": "Paris"}]), \
             patch('agents.job_hunter.search_job_offers', return_value={"offres": [], "total": 0}), \
             patch('agents.job_hunter.get_job_details', return_value={}):
            
            result = await job_hunter_agent.run(
                "Trouve moi des jobs de boulanger",
                deps=test_deps
            )
            assert result.output is not None
            assert isinstance(result.output, str)

@pytest.mark.asyncio
async def test_job_hunter_tool_calls(test_deps):
    """Verify that the agent can call tools using TestModel's call_tools flag."""
    mock_model = TestModel()
    
    with job_hunter_agent.override(model=mock_model):
        with patch('agents.job_hunter.search_referentiels', return_value=[{"code": "75056", "label": "Paris"}]), \
             patch('agents.job_hunter.search_job_offers', return_value={"offres": [], "total": 0}), \
             patch('agents.job_hunter.get_job_details', return_value={"id": "1234567A", "intitule": "Boulanger"}):
            
            result = await job_hunter_agent.run(
                "Donne moi plus d'infos sur l'offre 1234567A",
                deps=test_deps
            )
            assert result.output is not None
