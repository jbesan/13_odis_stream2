"""Deadline and dependency notification tests without external model calls."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents.state import GraphState
from core import postscoring
from core.models import SearchCriterias


@pytest.mark.parametrize("timeout_during_setup", [False, True])
def test_refiner_deadline_is_final_and_notifies_dependents_once(
    monkeypatch: pytest.MonkeyPatch,
    timeout_during_setup: bool,
) -> None:
    """Queued or already-started refiner work cannot overwrite its timeout."""
    timers = []
    queued = []
    notifications = []
    store = {}

    class Timer:
        """Manually driven timer for a deterministic scheduling test."""

        def __init__(self, seconds: float, callback: object) -> None:
            self.callback = callback
            timers.append(self)

        def start(self) -> None:
            """Leave time advancement under test control."""

        def cancel(self) -> None:
            """Record no real timer resources."""

    def client(*args: object, **kwargs: object) -> MagicMock:
        """Expire the stage while its setup is in progress."""
        if timeout_during_setup:
            timers[0].callback()
        return MagicMock()

    monkeypatch.setattr(postscoring.threading, "Timer", Timer)
    monkeypatch.setattr(postscoring, "get_odis_bg_store", lambda: store)
    monkeypatch.setattr(postscoring, "submit_background_work", queued.append)
    monkeypatch.setattr(postscoring.agent_config, "get_gemini_client", client)
    monkeypatch.setattr(
        postscoring.agent_config, "get_p_model", lambda *args, **kwargs: MagicMock()
    )
    monkeypatch.setattr(postscoring, "rehydrate_graph_state", lambda _: GraphState())
    monkeypatch.setattr(
        postscoring.refiner_agent,
        "run",
        AsyncMock(
            return_value=SimpleNamespace(
                output=SimpleNamespace(
                    odis_brief="late", global_pitch="late", pitches_per_city=[]
                )
            )
        ),
    )

    postscoring.launch_background_refining(
        SearchCriterias(),
        {},
        "run",
        on_terminal=lambda: notifications.append("terminal"),
    )
    if not timeout_during_setup:
        timers[0].callback()
    queued[0]()
    assert store["run"]["status_refiner"] == "timeout"
    assert "odis_brief" not in store["run"]
    assert "pitches" in store["run"]
    assert notifications == ["terminal"]


def test_refiner_fallback_pitches_populated_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timeout populates static pitches for candidate cities."""
    timers = []
    store = {}

    class Timer:
        def __init__(self, seconds: float, callback: object) -> None:
            self.callback = callback
            timers.append(self)

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    monkeypatch.setattr(postscoring.threading, "Timer", Timer)
    monkeypatch.setattr(postscoring, "get_odis_bg_store", lambda: store)
    monkeypatch.setattr(postscoring, "submit_background_work", lambda fn: None)

    top_cities = [
        {"codgeo": "33063", "name": "Bordeaux", "scores": {}},
        {"codgeo": "40119", "name": "Hagetmau", "scores": {}},
    ]
    commune_pressentie = {"codgeo": "64102", "name": "Bayonne", "scores": {}}

    postscoring.launch_background_refining(
        SearchCriterias(),
        {},
        "run_fallback",
        top_cities=top_cities,
        commune_pressentie=commune_pressentie,
    )
    timers[0].callback()

    assert store["run_fallback"]["status_refiner"] == "timeout"
    assert "33063" in store["run_fallback"]["pitches"]["pitches"]
    assert "40119" in store["run_fallback"]["pitches"]["pitches"]
    assert "64102" in store["run_fallback"]["pitches"]["pitches"]


def test_refiner_retries_on_failure_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attempt 1 failure is retried; attempt 2 success publishes done."""
    queued = []
    store = {}

    class Timer:
        def __init__(self, seconds: float, callback: object) -> None:
            pass

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    attempts = 0

    async def fake_run(*args: object, **kwargs: object) -> SimpleNamespace:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Transient Vertex AI error")
        return SimpleNamespace(
            output=SimpleNamespace(
                odis_brief="Success Briefing",
                global_pitch="Global Pitch",
                pitches_per_city=[SimpleNamespace(codgeo="33063", pitch="City Pitch")],
            )
        )

    monkeypatch.setattr(postscoring.threading, "Timer", Timer)
    monkeypatch.setattr(postscoring, "get_odis_bg_store", lambda: store)
    monkeypatch.setattr(postscoring, "submit_background_work", queued.append)
    monkeypatch.setattr(
        postscoring.agent_config, "get_gemini_client", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(
        postscoring.agent_config, "get_p_model", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(postscoring, "rehydrate_graph_state", lambda _: GraphState())
    monkeypatch.setattr(postscoring.refiner_agent, "run", AsyncMock(side_effect=fake_run))

    postscoring.launch_background_refining(
        SearchCriterias(),
        {},
        "run_retry",
        top_cities=[{"codgeo": "33063", "name": "Bordeaux"}],
    )
    queued[0]()

    assert attempts == 2
    assert store["run_retry"]["status_refiner"] == "done"
    assert store["run_retry"]["odis_brief"] == "Success Briefing"
    assert store["run_retry"]["pitches"]["pitches"]["33063"] == "City Pitch"


def test_refiner_all_attempts_fail_populates_static_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When all retry attempts fail, static fallback is published."""
    queued = []
    store = {}

    class Timer:
        def __init__(self, seconds: float, callback: object) -> None:
            pass

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            pass

    monkeypatch.setattr(postscoring.threading, "Timer", Timer)
    monkeypatch.setattr(postscoring, "get_odis_bg_store", lambda: store)
    monkeypatch.setattr(postscoring, "submit_background_work", queued.append)
    monkeypatch.setattr(
        postscoring.agent_config, "get_gemini_client", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(
        postscoring.agent_config, "get_p_model", lambda *a, **k: MagicMock()
    )
    monkeypatch.setattr(postscoring, "rehydrate_graph_state", lambda _: GraphState())
    monkeypatch.setattr(
        postscoring.refiner_agent,
        "run",
        AsyncMock(side_effect=RuntimeError("Persistent Vertex failure")),
    )

    postscoring.launch_background_refining(
        SearchCriterias(),
        {},
        "run_failed",
        top_cities=[{"codgeo": "33063", "name": "Bordeaux", "scores": {}}],
    )
    queued[0]()

    assert store["run_failed"]["status_refiner"] == "error"
    assert "33063" in store["run_failed"]["pitches"]["pitches"]
