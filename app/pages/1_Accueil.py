import time
import streamlit as st
import config as cfg
from ui import components as ui
from utils import data_loader
import logging

# --- Page Configuration ---
st.set_page_config(
    page_title="J'accueille",
    page_icon="👋",
    layout="wide"
)

# --- Authentication ---
from utils import auth
if not auth.check_password():
    st.stop()

logging.info(f"--- App re-run at {time.ctime(time.time())} ---")

# --- Main App Execution ---
data_loader.ensure_data_initialized()

# --- Demo Mode ---
if len(st.query_params) > 0 and 'demo' in st.query_params:
    with st.sidebar:
        if st.button('Quitter Mode Démo', key='quit_demo'):
            st.query_params.clear()
            st.rerun()

# --- CSS / Styling (V3 Global Green) ---
st.markdown("""
<style>
    /* Global App Styling */
    .stApp {
        background-color: #1B4429;
        color: white;
    }
    
    /* Text Color Overrides for specific Streamlit elements if needed */
    .stMarkdown, .stText, h1, h2, h3, p, label {
        color: white !important;
    }
    
    /* Global Container Padding */
    .block-container {
        padding-top: 1rem;
        padding-bottom: 3rem;
        max-width: 1200px; /* Limit width for cleaner look on large screens */
    }

    /* Header Container Styling */
    .header-container {
        color: white;
        padding: 40px 20px;
        text-align: center;
        margin-bottom: 20px;
    }
    .header-container h1 {
        color: white !important;
        font-family: 'Helvetica Neue', sans-serif;
        font-weight: 700;
        margin-bottom: 10px;
        font-size: 3rem;
    }
    .header-container p {
        color: #E0E0E0;
        font-size: 1.2rem;
        font-weight: 300;
    }

    /* Specificity fix: force dark green for everything inside white cards */
    .step-card, .step-card * {
        color: #1B4429 !important;
    }

    /* Step Cards Styling */
    .step-card {
        background-color: #FFFFFF; /* White card against Green BG */
        border-radius: 15px;
        padding: 25px 20px;
        text-align: center;
        height: 100%;
        min-height: 280px; /* Fixed height for uniformity */
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        display: flex;
        flex-direction: column;
        align-items: center;
    }
    .step-number {
        background-color: #FFD700; /* Yellow accent */
        color: #1B4429;
        width: 40px;
        height: 40px;
        border-radius: 50%;
        display: flex; /* Changed from inline-flex for better centering */
        align-items: center;
        justify-content: center;
        font-weight: bold;
        font-size: 1.2rem;
        margin-bottom: 15px;
    }
    .step-title {
        color: #1B4429 !important; /* Green text inside white card */
        font-weight: bold;
        font-size: 1.3rem;
        margin-bottom: 10px;
    }
    .step-text {
        color: #4A4A4A !important; /* Dark grey text inside white card */
        font-size: 1rem;
        line-height: 1.5;
    }
    
    /* Button Tweaks */
    .stButton button {
        border-radius: 8px;
        border: 1px solid white; 
    }
    /* Primary button style tweak to fit dark theme */
    div[data-testid="stButton"] > button[kind="primary"] {
        background-color: #FFD700;
        border: none;
    }
    div[data-testid="stButton"] > button[kind="primary"] p {
        color: #1B4429 !important; /* Force green text on inner p tag */
    }
    div[data-testid="stButton"] > button[kind="primary"]:hover {
        background-color: #E6C200;
    }
    /* Secondary button style tweak to fit dark theme */
    div[data-testid="stButton"] > button[kind="secondary"] {
        background-color: white;
        border: none;
    }
    div[data-testid="stButton"] > button[kind="secondary"]:hover {
        background-color: #F0F0F0;
    }
    div[data-testid="stButton"] > button[kind="secondary"] p {
        color: #1B4429 !important; /* Force green text on inner p tag */
    }
    
    /* Divider */
    hr {
        border-color: rgba(255,255,255,0.2);
    }
</style>
""", unsafe_allow_html=True)

from utils import common as utils

# --- Header Section (With Included Base64 Logo) ---
logo_path = utils.get_asset_path('logo-jaccueille-singa.png')
logo_b64 = utils.get_base64_image(logo_path)
# Increased width to 240px (3x original 80px)
logo_img_tag = f'<img src="data:image/png;base64,{logo_b64}" width="240" style="margin-bottom: 15px;">' if logo_b64 else ''

