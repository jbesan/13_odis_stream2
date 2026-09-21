import logging
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agents.grounding import extract_web_grounding
from agents.graph import _dynamic_toolsets_for_expert
from agents.state import GraphState, ODISDeps
from agents.web_search import (
    DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS,
    WEB_SEARCH_MODEL,
    WEB_SEARCH_SAFETY_MARGIN_SECONDS,
    WEB_SEARCH_TOOL_ID,
    execute_web_search_batch,
    pop_web_search_result,
    pop_web_search_usage,
    reserve_web_search_call,
    search_web_batch_tool,
)
from core.evidence import (
    WebGroundingSupport,
    WebSearchBatchResult,
    WebSearchCompactResult,
    WebSearchNeed,
    WebSource,
)
from agents.source_registry import source_references_for_result


class FakeModels:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeClient:
    def __init__(self, response):
        self.models = FakeModels(response)
        self.aio = SimpleNamespace(models=self.models)


def grounded_response(*, with_sources: bool = True, with_queries: bool = True):
    grounding = {}
    if with_queries:
        grounding["web_search_queries"] = ["aide FLE Albi"]
    if with_sources:
        grounding["grounding_chunks"] = [
            {
                "web": {
                    "uri": "https://example.org/fle",
                    "title": "Cours de français",
                    "domain": "example.org",
                }
            }
        ]
    if with_sources:
        grounding["grounding_supports"] = [
            {
                "segment": {
                    "text": "Une structure locale propose des cours de français.",
                    "start_index": 0,
                    "end_index": 56,
                },
                "grounding_chunk_indices": [0],
            }
        ]
    return SimpleNamespace(
        text="Une structure locale propose des cours de français.",
        candidates=[SimpleNamespace(grounding_metadata=grounding)],
        usage_metadata=SimpleNamespace(
            prompt_token_count=100,
            cached_content_token_count=40,
            candidates_token_count=10,
            thoughts_token_count=2,
            total_token_count=112,
            tool_use_prompt_token_count=0,
        ),
    )


@pytest.mark.asyncio
async def test_web_search_executes_one_direct_batch_call_and_keeps_provider_urls():
    client = FakeClient(grounded_response())
    result, usage = await execute_web_search_batch(
        [
            WebSearchNeed(
                key_terms=["cours FLE", "alphabétisation"],
                question="Où apprendre le français ?",
                location="Albi",
            ),
            WebSearchNeed(key_terms=["aide permis", "Tarn"], location="Albi"),
        ],
        client=client,
    )

    assert len(client.models.calls) == 1
    assert client.models.calls[0]["model"] == WEB_SEARCH_MODEL
    config = client.models.calls[0]["config"]
    # JSON output plus Google Search can return webSearchQueries while omitting
    # groundingChunks. Keep both provider schema and prompt-level JSON off.
    assert config.response_mime_type is None
    assert config.response_json_schema is None
    assert "sans URL" not in config.system_instruction
    assert "Ne mets jamais de liens" not in config.system_instruction
    assert "Ne produis pas de JSON" in config.system_instruction
    assert "Réponds en texte libre, pas en JSON" in client.models.calls[0]["contents"]
    assert config.thinking_config.thinking_budget == 0
    assert len(config.tools) == 1
    assert config.tools[0].google_search is not None
    assert result.status == "resolved"
    assert result.summary == "Une structure locale propose des cours de français."
    assert result.sources == [
        WebSource(
            url="https://example.org/fle",
            title="Cours de français",
            domain="example.org",
        )
    ]
    assert result.grounding_supports == [
        WebGroundingSupport(
            grounding_chunk_indices=[0],
            text="Une structure locale propose des cours de français.",
            start_index=0,
            end_index=56,
        )
    ]
    assert usage.input_tokens == 100
    assert usage.input_tokens_new == 60
    assert usage.cache_read_tokens == 40
    assert usage.output_tokens == 12
    assert usage.grounding_queries == 1
    assert usage.cost_eur > 0


@pytest.mark.asyncio
async def test_web_search_keeps_free_text_opaque_without_parsing_it():
    response = grounded_response()
    response.text += "\nRecherche terminée."
    client = FakeClient(response)

    result, _usage = await execute_web_search_batch(
        [WebSearchNeed(key_terms=["aide FLE"], location="Albi")], client=client
    )

    assert result.summary == (
        "Une structure locale propose des cours de français.\nRecherche terminée."
    )
    assert result.sources[0].url == "https://example.org/fle"


