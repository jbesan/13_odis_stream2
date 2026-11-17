"""Process-global server activity tracking for Cloud Run idle sleep."""

from __future__ import annotations

import threading
import time

DEFAULT_IDLE_TIMEOUT_MINUTES = 30

_ACTIVITY_LOCK = threading.Lock()
_last_server_activity_ts: float = time.time()


def record_server_activity(timestamp: float | None = None) -> float:
    """Record real server-wide user or background activity and return its timestamp."""
    global _last_server_activity_ts
    ts = float(timestamp) if timestamp is not None else time.time()
    with _ACTIVITY_LOCK:
        if ts > _last_server_activity_ts:
            _last_server_activity_ts = ts
        return _last_server_activity_ts


def get_last_server_activity_ts() -> float:
    """Return the timestamp of the most recent recorded server activity."""
    with _ACTIVITY_LOCK:
        return _last_server_activity_ts


def has_active_background_work() -> bool:
    """Return True if any background enrichment, refiner, or AI task is active."""
    from agents.utils import get_odis_bg_store

    store = get_odis_bg_store()
    if not isinstance(store, dict):
        return False

    active = False
    # Snapshot items to tolerate concurrent updates from background worker threads.
    for value in list(store.values()):
        if not isinstance(value, dict):
            continue
        if value.get("status") == "running" or value.get("status_refiner") == "running":
            active = True
            break

        run = value.get("hydration_run")
        if (
            run is not None
            and hasattr(run, "ready")
            and hasattr(run, "plan")
            and not getattr(run, "_cancelled", False)
            and any(not run.ready(code) for code in run.plan.codgeos)
        ):
            active = True
            break

        steps = value.get("auto_analysis_steps")
        if isinstance(steps, dict):
            for step in list(steps.values()):
                if not isinstance(step, dict):
                    continue
                if step.get("status") == "dispatched":
                    task_key = step.get("task_key")
                    task_entry = store.get(task_key) if task_key else None
                    if (
                        isinstance(task_entry, dict)
                        and task_entry.get("status") == "running"
                    ):
                        active = True
                        break
            if active:
                break

    if active:
        record_server_activity()
    return active


def is_server_idle(
    timeout_minutes: int = DEFAULT_IDLE_TIMEOUT_MINUTES, *, now: float | None = None
) -> bool:
    """Return True when the whole instance has been idle for at least timeout_minutes."""
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes must be greater than zero")
    if has_active_background_work():
        return False
    current_ts = float(now) if now is not None else time.time()
    return (current_ts - get_last_server_activity_ts()) >= (timeout_minutes * 60)
