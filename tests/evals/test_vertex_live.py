import os
import pytest
from pydantic_ai import Agent
from agents.agent_config import get_gemini_client, get_p_model
from pydantic_ai.capabilities import WebSearch

run_evals = os.getenv("RUN_EVALS", "false").lower() == "true"

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not run_evals,
        reason="Evaluation tests are skipped by default. Set RUN_EVALS=true to run them.",
    ),
]


@pytest.mark.asyncio
async def test_vertex_ai_live_execution():
    """
    Live integration test verifying that an agent configured with WebSearch
    executes successfully against Vertex AI without serialization errors.
    """
    client = get_gemini_client()

    # Get the model configured for housing_expert which uses WebSearch
    model = get_p_model("housing_expert", client=client)

    # Build a simple test agent using the same model and WebSearch capability
    agent = Agent(model, capabilities=[WebSearch()])

    # Run it live. If include_server_side_tool_invocations is sent, this will crash.
    result = await agent.run("Hello, what is the capital of France?")

    assert result.output is not None
    print(f"\nLive Result: {result.output}")
