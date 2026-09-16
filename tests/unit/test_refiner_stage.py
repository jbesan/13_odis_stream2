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
    assert notifications == ["terminal"]