@pytest.mark.asyncio
async def test_web_search_reports_query_without_fabricating_a_url():
    client = FakeClient(grounded_response(with_sources=False))
    result, _usage = await execute_web_search_batch(
        [WebSearchNeed(key_terms=["aide FLE"], location="Albi")], client=client
    )

    assert result.status == "partial"
    assert result.summary == "Une structure locale propose des cours de français."
    assert result.queries == ["aide FLE Albi"]
    assert result.sources == []


@pytest.mark.asyncio
async def test_web_search_discards_model_summary_without_provider_search_activity():
    client = FakeClient(grounded_response(with_sources=False, with_queries=False))
    result, _usage = await execute_web_search_batch(
        [WebSearchNeed(key_terms=["aide FLE"], location="Albi")], client=client
    )

    assert result.status == "unavailable"
    assert result.summary is None
    assert result.queries == []
    assert result.sources == []


def test_web_batch_return_is_extracted_by_source_ledger():
    tool_result = WebSearchBatchResult(
        status="resolved",
        queries=["aide FLE Albi"],
        sources=[
            WebSource(
                url="https://example.org/fle",
                title="Cours de français",
                domain="example.org",
            )
        ],
        grounding_supports=[
            WebGroundingSupport(
                grounding_chunk_indices=[0],
                text="Cours disponibles",
                start_index=0,
            )
        ],
    )
    result = SimpleNamespace(
        all_messages=lambda: [
            SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        tool_name=WEB_SEARCH_TOOL_ID,
                        args={"searches": [{"key_terms": ["aide FLE"]}]},
                        content=tool_result,
                    )
                ]
            )
        ]
    )

    grounding = extract_web_grounding(result)
    assert grounding["query_count"] == 1
    assert grounding["sources"][0]["url"] == "https://example.org/fle"
    assert grounding["supports"] == [
        {
            "grounding_chunk_indices": [0],
            "text": "Cours disponibles",
            "start_index": 0,
        }
    ]

    references = source_references_for_result("social_integration_expert", result)
    web = [item for item in references if item["source_key"] == "web"]
    assert web[0]["reference_id"] == "Ref-1"
    assert web[0]["source_url"] == "https://example.org/fle"
    assert web[0]["search_terms"] == ["aide FLE"]
    assert web[0]["grounding_supports"][0]["grounding_chunk_indices"] == [0]


def test_serialized_web_batch_return_keeps_grounding_supports():
    tool_result = WebSearchBatchResult(
        status="resolved",
        queries=["aide locale"],
        sources=[WebSource(url="https://example.org/aide", title="Aide locale")],
        grounding_supports=[
            {
                "grounding_chunk_indices": [0],
                "text": "Une aide locale.",
                "start_index": 0,
                "end_index": 17,
            }
        ],
    )
    result = SimpleNamespace(
        all_messages=lambda: [
            SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        tool_name=WEB_SEARCH_TOOL_ID,
                        content=tool_result.model_dump_json(),
                    )
                ]
            )
        ]
    )

    grounding = extract_web_grounding(result)

    assert grounding["queries"] == ["aide locale"]
    assert grounding["sources"][0]["url"] == "https://example.org/aide"
    assert grounding["supports"][0]["grounding_chunk_indices"] == [0]


def test_web_batch_without_provider_metadata_is_not_presented_as_consulted():
    tool_result = WebSearchBatchResult(status="unavailable")
    result = SimpleNamespace(
        all_messages=lambda: [
            SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        tool_name=WEB_SEARCH_TOOL_ID,
                        args={
                            "searches": [{"key_terms": ["tarification solidaire TCL"]}]
                        },
                        content=tool_result,
                    )
                ]
            )
        ]
    )

    references = source_references_for_result("mobility_expert", result)
    web = [item for item in references if item["source_key"] == "web"]

    assert web[0]["status"] == "non confirmée"
    assert web[0]["grounding_confirmed"] is False
    assert web[0]["search_terms"] == ["tarification solidaire TCL"]


