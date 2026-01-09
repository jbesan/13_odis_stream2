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
    message = "Trouve moi des jobs de boulanger"
    
    with patch.object(JobHunterAgent, '_execute_tool_loop') as mock_loop:
        mock_loop.return_value = "Search Results"
        agent.run(message, context)
        
        args, kwargs = mock_loop.call_args
        prompt = args[0]
        full_msg = args[1]
        assert "expert emploi ODIS" in prompt
        assert "PROJET DE VIE" in full_msg

@patch('agents.job_hunter.get_job_details')
def test_job_hunter_details_intent(mock_get, agent):
    """Verify that a message with a job ID triggers manual detail fetch and synthesis."""
    context = AgentContext()
    mock_get.return_value = {"id": "1234567A", "intitule": "Boulanger", "url_postulation": "http://test"}
    
    message = "Donne moi plus d'infos sur l'offre 1234567A"
    with patch.object(JobHunterAgent, '_execute_tool_loop') as mock_loop:
        mock_loop.return_value = "Details Results"
        agent.run(message, context)
        
        # Verify manual tool call happened
        mock_get.assert_called_with("1234567A")
        
        # Verify synthesis loop was called without tools
        # args[0] is prompt, args[1] is message, etc.
        args, kwargs = mock_loop.call_args
        prompt = args[0]
        # Check tools list (handle positional or keyword args)
        if 'tools' in kwargs:
            tools = kwargs['tools']
        else:
            tools = args[2] if len(args) > 2 else None
            
        assert "Synthétise l'offre suivante" in prompt
        assert tools == []

@patch('agents.job_hunter.get_job_details')
def test_job_hunter_details_intent_mixed_case(mock_get, agent):
    """Verify case-insensitivity with manual call logic."""
    context = AgentContext()
    mock_get.return_value = {"id": "7654321B", "intitule": "Test"}
    message = "offre 7654321b"
    
    with patch.object(JobHunterAgent, '_execute_tool_loop') as mock_loop:
        mock_loop.return_value = "Details Results"
        agent.run(message, context)
        
        mock_get.assert_called_with("7654321B")
        args, kwargs = mock_loop.call_args
        assert "Synthétise l'offre suivante" in args[0]
