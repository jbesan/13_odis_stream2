import time
import streamlit as st
import config as cfg
import logging
from utils import memory, auth, data_loader
from utils import common as utils


# --- Page Configuration ---
st.set_page_config(page_title="OD&IS", page_icon="👋", layout="wide")

# --- RESET: Clear search memory when returning to home ---
if "search_results" in st.session_state:
    memory.reset_app_state()

# --- Authentication ---

from services import telemetry
from ui import components as ui_comp

if not auth.check_password():
    st.stop()

telemetry.log_page_view("Accueil")

logging.info(f"--- App re-run at {time.ctime(time.time())} ---")

# --- Main App Execution ---
data_loader.ensure_data_initialized()

# --- Sidebar / Org Context ---
with st.sidebar:
    if len(st.query_params) > 0 and "demo" in st.query_params:
        if st.button("Quitter Mode Démo", key="quit_demo"):
            st.query_params.clear()
            st.rerun()

    # Always show logo and badge in sidebar if org is active
    org = st.session_state.get("org")
    if org:
        if org.id == "jaccueille":
            logo_path = utils.get_asset_path("logo-jaccueille-singa.png")
            logo_b64 = utils.get_base64_image(logo_path)
            if logo_b64:
                st.markdown(
                    f'<img src="data:image/png;base64,{logo_b64}" width="150" style="margin-bottom: 20px;">',
                    unsafe_allow_html=True,
                )

    ui_comp.render_admin_sidebar_link()



# --- CSS / Styling (V3 Global Green) ---
st.markdown(
    """
<style>
    /* Global App Styling */
    .stApp {
        background-color: #1B4429;
        color: white;
    }
    
    # /* Text Color Overrides for specific Streamlit elements if needed */
    # .stMarkdown, .stText, h1, h2, h3, p, label {
    #     color: white !important;
    # }
    
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
""",
    unsafe_allow_html=True,
)


# --- Header Section (With Included Base64 Logo) ---
logo_path = utils.get_asset_path("logo-jaccueille-singa.png")
logo_b64 = utils.get_base64_image(logo_path)
# Increased width to 240px (3x original 80px)
logo_img_tag = (
    f'<img src="data:image/png;base64,{logo_b64}" width="240" style="margin-bottom: 15px;">'
    if logo_b64
    else ""
)

header_html = f"""
<div class="header-container">
    {logo_img_tag}
    <h1 style="margin:0;">Bienvenue sur OD&IS</h1>
    <p style="margin-top:15px;">L'outil d'aide à la mobilité pour l'intégration des personnes réfugiées.</p>
</div>
"""
st.markdown(header_html, unsafe_allow_html=True)


# --- Input & Navigation Section ---
if cfg.is_ai_free_mode():
    st.subheader("Entrée de données", divider="yellow", width="stretch")

    st.markdown(
        """
        <div class="step-card" style="min-height: 250px; justify-content: space-between; padding: 30px;">
            <div>
                <div style="font-size: 3rem; margin-bottom: 15px;">📋</div>
                <h3 style="font-weight: bold; font-size: 1.5rem; margin-bottom: 10px;">Entretien Classique</h3>
                <div style="font-size: 1.1rem; margin-bottom: 20px;">
                    Renseignez les informations de la personne accompagnée à l'aide d'un formulaire structuré.
                </div>
            </div>
        </div>
    """,
        unsafe_allow_html=True,
    )

    with st.container(border=True):
        if st.button(
            "Démarrer l'entretien", type="primary", width="stretch", key="btn_classic"
        ):
            st.switch_page("pages/2_Formulaire.py")
