"""Offline production-contract tests for the social adaptive-expert pilot."""

import asyncio
import time
from dataclasses import replace

import pytest
from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.evidence.executor import execute_trusted_batch
from agents.evidence.orchestrator import run_adaptive_social_expert
from agents.evidence.projector import project_social_context
from agents.evidence.registry import NormalizedToolResult, SOCIAL_TOOL_REGISTRY
from agents.evidence.web_child import WebChildDeps, web_child_agent
from agents.graph import adaptive_expert_enabled
from agents.state import GraphState, UsageStats
from core.evidence import (
    EvidenceGap,
    EvidencePlan,
    ExpertWorkingState,
    FinalExpertReport,
    ToolRequest,
    WebEvidenceBundle,
    WebSource,
)
from core.models import CommuneResult


def _state() -> GraphState:
    return GraphState(
        focus_city=CommuneResult(name="Bordeaux", codgeo="33063"),
        run_id="pilot-run",
        expert_tasks={
            "social_integration_expert": "Évaluer les possibilités de cours de français."
        },
    )


def _output_tool(info: AgentInfo, fragment: str) -> str:
    return next(
        tool.name for tool in info.output_tools if fragment in tool.name.lower()
    )


def _dossier_evidence_id() -> str:
    return project_social_context(_state()).dossier_evidence[0].evidence_id


def test_feature_flag_is_explicit_and_domain_scoped(monkeypatch):
    monkeypatch.delenv("ODIS_ADAPTIVE_EXPERTS", raising=False)
    assert not adaptive_expert_enabled("social_integration_expert")
    monkeypatch.setenv(
        "ODIS_ADAPTIVE_EXPERTS", "healthcare_expert, social_integration_expert"
    )
    assert adaptive_expert_enabled("social_integration_expert")
    assert not adaptive_expert_enabled("housing_expert")


@pytest.mark.asyncio
async def test_web_child_uses_native_json_not_a_custom_output_tool():
    observed_function_tools: list[str] = []
    observed_output_tools: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        observed_function_tools.extend(tool.name for tool in info.function_tools)
        observed_output_tools.extend(tool.name for tool in info.output_tools)
        return ModelResponse(
            parts=[
                TextPart(
                    '{"status":"not_found","summary":"Aucun résultat.","sources":[]}'
                )
            ]
        )

    with web_child_agent.override(model=FunctionModel(model)):
        result = await web_child_agent.run(
            "Recherche test",
            deps=WebChildDeps(query="cours FLE", reason="test"),
        )
    assert result.output.status == "not_found"
    assert observed_function_tools == []
    assert observed_output_tools == []


@pytest.mark.asyncio
async def test_fast_path_is_one_parent_call_and_has_no_executable_tools():
    calls = 0
    function_tools: list[str] = []

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        function_tools.extend(tool.name for tool in info.function_tools)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    _output_tool(info, "finalexpertreport"),
                    {
                        "kind": "final_report",
                        "analysis": [
                            {
                                "text": "Le dossier permet de cibler immédiatement l'accompagnement linguistique.",
                                "evidence_ids": [_dossier_evidence_id()],
                                "status": "supported",
                            }
                        ],
                        "recommended_actions": [
                            {
                                "action": "Contacter la structure déjà identifiée.",
                                "rationale": "Le besoin de français est documenté.",
                                "evidence_ids": [_dossier_evidence_id()],
                            }
                        ],
                    },
                )
            ]
        )

    result = await run_adaptive_social_expert(
        _state(), model=FunctionModel(model), model_id="offline"
    )
    assert calls == 1
    assert function_tools == []
    assert result.artifact.parent_model_calls == 1
    assert result.artifact.trusted_tool_calls == 0
    assert "### Analyse" in result.artifact.markdown
    assert "### Faits étayés" not in result.artifact.markdown
    assert _dossier_evidence_id() not in result.artifact.markdown


