import streamlit as st
from utils import data_loader

# --- Page Configuration (Standard ODIS) ---
st.set_page_config(
    page_title="ODIS",
    page_icon="👋",
    layout="wide"
)

# --- Initialize Data ---
data_loader.ensure_data_initialized()

# --- Silent Redirect ---
# This makes main.py purely an entry point that leads to the first page
st.switch_page("pages/1_Accueil.py")
