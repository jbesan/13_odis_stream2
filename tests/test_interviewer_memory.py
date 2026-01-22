import pytest
from pydantic_ai.models.test import TestModel
from unittest.mock import MagicMock, patch
from agents.interviewer import interviewer_agent, InterviewerResult
from agents.state import ODISGraphState, ODISDeps
from core.models import SearchCriterias, CriteriaItem

@pytest.fixture
def test_deps():
    state = ODISGraphState()
    # Mock search_criteria with a commune_actuelle
    state.search_criteria.commune_actuelle = CriteriaItem(code='33063', label='Bordeaux')
    return ODISDeps(state=state, client=MagicMock())

@pytest.mark.asyncio
async def test_interviewer_memory_commune_actuelle(test_deps):
    """Verify that the agent doesn't ask for the current city if it's already known."""
    
    # We use a real model here or a very specific mock if we want to check logic, 
    # but for "not asking", checking the prompt is often enough if we trust the LLM.
    # However, let's try to run it with a mock that returns a response 
    # and check if the prompt sent to the model included the criteria.
    
    mock_model = TestModel()
    
    with interviewer_agent.override(model=mock_model):
        with patch('agents.interviewer.search_referentiels', return_value=[]), \
             patch('agents.interviewer.search_referentiels_batch', return_value={}):
            # Run the agent
            result = await interviewer_agent.run("Bonjour", deps=test_deps)
            
            # Verify that the system prompt was constructed correctly and included the criteria
            # In PydanticAI, we can check the messages sent to the model
            
            from pydantic_ai.messages import ModelRequest, SystemPromptPart
            
            # Find the system prompt part
            full_prompt = ""
            for msg in result.new_messages():
                if isinstance(msg, ModelRequest):
                    for part in msg.parts:
                        if isinstance(part, SystemPromptPart):
                            full_prompt += part.content
            
            if not full_prompt:
                for msg in result.all_messages():
                    if isinstance(msg, ModelRequest):
                        for part in msg.parts:
                            if isinstance(part, SystemPromptPart):
                                full_prompt += part.content
            
            assert "Bordeaux" in full_prompt
            assert '"code": "33063"' in full_prompt
