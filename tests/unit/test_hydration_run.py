"""Execution-order, deadline and extension contracts for deterministic hydration."""

from concurrent.futures import ThreadPoolExecutor
from itertools import permutations
from threading import Event
from typing import Any

import pytest

from core.enrichment_status import EnrichmentStatus
from core.hydration import HydrationProvider, HydrationRun, HydrationTaskResult
from core.hydration_payloads import JobsPayload, apply_jobs, project_jobs
from core.models import CommuneResult, SearchCriterias, SearchResultsData
from core.postscoring import PostScoringRun
from ui import results_actions
from services.app_session import AppSession
from agents.utils import get_odis_bg_store


def job_result() -> HydrationTaskResult:
    """Return one typed offer for order-independent publication checks."""
    return HydrationTaskResult(
        status=EnrichmentStatus.SUCCESS_NONEMPTY,
        payload=JobsPayload(
            jobs=[[{"id": "j1", "title": "Boulanger", "contract_type": "CDI"}]], total=1
        ),
    )


def provider(name: str = "jobs") -> HydrationProvider:
    """Build an offline provider adapter.

    Args:
        name: Unique provider name.

    Returns:
        A provider returning one offer per commune.
    """
    return HydrationProvider(
        name,
        lambda codes: {code: job_result() for code in codes},
        apply_jobs,
        project_jobs,
        ("employment",),
    )


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_join_is_per_city_and_reduce_independent_of_completion_order(
    order: tuple[int, ...],
) -> None:
    """No commune is published before all planned results have arrived."""
    adapters = tuple(provider(name) for name in ("a", "b", "c"))
    run = HydrationRun(["A", "B", "A"], adapters, {}, "run")
    city = CommuneResult(codgeo="A", name="A")
    assert run.plan.codgeos == ("A", "B")
    for index, item in enumerate(order):
        run._finish(adapters[item], ("A",), {"A": job_result()}, "missing")
        run.reduce_into(city)
        assert city.commune_results_hydrated is (index == 2)
    assert not run.ready("B")
    assert city.employment.matching_job_offers[0][0].id == "j1"
    assert set(city.hydration_sources) == {"a", "b", "c"}


