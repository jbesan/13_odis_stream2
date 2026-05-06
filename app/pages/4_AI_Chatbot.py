import streamlit as st
import os
import time
import asyncio
import json
import logging
from dotenv import load_dotenv
from typing import List, Dict, Any
from google import genai
from google.genai import types

# Ensure environment is loaded
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(base_dir, ".env")
load_dotenv(env_path)

# Patch variable d'env pour PydanticAI
if "GOOGLE_API_KEY" not in os.environ and "GEMINI_API_KEY" in os.environ:
    os.environ["GOOGLE_API_KEY"] = os.getenv("GEMINI_API_KEY")

from agents.graph import create_odis_graph
from agents.state import ODISDeps, ODISGraphState
from services.telemetry import get_interaction_id, reset_interaction_id

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Assistant ODIS", page_icon="🤖", layout="wide")

# --- Authentication ---
from utils import auth
if not auth.check_password():
    st.stop()

st.markdown("## Assistant IA 🤖 ODIS 2.2")
from utils import data_loader
from services import mcp_server

with st.spinner("Chargement des données ODIS..."):
    app_data = data_loader.get_app_data()
    mcp_server.set_data_context(app_data)
st.markdown("Identifions ensemble le projet de vie et les meilleures options de relocalisation.")

# --- Sidebar ---
with st.sidebar:
    if st.button("🏠 Retour à l'Accueil", width="stretch"):
        st.switch_page("pages/1_Accueil.py")
    
 
    st.markdown("""
        <style>
            .st-key-btn_recommencer .stButton p {color: #1B4429;}
        </style>
    """, unsafe_allow_html=True)
    if st.button("Nouvelle Discussion", type="primary", key="btn_recommencer", width="stretch"):
        print("="*150)
        st.session_state.chat_history = []
        new_interaction_id = reset_interaction_id()  # Generate a fresh interaction ID
        st.session_state.agent_state = ODISGraphState(
            interaction_id=new_interaction_id,
            username=st.session_state.get("username", "unknown")
        )
        st.rerun()

    from ui import feedback
    st.write("")
    feedback.render_feedback_button()
    
    st.divider()
    st.checkbox("⚙️ Infos techniques", value=False, key="show_tech_info")

    api_key = os.getenv("GOOGLE_API_KEY") # Utilisation cohérente de GEMINI_API_KEY
    if not api_key:
        st.error("Clé API GEMINI non trouvée.")

    # Read tech info visibility from session state (set by checkbox at the bottom)
    show_tech = st.session_state.get("show_tech_info", False)

    # API Usage Tracking
    if st.session_state.get("agent_state") and show_tech:
        state = st.session_state.agent_state
        with st.expander("📊 Consommation LLM", expanded=False):
            u = state.usage
            m1, m2 = st.columns(2)
            m1.metric("Tokens", f"{u.total_tokens:,}".replace(",", " "))
            m2.metric("Coût", f"${u.cost_usd:.4f}")
        
        # Detailed breakdown
        breakdown = getattr(u, 'breakdown', {})
        if breakdown:
            with st.expander("💸 Détails par Modèle & Agent", expanded=False):
                by_model: Dict[str, Any] = {}
                for node, data in breakdown.items():
                    mid = data['model']
                    if mid not in by_model:
                        by_model[mid] = {"cost": 0.0, "nodes": []}
                    by_model[mid]["cost"] += data['cost']
                    by_model[mid]["nodes"].append((node, data))

                for mid, info in by_model.items():
                    st.markdown(f"📦 **{mid}** : `${info['cost']:.4f}`")
                    for node, data in info["nodes"]:
                        st.caption(f"└ {node} : {data['total']} t ({data['input']} / {data['output']}) - ${data['cost']:.4f}")
                    st.divider()

        with st.expander("⚙️ État Agent", expanded=False):
            st.json(state.model_dump(mode='json', exclude={'search_results'})) # Optimisation affichage

        with st.expander("⚙️ Briefing", expanded=False):
            st.write(state.odis_brief)

        criteria_hash = state.criteria_hash
        
        if state.focus_city and state.focus_city.name:
            st.write(f"Ville cible: **{state.focus_city.name}**")
            for agent in ['scout', 'web', 'job_hunter']:
                with st.expander(f"⚙️ {agent.upper()} Results", expanded=False):
                    try:
                        if state.search_results:
                            res = state.search_results.get_by_code(state.focus_city.codgeo)
                            if not res and state.focus_city.name:
                                norm_name = state.focus_city.name.lower().strip()
                                res = next((r for r in state.search_results.results if r.name.lower().strip() == norm_name), None)
                            
                            if res and res.expert_analysis and agent in res.expert_analysis:
                                st.write(res.expert_analysis[agent])
                            else:
                                st.write("Pas de données.")
                        else:
                            st.write("Pas de résultats de recherche disponibles.")
                    except Exception as e:
                        st.write(f"Erreur: {e}")
    
    if st.session_state.get("agent_state") and not show_tech:
        # Show a minimal summary if hidden? No, just keep it clean.
        pass

