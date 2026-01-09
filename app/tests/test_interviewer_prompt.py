import pytest
from unittest.mock import MagicMock, patch
from agents.interviewer import InterviewerAgent
from agents.state import AgentContext
from config import CLASSES_SCOLAIRES, HEBERGEMENT_OPTIONS

@pytest.fixture
def agent():
    return InterviewerAgent(model_id="gemini-3-flash-preview", client=MagicMock())

def test_interviewer_prompt_injection(agent):
    """Verify that config values are correctly injected into the prompt and tool docstring."""
    context = AgentContext()
    # Simulate orchestrator prepending a briefing
    briefing_header = "### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)"
    message = f"---\n{briefing_header}\nSummary points\n---\nBonjour"
    
    with patch.object(InterviewerAgent, '_execute_tool_loop') as mock_loop:
        mock_loop.return_value = "Response"
        agent.run(message, context)
        
        args, kwargs = mock_loop.call_args
        prompt = args[0]
        tools = args[2]
        
        # Check that briefing is injected into the prompt
        assert "### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)" in prompt
        # The CLASSES_SCOLAIRES etc are now injected into the prompt via .replace
        assert str(CLASSES_SCOLAIRES) in prompt
        
        # Check tool docstring injection
        update_criteria_tool = next(t for t in tools if t.__name__ == "update_search_criteria")
        assert str(CLASSES_SCOLAIRES) in update_criteria_tool.__doc__
        assert str(HEBERGEMENT_OPTIONS) in update_criteria_tool.__doc__
