"""Typed evidence contracts shared by the adaptive experts, graph, and UI."""

from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, JsonValue


EvidenceStatus = Literal[
    "resolved",
    "empty",
    "not_found",
    "partial",
    "unavailable",
    "timeout",
    "conflicting",
    "out_of_scope",
]
GapStatus = Union[Literal["open"], EvidenceStatus]
TrustTier = Literal["authoritative", "corroborating", "discovery"]
GapResolutionStrategy = Literal["trusted_tool", "manual", "out_of_scope"]


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Finding(FrozenModel):
    statement: str
    evidence_ids: list[str] = Field(default_factory=list)
    status: Literal["observed", "inferred", "uncertain"] = "observed"
    limitation: str | None = None


class EvidenceGap(FrozenModel):
    gap_id: str
    question: str
    materiality: Literal["low", "medium", "high"] = "medium"
    impact_if_unresolved: str
    manual_resolution_step: str | None = None
    resolution_strategy: GapResolutionStrategy = "trusted_tool"


class ToolRequest(FrozenModel):
    request_id: str
    gap_ids: list[str] = Field(min_length=1)
    tool_id: str
    arguments: dict[str, JsonValue]


class ConditionalWebFallback(FrozenModel):
    gap_ids: list[str]
    query: str
    reason: str
    run_when: set[Literal["not_found", "unavailable"]] = Field(
        default_factory=lambda: {"not_found", "unavailable"}
    )


class ExpertWorkingState(FrozenModel):
    findings: list[Finding] = Field(default_factory=list)
    gaps: list[EvidenceGap] = Field(default_factory=list)


class EvidencePlan(FrozenModel):
    kind: Literal["evidence_plan"] = "evidence_plan"
    working_state: ExpertWorkingState
    trusted_requests: list[ToolRequest] = Field(max_length=4)
    web_fallbacks: list[ConditionalWebFallback] = Field(
        default_factory=list, max_length=1
    )


class AnalysisInsight(FrozenModel):
    """A selective interpretation shown to the social worker."""

    text: str = Field(min_length=1, max_length=1200)
    evidence_ids: list[str] = Field(default_factory=list)
    gap_ids: list[str] = Field(default_factory=list)
    status: Literal["supported", "inferred", "uncertain"] = "supported"
    limitation: str | None = Field(default=None, max_length=500)


class RecommendedAction(FrozenModel):
    """A practical next step tied to evidence or an application-owned gap."""

    action: str = Field(min_length=1, max_length=500)
    rationale: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)
    gap_ids: list[str] = Field(default_factory=list)


class FinalExpertReport(FrozenModel):
    kind: Literal["final_report"] = "final_report"
    analysis: list[AnalysisInsight] = Field(min_length=1, max_length=4)
    recommended_actions: list[RecommendedAction] = Field(
        default_factory=list, max_length=4
    )


ExpertStep = Annotated[
    Union[FinalExpertReport, EvidencePlan], Field(discriminator="kind")
]


class ToolSpecView(FrozenModel):
    tool_id: str
    description: str
    input_schema: dict[str, JsonValue]
    source_tag: str
    trust_tier: TrustTier


class DossierEvidence(FrozenModel):
    evidence_id: str
    label: str
    value: JsonValue
    source_tag: str = "Dossier OD&IS"
    trust_tier: TrustTier = "authoritative"


class EvidenceRecord(FrozenModel):
    evidence_id: str
    gap_ids: list[str] = Field(default_factory=list)
    request_id: str | None = None
    source_tag: str
    trust_tier: TrustTier
    status: EvidenceStatus
    summary: str
    payload: JsonValue | None = None
    source_url: str | None = None


class GapRecord(FrozenModel):
    gap_id: str
    question: str
    materiality: Literal["low", "medium", "high"]
    impact_if_unresolved: str
    status: GapStatus
    attempted_tool_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    web_attempted: bool = False
    manual_resolution_step: str | None = None


class WebSource(FrozenModel):
    title: str | None = None
    url: str


class WebEvidenceBundle(FrozenModel):
    status: EvidenceStatus
    summary: str
    sources: list[WebSource] = Field(default_factory=list)


class DomainArtifact(FrozenModel):
    domain: str
    markdown: str
    analysis: list[AnalysisInsight] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    gaps: list[GapRecord] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    parent_model_calls: int = 1
    trusted_tool_calls: int = 0
    web_model_calls: int = 0
