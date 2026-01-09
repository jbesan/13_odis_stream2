import pytest
from unittest.mock import MagicMock, patch
from agents.job_hunter import JobHunterAgent, JOB_HUNTER_PROMPT
from agents.state import AgentContext

@pytest.fixture
def agent():
    # JobHunterAgent(model_id, client)
    return JobHunterAgent(model_id="gemini-2.5-flash-lite", client=MagicMock())

def test_job_hunter_search_intent(agent):
    """Verify that a general message triggers search logic."""
    context = AgentContext()
    message = "--- ### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)\nProjet: Boulanger\n---\nTrouve moi des jobs de boulanger"
    
    with patch.object(JobHunterAgent, '_execute_tool_loop') as mock_loop:
        mock_loop.return_value = "Search Results"
        agent.run(message, context)
        
        args, kwargs = mock_loop.call_args
        prompt = args[0]
        full_msg = args[1]
        assert "Job Hunter ODIS" in prompt
        assert "Trouve moi des jobs de boulanger" in full_msg

def test_job_hunter_details_intent(agent):
    """Verify that a message with a job ID triggers detail fetch intent."""
    context = AgentContext()
    message = "Donne moi plus d'infos sur l'offre 1234567A"
    
    with patch.object(JobHunterAgent, '_execute_tool_loop') as mock_loop:
        mock_loop.return_value = "Details Results"
        agent.run(message, context)
        
        args, kwargs = mock_loop.call_args
        prompt = args[0]
        tools = args[2] if len(args) > 2 else kwargs.get('tools')
        
        assert "DETAIL d'une offre d'emploi précise" in prompt
        # Job Details and other tools are passed to the loop
        assert any(t.__name__ == 'get_job_details' for t in tools)

def test_job_hunter_details_intent_mixed_case(agent):
    """Verify case-insensitivity for job ID detection."""
    context = AgentContext()
    message = "offre 7654321b"
    
    with patch.object(JobHunterAgent, '_execute_tool_loop') as mock_loop:
        mock_loop.return_value = "Details Results"
        agent.run(message, context)
        
        args, kwargs = mock_loop.call_args
        prompt = args[0]
        assert "DETAIL d'une offre d'emploi précise" in prompt
        assert "7654321B" in prompt
