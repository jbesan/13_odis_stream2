"""Two-call adaptive orchestration for the social-integration pilot."""

import json
import time
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping

from pydantic_core import to_jsonable_python

from agents.agent_config import get_model_settings
from agents.evidence.adaptive_agent import (
    ExpertRunDeps,
    adaptive_social_integration_agent,
)
from agents.evidence.executor import execute_trusted_batch, validate_plan_arguments
from agents.evidence.projector import project_social_context
from agents.evidence.projector import ExpertEvidencePacket
from agents.evidence.registry import RegisteredTool, SOCIAL_TOOL_REGISTRY
from agents.evidence.rendering import render_final_report
from agents.evidence.web_child import WebChildDeps, provider_sources, web_child_agent
from agents.state import GraphState, UsageStats
from core.evidence import (
    DomainArtifact,
    EvidencePlan,
    EvidenceRecord,
    EvidenceStatus,
    FinalExpertReport,
    GapRecord,
    GapStatus,
    WebEvidenceBundle,
)

logger = logging.getLogger("adaptive_social_expert")


WebRunner = Callable[
    [str, str, Any, float | None],
    Awaitable[tuple[WebEvidenceBundle, UsageStats]],
]


@dataclass(frozen=True)
class AdaptiveRunResult:
    artifact: DomainArtifact
    usage: UsageStats


def _cited_dossier_records(
    packet: ExpertEvidencePacket, report: FinalExpertReport
) -> list[EvidenceRecord]:
    cited = {
        evidence_id
        for item in [*report.analysis, *report.recommended_actions]
        for evidence_id in item.evidence_ids
    }
    return [
        EvidenceRecord(
            evidence_id=item.evidence_id,
            source_tag=item.source_tag,
            trust_tier=item.trust_tier,
            status="resolved",
            summary=f"Donnée du dossier: {item.label}.",
        )
        for item in packet.dossier_evidence
        if item.evidence_id in cited
    ]


def _capture_usage(result: Any, node: str, model_id: str) -> UsageStats:
    usage = result.usage
    model_name = model_id.lower()
    if "3.5-flash-lite" in model_name:
        rate_in, rate_out = (0.30, 2.50)
    elif "3.1-flash-lite" in model_name:
        rate_in, rate_out = (0.25, 1.50)
    else:
        rate_in, rate_out = (0.10, 0.40)
    cost = (usage.input_tokens * rate_in + usage.output_tokens * rate_out) / 1_000_000
    return UsageStats(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
        cost_usd=cost,
        cache_read_tokens=getattr(usage, "cache_read_tokens", 0),
        cache_write_tokens=getattr(usage, "cache_write_tokens", 0),
        cache_hit_ratio=getattr(usage, "cache_hit_ratio", 0.0),
        requests=getattr(usage, "requests", 0),
        tool_calls=getattr(usage, "tool_calls", 0),
        breakdown={
            node: {
                "model": model_id,
                "input": usage.input_tokens,
                "output": usage.output_tokens,
                "total": usage.total_tokens,
                "cost": cost,
                "requests": getattr(usage, "requests", 0),
                "tool_calls": getattr(usage, "tool_calls", 0),
                "cache_read_tokens": getattr(usage, "cache_read_tokens", 0),
                "cache_write_tokens": getattr(usage, "cache_write_tokens", 0),
                "cache_hit_ratio": getattr(usage, "cache_hit_ratio", 0.0),
            }
        },
    )


async def _default_web_runner(
    query: str, reason: str, model: Any, deadline_at: float | None
) -> tuple[WebEvidenceBundle, UsageStats]:
    result = await web_child_agent.run(
        f"Recherche une seule fois: {query}\nObjectif: {reason}",
        deps=WebChildDeps(query=query, reason=reason),
        model=model,
        model_settings=_model_settings_for_deadline(deadline_at),
    )
    provider_backed_sources = provider_sources(result.all_messages())
    output = result.output
    if provider_backed_sources:
        output = output.model_copy(update={"sources": provider_backed_sources})
    else:
        output = output.model_copy(update={"status": "partial", "sources": []})
    return output, _capture_usage(result, "social_web_child", str(model))


def _model_settings_for_deadline(deadline_at: float | None) -> dict[str, Any]:
    settings = dict(get_model_settings("social_integration_expert"))
    if deadline_at is not None:
        remaining = max(0.05, deadline_at - time.time())
        configured = settings.get("timeout")
        settings["timeout"] = (
            min(float(configured), remaining) if configured is not None else remaining
        )
    return settings


def _gap_records(
    plan: EvidencePlan, records: list[EvidenceRecord], *, web_attempted: set[str]
) -> list[GapRecord]:
    priority: list[EvidenceStatus] = [
        "resolved",
        "conflicting",
        "partial",
        "timeout",
        "unavailable",
        "not_found",
        "empty",
        "out_of_scope",
    ]
    built: list[GapRecord] = []
    for gap in plan.working_state.gaps:
        related = [record for record in records if gap.gap_id in record.gap_ids]
        statuses = {record.status for record in related}
        status: GapStatus
        if gap.resolution_strategy == "out_of_scope":
            status = "out_of_scope"
        else:
            status = next(
                (candidate for candidate in priority if candidate in statuses), "open"
            )
        requests = [
            request.tool_id
            for request in plan.trusted_requests
            if gap.gap_id in request.gap_ids
        ]
        built.append(
            GapRecord(
                gap_id=gap.gap_id,
                question=gap.question,
                materiality=gap.materiality,
                impact_if_unresolved=gap.impact_if_unresolved,
                status=status,
                attempted_tool_ids=requests,
                evidence_ids=[record.evidence_id for record in related],
                web_attempted=gap.gap_id in web_attempted,
                manual_resolution_step=gap.manual_resolution_step,
            )
        )
    return built


