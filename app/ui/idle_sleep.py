"""Disconnect inactive browser tabs globally so Cloud Run can scale to zero."""

from __future__ import annotations

import time

import streamlit as st

from services.server_activity import (
    DEFAULT_IDLE_TIMEOUT_MINUTES,
    get_last_server_activity_ts,
    has_active_background_work,
    is_server_idle,
    record_server_activity,
)

__all__ = [
    "DEFAULT_IDLE_TIMEOUT_MINUTES",
    "get_last_server_activity_ts",
    "has_active_background_work",
    "inject_idle_disconnect",
    "is_server_idle",
    "record_server_activity",
]


def _mark_idle_check_rerun() -> None:
    """Flag the rerun triggered by the client idle-check probe."""
    st.session_state["_odis_idle_check_rerun"] = True


_IDLE_DISCONNECT_JS = r"""
export default function(component) {
    const configuredTimeoutMs = Number(component.data?.timeout_ms);
    const timeoutMs = Number.isFinite(configuredTimeoutMs) && configuredTimeoutMs > 0
        ? Math.max(1000, configuredTimeoutMs)
        : 30 * 60 * 1000;
    const serverIdleAgeMs = Number(component.data?.server_idle_age_ms);
    const shouldDisconnectFromServer = Boolean(component.data?.should_disconnect);
    const componentHost = component.parentElement.host || component.parentElement;
    const activityEvents = [
        "keydown",
        "pointerdown",
        "scroll",
        "touchstart",
    ];
    const storedLastActivity = Number(
        componentHost.dataset.idleDisconnectLastActivity
    );
    const now = Date.now();
    const serverEquivalentLastActivity =
        Number.isFinite(serverIdleAgeMs) && serverIdleAgeMs >= 0
            ? now - serverIdleAgeMs
            : now;
    let lastActivity = Math.max(
        Number.isFinite(storedLastActivity) && storedLastActivity > 0
            ? storedLastActivity
            : 0,
        serverEquivalentLastActivity
    );
    if (!Number.isFinite(lastActivity) || lastActivity <= 0) {
        lastActivity = now;
    }

    let disconnecting = false;
    let awaitingServerCheck = false;
    let fallbackTimerId = null;

    componentHost.dataset.idleDisconnect = "active";
    componentHost.dataset.idleDisconnectTimeoutMs = String(timeoutMs);
    componentHost.dataset.idleDisconnectLastActivity = String(lastActivity);

    function recordActivity() {
        lastActivity = Date.now();
        awaitingServerCheck = false;
        if (fallbackTimerId !== null) {
            window.clearTimeout(fallbackTimerId);
            fallbackTimerId = null;
        }
        componentHost.dataset.idleDisconnectLastActivity = String(lastActivity);
    }

    function handlePageShow(event) {
        if (event.persisted) recordActivity();
    }

    function disconnect() {
        if (disconnecting) return;
        disconnecting = true;
        if (fallbackTimerId !== null) {
            window.clearTimeout(fallbackTimerId);
            fallbackTimerId = null;
        }

        // A normal document navigation unloads Streamlit and closes its
        // WebSocket. The static HTML response completes immediately and keeps
        // no active request open against Cloud Run.
        const idleUrl = new URL("/app/static/idle.html", window.location.href);
        const returnTarget =
            window.location.pathname + window.location.search + window.location.hash;
        idleUrl.searchParams.set("return", returnTarget || "/");
        window.location.assign(idleUrl);
    }

    function checkIdleTime() {
        if (disconnecting) return;
        if (shouldDisconnectFromServer) {
            disconnect();
            return;
        }
        if (Date.now() - lastActivity >= timeoutMs) {
            if (
                typeof component.setTriggerValue === "function" &&
                !awaitingServerCheck
            ) {
                awaitingServerCheck = true;
                component.setTriggerValue("check_idle", Date.now());
                fallbackTimerId = window.setTimeout(disconnect, 5000);
            } else if (!awaitingServerCheck) {
                disconnect();
            }
        }
    }

    activityEvents.forEach((eventName) => {
        document.addEventListener(eventName, recordActivity, {
            capture: true,
            passive: true,
        });
    });
    window.addEventListener("pageshow", handlePageShow);
    // Check synchronously as well as on an interval. Streamlit can remount a
    // V2 renderer before its first timer tick; the persisted timestamp keeps
    // those remounts from postponing the deadline indefinitely.
    checkIdleTime();
    const intervalId = disconnecting
        ? null
        : window.setInterval(checkIdleTime, 5000);

    // Streamlit V2 invokes this cleanup when the component is unmounted.
    return () => {
        if (intervalId !== null) window.clearInterval(intervalId);
        if (fallbackTimerId !== null) window.clearTimeout(fallbackTimerId);
        activityEvents.forEach((eventName) => {
            document.removeEventListener(eventName, recordActivity, {
                capture: true,
            });
        });
        window.removeEventListener("pageshow", handlePageShow);
    };
}
"""


# Register once per Python module. Defining a V2 component inside the mounting
# function re-registers it on each script run and produces avoidable lifecycle work.
_idle_disconnect_component = st.components.v2.component(
    "odis_idle_disconnect",
    js=_IDLE_DISCONNECT_JS,
    isolate_styles=True,
)


def inject_idle_disconnect(
    timeout_minutes: int = DEFAULT_IDLE_TIMEOUT_MINUTES,
) -> None:
    """Mount a server-coordinated inactivity monitor that disconnects idle tabs."""
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes must be greater than zero")

    now = time.time()
    should_disconnect = is_server_idle(timeout_minutes=timeout_minutes, now=now)
    last_ts = get_last_server_activity_ts()
    idle_age_ms = max(0, int((now - last_ts) * 1000))

    _idle_disconnect_component(
        data={
            "timeout_ms": int(timeout_minutes * 60 * 1000),
            "server_last_activity_ms": int(last_ts * 1000),
            "server_idle_age_ms": idle_age_ms,
            "should_disconnect": should_disconnect,
        },
        key="odis_idle_disconnect_monitor",
        on_check_idle_change=_mark_idle_check_rerun,
    )
