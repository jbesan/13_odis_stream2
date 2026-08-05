"""Disconnect inactive browser tabs so Cloud Run can eventually scale to zero."""

from __future__ import annotations

import streamlit as st


_IDLE_DISCONNECT_JS = r"""
export default function(component) {
    const configuredTimeoutMs = Number(component.data?.timeout_ms);
    const timeoutMs = Number.isFinite(configuredTimeoutMs) && configuredTimeoutMs > 0
        ? Math.max(1000, configuredTimeoutMs)
        : 10 * 60 * 1000;
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
    let lastActivity = Number.isFinite(storedLastActivity) && storedLastActivity > 0
        ? storedLastActivity
        : Date.now();
    let disconnecting = false;

    componentHost.dataset.idleDisconnect = "active";
    componentHost.dataset.idleDisconnectTimeoutMs = String(timeoutMs);
    componentHost.dataset.idleDisconnectLastActivity = String(lastActivity);

    function recordActivity() {
        lastActivity = Date.now();
        componentHost.dataset.idleDisconnectLastActivity = String(lastActivity);
    }

    function handlePageShow(event) {
        if (event.persisted) recordActivity();
    }

    function disconnect() {
        if (disconnecting) return;
        disconnecting = true;

        // A normal document navigation unloads Streamlit and closes its
        // WebSocket. The static response completes immediately and keeps no
        // active request open against Cloud Run.
        const idleUrl = new URL("app/static/idle.png", window.location.href);
        window.location.assign(idleUrl);
    }

    function checkIdleTime() {
        if (Date.now() - lastActivity >= timeoutMs) disconnect();
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
        : window.setInterval(checkIdleTime, 2000);

    // Streamlit V2 invokes this cleanup when the component is unmounted.
    return () => {
        if (intervalId !== null) window.clearInterval(intervalId);
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


def inject_idle_disconnect(timeout_minutes: int = 10) -> None:
    """Mount a per-tab inactivity monitor that disconnects the Streamlit session."""
    if timeout_minutes <= 0:
        raise ValueError("timeout_minutes must be greater than zero")

    _idle_disconnect_component(
        data={"timeout_ms": int(timeout_minutes * 60 * 1000)},
        key="odis_idle_disconnect_monitor",
    )