else:
    st.subheader(
        "Un outil, deux ambiances (votre choix)", divider="yellow", width="stretch"
    )

    col_form, col_ia = st.columns(2, gap="large")

    with col_form:
        st.markdown(
            """
            <div class="step-card" style="min-height: 250px; justify-content: space-between; padding: 30px;">
                <div>
                    <div style="font-size: 3rem; margin-bottom: 15px;">📋</div>
                    <h3 style="font-weight: bold; font-size: 1.5rem; margin-bottom: 10px;">Entretien Classique</h3>
                    <div style="font-size: 1.1rem; margin-bottom: 20px;">
                        Remplissez un formulaire détaillé étape par étape pour construire le profil et affiner le projet de vie avec la personne.
                    </div>
                </div>
            </div>
        """,
            unsafe_allow_html=True,
        )

        # We place the inputs and buttons outside the custom HTML card to use Streamlit's native interactivity easily,
        # but we can wrap it in a container to visually attach it if needed, or simply place it right below.
        with st.container(border=True):
            if st.button(
                "Démarrer l'entretien",
                type="primary",
                width="stretch",
                key="btn_classic_two_col",
            ):
                st.switch_page("pages/2_Formulaire.py")

    @st.dialog("Analyse de votre document", width="large")
    def show_unstructured_input_dialog():
        st.text(
            "Collez ici un texte décrivant la situation (email, notes d'entretien, export CRM...) :"
        )

        text_input = st.text_area(
            "Texte source",
            height="content",
            width="stretch",
            placeholder="Ex: J'accompagne une famille de 4 personnes (2 adultes, 2 enfants en primaire). Ils cherchent un logement social à Bordeaux...",
            label_visibility="collapsed",
        )

        col1, col2 = st.columns([3, 1])

        analysis_key = "unstructured_analysis_result"

        with col1:
            if st.button(
                "Détecter les critères de recherche", type="primary", width="content"
            ):
                if not text_input.strip():
                    st.warning("Veuillez saisir du texte avant de lancer l'analyse.")
                else:
                    with st.spinner("Analyse en cours par l'agent Extracteur..."):
                        try:
                            from agents.utils import run_autodetect_safe

                            result_data = run_autodetect_safe(text_input)

                            st.session_state[analysis_key] = {
                                "response": result_data.response,
                                "criteria": result_data.search_criteria,
                            }
                            telemetry.log_usage_event(
                                "auto_detect_criteria",
                                {"text_length": len(text_input)},
                            )
                        except Exception as e:
                            st.error(f"Une erreur est survenue lors de l'analyse : {e}")

        if analysis_key in st.session_state:
            st.divider()
            st.text("Données pertinentes identifiées")
            st.info(st.session_state[analysis_key]["response"])

            if st.button(
                "✅ Confirmer et Pré-remplir le formulaire",
                type="primary",
                width="stretch",
            ):
                # Apply the criteria to the UI session states
                data_loader.apply_search_criteria_to_ui(
                    st.session_state[analysis_key]["criteria"]
                )

                # Reset the dialog state for next time
                del st.session_state[analysis_key]

                # Switch to form
                st.switch_page("pages/2_Formulaire.py")

    with col_ia:
        st.markdown(
            """
            <div class="step-card" style="min-height: 250px; justify-content: space-between; padding: 30px;">
                <div>
                    <div style="font-size: 3rem; margin-bottom: 15px;">⚡️</div>
                    <h3 style="font-weight: bold; font-size: 1.5rem; margin-bottom: 10px;">Auto Détection</h3>
                    <div style="font-size: 1.1rem; margin-bottom: 20px;">
                        Copiez-collez un email ou un document texte décrivant la situation et les besoins de la personne accompagnée, on essaiera de pré-remplire le fomulaire pour vous.
                    </div>
                </div>
            </div>
        """,
            unsafe_allow_html=True,
        )

        with st.container(border=True):
            if st.button(
                "Démarrer Auto-Detect", type="primary", width="stretch", key="btn_ia"
            ):
                show_unstructured_input_dialog()


# st.markdown("<br><br>", unsafe_allow_html=True)
# col_skip1, col_skip2, col_skip3 = st.columns([1,2,1])
# with col_skip2:
#     if st.button("Passer directement aux résultats ➞", type="secondary", width="stretch"):
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
        "text": "Renseignez le profil et les besoins spécifiques de la personne.",
    },
    {
        "num": "2",
        "title": "Calcul des scores",
        "icon": "⚙️",
        "text": "Identifiez les territoires les plus pertinents au regard du projet de vie.",
    },
    {
        "num": "3",
        "title": "Exploration & Synthèse",
        "icon": "🗺️",
        "text": "Découvrez les territoires les plus accueillants correspondant au profil.",
    },
]

for col, step in zip(step_cols, steps):
    with col:
        st.markdown(
            f"""
        <div class="step-card">
            <div class="step-number">{step["num"]}</div>
            <div style="font-size: 2.5rem; margin-bottom: 10px;">{step["icon"]}</div>
            <div class="step-title">{step["title"]}</div>
            <p class="step-text">{step["text"]}</p>
        </div>
        """,
            unsafe_allow_html=True,
        )

st.divider()

# --- Footer ---
st.markdown(
    """
    <div style='text-align: center; color: #a0c0a0; font-size: 0.8em; margin-top: 20px;'>
        OD&IS - Outil de Données & Intégration Sociale<br>
        Les données collectées sont utilisées uniquement pour cette session.
    </div>
    """,
    unsafe_allow_html=True,
)