@pytest.mark.asyncio
async def test_plan_executes_trusted_batch_then_finalizes_on_second_parent_call():
    calls = 0
    adapter_calls = 0

    async def adapter(args):
        nonlocal adapter_calls
        adapter_calls += 1
        return NormalizedToolResult(
            status="resolved",
            summary="Une association trouvée.",
            payload=[{"name": "A"}],
        )

    registry = {
        "search_rna_rag_batch_tool": replace(
            SOCIAL_TOOL_REGISTRY["search_rna_rag_batch_tool"], adapter=adapter
        )
    }

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {
                "kind": "evidence_plan",
                "working_state": {
                    "findings": [],
                    "gaps": [
                        {
                            "gap_id": "g-fle",
                            "question": "Existe-t-il des cours FLE ?",
                            "materiality": "high",
                            "impact_if_unresolved": "L'offre linguistique reste incertaine.",
                        }
                    ],
                },
                "trusted_requests": [
                    {
                        "request_id": "req-fle",
                        "gap_ids": ["g-fle"],
                        "tool_id": "search_rna_rag_batch_tool",
                        "arguments": {
                            "queries": ["cours FLE"],
                            "codgeo": "33063",
                        },
                    }
                ],
                "web_fallbacks": [],
            }
            tool_name = _output_tool(info, "evidenceplan")
        else:
            payload = {
                "kind": "final_report",
                "analysis": [
                    {
                        "text": "Une piste associative crédible permet d'engager un accompagnement FLE.",
                        "evidence_ids": [
                            "pilot-run:social_integration_expert:tool:req-fle"
                        ],
                        "status": "supported",
                    }
                ],
                "recommended_actions": [
                    {
                        "action": "Prendre contact avec la structure repérée.",
                        "rationale": "Vérifier l'éligibilité et les horaires avant orientation.",
                        "evidence_ids": [
                            "pilot-run:social_integration_expert:tool:req-fle"
                        ],
                    }
                ],
            }
            tool_name = _output_tool(info, "finalexpertreport")
        return ModelResponse(parts=[ToolCallPart(tool_name, payload)])

    result = await run_adaptive_social_expert(
        _state(), model=FunctionModel(model), model_id="offline", registry=registry
    )
    assert calls == 2
    assert adapter_calls == 1
    assert result.artifact.parent_model_calls == 2
    assert result.artifact.trusted_tool_calls == 1
    assert result.artifact.evidence[0].payload is None
    assert result.artifact.gaps[0].status == "resolved"
    assert result.artifact.analysis[0].status == "supported"


@pytest.mark.asyncio
async def test_empty_research_plan_is_repaired_before_tool_execution():
    """A researchable gap cannot silently pass with an empty trusted batch."""
    calls = 0
    adapter_calls = 0

    async def adapter(args):
        nonlocal adapter_calls
        adapter_calls += 1
        return NormalizedToolResult(
            status="resolved", summary="Une structure trouvée.", payload={}
        )

    registry = {
        "search_rna_rag_batch_tool": replace(
            SOCIAL_TOOL_REGISTRY["search_rna_rag_batch_tool"], adapter=adapter
        )
    }

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls <= 2:
            payload = {
                "kind": "evidence_plan",
                "working_state": {
                    "findings": [],
                    "gaps": [
                        {
                            "gap_id": "g-open",
                            "question": "Une structure locale est-elle disponible ?",
                            "impact_if_unresolved": "Le TS doit confirmer manuellement.",
                        }
                    ],
                },
                "trusted_requests": (
                    []
                    if calls == 1
                    else [
                        {
                            "request_id": "r-open",
                            "gap_ids": ["g-open"],
                            "tool_id": "search_rna_rag_batch_tool",
                            "arguments": {
                                "queries": ["accompagnement social"],
                                "codgeo": "33063",
                            },
                        }
                    ]
                ),
                "web_fallbacks": [],
            }
            name = _output_tool(info, "evidenceplan")
        else:
            payload = {
                "kind": "final_report",
                "analysis": [
                    {
                        "text": "Une structure locale offre une piste d'accompagnement exploitable.",
                        "evidence_ids": [
                            "pilot-run:social_integration_expert:tool:r-open"
                        ],
                        "status": "supported",
                    }
                ],
                "recommended_actions": [],
            }
            name = _output_tool(info, "finalexpertreport")
        return ModelResponse(parts=[ToolCallPart(name, payload)])

    result = await run_adaptive_social_expert(
        _state(),
        model=FunctionModel(model),
        model_id="offline",
        registry=registry,
    )
    assert calls == 3
    assert adapter_calls == 1
    assert result.artifact.trusted_tool_calls == 1
    assert result.artifact.web_model_calls == 0
    assert result.artifact.gaps[0].status == "resolved"


@pytest.mark.asyncio
async def test_manual_gap_can_finalize_but_cannot_be_self_closed():
    calls = 0

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {
                "kind": "evidence_plan",
                "working_state": {
                    "findings": [],
                    "gaps": [
                        {
                            "gap_id": "g-manual",
                            "question": "Une initiative informelle existe-t-elle ?",
                            "impact_if_unresolved": "La piste locale reste incertaine.",
                            "resolution_strategy": "manual",
                            "manual_resolution_step": "Contacter la maison des associations.",
                        }
                    ],
                },
                "trusted_requests": [],
                "web_fallbacks": [],
            }
            name = _output_tool(info, "evidenceplan")
        else:
            payload = {
                "kind": "final_report",
                "analysis": [
                    {
                        "text": "L'existence d'une initiative informelle reste à confirmer.",
                        "gap_ids": ["g-manual"],
                        "status": "uncertain",
                    }
                ],
                "recommended_actions": [
                    {
                        "action": "Contacter la maison des associations.",
                        "rationale": "Aucune source de confiance enregistrée ne couvre cette question.",
                        "gap_ids": ["g-manual"],
                    }
                ],
            }
            name = _output_tool(info, "finalexpertreport")
        return ModelResponse(parts=[ToolCallPart(name, payload)])

    result = await run_adaptive_social_expert(
        _state(), model=FunctionModel(model), model_id="offline"
    )
    assert calls == 2
    assert "resolved_gaps" not in FinalExpertReport.model_fields
    assert result.artifact.trusted_tool_calls == 0
    assert result.artifact.gaps[0].status == "open"
    assert "Contacter la maison des associations" in result.artifact.markdown


