import pytest
from google.genai import Client
from google.genai.types import GenerateContentResponse, Candidate, Content, Part
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.google_cloud import GoogleCloudProvider


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

    model = get_p_model("housing_expert", client=client)
    assert model.profile["google_supports_server_side_tool_invocations"] is True

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


async def _capture_native_tool_config(provider):
    """Run one mocked native-tool request and return its transport decision."""

    captured_config = []

    async def mock_generate_content(*args, **kwargs):
        captured_config.append(kwargs.get("config"))
        return GenerateContentResponse(
            candidates=[
                Candidate(content=Content(parts=[Part(text="Mocked response")]))
            ]
        )

    provider._client.aio.models.generate_content = mock_generate_content
    model = GoogleModel("gemini-3.1-flash-lite", provider=provider)
    await Agent(model, capabilities=[WebSearch(max_uses=1)]).run("Hello")
    assert len(captured_config) == 1
    return model, captured_config[0]


@pytest.mark.asyncio
async def test_google_model_uses_vertex_transport_even_with_google_provider_name():
    """Pin PydanticAI #7280: the pre-built client transport is authoritative."""

    client = Client(vertexai=True, project="odis-stream2", location="eu")
    model, config = await _capture_native_tool_config(GoogleProvider(client=client))

    tool_config = config.get("tool_config") if config else None
    assert (
        not tool_config
        or tool_config.get("include_server_side_tool_invocations") is not True
    )


@pytest.mark.asyncio
async def test_google_model_uses_gemini_transport_even_with_cloud_provider_name():
    """Pin the mirrored #7280 case for an injected Gemini API client."""

    client = Client(vertexai=False, api_key="offline-test-key")
    model, config = await _capture_native_tool_config(
        GoogleCloudProvider(client=client)
    )

    tool_config = config.get("tool_config") if config else None
    assert tool_config is not None
    assert tool_config.get("include_server_side_tool_invocations") is True