# --- Helpers ---
def display_message(role, content):
    with st.chat_message(role):
        st.markdown(content)

# --- 🛠️ ASYNC HANDLING (Cloud Run & Streamlit Safe) ---
from agents.utils import run_async_safe

# --- Session State ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "agent_state" not in st.session_state:
    st.session_state.agent_state = ODISGraphState(
        interaction_id=get_interaction_id(),
        username=st.session_state.get("username", "unknown")
    )

# --- Chat Interface ---

# Display History
for msg in st.session_state.chat_history:
    display_message(msg["role"], msg["content"])

# Input
if prompt := st.chat_input("Répondez ici...", key="chat_input"):
    # 1. User Message
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    display_message("user", prompt)
    
    # 2. Agent Response
    with st.spinner("L'Agent ODIS réfléchit..."):
        response_text = None
        try:
            # Préparation
            current_state = st.session_state.agent_state
            # Refresh identifiers on every turn to ensure they are always current
            current_state.interaction_id = get_interaction_id()
            current_state.username = st.session_state.get("username", "unknown")
            current_state.messages.append({"role": "user", "content": prompt})
            
            logger.info(f"💁 [USER] message: {prompt[:50]}")
            
            # Capture identifiers before async call (thread-safe snapshot)
            _interaction_id = current_state.interaction_id
            _username = current_state.username

            # 🚀 LANCEMENT ASYNC SECURISE
            final_output = run_async_safe(current_state.model_dump())
            
            # Mise à jour Session
            st.session_state.agent_state = ODISGraphState.model_validate(final_output)
            
            # Extraction Réponse
            if st.session_state.agent_state.messages:
                last_msg = st.session_state.agent_state.messages[-1]
                if last_msg["role"] == "assistant":
                    response_text = last_msg["content"]
            
            if not response_text:
                response_text = "Je n'ai pas pu générer de réponse."

            # Log State to BigQuery — pass identifiers explicitly to avoid thread-safety issues
            try:
                from services import bq_logger
                bq_logger.log_agent_state_to_bq(
                    prompt, final_output,
                    interaction_id=_interaction_id,
                    username=_username
                )
            except Exception as bq_e:
                logging.warning(f"Failed to log BQ state: {str(bq_e)}")

        except Exception as e:
            logging.getLogger(__name__).exception(f"⚠️ Erreur technique : {str(e)}")
            response_text = f"⚠️ Erreur technique : {str(e)}"
            with st.expander("Détails de l'erreur"):
                st.error(str(e))

        # 3. Affichage Réponse
        st.session_state.chat_history.append({"role": "assistant", "content": response_text})
        display_message("assistant", response_text)
        
        # Petit délai pour UI smooth
        time.sleep(0.1)
        st.rerun()

# --- Focus Retention (JS) ---
import streamlit.components.v1 as components
components.html(
    """
    <script>
    const input = window.parent.document.querySelector('textarea[data-testid="stChatInputTextArea"]');
    if (input) {
        setTimeout(() => { input.focus(); }, 100);
    }
    </script>
    """,
    height=0,
    width=0,
)