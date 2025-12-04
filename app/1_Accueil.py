import time
import copy
import streamlit as st
import config  as cfg
import data_loader
import logging
import logger # Initialize logging configuration

st.set_page_config(
    page_title="J'accueille",
    page_icon="👋",
)

logging.info(f"--- App re-run at {time.ctime(time.time())} ---")

st.set_page_config(layout="wide", page_title='OD&IS: Recherche Inversée')


# --- Main App Execution ---
# --- Main App Execution ---
data_loader.ensure_data_initialized()

# Display demo mode exit button if in demo mode
if len(st.query_params) > 0 and 'demo' in st.query_params:
    if st.sidebar.button('Quitter Mode Démo', key='quit_demo'):
        st.query_params.clear()
        st.rerun()
    st.markdown('<style> .st-emotion-cache-16txtl3 {position:relative; top:80vh}</style>', unsafe_allow_html=True)

# Let's center all the text on this page
st.markdown(
    """
    <style>
    .stApp {
        text-align: center;
    }
    </style>
    """,
    unsafe_allow_html=True
)
# st.image('./images/logo-jaccueille-singa.png', width=250)

st.markdown("""
            <style>
                .st-key-header_accueil {background-color: #1B4429; color: white; padding:30px; border-radius:30px}
                .st-key-lancement_formulaire .stButton p {color: #1B4429; font-size:1.3rem;}
                .st-key-lancement_formulaire .stButton div {margin: 0px;}
            </style>
            """
        , unsafe_allow_html=True)


with st.container(width="stretch", key="header_accueil", horizontal_alignment="center"):
    st.image('images/logo-jaccueille-singa.png', width=200)

    st.title("Bienvenue sur OD&IS")
    st.markdown("L'outil d'aide à la mobilité pour l'intégration des personnes réfugiées.")
    st.markdown("\n")

    col1, col2 = st.columns([5,4])
    with col1:
        st.text("")
        st.text("")
        st.markdown('<div style="text-align: right;">Nom de la personne accompagnée (optionnel)</div>', unsafe_allow_html=True)
    with col2:
        person_name_input = st.text_input(label="nom", width=150, value=st.session_state.ui_nom)

    with st.container(horizontal_alignment="center"):

        if st.button("Commencer le parcours de recherche", type="secondary", key="lancement_formulaire"):
            st.session_state.ui_nom = person_name_input
            st.switch_page("pages/2_Formulaire.py")

        if st.button("Aller directement à la page résultats", type="tertiary"):
            st.switch_page("pages/3_Resultats.py")

st.markdown("\n")
st.image('images/explications_acceuil.png', width="stretch")

st.markdown("\n")
st.markdown("L'outil respecte la conformité RGPD. Les données collectées sont utilisées uniquement pour fournir le résultat affiché à l'utilisateur et ne sont pas stockées sur nos serveurs.")
