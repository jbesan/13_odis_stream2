"""Bounded fan-out and per-commune reduction, independent of the LLM graph."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializeAsAny,
    ValidationError,
    model_validator,
)

from core.enrichment_status import EnrichmentStatus, is_terminal_enrichment_status
from core.models import CommuneResult

logger = logging.getLogger(__name__)
_EXECUTOR = ThreadPoolExecutor(max_workers=8, thread_name_prefix="hydration")
_CAPACITY = threading.BoundedSemaphore(64)
SUCCESS_STATUSES = {
    EnrichmentStatus.SUCCESS_NONEMPTY,
    EnrichmentStatus.SUCCESS_EMPTY,
    EnrichmentStatus.PARTIAL,
}


def submit_background_work(work: Callable[[], None]) -> Future[None]:
    """Submit an optional stage to the same bounded execution budget.

    Args:
        work: Context-free background stage.

    Returns:
        The submitted future.

    Raises:
        RuntimeError: Capacity is exhausted or the executor is unavailable.
    """
    if not _CAPACITY.acquire(blocking=False):
        raise RuntimeError("Hydration executor capacity reached")
    try:
        future = _EXECUTOR.submit(work)
    except RuntimeError:
        _CAPACITY.release()
        raise
    future.add_done_callback(lambda _: _CAPACITY.release())
    return future


class HydrationTaskResult(BaseModel):
    """Validated terminal outcome; successful tasks always carry a payload."""

    model_config = ConfigDict(frozen=True)
    status: EnrichmentStatus
    payload: SerializeAsAny[BaseModel] | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        """Reject pending outcomes and success without validated data.

        Returns:
            The valid result.

        Raises:
            ValueError: The result cannot represent a completed task.
        """
        if not is_terminal_enrichment_status(self.status):
            raise ValueError("A task result must be terminal")
        if self.status in SUCCESS_STATUSES and self.payload is None:
            raise ValueError("Successful hydration requires a payload")
        return self


class HydrationPlan(BaseModel):
    """Closed set of expected providers for each execution."""

    model_config = ConfigDict(frozen=True)
    run_id: str = Field(default_factory=lambda: uuid4().hex)
    codgeos: tuple[str, ...]
    providers: tuple[str, ...]
    timeout_seconds: float = Field(default=30, gt=0)


@dataclass(frozen=True)
class HydrationProvider:
    """Provider adapter: fetch typed results, apply payloads, project legacy UI data."""

    name: str
    fetch: Callable[[tuple[str, ...]], Mapping[str, HydrationTaskResult]]
    apply: Callable[[CommuneResult, BaseModel], None]
    project: Callable[[str, HydrationTaskResult | None], dict[str, dict[str, Any]]]
    fields: tuple[str, ...]
    batched: bool = False


class HydrationRun:
    """Own task outcomes; workers never mutate the displayed domain model."""

    def __init__(
        self,
        codgeos: list[str],
        providers: tuple[HydrationProvider, ...],
        store: dict[str, Any],
        key: str,
        *,
        timeout_seconds: float = 30,
        executor: ThreadPoolExecutor = _EXECUTOR,
    ) -> None:
        """Create an explicit plan before any work starts.

        Args:
            codgeos: Target communes, deduplicated in input order.
            providers: Registered adapters for this run.
            store: Background store retaining the run and compatibility views.
            key: Unique execution key used by UI consumers.
            timeout_seconds: End-to-end deadline, including queue time.
            executor: Bounded shared executor, replaceable in tests.

        Raises:
            ValueError: Provider names are duplicated.
        """
        names = tuple(provider.name for provider in providers)
        if len(set(names)) != len(names):
            raise ValueError("Hydration provider names must be unique")
        self.plan = HydrationPlan(
            codgeos=tuple(dict.fromkeys(map(str, codgeos))),
            providers=names,
            timeout_seconds=timeout_seconds,
        )
        self.providers = providers
        self.store, self.key, self.executor = store, key, executor
        self._lock = threading.RLock()
        self._results: dict[str, dict[str, HydrationTaskResult]] = {
            code: {} for code in self.plan.codgeos
        }
        self._publication_errors: dict[str, str] = {}
        self._deadline = time.monotonic() + timeout_seconds
        self._timer: threading.Timer | None = None
        self._futures: list[Future[Any]] = []
        self._cancelled = False
        self._started = False
        self.on_change: Callable[[], None] = lambda: None
        current = store.get(key)
        if not isinstance(current, dict):
            current = {}
        old = current.get("hydration_run")
        if isinstance(old, HydrationRun):
            old.cancel()
        current = {"hydration_run": self}
        store[key] = current
        for provider in providers:
            for code in self.plan.codgeos:
                for name, values in provider.project(code, None).items():
                    current[name] = {**current.get(name, {}), **values}

    def _active(self) -> bool:
        """Return whether this execution still owns its store slot."""
        current = self.store.get(self.key)
        return (
            not self._cancelled
            and isinstance(current, dict)
            and current.get("hydration_run") is self
        )

    def start(self) -> None:
        """Fan out tasks after publishing the complete plan."""
        with self._lock:
            if self._started:
                raise RuntimeError("Hydration run already started")
            self._started = True
        self._timer = threading.Timer(self.plan.timeout_seconds, self.expire)
        self._timer.daemon = True
        self._timer.start()
        for provider in self.providers:
            groups = (
                [self.plan.codgeos]
                if provider.batched
                else [(c,) for c in self.plan.codgeos]
            )
            for codes in groups:
                if not codes:
                    continue
                if not _CAPACITY.acquire(blocking=False):
                    logger.warning(
                        "Hydration executor capacity reached: %s", provider.name
                    )
                    self._finish(provider, codes, {}, "executor_capacity")
                    continue
                try:
                    future = self.executor.submit(self._work, provider, codes)
                except RuntimeError:
                    _CAPACITY.release()
                    logger.exception("Hydration executor unavailable")
                    self._finish(provider, codes, {}, "executor_unavailable")
                else:
                    future.add_done_callback(lambda _: _CAPACITY.release())
                    self._futures.append(future)

    def _work(self, provider: HydrationProvider, codes: tuple[str, ...]) -> None:
        """Fetch one provider batch and publish honest failures.

        Args:
            provider: Adapter to execute.
            codes: Communes assigned to this task.
        """
        with self._lock:
            if not self._active() or time.monotonic() >= self._deadline:
                return
        try:
            results = provider.fetch(codes)
        except ValidationError:
            logger.exception("Hydration payload validation failed: %s", provider.name)
            self._finish(provider, codes, {}, "payload_validation_failed")
        except Exception:
            logger.exception("Hydration provider failed: %s", provider.name)
            self._finish(provider, codes, {}, "provider_failed")
        else:
            self._finish(provider, codes, results, "missing_provider_result")

    def _finish(
        self,
        provider: HydrationProvider,
        codes: tuple[str, ...],
        results: Mapping[str, HydrationTaskResult],
        error_code: str,
    ) -> None:
        """Accept each outcome once; reject superseded or late publications.

        Args:
            provider: Completed adapter.
            codes: Expected communes.
            results: Validated provider outcomes.
            error_code: Failure code for absent outcomes.
        """
        if time.monotonic() >= self._deadline:
            self.expire()
            return
        with self._lock:
            if not self._active():
                return
            for code in codes:
                if provider.name in self._results[code]:
                    continue
                result = results.get(code)
                if not isinstance(result, HydrationTaskResult):
                    result = HydrationTaskResult(
                        status=EnrichmentStatus.ERROR, error_code=error_code
                    )
                self._record(provider, code, result)
            if all(self.ready(code) for code in self.plan.codgeos) and self._timer:
                self._timer.cancel()
        self.on_change()

    def _record(
        self, provider: HydrationProvider, code: str, result: HydrationTaskResult
    ) -> None:
        """Store an outcome and replace compatibility maps without in-place edits.

        Args:
            provider: Adapter defining the compatibility projection.
            code: Target commune.
            result: Accepted final outcome.
        """
        result = result.model_copy(deep=True)
        self._results[code][provider.name] = result
        current = self.store[self.key]
        for name, values in provider.project(code, result).items():
            current[name] = {**current.get(name, {}), **values}

    def expire(self) -> None:
        """Close unfinished tasks; late HTTP completions cannot replace timeouts."""
        with self._lock:
            if not self._active():
                return
            for code in self.plan.codgeos:
                for provider in self.providers:
                    if provider.name not in self._results[code]:
                        self._record(
                            provider,
                            code,
                            HydrationTaskResult(
                                status=EnrichmentStatus.TIMEOUT,
                                error_code="deadline_exceeded",
                            ),
                        )
            for future in self._futures:
                future.cancel()
        self.on_change()

    def cancel(self) -> None:
        """Retire this attempt without changing any other run."""
        with self._lock:
            self._cancelled = True
            if self._timer:
                self._timer.cancel()
            for future in self._futures:
                future.cancel()

    def ready(self, code: str) -> bool:
        """Return whether the explicit per-commune join is complete.

        Args:
            code: Target commune.

        Returns:
            True only when every planned provider has a terminal outcome.
        """
        with self._lock:
            return code in self._results and len(self._results[code]) == len(
                self.providers
            )

    def reduce_into(self, commune: CommuneResult) -> None:
        """Validate a private copy, then publish owned fields on the UI thread.

        Args:
            commune: Current UI model; unchanged if validation fails.

        Raises:
            Exception: An adapter or model rejected the publication, logged here.
        """
        code = str(commune.codgeo)
        with self._lock:
            if not self._active() or not self.ready(code):
                return
            results = dict(self._results[code])
        candidate = commune.model_copy(deep=True)
        try:
            if any(
                result.error_code == "payload_validation_failed"
                for result in results.values()
            ):
                raise ValueError("Hydration provider payload failed validation")
            for provider in self.providers:
                result = results[provider.name]
                if result.payload is not None:
                    provider.apply(candidate, result.payload)
            candidate.hydration_sources = {
                name: {"status": result.status.value, "error_code": result.error_code}
                for name, result in results.items()
            }
            candidate = CommuneResult.model_validate(candidate.model_dump())
        except Exception:
            logger.exception("Hydration reduce failed for commune %s", code)
            self._publication_errors[code] = "validation_failed"
            self.store.get(self.key, {}).setdefault("hydration_publication_errors", {})[
                code
            ] = "validation_failed"
            raise
        with self._lock:
            if not self._active():
                return
            for field in dict.fromkeys(f for p in self.providers for f in p.fields):
                setattr(commune, field, getattr(candidate, field))
            commune.hydration_sources = candidate.hydration_sources
            commune.commune_results_hydrated = True
