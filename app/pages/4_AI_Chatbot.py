
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
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error("Clé API GEMINI non trouvée.")

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
    from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx

    ctx = get_script_run_ctx()
    
    def wrapper(coro):
        if ctx:
            add_script_run_ctx(threading.current_thread(), ctx)
        return asyncio.run(coro)

    import threading
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(wrapper, coro)
        return future.result()

@st.cache_resource
def get_shared_client():
    """Retourne un nouveau client genai."""
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

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
if prompt := st.chat_input("Répondez ici...", key="chat_input"):
    # 1. User Message
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    display_message("user", prompt)
    
    # 2. Agent Response
    with st.spinner("L'Agent ODIS réfléchit... (et ça peut prendre un moment... 😴)"):
        # Pre-process state in main thread
        st.session_state.agent_state.messages.append({"role": "user", "content": prompt})
        state_to_send = st.session_state.agent_state

        async def run_logic(input_data: dict):
            """Orchestration de l'exécution du graphe (Thread Worker)."""
            import random
            from agents.utils import AGENT_TOASTS
            
            try:
                # 1. Re-validate to get a fresh model instance (handles redefinitions)
                input_state = ODISGraphState.model_validate(input_data)

                # 2. Instantiate client inside the thread's loop
                client = get_shared_client()
                
                # 3. Pass deps via config
                deps = ODISDeps(state=input_state, client=client)
                config = {"configurable": {"deps": deps}}

                # 4. Exécution du graphe via astream_events pour capturer le démarrage des noeuds
                final_state = input_state
                async for event in odis_graph.astream_events(input_state, config=config, version="v2"):
                    kind = event.get("event")
                    
                    # Détection du démarrage d'un noeud (node)
                    if kind == "on_chain_start":
                        # Les noeuds LangGraph sont des chaines. Le nom est dans metadata.
                        node_name = event.get("metadata", {}).get("langgraph_node")
                        if node_name:
                            # Normalisation pour gérer les noeuds "solo" (ex: scout_solo -> scout)
                            base_node = node_name.replace("_solo", "")
                            if base_node in AGENT_TOASTS:
                                info = AGENT_TOASTS[base_node]
                                msg = random.choice(info["messages"])
                                st.toast(msg, icon=info["emoji"])
                    
                    # Mise à jour de l'état final à la fin de la chaine globale
                    if kind == "on_chain_end" and event.get("name") == "LangGraph":
                        final_state = event.get("data", {}).get("output")

                return final_state
            except Exception as e:
                logger.error(f"❌ Graph Error: {e}", exc_info=True)
                raise e

        try:
            # Exécution DANS UN THREAD SÉPARÉ - On passe l'état en argument sous forme de dict (Sanitization)
            final_output = run_async_in_thread(run_logic(state_to_send.model_dump()))
            
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

# --- Focus Retention (JS) ---
import streamlit.components.v1 as components
components.html(
    """
    <script>
    const input = window.parent.document.querySelector('textarea[data-testid="stChatInputTextArea"]');
    if (input) {
        // We use a small delay to ensure Streamlit has finished rendering
        setTimeout(() => {
            input.focus();
        }, 100);
    }
    </script>
    """,
    height=0,
    width=0,
)

