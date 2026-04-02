import streamlit as st
import gc
import logging
import tracemalloc
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

def reset_app_state():
    """
    Clears almost all session state to provide a fresh start, while preserving 
    authentication and heavy data caches. Used when returning to Home.
    """
    logger.info("🧹 [MEMORY] Resetting session state...")
    
    # Define keys to preserve (auth and heavy caches)
    to_keep = {
        'app_data',        # cached datasets
        '_data_hash',      # caching verification
        'password_correct',# auth status
        'username',        # current user
        'rna_rag_service', # AI tools
        'rna_rag_status',  # AI tools
    }
    
    cleared_count = 0
    # Must use list() to avoid RuntimeError: dictionary changed size during iteration
    for key in list(st.session_state.keys()):
        if key not in to_keep:
            del st.session_state[key]
            cleared_count += 1
    
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

