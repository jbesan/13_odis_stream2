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


from pydantic import BaseModel, Field
from typing import Literal


class SimpleStructuredCheck(BaseModel):
    capital: str = Field(description="Capital city")
    country: str = Field(description="Country name")
    status: Literal["confirmed", "uncertain"] = "confirmed"


@pytest.mark.asyncio
async def test_vertex_ai_live_execution():
    """
    Live integration test verifying that an agent configured with WebSearch
    and structured output executes successfully against Vertex AI (eu endpoint)
    without serialization errors, and reports usage correctly.
    """
    client = get_gemini_client()

    # Get the model configured for housing_expert which uses WebSearch
    model = get_p_model("housing_expert", client=client)

    # Build test agent using the same model, WebSearch capability, and structured output
    agent = Agent(
        model,
        capabilities=[WebSearch(max_uses=1)],
        output_type=SimpleStructuredCheck,
    )

    # Run it live. If include_server_side_tool_invocations is sent or structured output fails, this will crash.
    result = await agent.run("Quelle est la capitale de la France ?")

    assert result.output is not None
    assert isinstance(result.output, SimpleStructuredCheck)
    assert "paris" in result.output.capital.lower()
    assert result.output.country.lower() in ("france", "la france")

    # Verify usage fields
    assert result.usage.input_tokens > 0
    assert result.usage.output_tokens > 0
    assert result.usage.total_tokens > 0
    print(f"\n✅ Vertex EU Live Result: {result.output}")
    print(f"📊 Usage: In={result.usage.input_tokens}, Out={result.usage.output_tokens}, Total={result.usage.total_tokens}")