header_html = f"""
<div class="header-container">
    {logo_img_tag}
    <h1 style="margin:0;">Bienvenue sur OD&IS</h1>
    <p style="margin-top:15px;">L'outil d'aide à la mobilité pour l'intégration des personnes réfugiées.</p>
</div>
"""
st.markdown(header_html, unsafe_allow_html=True)


# --- Input & Navigation Section ---
st.subheader("Un outil, deux ambiances (votre choix)", divider="yellow",  width="stretch")

col_form, col_ia = st.columns(2, gap="large")

with col_form:
    st.markdown("""
        <div class="step-card" style="min-height: 250px; justify-content: space-between; padding: 30px;">
            <div>
                <div style="font-size: 3rem; margin-bottom: 15px;">📋</div>
                <h3 style="font-weight: bold; font-size: 1.5rem; margin-bottom: 10px;">Entretien Classique</h3>
                <div style="font-size: 1.1rem; margin-bottom: 20px;">
                    Remplissez un formulaire détaillé étape par étape pour construire le profil et affiner le projet de vie avec la personne.
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    # We place the inputs and buttons outside the custom HTML card to use Streamlit's native interactivity easily,
    # but we can wrap it in a container to visually attach it if needed, or simply place it right below.
    with st.container(border=True):
        # person_name_input = st.text_input(
        #     "Nom (optionnel)", 
        #     placeholder="Ex: Jean",
        #     value=st.session_state.get('ui_nom', ''),
        #     label_visibility="collapsed"
        # )
        if st.button("Démarrer l'entretien", type="primary", use_container_width=True, key="btn_classic"):
            # st.session_state.ui_nom = person_name_input
            st.switch_page("pages/2_Formulaire.py")

with col_ia:
    st.markdown("""
        <div class="step-card" style="min-height: 250px; justify-content: space-between; padding: 30px;">
            <div>
                <div style="font-size: 3rem; margin-bottom: 15px;">🤖</div>
                <h3 style="font-weight: bold; font-size: 1.5rem; margin-bottom: 10px;">Assistant IA (Beta)</h3>
                <div style="font-size: 1.1rem; margin-bottom: 20px;">
                    Discutez naturellement et librement avec notre agent intelligent pour identifier les besoins et trouver les bassins de vie adéquats.
                </div>
            </div>
        </div>
    """, unsafe_allow_html=True)
    
    with st.container(border=True):
        #  st.write("") # Alignment spacer to match the input height on the left
         if st.button("Lancer l'Assistant IA", type="primary", use_container_width=True, key="btn_ia"):
             st.switch_page("pages/4_AI_Chatbot.py")

# st.markdown("<br><br>", unsafe_allow_html=True)
# col_skip1, col_skip2, col_skip3 = st.columns([1,2,1])
# with col_skip2:
#     if st.button("Passer directement aux résultats ➞", type="secondary", use_container_width=True):
#         st.switch_page("pages/3_Resultats.py")



st.markdown("---")

# --- "How it works" Steps ---
st.subheader("Comment ça marche ?", divider="yellow", width="stretch")

step_cols = st.columns(3, gap="medium")

steps = [
    {
        "num": "1",
        "title": "Identifier les besoins",
        "icon": "👤",
        "text": "Renseignez le profil et les besoins spécifiques de la personne."
    },
    {
        "num": "2",
        "title": "Choisir les critères",
        "icon": "⚙️",
        "text": "Sélectionnez les critères géographiques et sociaux importants."
    },
    {
        "num": "3",
        "title": "Explorer",
        "icon": "🗺️",
        "text": "Découvrez les territoires les plus accueillants correspondant au profil."
    }
]

for col, step in zip(step_cols, steps):
    with col:
        st.markdown(f"""
        <div class="step-card">
            <div class="step-number">{step['num']}</div>
            <div style="font-size: 2.5rem; margin-bottom: 10px;">{step['icon']}</div>
            <div class="step-title">{step['title']}</div>
            <p class="step-text">{step['text']}</p>
        </div>
        """, unsafe_allow_html=True)

st.divider()

# --- Footer ---
st.markdown(
    """
    <div style='text-align: center; color: #a0c0a0; font-size: 0.8em; margin-top: 20px;'>
        OD&IS - Outil de Données & Intégration Sociale<br>
        Les données collectées sont utilisées uniquement pour cette session.
    </div>
    """,
    unsafe_allow_html=True
)
