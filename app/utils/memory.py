import streamlit as st
import gc
import logging
import tracemalloc
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

def clear_search_state():
    """
    Clears all heavy search-related artifacts from the Streamlit session state
    to reclaim memory. This is used before starting a new search or when
    resetting the application to its initial state.
    """
    logger.info("🧹 [MEMORY] Clearing search state...")
    
    # Define keys that hold large objects or DataFrames
    heavy_keys = [
        'processed_gdf',        # Pruned GeoDataFrame for mapping
        'unaggregated_gdf',     # Alias for processed_gdf
        'search_results',       # Large Pydantic model tree
        'engine',               # ScoringEngine instance (carries some state)
        'map_object',           # Folium Map object
        'pdf_data',             # Cached PDF bytes
        'pdf_modal_data',       # Cached PDF bytes for dialog
        'show_pdf_modal',       # Flag to trigger PDF dialog
        'pdf_modal_rerun_done', # Flag to track rerun status
        'selected_geo'          # Filtered GeoDataFrame subset
    ]
    
    cleared_count = 0
    for key in heavy_keys:
        if key in st.session_state:
            del st.session_state[key]
            cleared_count += 1
            
    if cleared_count > 0:
        logger.info(f"🧹 [MEMORY] Removed {cleared_count} heavy objects from session state.")
        perform_garbage_collection()
    else:
        logger.debug("🧹 [MEMORY] No heavy objects found to clear.")

def perform_garbage_collection():
    """
    Explicitly triggers Python's garbage collection and logs the result.
    """
    gc.collect()
    logger.info("♻️ [MEMORY] Garbage collection triggered.")

