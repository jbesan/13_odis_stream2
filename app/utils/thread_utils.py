"""Thread utilities for background workers in Streamlit."""

import logging
import threading
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

logger = logging.getLogger(__name__)


def attach_script_run_ctx(thread: threading.Thread) -> threading.Thread:
    """Attaches the active Streamlit ScriptRunContext to a background worker thread.

    If running within an active Streamlit session, this ensures the worker thread
    inherits the ScriptRunContext so cached functions and internal Streamlit tools
    run without emitting 'missing ScriptRunContext!' warnings.
    If running outside Streamlit (e.g. CLI, background worker, or unit tests),
    it safely does nothing.

    Args:
        thread: The worker thread to attach context to before starting.

    Returns:
        The same thread instance for fluent chaining.
    """
    ctx = get_script_run_ctx(suppress_warning=True)
    if ctx is not None:
        add_script_run_ctx(thread, ctx)
    return thread
