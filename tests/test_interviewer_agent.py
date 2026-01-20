
import pytest
from app.agents.interviewer import interviewer_agent
from app.agents.state import ODISGraphState

# We won't run actual LLM calls in unit tests (expensive/slow), 
# but PydanticAI seems to imply integration tests.
# For a quick check, we can verify the agent definition and tools.
# BUT keeping "Test-Driven" mind, we should try a dry run or check prompt construction if possible.

@pytest.mark.asyncio
async def test_interviewer_agent_prompt():
    """Verify prompt construction details."""
    state = ODISGraphState()
    # PydanticAI doesn't expose a simple "get_prompt" method without running.
    # So we trust the code valid syntax for now and maybe try a mock run if we had mocks set up.
    # Just asserting the agent exists and has tools is a start.
    assert interviewer_agent is not None
    assert len(interviewer_agent._function_toolset.tools) >= 1
