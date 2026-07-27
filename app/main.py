import streamlit as st
import logfire
import utils.logger  # Ensures logfire.configure() is executed before any spans are created
from utils import data_loader

# --- Page Configuration (Standard ODIS) ---
st.set_page_config(page_title="OD&IS", page_icon="👋", layout="wide")

# --- Authentication (Standard ODIS) ---
# We check passwords here too to ensure that hitting the root URL with
# query params (e.g. ?demo=3) doesn't reset the session or bypass auth.
from utils import auth

if not auth.check_password():
    st.stop()

# --- Initialize Data ---
with logfire.span("ODIS Session"):
    data_loader.ensure_data_initialized()

# --- Shared Search Interception ---
if "search" in st.query_params:
    share_id = st.query_params.get("search")
    if share_id:
        from services import share_service
        try:
            config_obj, results_obj = share_service.load_shared_search(share_id)
            if config_obj and results_obj:
                share_service.restore_shared_search_to_session_state(config_obj, results_obj, share_id)
                st.switch_page("pages/3_Resultats.py")
            else:
                st.session_state["share_error"] = f"La recherche partagée '{share_id}' est introuvable ou a expiré."
        except Exception as e:
            st.session_state["share_error"] = f"Impossible de lire la recherche partagée '{share_id}' : {e}"

# --- Silent Redirect ---
# This makes main.py purely an entry point that leads to the first page
st.switch_page("pages/1_Accueil.py")
