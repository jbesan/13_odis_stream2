import pytest
from pydantic_ai.models.test import TestModel
from unittest.mock import MagicMock, patch
from agents.interviewer import interviewer_agent, InterviewerResult
from agents.state import ODISGraphState, ODISDeps
from core.models import SearchCriterias

@pytest.fixture
def test_deps():
    state = ODISGraphState()
    return ODISDeps(state=state, client=MagicMock())

@pytest.mark.asyncio
async def test_interviewer_agent_prompt_construction(test_deps):
    """Verify that the system prompt constructs without errors."""
    mock_model = TestModel()
    with interviewer_agent.override(model=mock_model):
        with patch('agents.interviewer.search_referentiels_batch', return_value={}):
            # We just need to check if it runs, which triggers prompt construction
            await interviewer_agent.run("Bonjour", deps=test_deps)

@pytest.mark.asyncio
async def test_interviewer_structured_output(test_deps):
    """Verify that the interviewer returns the expected Structured Output."""
    mock_model = TestModel()
    
    with interviewer_agent.override(model=mock_model):
        with patch('agents.interviewer.search_referentiels_batch', return_value={}):
            result = await interviewer_agent.run(
                "Je cherche un job à Paris",
                deps=test_deps
            )
            assert isinstance(result.output, InterviewerResult)
            assert result.output.response is not None
            assert isinstance(result.output.search_criteria, (SearchCriterias, type(None)))
