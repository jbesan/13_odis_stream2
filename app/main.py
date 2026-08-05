import streamlit as st
import logfire
import utils.logger  # noqa: F401  # Import configures logging before any spans are created.
from utils import data_loader
from ui import page_shell

# --- Page Configuration (Standard ODIS) ---
st.set_page_config(page_title="OD&IS", page_icon="👋", layout="wide")

# Authentication and shared-link routing always run before data initialization.
page_shell.enter_page(
    None, handle_shared_search=True, redirect_shared_to_results=True
)

# --- Initialize State / Start Async Preload ---
with logfire.span("ODIS Session"):
    data_loader.initialize_session_state()
    data_loader.preload_scoring_datasets_async()

# --- Silent Redirect ---
# This makes main.py purely an entry point that leads to the first page
st.switch_page("pages/1_Accueil.py")
