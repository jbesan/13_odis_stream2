import streamlit as st
import gc
import logging
from services.app_session import AppSession

logger = logging.getLogger(__name__)


def reset_app_state():
    """
    Clears almost all session state to provide a fresh start, while preserving
    authentication and heavy data caches. Used when returning to Home.
    """
    logger.info("🧹 [MEMORY] Resetting session state...")

    cleared_count = AppSession(st.session_state).reset_for_home()

    if cleared_count > 0:
        logger.info(f"🧹 [MEMORY] Removed {cleared_count} objects from session state.")
        perform_garbage_collection()
    else:
        logger.debug("🧹 [MEMORY] No objects found to clear.")


def perform_garbage_collection():
    """
    Explicitly triggers Python's garbage collection and logs the result.
    """
    gc.collect()
    logger.info("♻️ [MEMORY] Garbage collection triggered.")
