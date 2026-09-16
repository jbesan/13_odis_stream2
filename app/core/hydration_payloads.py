"""Typed provider payloads and reducers for the built-in hydrations."""

from pydantic import BaseModel, Field

from core.hydration import HydrationTaskResult
from core.models import (
    AssociationDetail,
    CommuneResult,
    InclusionServiceDetail,
    JobOfferDetail,
)


class AssociationsPayload(BaseModel):
    """Association batch slice belonging to one commune."""

    refugee: list[AssociationDetail] = Field(default_factory=list)
    inclusion: dict[str, list[AssociationDetail]] = Field(default_factory=dict)


class ServicesPayload(BaseModel):
    """Validated inclusion services grouped by thematic label."""

    services: dict[str, list[InclusionServiceDetail]] = Field(default_factory=dict)


class JobsPayload(BaseModel):
    """Offers per adult and provider's matching count."""

    jobs: list[list[JobOfferDetail]] = Field(default_factory=list)
    total: int = Field(default=0, ge=0)


def apply_associations(commune: CommuneResult, payload: BaseModel) -> None:
    """Apply validated association data to a private candidate.

    Args:
        commune: Reducer-owned candidate.
        payload: Association result to validate and apply.
    """
    data = AssociationsPayload.model_validate(payload.model_dump())
    commune.inclusion.asso_refugee_list = data.refugee
    commune.inclusion.asso_refugee_count = len(data.refugee)
    commune.inclusion.asso_inclusion_list_by_cat = data.inclusion
    commune.inclusion.asso_inclusion_count = sum(map(len, data.inclusion.values()))


def apply_services(commune: CommuneResult, payload: BaseModel) -> None:
    """Apply validated service data to a private candidate.

    Args:
        commune: Reducer-owned candidate.
        payload: Service result to validate and apply.
    """
    data = ServicesPayload.model_validate(payload.model_dump())
    commune.inclusion.services_detailed = data.services


def apply_jobs(commune: CommuneResult, payload: BaseModel) -> None:
    """Apply validated job data to a private candidate.

    Args:
        commune: Reducer-owned candidate.
        payload: Job result to validate and apply.
    """
    data = JobsPayload.model_validate(payload.model_dump())
    commune.employment.matching_job_offers = data.jobs
    commune.employment.standard_jobs_matching_total = data.total


def project_associations(code: str, result: HydrationTaskResult | None) -> dict:
    """Build the read-only compatibility view used by association panels.

    Args:
        code: Target commune.
        result: Terminal outcome.

    Returns:
        Legacy keys for existing presentation components.
    """
    status = {
        "status": result.status.value if result else "pending",
        "error_code": result.error_code if result else None,
    }
    view = {"association_enrichment_status": {code: status}}
    if result is not None and result.payload is not None:
        view["enrichment"] = {code: result.payload.model_dump(mode="json")}
    return view


def project_services(code: str, result: HydrationTaskResult | None) -> dict:
    """Build the read-only service panel view.

    Args:
        code: Target commune.
        result: Terminal outcome.

    Returns:
        Legacy service payload and status maps.
    """
    status = {
        "status": result.status.value if result else "pending",
        "error_code": result.error_code if result else None,
    }
    view = {"inclusion_services_status": {code: status}}
    if result is not None and result.payload is not None:
        view["inclusion_services_enrichment"] = {
            code: result.payload.model_dump(mode="json")["services"]
        }
    return view


def project_jobs(code: str, result: HydrationTaskResult | None) -> dict:
    """Build the read-only jobs panel view.

    Args:
        code: Target commune.
        result: Terminal outcome.

    Returns:
        Legacy jobs payload and status map.
    """
    data = (
        result.payload.model_dump(mode="json")
        if result is not None and result.payload is not None
        else {"jobs": []}
    )
    return {
        "jobs_enrichment": {
            code: {
                **data,
                "status": result.status.value if result else "pending",
                "error_code": result.error_code if result else None,
            }
        }
    }
