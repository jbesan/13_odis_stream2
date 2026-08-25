"""Validated, graph-owned parallel dispatch of trusted evidence tools."""

import asyncio
import time
from typing import Mapping

import logfire
from pydantic import ValidationError

from agents.evidence.registry import RegisteredTool, SOCIAL_TOOL_REGISTRY
from core.evidence import EvidencePlan, EvidenceRecord


def validate_plan_arguments(
    plan: EvidencePlan,
    *,
    target_codgeo: str,
    target_city_name: str,
    registry: Mapping[str, RegisteredTool] = SOCIAL_TOOL_REGISTRY,
) -> None:
    gaps = plan.working_state.gaps
    if not gaps:
        raise ValueError(
            "EvidencePlan doit déclarer au moins une lacune; sinon retourne "
            "FinalExpertReport."
        )

    gap_ids = [gap.gap_id for gap in gaps]
    if len(gap_ids) != len(set(gap_ids)):
        raise ValueError("Les gap_id doivent être uniques.")

    request_ids = [request.request_id for request in plan.trusted_requests]
    if len(request_ids) != len(set(request_ids)):
        raise ValueError("Les request_id doivent être uniques.")

    known_gaps = set(gap_ids)
    requested_gap_ids: set[str] = set()
    for request in plan.trusted_requests:
        if len(request.gap_ids) != 1:
            raise ValueError(
                "Chaque requête de confiance du pilote social doit viser exactement "
                "une lacune."
            )
        if not set(request.gap_ids) <= known_gaps:
            raise ValueError("Chaque requête doit référencer des gaps déclarés.")
        requested_gap_ids.update(request.gap_ids)
        registered = registry.get(request.tool_id)
        if registered is None:
            raise ValueError(f"Tool non autorisé: {request.tool_id}")
        try:
            args = registered.args_model.model_validate(request.arguments)
        except ValidationError as exc:
            raise ValueError(
                f"Arguments invalides pour {request.tool_id}: {exc}"
            ) from exc
        if hasattr(args, "codgeo") and args.codgeo != target_codgeo:
            raise ValueError("Le code INSEE d'une requête doit être celui de la cible.")
        if (
            hasattr(args, "location")
            and target_city_name.lower() not in args.location.lower()
        ):
            raise ValueError(
                "La localisation d'une requête doit viser la commune cible."
            )

    for gap in gaps:
        if (
            gap.resolution_strategy == "trusted_tool"
            and gap.gap_id not in requested_gap_ids
        ):
            raise ValueError(
                f"La lacune {gap.gap_id} exige une requête de confiance liée."
            )
        if gap.resolution_strategy == "manual" and not gap.manual_resolution_step:
            raise ValueError(
                f"La lacune manuelle {gap.gap_id} exige manual_resolution_step."
            )
        if (
            gap.resolution_strategy != "trusted_tool"
            and gap.gap_id in requested_gap_ids
        ):
            raise ValueError(
                f"La lacune {gap.gap_id} ne doit pas être liée à un outil avec "
                f"resolution_strategy={gap.resolution_strategy}."
            )

    for fallback in plan.web_fallbacks:
        fallback_gaps = set(fallback.gap_ids)
        if not fallback_gaps or not fallback_gaps <= known_gaps:
            raise ValueError("Le fallback web doit référencer des gaps déclarés.")
        if not fallback_gaps <= requested_gap_ids:
            raise ValueError(
                "Le fallback web doit suivre une requête de confiance liée."
            )


async def execute_trusted_batch(
    plan: EvidencePlan,
    *,
    run_id: str,
    deadline_at: float | None,
    registry: Mapping[str, RegisteredTool] = SOCIAL_TOOL_REGISTRY,
) -> list[EvidenceRecord]:
    """Run every independent request concurrently; failures remain local to a child."""
    results: list[EvidenceRecord | None] = [None] * len(plan.trusted_requests)

    async def execute_one(index: int) -> None:
        request = plan.trusted_requests[index]
        registered = registry[request.tool_id]
        evidence_id = f"{run_id}:social_integration_expert:tool:{request.request_id}"
        with logfire.span(
            "execute_adaptive_tool {tool_id}",
            tool_id=request.tool_id,
            request_id=request.request_id,
            gap_ids=request.gap_ids,
        ):
            try:
                args = registered.args_model.model_validate(request.arguments)
                remaining = (
                    max(0.05, deadline_at - time.time())
                    if deadline_at is not None
                    else registered.timeout_seconds
                )
                timeout = min(registered.timeout_seconds, remaining)
                normalized = await asyncio.wait_for(
                    registered.adapter(args), timeout=timeout
                )
                results[index] = EvidenceRecord(
                    evidence_id=evidence_id,
                    gap_ids=request.gap_ids,
                    request_id=request.request_id,
                    source_tag=registered.spec.source_tag,
                    trust_tier=registered.spec.trust_tier,
                    status=normalized.status,
                    summary=normalized.summary,
                    payload=normalized.payload,
                )
            except asyncio.TimeoutError:
                results[index] = EvidenceRecord(
                    evidence_id=evidence_id,
                    gap_ids=request.gap_ids,
                    request_id=request.request_id,
                    source_tag=registered.spec.source_tag,
                    trust_tier=registered.spec.trust_tier,
                    status="timeout",
                    summary="La source n'a pas répondu avant la limite de temps.",
                )
            except Exception as exc:
                results[index] = EvidenceRecord(
                    evidence_id=evidence_id,
                    gap_ids=request.gap_ids,
                    request_id=request.request_id,
                    source_tag=registered.spec.source_tag,
                    trust_tier=registered.spec.trust_tier,
                    status="unavailable",
                    summary=f"Source indisponible ({type(exc).__name__}).",
                )

    async with asyncio.TaskGroup() as group:
        for index in range(len(plan.trusted_requests)):
            group.create_task(execute_one(index))
    return [result for result in results if result is not None]