@pytest.mark.asyncio
async def test_trusted_requests_start_concurrently():
    starts: list[float] = []

    async def adapter(args):
        starts.append(time.monotonic())
        await asyncio.sleep(0.08)
        return NormalizedToolResult(status="resolved", summary="ok", payload={})

    registry = {
        "search_rna_rag_batch_tool": replace(
            SOCIAL_TOOL_REGISTRY["search_rna_rag_batch_tool"], adapter=adapter
        )
    }
    plan = EvidencePlan(
        working_state=ExpertWorkingState(
            gaps=[
                EvidenceGap(
                    gap_id="g1",
                    question="q1",
                    impact_if_unresolved="i1",
                ),
                EvidenceGap(
                    gap_id="g2",
                    question="q2",
                    impact_if_unresolved="i2",
                ),
            ]
        ),
        trusted_requests=[
            ToolRequest(
                request_id="r1",
                gap_ids=["g1"],
                tool_id="search_rna_rag_batch_tool",
                arguments={"queries": ["a"], "codgeo": "33063"},
            ),
            ToolRequest(
                request_id="r2",
                gap_ids=["g2"],
                tool_id="search_rna_rag_batch_tool",
                arguments={"queries": ["b"], "codgeo": "33063"},
            ),
        ],
    )
    started = time.monotonic()
    records = await execute_trusted_batch(
        plan, run_id="r", deadline_at=None, registry=registry
    )
    elapsed = time.monotonic() - started
    assert len(records) == 2
    assert len(starts) == 2
    assert abs(starts[0] - starts[1]) < 0.04
    assert elapsed < 0.14


@pytest.mark.asyncio
async def test_predeclared_web_runs_only_after_not_found():
    calls = 0

    async def adapter(args):
        return NormalizedToolResult(
            status="not_found", summary="Aucun résultat RNA.", payload=[]
        )

    async def web_runner(query, reason, model, deadline_at):
        return (
            WebEvidenceBundle(
                status="resolved",
                summary="Une source locale trouvée.",
                sources=[WebSource(url="https://example.org/fle")],
            ),
            UsageStats(requests=1),
        )

    registry = {
        "search_rna_rag_batch_tool": replace(
            SOCIAL_TOOL_REGISTRY["search_rna_rag_batch_tool"], adapter=adapter
        )
    }

    def model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            payload = {
                "kind": "evidence_plan",
                "working_state": {
                    "findings": [],
                    "gaps": [
                        {
                            "gap_id": "g1",
                            "question": "Cours FLE local ?",
                            "impact_if_unresolved": "Incertitude.",
                        }
                    ],
                },
                "trusted_requests": [
                    {
                        "request_id": "r1",
                        "gap_ids": ["g1"],
                        "tool_id": "search_rna_rag_batch_tool",
                        "arguments": {"queries": ["FLE"], "codgeo": "33063"},
                    }
                ],
                "web_fallbacks": [
                    {
                        "gap_ids": ["g1"],
                        "query": "cours FLE Bordeaux",
                        "reason": "fallback local",
                        "run_when": ["not_found"],
                    }
                ],
            }
            name = _output_tool(info, "evidenceplan")
        else:
            payload = {
                "kind": "final_report",
                "analysis": [
                    {
                        "text": "Une piste web vérifiable réduit l'incertitude sur l'offre FLE.",
                        "evidence_ids": ["pilot-run:social_integration_expert:web:1"],
                        "status": "supported",
                    }
                ],
                "recommended_actions": [],
            }
            name = _output_tool(info, "finalexpertreport")
        return ModelResponse(parts=[ToolCallPart(name, payload)])

    result = await run_adaptive_social_expert(
        _state(),
        model=FunctionModel(model),
        model_id="offline",
        registry=registry,
        web_runner=web_runner,
    )
    assert result.artifact.web_model_calls == 1
    assert result.artifact.evidence[-1].source_url == "https://example.org/fle"
