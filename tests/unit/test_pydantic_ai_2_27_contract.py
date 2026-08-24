from typing import Annotated, Literal, Union
import asyncio
import pytest
from pydantic import BaseModel, Field, JsonValue
from pydantic_ai import Agent, ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from agents.agent_config import create_agent
from agents.graph import capture_usage


# --- Representative Domain Contracts from EXPERT_AGENTS_REARCHITECTURE.md ---


class Finding(BaseModel):
    statement: str
    evidence_ids: list[str] = []
    status: Literal["observed", "inferred", "uncertain"] = "observed"
    limitation: str | None = None


class EvidenceGap(BaseModel):
    gap_id: str
    question: str
    materiality: Literal["low", "medium", "high"] = "medium"
    impact_if_unresolved: str
    status: Literal[
        "open",
        "resolved",
        "empty",
        "not_found",
        "partial",
        "unavailable",
        "timeout",
        "conflicting",
        "out_of_scope",
    ] = "open"


class ToolRequest(BaseModel):
    request_id: str
    gap_ids: list[str]
    tool_id: str
    arguments: dict[str, JsonValue] = {}


class ConditionalWebFallback(BaseModel):
    gap_ids: list[str]
    query: str
    reason: str
    run_when: set[Literal["not_found", "unavailable"]] = {"not_found", "unavailable"}


class ExpertWorkingState(BaseModel):
    findings: list[Finding] = []
    gaps: list[EvidenceGap] = []


class EvidencePlan(BaseModel):
    kind: Literal["evidence_plan"] = "evidence_plan"
    working_state: ExpertWorkingState
    trusted_requests: list[ToolRequest] = []
    web_fallbacks: list[ConditionalWebFallback] = []


class FinalExpertReport(BaseModel):
    kind: Literal["final_report"] = "final_report"
    findings: list[Finding]
    resolved_gaps: list[EvidenceGap] = []
    unresolved_gaps: list[EvidenceGap] = []
    concise_narrative: str


ExpertStep = Annotated[
    Union[FinalExpertReport, EvidencePlan],
    Field(discriminator="kind"),
]


# --- Offline Tests ---


@pytest.mark.asyncio
async def test_discriminated_union_final_report_fast_path():
    """Verify that Pydantic AI 2.27 resolves FinalExpertReport cleanly on turn 1."""

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result_tool_name = (
            info.output_tools[0].name if info.output_tools else "final_result"
        )
        args = {
            "kind": "final_report",
            "findings": [
                {
                    "statement": "Le bassin d'emploi est favorable.",
                    "evidence_ids": ["E-criteria-01"],
                    "status": "observed",
                }
            ],
            "resolved_gaps": [],
            "unresolved_gaps": [],
            "concise_narrative": "Évaluation positive immédiate sans besoin d'outil.",
        }
        return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    agent = Agent(
        model=FunctionModel(call_model),
        output_type=ExpertStep,
    )

    result = await agent.run("Analyse le bassin d'emploi.")
    assert isinstance(result.output, FinalExpertReport)
    assert result.output.kind == "final_report"
    assert len(result.output.findings) == 1
    assert result.output.findings[0].statement == "Le bassin d'emploi est favorable."


@pytest.mark.asyncio
async def test_discriminated_union_evidence_plan_branch():
    """Verify that Pydantic AI 2.27 resolves EvidencePlan when tools are requested."""

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # Find the tool corresponding to evidence_plan (case-insensitive)
        plan_tool = next(
            (
                t.name
                for t in info.output_tools
                if "evidenceplan" in t.name.lower()
                or "evidence_plan" in t.name.lower()
            ),
            info.output_tools[0].name if info.output_tools else "final_result",
        )
        args = {
            "kind": "evidence_plan",
            "working_state": {
                "findings": [],
                "gaps": [
                    {
                        "gap_id": "G-01",
                        "question": "Y a-t-il des cours de FLE disponibles ?",
                        "materiality": "high",
                        "impact_if_unresolved": "Risque d'intégration linguistique ralentie.",
                    }
                ],
            },
            "trusted_requests": [
                {
                    "request_id": "REQ-01",
                    "gap_ids": ["G-01"],
                    "tool_id": "search_rna_rag_batch_tool",
                    "arguments": {"queries": ["cours FLE", "alphabétisation"]},
                }
            ],
            "web_fallbacks": [
                {
                    "gap_ids": ["G-01"],
                    "query": "cours FLE association 33138",
                    "reason": "Vérifier associations locales si RAG vide",
                    "run_when": ["not_found", "unavailable"],
                }
            ],
        }
        return ModelResponse(parts=[ToolCallPart(plan_tool, args)])

    agent = Agent(
        model=FunctionModel(call_model),
        output_type=ExpertStep,
    )

    result = await agent.run("Planifie les recherches pour le FLE.")
    assert isinstance(result.output, EvidencePlan)
    assert result.output.kind == "evidence_plan"
    assert len(result.output.working_state.gaps) == 1
    assert len(result.output.trusted_requests) == 1
    assert (
        result.output.trusted_requests[0].tool_id == "search_rna_rag_batch_tool"
    )


@pytest.mark.asyncio
async def test_toolless_planner_has_no_executable_tools():
    """
    Verify that an expert constructed without executable tools passes zero function tools
    to the model request parameters, preventing internal tool execution loops.
    """
    observed_tools: list[str] = []

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        observed_tools.extend([t.name for t in info.function_tools])
        result_tool = next(
            (
                t.name
                for t in info.output_tools
                if "finalexpertreport" in t.name.lower()
                or "final_expert_report" in t.name.lower()
            ),
            info.output_tools[0].name if info.output_tools else "final_result",
        )
        args = {
            "kind": "final_report",
            "findings": [],
            "resolved_gaps": [],
            "unresolved_gaps": [],
            "concise_narrative": "No tools needed.",
        }
        return ModelResponse(parts=[ToolCallPart(result_tool, args)])

    agent = Agent(
        model=FunctionModel(call_model),
        output_type=ExpertStep,
        # Intentionally no executable tools
    )

    result = await agent.run("Évalue la situation.")
    assert isinstance(result.output, FinalExpertReport)
    # The agent should expose NO executable function tools to the model
    assert observed_tools == []


@pytest.mark.asyncio
async def test_agent_run_cancellation_and_timeout():
    """Verify that agent calls can be cancelled cleanly within deadline budgets."""

    async def slow_model_call(
        messages: list[ModelMessage], info: AgentInfo
    ) -> ModelResponse:
        await asyncio.sleep(2.0)
        return ModelResponse(parts=[])

    agent = Agent(
        model=FunctionModel(slow_model_call),
        output_type=str,
    )

    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(agent.run("Requête lente"), timeout=0.05)


def test_deferred_model_check_factory():
    """Verify create_agent creates agents with defer_model_check=True without API calls."""
    agent = create_agent("social_integration_expert")
    assert agent.name == "social_integration_expert"


@pytest.mark.asyncio
async def test_usage_capture_compatibility():
    """Verify capture_usage processes Pydantic AI 2.27 run results without errors."""
    from pydantic_ai.messages import TextPart

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="Test response")])

    agent = Agent(model=FunctionModel(call_model), output_type=str)
    result = await agent.run("Test message")

    usage = capture_usage(result, "test_node", "google:gemini-3.1-flash-lite")
    assert usage.input_tokens >= 0
    assert usage.output_tokens >= 0
    assert "test_node" in usage.breakdown
