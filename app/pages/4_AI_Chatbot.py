
import streamlit as st
import os
import time
import asyncio
import json
import logging
from typing import List, Dict, Any
from google import genai

from agents.graph import create_odis_graph
from agents.state import ODISDeps, ODISGraphState

logger = logging.getLogger(__name__)

st.set_page_config(page_title="Assistant ODIS", page_icon="🤖", layout="wide")

# --- Authentication ---
from utils import auth
if not auth.check_password():
    st.stop()

st.title("🤖 Assistant ODIS 2.1")

# Ensure datasets are loaded
from utils.data_loader import init_datasets
from services import mcp_server

with st.spinner("Chargement des données ODIS..."):
    app_data = init_datasets()
    mcp_server.set_data_context(app_data)
st.markdown("**Assistant pour travailleurs sociaux** - Aide à la décision et recherche multicritères.")

# --- Sidebar ---
with st.sidebar:
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error("Clé API Google non trouvée.")

    # API Usage Tracking
    if st.session_state.get("agent_state"):
        state = st.session_state.agent_state
        st.subheader("📊 Consommation LLM")
        u = state.usage
        
        # Simple summary
        m1, m2 = st.columns(2)
        m1.metric("Tokens", f"{u.total_tokens:,}".replace(",", " "))
        m2.metric("Coût", f"${u.cost_usd:.4f}")
        
        # Detailed breakdown
        breakdown = getattr(u, 'breakdown', {})
        if breakdown:
            with st.expander("💸 Détails par Modèle & Agent", expanded=False):
                # Grouping by model for a better view
                by_model = {}
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

        with st.expander("⚙️ État Interne", expanded=False):
            st.json(state.search_criteria)

    st.divider()
    st.markdown("""
        <style>
            .st-key-btn_recommencer .stButton p {color: #1B4429;}
        </style>
        """,
        unsafe_allow_html=True
    )
    if st.button("Recommencer", type="primary", key="btn_recommencer"):
        st.session_state.chat_history = []
        st.session_state.agent_state = ODISGraphState()
        print("################################## NEW CONVERSATION ##################################")
        st.rerun()

# --- Helpers ---
def display_message(role, content):
    with st.chat_message(role):
        st.write(content)

def run_async_in_thread(coro):
    """
    Exécute une coroutine dans un thread séparé avec une boucle d'événement dédiée.
    Cela permet d'isoler l'exécution asynchrone (PydanticAI/LangGraph) du thread Streamlit.
    """
    import concurrent.futures
    import asyncio
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()

@st.cache_resource
def get_shared_client():
    """Retourne un nouveau client genai."""
    return genai.Client(api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

# --- Session State ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

if "agent_state" not in st.session_state:
    st.session_state.agent_state = ODISGraphState()

# --- Graph Initialization ---
# Module level initialization is safer and more standard
odis_graph = create_odis_graph()

# --- Chat Interface ---

# Display History
for msg in st.session_state.chat_history:
    display_message(msg["role"], msg["content"])

# Input
if prompt := st.chat_input("Bonjour, qui accompagnez-vous aujourd'hui ?" if not st.session_state.chat_history else "Répondez-ici"):
    # 1. User Message
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    display_message("user", prompt)
    
    # 2. Agent Response
    with st.spinner("L'Orchestrateur ODIS réfléchit..."):
        # Pre-process state in main thread
        st.session_state.agent_state.messages.append({"role": "user", "content": prompt})
        state_to_send = st.session_state.agent_state

        async def run_logic(input_state: ODISGraphState):
            """Orchestration de l'exécution du graphe (Thread Worker)."""
            try:
                # Instantiate client inside the thread's loop
                client = get_shared_client()
                
                # Pass deps via config
                deps = ODISDeps(state=input_state, client=client)
                config = {"configurable": {"deps": deps}}

                # Exécution du graphe
                return await odis_graph.ainvoke(input_state, config=config)
            except Exception as e:
                logger.error(f"❌ Graph Error: {e}", exc_info=True)
                raise e

        try:
            # Exécution DANS UN THREAD SÉPARÉ - On passe l'état en argument
            final_output = run_async_in_thread(run_logic(state_to_send))
            
            # Mise à jour de l'état (Main Thread)
            st.session_state.agent_state = ODISGraphState.model_validate(final_output)
            
            # Récupération de la dernière réponse assistant
            response_text = None
            if st.session_state.agent_state.messages:
                last_msg = st.session_state.agent_state.messages[-1]
                if last_msg["role"] == "assistant":
                    response_text = last_msg["content"]
        except Exception as e:
             response_text = f"Désolé, une erreur technique est survenue : {e}"

        # 3. UI Update (Main Thread)
        if response_text:
            st.session_state.chat_history.append({"role": "assistant", "content": response_text})
            display_message("assistant", response_text)
            
            # Show expert details if available
            if st.session_state.agent_state.experts_results:
                with st.expander("🔍 Expertise ODIS (Détails)"):
                    st.json(st.session_state.agent_state.experts_results)
        
        st.rerun()

