import asyncio
import threading
import time
from unittest.mock import patch

from agents.utils import (
    DEFAULT_GRAPH_RUN_TIMEOUT_SECONDS,
    cancel_background_city_analysis,
    get_graph_run_timeout_seconds,
    launch_background_city_analysis,
)


def wait_for(predicate, timeout: float = 2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Background run did not reach the expected state")


def test_default_graph_deadline_is_sixty_seconds(monkeypatch):
    monkeypatch.delenv("ODIS_GRAPH_RUN_TIMEOUT_SECONDS", raising=False)
    assert DEFAULT_GRAPH_RUN_TIMEOUT_SECONDS == 60.0
    assert get_graph_run_timeout_seconds() == 60.0


def test_background_run_snapshots_input_and_records_lifecycle_metadata():
    store = {}
    started = threading.Event()
    release = threading.Event()
    seen_inputs = []

    async def fake_run_logic(input_data):
        seen_inputs.append(input_data)
        started.set()
        await asyncio.to_thread(release.wait)
        return {"search_results": {"origin": "snapshot"}}

    criteria = {"org_context": "jaccueille", "profile": "initial"}
    results = {"results": [{"codgeo": "13055", "name": "Marseille"}]}

    with (
        patch("agents.utils.get_odis_bg_store", return_value=store),
        patch("agents.utils.run_logic", side_effect=fake_run_logic),
    ):
        record = launch_background_city_analysis(
            "Marseille",
            "13055",
            criteria,
            results,
            "criteria-hash",
            username="ts@example.org",
            timeout_seconds=1.0,
        )
        assert started.wait(1.0)

        criteria["profile"] = "mutated-after-launch"
        results["results"][0]["name"] = "Mutated"
        assert seen_inputs[0]["search_criteria"]["profile"] == "initial"
        assert seen_inputs[0]["search_results"]["results"][0]["name"] == "Marseille"
        assert record["run_id"]
        assert record["attempt"] == 1
        assert record["owner_username"] == "ts@example.org"
        assert record["organization_id"] == "jaccueille"
        assert record["deadline_at"] > record["start_time"]

        release.set()
        wait_for(lambda: store[record["task_key"]]["status"] == "done")


def test_retry_supersedes_running_attempt_and_drops_late_completion():
    store = {}
    first_started = threading.Event()
    seen_inputs = []

    async def fake_run_logic(input_data):
        seen_inputs.append(input_data)
        if input_data["run_attempt"] == 1:
            first_started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                # Simulate a provider task returning after application
                # cancellation. The outer run contract must still drop it.
                return {"search_results": {"origin": "stale"}}
        return {"search_results": {"origin": "current"}}

    with (
        patch("agents.utils.get_odis_bg_store", return_value=store),
        patch("agents.utils.run_logic", side_effect=fake_run_logic),
    ):
        prior_results = {
            "results": [
                {
                    "codgeo": "13055",
                    "expert_analysis": {"mobility_expert": "old report"},
                    "odis_synthesis": [
                        {"role": "assistant", "content": "old synthesis"}
                    ],
                }
            ]
        }
        first = launch_background_city_analysis(
            "Marseille",
            "13055",
            {},
            prior_results,
            "criteria-hash",
            timeout_seconds=1.0,
        )
        assert first_started.wait(1.0)

        second = launch_background_city_analysis(
            "Marseille",
            "13055",
            {},
            prior_results,
            "criteria-hash",
            timeout_seconds=1.0,
            retry=True,
        )
        assert second["run_id"] == first["run_id"]
        assert second["attempt"] == 2
        wait_for(lambda: store[second["task_key"]]["status"] == "done")

        current = store[second["task_key"]]
        assert current["attempt"] == 2
        assert current["result"] == {"origin": "current"}
        retried_city = seen_inputs[1]["search_results"]["results"][0]
        assert retried_city["expert_analysis"] == {}
        assert retried_city["odis_synthesis"] == []
        assert prior_results["results"][0]["expert_analysis"] == {
            "mobility_expert": "old report"
        }


def test_background_run_timeout_is_terminal_and_retryable():
    store = {}

    async def slow_run_logic(_input_data):
        await asyncio.Event().wait()

    with (
        patch("agents.utils.get_odis_bg_store", return_value=store),
        patch("agents.utils.run_logic", side_effect=slow_run_logic),
    ):
        record = launch_background_city_analysis(
            "Marseille", "13055", {}, {}, "criteria-hash", timeout_seconds=0.02
        )
        wait_for(lambda: store[record["task_key"]]["status"] == "timeout")

    terminal = store[record["task_key"]]
    assert terminal["error_code"] == "deadline_exceeded"
    assert terminal["retryable"] is True


def test_user_cancellation_is_terminal_and_prevents_publication():
    store = {}
    started = threading.Event()

    async def slow_run_logic(_input_data):
        started.set()
        await asyncio.Event().wait()

    with (
        patch("agents.utils.get_odis_bg_store", return_value=store),
        patch("agents.utils.run_logic", side_effect=slow_run_logic),
    ):
        record = launch_background_city_analysis(
            "Marseille", "13055", {}, {}, "criteria-hash", timeout_seconds=1.0
        )
        assert started.wait(1.0)
        assert cancel_background_city_analysis(record["task_key"])
        wait_for(lambda: store[record["task_key"]]["status"] == "cancelled")

    assert "result" not in store[record["task_key"]]
    assert store[record["task_key"]]["error_code"] == "cancelled"
