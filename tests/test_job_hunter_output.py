import pytest
from unittest.mock import MagicMock, patch
from app.agents.job_hunter import JobHunterAgent
from app.agents.state import AgentContext

@pytest.fixture
def agent():
    return JobHunterAgent(model_id="gemini-2.5-flash-lite", client=MagicMock())

def test_job_hunter_synthesis_logic(agent):
    """
    Verify that the Job Hunter agent correctly synthesizes a list of jobs.
    We mock the tool loop to return a specific synthesis and check if it follows instructions.
    Note: Since we can't easily test the LLM's internal multi-turn behavior without a real model,
    we'll verify that the prompt contains the new instructions.
    """
    context = AgentContext()
    context.focus_city = "Castres"
    message = "--- ### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)\nProjet: Mécanicien automobile (I1604)\n---\nTrouve des jobs"
    
    with patch.object(JobHunterAgent, '_execute_tool_loop') as mock_loop:
        mock_loop.return_value = "Voici 3 offres pour Mécanicien automobile..."
        agent.run(message, context)
        
        args, kwargs = mock_loop.call_args
        prompt = args[0]
        
        # Verify instructions are in the prompt
        assert "3 offres les plus pertinentes" in prompt
        assert "total" in prompt.lower()
        assert "Live" in prompt
        assert "SEUL chiffre précis" in prompt
