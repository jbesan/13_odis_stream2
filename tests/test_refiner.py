import pytest
from unittest.mock import MagicMock, patch
from agents.graph import refiner_node
from agents.state import ODISGraphState, ODISDeps, SearchCriterias
from core.models import CriteriaItem
from agents.refiner import RefinerResult

@pytest.fixture
def state():
    s = ODISGraphState()
    s.search_criteria = SearchCriterias(
        commune_actuelle=CriteriaItem(code='33063', label='Bordeaux'),
        codes_metiers=[[CriteriaItem(code='D1102', label='Boulangerie')]]
    )
    s.messages = [
        {"role": "user", "content": "Je cherche une ville pour ma famille."},
        {"role": "assistant", "content": "D'accord, je vais vous aider."}
    ]
    return s

@pytest.fixture
def config(state):
    return {"configurable": {"deps": ODISDeps(state=state, client=MagicMock())}}

@pytest.mark.asyncio
async def test_refiner_node_updates_state(state, config):
    """Verify that refiner_node calls the agent and returns state updates."""
    mock_result = MagicMock()
    mock_result.output = RefinerResult(
        briefing="Nouveau briefing."
    )
    
    with patch('agents.refiner.refiner_agent.run') as mock_run, \
         patch('agents.graph.get_p_model') as mock_model:
        mock_run.return_value = mock_result
        mock_model.return_value = MagicMock()
        
        output = await refiner_node(state, config)
        
        assert output["briefing"] == "Nouveau briefing."
        assert output["last_summarized_idx"] == 2
        assert "focus_city" not in output
        mock_run.assert_called_once()

@pytest.mark.asyncio
async def test_refiner_node_skips_if_nothing_new(state, config):
    """Verify that refiner_node skips if no new developments."""
    state.briefing = "Existing briefing"
    state.last_summarized_idx = 2
    
    with patch('agents.refiner.refiner_agent.run') as mock_run:
        output = await refiner_node(state, config)
        
        assert output == {} # Skipped
        mock_run.assert_not_called()

@pytest.mark.asyncio
async def test_refiner_node_with_experts(state, config):
    """Verify that expert results trigger an update even if no new messages."""
    from agents.refiner import RefinerResult
    state.briefing = "Existing briefing"
    state.last_summarized_idx = 2
    state.scoring_results = {"scout": "Infos sur Bordeaux"}
    
    mock_result = MagicMock()
    mock_result.output = RefinerResult(
        briefing="Briefing mis à jour."
    )
    
    with patch('agents.refiner.refiner_agent.run') as mock_run, \
         patch('agents.graph.get_p_model') as mock_model:
        mock_run.return_value = mock_result
        mock_model.return_value = MagicMock()
        
        output = await refiner_node(state, config)
        
        assert output["briefing"] == "Briefing mis à jour."
        mock_run.assert_called_once()
