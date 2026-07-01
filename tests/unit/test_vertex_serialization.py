import pytest
from google.genai import Client
from google.genai.types import GenerateContentResponse, Candidate, Content, Part
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch


@pytest.mark.asyncio
async def test_vertex_ai_tool_config_serialization():
    """
    Verifies that requests sent to Vertex AI do not contain the unsupported
    'include_server_side_tool_invocations' parameter.
    """
    # 1. Initialize client but mock generate_content to capture the config dict
    client = Client(vertexai=True, project="odis-stream2", location="eu")

    captured_config = []

    async def mock_generate_content(*args, **kwargs):
        if "config" in kwargs:
            captured_config.append(kwargs["config"])
        return GenerateContentResponse(
            candidates=[
                Candidate(content=Content(parts=[Part(text="Mocked response")]))
            ]
        )

    client.aio.models.generate_content = mock_generate_content

    # 2. Setup the agent with GoogleCloudProvider and WebSearch
    from agents.agent_config import get_p_model

    model = get_p_model("housing_expert")
    model.provider._client = client

    agent = Agent(model, capabilities=[WebSearch()])

    # 3. Run the agent to trigger config serialization
    await agent.run("Hello")

    # 4. Assertions on the captured config
    assert len(captured_config) == 1
    config = captured_config[0]

    # In dict representation, Pydantic AI serializes config to dict/GenerateContentConfigDict
    # Let's inspect the 'tool_config' key
    tool_config = config.get("tool_config")

    if tool_config:
        # Vertex AI will reject the request if include_server_side_tool_invocations is True
        assert tool_config.get("include_server_side_tool_invocations") is not True
