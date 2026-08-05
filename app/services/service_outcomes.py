"""Explicit outcomes for calls that cross an external service boundary.

``None`` and an empty dataframe are valid domain values in several parts of
the application.  They must therefore not also be used to communicate that a
provider, an authorization or a payload failed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, TypeVar


T = TypeVar("T")


class OutcomeStatus(str, Enum):
    """Stable, user-safe categories for an external operation."""

    SUCCESS = "success"
    SUCCESS_EMPTY = "success_empty"
    NOT_FOUND = "not_found"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    INVALID_PAYLOAD = "invalid_payload"
    PARTIAL = "partial"


@dataclass(frozen=True)
class ServiceOutcome(Generic[T]):
    """Value and classified outcome returned by an external boundary.

    ``error_code`` is deliberately safe to show to users and stable enough to
    use in Cloud Logging queries. Provider details remain in the log record.
    """

    status: OutcomeStatus
    value: T | None = None
    error_code: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status in {OutcomeStatus.SUCCESS, OutcomeStatus.SUCCESS_EMPTY}