def test_new_provider_requires_no_readiness_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The UI reads the declared join even for a previously unknown provider."""
    adapters = (provider(), provider("future_provider"))
    store: dict[str, Any] = {}
    run = HydrationRun(["A"], adapters, store, "run")
    city = CommuneResult(codgeo="A", name="A")
    monkeypatch.setattr(results_actions.st, "session_state", {})
    monkeypatch.setattr(results_actions, "odis_get_bg_result", store.get)
    run._finish(adapters[0], ("A",), {"A": job_result()}, "missing")
    assert not results_actions._is_hydration_ready_for_city(city, "run")
    run._finish(
        adapters[1],
        ("A",),
        {"A": HydrationTaskResult(status=EnrichmentStatus.ERROR)},
        "missing",
    )
    assert results_actions._is_hydration_ready_for_city(city, "run")
    assert city.hydration_sources["future_provider"]["status"] == "error"


def test_late_outcomes_and_old_deadlines_cannot_modify_replacement() -> None:
    """Each attempt owns its outcomes; terminal timeouts cannot become success."""
    adapter = provider()
    store: dict[str, Any] = {}
    old = HydrationRun(["A"], (adapter,), store, "run")
    new = HydrationRun(["A"], (adapter,), store, "run")
    old.expire()
    old._finish(adapter, ("A",), {"A": job_result()}, "missing")
    assert not new.ready("A")
    new.expire()
    new._finish(adapter, ("A",), {"A": job_result()}, "missing")
    city = CommuneResult(codgeo="A", name="A")
    new.reduce_into(city)
    assert city.commune_results_hydrated
    assert not city.employment.matching_job_offers
    assert city.hydration_sources["jobs"]["status"] == "timeout"


def test_reduce_failure_leaves_original_model_unchanged() -> None:
    """Adapters work on a private candidate, so partial writes cannot escape."""

    def broken(city: CommuneResult, payload: Any) -> None:
        """Simulate a reducer that fails after starting its update."""
        city.name = "corrupted"
        raise ValueError("invalid provider payload")

    adapter = HydrationProvider(
        "bad", provider().fetch, broken, project_jobs, ("name",)
    )
    run = HydrationRun(["A"], (adapter,), {}, "run")
    run._finish(adapter, ("A",), {"A": job_result()}, "missing")
    city = CommuneResult(codgeo="A", name="original")
    before = city.model_dump()
    with pytest.raises(ValueError, match="invalid provider"):
        run.reduce_into(city)
    assert city.model_dump() == before


def test_snapshot_retirement_does_not_cancel_another_session() -> None:
    """Retiring the active execution preserves same-criteria runs elsewhere."""
    store = get_odis_bg_store()
    adapter = provider()
    own = HydrationRun(["A"], (adapter,), store, "own-test-execution")
    other = HydrationRun(["A"], (adapter,), store, "other-test-execution")
    try:
        AppSession({"active_search_hash": own.key})._retire_current_run()
        assert own.key not in store
        other._finish(adapter, ("A",), {"A": job_result()}, "missing")
        assert other.ready("A")
    finally:
        store.pop(other.key, None)
        other.cancel()


def test_fast_city_completes_while_slow_city_is_still_fetching() -> None:
    """Real worker futures demonstrate per-city fan-out without a global barrier."""
    release = Event()
    fast_done = Event()

    def fetch(codes: tuple[str, ...]) -> dict[str, HydrationTaskResult]:
        """Hold only the slow city's worker."""
        if codes == ("slow",):
            assert release.wait(3)
        return {code: job_result() for code in codes}

    adapter = HydrationProvider(
        "jobs", fetch, apply_jobs, project_jobs, ("employment",)
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        run = HydrationRun(["slow", "fast"], (adapter,), {}, "run", executor=pool)
        run.on_change = lambda: fast_done.set() if run.ready("fast") else None
        try:
            run.start()
            assert fast_done.wait(2)
            assert not run.ready("slow")
            city = CommuneResult(codgeo="fast", name="Fast")
            run.reduce_into(city)
            assert city.commune_results_hydrated
        finally:
            release.set()
        for future in run._futures:
            future.result(timeout=2)


@pytest.mark.parametrize("auto", [True, False])
@pytest.mark.parametrize("refiner_first", [True, False])
def test_auto_analysis_is_a_conditional_dependent_stage(
    monkeypatch: pytest.MonkeyPatch,
    auto: bool,
    refiner_first: bool,
) -> None:
    """Dispatch once after hydrated data and briefing, or leave analysis skipped."""
    adapter = provider()
    store: dict[str, Any] = {}
    run = HydrationRun(["A"], (adapter,), store, "run")
    results = SearchResultsData(results=[CommuneResult(codgeo="A", name="A")])
    coordinator = PostScoringRun(
        run,
        SearchCriterias(),
        results,
        auto_analysis=auto,
        interaction_id="test",
        username="test",
        org_id="test",
    )
    calls = []
    monkeypatch.setattr(
        "core.postscoring.launch_background_city_analysis",
        lambda **kwargs: calls.append(kwargs),
    )
    store["run"]["status_refiner"] = "running"
    run.on_change = coordinator.advance
    if refiner_first:
        store["run"]["status_refiner"] = "done"
        store["run"]["odis_brief"] = "Published brief"
        coordinator.advance()
    run._finish(adapter, ("A",), {"A": job_result()}, "missing")
    if not refiner_first:
        assert not calls
        store["run"]["status_refiner"] = "error"
    coordinator.advance()
    coordinator.advance()
    assert len(calls) == int(auto)
    if auto:
        published = calls[0]["search_results"].results[0]
        assert published.commune_results_hydrated
        assert published.employment.matching_job_offers[0][0].id == "j1"
        assert calls[0]["trigger"] == "post_scoring_auto"
    else:
        assert coordinator.steps["A"]["status"] == "skipped"
    # The orchestration only reduced its private input, not the live UI object.
    assert not results.results[0].commune_results_hydrated
