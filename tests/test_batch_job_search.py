import pytest
from pydantic_ai.models.test import TestModel
from unittest.mock import MagicMock, patch
import sys
import os

# Ensure app is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app')))

from agents.job_hunter import job_hunter_agent
from agents.state import ODISGraphState, ODISDeps
from agents.tools import search_job_offers_batch

@pytest.fixture
def test_deps():
    state = ODISGraphState(
        briefing="Projet: Boulanger et Pâtissier",
        focus_city="Paris",
        search_criteria={}
    )
    mock_client = MagicMock()
    return ODISDeps(state=state, client=mock_client)

def test_search_job_offers_batch_logic():
    """Verify the internal logic of search_job_offers_batch function in tools.py."""
    with patch('agents.tools._search_job_offers_logic') as mock_logic:
        mock_logic.return_value = {"offres": [], "total": 10}
        
        queries = [
            {"rome": "D1102", "location": "75056"},
            {"rome": "D1104", "location": "75056"}
        ]
        
        results = search_job_offers_batch(queries)
        
        assert "D1102|75056|" in results
        assert "D1104|75056|" in results
        assert results["D1102|75056|"]["total"] == 10
        assert mock_logic.call_count == 2

@pytest.mark.asyncio
async def test_job_hunter_tool_registration(test_deps):
    """Verify that JobHunter has the batch tool and NOT the unitary one."""
    
    # Check tool names in the agent
    # In PydanticAI, tools are accessible via _function_toolset (contains 'tools' dict)
    tool_names = list(job_hunter_agent._function_toolset.tools.keys())
    
    assert "search_job_offers_batch_tool" in tool_names
    assert "search_job_offers_tool" not in tool_names

@pytest.mark.asyncio
async def test_job_hunter_execution_with_batch_mock(test_deps):
    """Verify the agent runs and handles batch tool responses."""
    mock_model = TestModel()
    
    with job_hunter_agent.override(model=mock_model):
        # Mock dependencies (referentiels and job offers)
        # We patch the tools used by the agent
        with patch('agents.job_hunter.search_referentiels_batch', return_value={
                 "communes:Paris": [{"code": "75056", "label": "Paris"}]
             }), \
             patch('agents.job_hunter.search_job_offers_batch', return_value={
                 "D1102|75056|": {"offres": [{"id": "1", "intitule": "Boulanger"}], "total": 1},
                 "D1104|75056|": {"offres": [{"id": "2", "intitule": "Pâtissier"}], "total": 1}
             }), \
             patch('agents.job_hunter.get_job_details', return_value={}):
            
            result = await job_hunter_agent.run(
                "Cherche des jobs de boulanger et pâtissier à Paris",
                deps=test_deps
            )
            assert result.output is not None
            # The agent might not produce content if the mock model doesn't return anything,
            # but we want to ensure no crash and tool availability.