def test_web_tool_is_injected_only_when_skill_card_allows_it():
    allowed = _dynamic_toolsets_for_expert(
        GraphState(
            expert_skill_tools={"social_integration_expert": [WEB_SEARCH_TOOL_ID]}
        ),
        "social_integration_expert",
    )
    denied = _dynamic_toolsets_for_expert(
        GraphState(expert_skill_tools={"social_integration_expert": []}),
        "social_integration_expert",
    )

    assert allowed is not None
    assert list(allowed[0].tools) == [WEB_SEARCH_TOOL_ID]
    assert denied is None


@pytest.mark.asyncio
async def test_expert_worker_passes_dynamic_web_tool_and_deduplicates_usage():
    state = GraphState(
        messages=[{"role": "user", "content": "Analyse l'intégration."}],
        run_id="run-1",
        expert_tasks={"social_integration_expert": "Cherche les aides locales."},
        expert_skill_tools={"social_integration_expert": [WEB_SEARCH_TOOL_ID]},
    )
    deps = ODISDeps(state=state, client=object())
    calls = {}
    tool_result = WebSearchBatchResult(
        status="resolved",
        queries=["aide locale"],
        sources=[WebSource(url="https://example.org/aide", title="Aide locale")],
    )
    parent_result = SimpleNamespace(
        output=SimpleNamespace(result="Analyse locale."),
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            requests=1,
            tool_calls=1,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ),
        all_messages=lambda: [
            SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        tool_name=WEB_SEARCH_TOOL_ID,
                        content=tool_result,
                    )
                ]
            )
        ],
    )

    class FakeAgent:
        async def run(self, *args, **kwargs):
            calls["toolsets"] = kwargs["toolsets"]
            scope = f"run-1:social_integration_expert:{id(context)}"
            # Use a real UsageStats so the graph merge exercises the same
            # accounting path as the direct wrapper.
            from agents.state import UsageStats

            deps.web_search_usage[scope] = UsageStats(
                input_tokens=20,
                input_tokens_new=20,
                output_tokens=8,
                total_tokens=28,
                requests=1,
                grounding_queries=1,
                token_cost_eur=0.001,
                cost_eur=0.001,
                breakdown={"web_search_batch": {"token_cost_eur": 0.001}},
            )
            return parent_result

    context = SimpleNamespace(
        inputs="social_integration_expert", state=state, deps=deps
    )
    with (
        patch("agents.graph.social_integration_expert_agent", FakeAgent()),
        patch("agents.graph.get_model", return_value="google:gemini-3.1-flash-lite"),
        patch("agents.graph.get_p_model", return_value=object()),
        patch("agents.graph.get_model_settings", return_value={}),
        patch("agents.graph.log_agent_trace"),
    ):
        from agents.graph import expert_worker_step

        artifact = await expert_worker_step(context)

    assert calls["toolsets"] is not None
    assert WEB_SEARCH_TOOL_ID in calls["toolsets"][0].tools
    assert artifact.usage.requests == 2
    assert artifact.usage.grounding_queries == 1
    assert (
        artifact.usage.breakdown["social_integration_expert"]["grounding_queries"] == []
    )
    assert artifact.usage.breakdown["web_search_batch"]["token_cost_eur"] == 0.001
    assert any(source.get("reference_id") == "Ref-1" for source in artifact.sources)


@pytest.mark.asyncio
async def test_function_tool_enforces_one_batch_call_per_scope():
    deps = ODISDeps(state=GraphState(), client=None)
    scope, first = reserve_web_search_call(deps)
    second_scope, second = reserve_web_search_call(deps)

    assert first is True
    assert second_scope == scope
    assert second is False
    assert pop_web_search_usage(deps, scope).requests == 0

    # The public function still returns an application-level status instead of
    # making a second provider request when invoked after the reservation.
    ctx = SimpleNamespace(deps=deps)
    result = await search_web_batch_tool(
        ctx, [WebSearchNeed(key_terms=["aide locale"])]
    )
    assert result.status == "unavailable"


