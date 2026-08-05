"""Common, serializable states for best-effort post-scoring enrichments."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class EnrichmentStatus(StrEnum):
    PENDING = "pending"
    SUCCESS_NONEMPTY = "success_nonempty"
    SUCCESS_EMPTY = "success_empty"
    PARTIAL = "partial"
    ERROR = "error"
    TIMEOUT = "timeout"
    NOT_CONFIGURED = "not_configured"


TERMINAL_ENRICHMENT_STATUSES = frozenset(
    {
        EnrichmentStatus.SUCCESS_NONEMPTY,
        EnrichmentStatus.SUCCESS_EMPTY,
        EnrichmentStatus.PARTIAL,
        EnrichmentStatus.ERROR,
        EnrichmentStatus.TIMEOUT,
        EnrichmentStatus.NOT_CONFIGURED,
    }
)

# The refiner predates the provider status enum. Keep its small compatibility
# boundary here so views do not scatter their own `done`/`error` checks.
TERMINAL_REFINER_STATUSES = frozenset({"done", "error", "timeout", "not_configured"})


def enrichment_result(
    status: EnrichmentStatus,
    *,
    data: Any = None,
    error_code: str | None = None,
    detail: str | None = None,
    attempts: int = 1,
    retryable: bool = False,
) -> dict[str, Any]:
    """Build a safe result payload for the session-scoped background store."""
    result: dict[str, Any] = {
        "status": status.value,
        "attempts": attempts,
        "retryable": retryable,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    if data is not None:
        result["data"] = data
    if error_code:
        result["error_code"] = error_code
    if detail:
        result["detail"] = detail
    return result


def is_terminal_enrichment_status(status: str | None) -> bool:
    """Return whether a status stops Streamlit polling."""
    return status in {item.value for item in TERMINAL_ENRICHMENT_STATUSES}


def is_terminal_refiner_status(status: str | None) -> bool:
    """Return whether the optional refiner has reached a final state."""
    return status in TERMINAL_REFINER_STATUSES
