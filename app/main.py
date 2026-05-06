import streamlit as st
import logfire
from utils import data_loader

# --- Page Configuration (Standard ODIS) ---
st.set_page_config(
    page_title="ODIS",
    page_icon="👋",
    layout="wide"
)

# --- Authentication (Standard ODIS) ---
# We check passwords here too to ensure that hitting the root URL with 
# query params (e.g. ?demo=3) doesn't reset the session or bypass auth.
from utils import auth
if not auth.check_password():
    st.stop()

# --- Initialize Data ---
with logfire.span("ODIS Session"):
    data_loader.ensure_data_initialized()

    # --- Silent Redirect ---
    # This makes main.py purely an entry point that leads to the first page
    st.switch_page("pages/1_Accueil.py")