async def run_adaptive_social_expert(
    state: GraphState,
    *,
    model: Any,
    model_id: str,
    registry: Mapping[str, RegisteredTool] = SOCIAL_TOOL_REGISTRY,
    web_runner: WebRunner | None = None,
) -> AdaptiveRunResult:
    """Run the fast path or exactly one assess/batch/finalize cycle."""
    packet = project_social_context(state)
    specs = tuple(registered.spec for registered in registry.values())
    first_deps = ExpertRunDeps(
        packet=packet,
        phase="assess",
        tool_specs=specs,
        allowed_evidence_ids=frozenset(packet.evidence_ids),
    )
    first = await adaptive_social_integration_agent.run(
        packet.mission,
        deps=first_deps,
        model=model,
        model_settings=_model_settings_for_deadline(state.run_deadline_at),
    )
    usage = _capture_usage(first, "social_parent_assess", model_id)
    if isinstance(first.output, FinalExpertReport):
        artifact = DomainArtifact(
            domain=packet.domain,
            markdown=render_final_report(first.output, []),
            analysis=first.output.analysis,
            recommended_actions=first.output.recommended_actions,
            evidence=_cited_dossier_records(packet, first.output),
        )
        return AdaptiveRunResult(artifact=artifact, usage=usage)

    plan = first.output
    validate_plan_arguments(
        plan,
        target_codgeo=packet.target_codgeo,
        target_city_name=packet.target_city_name,
        registry=registry,
    )
    records = await execute_trusted_batch(
        plan,
        run_id=state.run_id,
        deadline_at=state.run_deadline_at,
        registry=registry,
    )
    if not plan.trusted_requests:
        # This branch is valid only for explicitly manual or out-of-scope gaps;
        # plan validation rejects uncovered researchable gaps.
        logger.warning(
            "Adaptive social expert returned an explicit manual/out-of-scope "
            "EvidencePlan; finalizing without trusted or web calls."
        )

    attempted_web: set[str] = set()
    run_web = web_runner or _default_web_runner
    if plan.web_fallbacks:
        fallback = plan.web_fallbacks[0]
        statuses = {
            record.status
            for record in records
            if set(record.gap_ids) & set(fallback.gap_ids)
        }
        if statuses & set(fallback.run_when):
            bundle, web_usage = await run_web(
                fallback.query, fallback.reason, model, state.run_deadline_at
            )
            usage.merge(web_usage)
            attempted_web.update(fallback.gap_ids)
            if bundle.sources:
                for index, source in enumerate(bundle.sources, start=1):
                    records.append(
                        EvidenceRecord(
                            evidence_id=f"{state.run_id}:social_integration_expert:web:{index}",
                            gap_ids=fallback.gap_ids,
                            source_tag="Source Web Vérifiée (URL)",
                            trust_tier="discovery",
                            status=bundle.status,
                            summary=bundle.summary,
                            source_url=source.url,
                        )
                    )
            else:
                records.append(
                    EvidenceRecord(
                        evidence_id=f"{state.run_id}:social_integration_expert:web:0",
                        gap_ids=fallback.gap_ids,
                        source_tag="Gemini Native Search",
                        trust_tier="discovery",
                        status=bundle.status,
                        summary=bundle.summary,
                    )
                )

    gaps = _gap_records(plan, records, web_attempted=attempted_web)
    delta = json.dumps(
        {
            "evidence": [to_jsonable_python(record) for record in records],
            "gaps": [to_jsonable_python(gap) for gap in gaps],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    allowed_ids = packet.evidence_ids | {record.evidence_id for record in records}
    final_deps = ExpertRunDeps(
        packet=packet,
        phase="finalize",
        tool_specs=specs,
        allowed_evidence_ids=frozenset(allowed_ids),
        allowed_gap_ids=frozenset(gap.gap_id for gap in plan.working_state.gaps),
        evidence_delta=delta,
    )
    final = await adaptive_social_integration_agent.run(
        "Produis l'analyse sélective destinée au Travailleur Social à partir "
        "des preuves normalisées et des statuts de lacunes calculés par "
        "l'application.",
        deps=final_deps,
        model=model,
        model_settings=_model_settings_for_deadline(state.run_deadline_at),
        message_history=first.all_messages(),
    )
    usage.merge(_capture_usage(final, "social_parent_finalize", model_id))
    assert isinstance(final.output, FinalExpertReport)
    persisted_records = _cited_dossier_records(packet, final.output) + [
        record.model_copy(update={"payload": None}) for record in records
    ]
    artifact = DomainArtifact(
        domain=packet.domain,
        markdown=render_final_report(final.output, gaps),
        analysis=final.output.analysis,
        recommended_actions=final.output.recommended_actions,
        gaps=gaps,
        evidence=persisted_records,
        parent_model_calls=2,
        trusted_tool_calls=len(plan.trusted_requests),
        web_model_calls=1 if attempted_web else 0,
    )
    return AdaptiveRunResult(artifact=artifact, usage=usage)