@pytest.mark.asyncio
async def test_web_search_tool_returns_compact_result_and_preserves_out_of_band_metadata():
    client = FakeClient(grounded_response())
    state = GraphState(run_id="test-run")
    deps = ODISDeps(state=state, client=client)
    ctx = SimpleNamespace(deps=deps)

    # Call the tool with a search request
    compact = await search_web_batch_tool(
        ctx, [WebSearchNeed(key_terms=["cours FLE"], location="Albi")]
    )

    # 1. Verify that the LLM receives a compact result without long redirect URLs or grounding offsets
    assert isinstance(compact, WebSearchCompactResult)
    assert compact.status == "resolved"
    assert compact.summary == "Une structure locale propose des cours de français."
    assert compact.consulted_domains == ["example.org"]
    dumped = compact.model_dump()
    assert "sources" not in dumped
    assert "grounding_supports" not in dumped
    assert "queries" not in dumped

    # 2. Verify that the full result is stored out-of-band in deps
    scope = f"standalone:{id(deps)}"
    full_result = pop_web_search_result(deps, scope)
    assert isinstance(full_result, WebSearchBatchResult)
    assert full_result.status == "resolved"
    assert len(full_result.sources) == 1
    assert full_result.sources[0].url == "https://example.org/fle"
    assert len(full_result.grounding_supports) == 1
    assert full_result.grounding_supports[0].grounding_chunk_indices == [0]

    # 3. Verify that the source registry constructs the full source ledger from the out-of-band result
    references = source_references_for_result(
        "social_integration_expert", None, web_result=full_result
    )
    web_refs = [r for r in references if r["source_key"] == "web"]
    assert len(web_refs) == 1
    assert web_refs[0]["reference_id"] == "Ref-1"
    assert web_refs[0]["source_url"] == "https://example.org/fle"
    assert web_refs[0]["grounding_domain"] == "example.org"
    assert web_refs[0]["grounding_supports"][0]["grounding_chunk_indices"] == [0]


@pytest.mark.asyncio
async def test_execute_web_search_batch_configures_http_timeout():
    assert DEFAULT_WEB_SEARCH_TIMEOUT_SECONDS == 10.0
    client = FakeClient(grounded_response())
    result, usage = await execute_web_search_batch(
        [WebSearchNeed(key_terms=["aide FLE"], location="Albi")],
        client=client,
        timeout_seconds=8.0,
    )
    assert len(client.models.calls) == 1
    config = client.models.calls[0]["config"]
    assert config.http_options is not None
    assert config.http_options.timeout == 8000


@pytest.mark.asyncio
async def test_web_search_tool_handles_timeout_gracefully_with_warning(caplog):
    class TimeoutModels:
        async def generate_content(self, **kwargs):
            raise TimeoutError("Socket receive timed out")

    client = SimpleNamespace(aio=SimpleNamespace(models=TimeoutModels()))
    state = GraphState(run_id="test-timeout-run")
    deps = ODISDeps(state=state, client=client)
    ctx = SimpleNamespace(deps=deps)

    with caplog.at_level(logging.WARNING):
        compact = await search_web_batch_tool(
            ctx, [WebSearchNeed(key_terms=["cours FLE"], location="Albi")]
        )

    assert isinstance(compact, WebSearchCompactResult)
    assert compact.status == "unavailable"
    assert any("timed out" in r.message for r in caplog.records)
    # Ensure it logged as WARNING and NOT as ERROR
    assert not any(r.levelno >= logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_web_search_tool_skips_when_remaining_deadline_insufficient(caplog):
    client = FakeClient(grounded_response())
    # Deadline is less than WEB_SEARCH_SAFETY_MARGIN_SECONDS (15s)
    state = GraphState(
        run_id="test-short-deadline",
        run_deadline_at=time.time() + (WEB_SEARCH_SAFETY_MARGIN_SECONDS - 5.0),
    )
    deps = ODISDeps(state=state, client=client)
    ctx = SimpleNamespace(deps=deps)

    with caplog.at_level(logging.WARNING):
        compact = await search_web_batch_tool(
            ctx, [WebSearchNeed(key_terms=["cours FLE"], location="Albi")]
        )

    assert isinstance(compact, WebSearchCompactResult)
    assert compact.status == "unavailable"
    # Verify no HTTP request was made to Gemini
    assert len(client.models.calls) == 0
    assert any(
        "insufficient time before graph deadline" in r.message for r in caplog.records
    )
