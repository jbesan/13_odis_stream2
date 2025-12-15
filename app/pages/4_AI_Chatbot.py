
import streamlit as st
import os
import time
from gemini_client import OdisAgent
# from ui import inject_custom_css
import json
import logging
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Assistant ODIS", page_icon="🤖", layout="wide")

# Inject global CSS
# inject_custom_css()

st.title("🤖 Assistant ODIS 2.0")

# --- Data Loading (Shared with App) ---
# Ensure datasets are loaded (cached) to prevent mcp_server from reloading them in isolation
from data_loader import init_datasets
import mcp_server

with st.spinner("Chargement des données ODIS..."):
    # This uses st.cache_resource, so it's fast if already loaded in Home
    app_data = init_datasets()
    # Inject into MCP Server so it uses the same memory
    mcp_server.set_data_context(app_data)
st.markdown("**Assistant pour travailleurs sociaux** - Aide à la décision et recherche multicritères.")

# --- Sidebar ---
with st.sidebar:
    st.header("Configuration")
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error("Clé API Google non trouvée en variable d'environnement.")
    
    st.info("Cet assistant utilise Gemini 2.0 et vos données ODIS locales.")
    if st.button("Réinitialiser la conversation"):
        st.session_state.chat_history = []
        st.session_state.agent = None
        st.rerun()

# --- Session State ---
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []
    
if "agent" not in st.session_state:
    st.session_state.agent = None

# Try to initialize if not done yet and key is available
if st.session_state.agent is None and api_key:
    try:
        st.session_state.agent = OdisAgent(api_key=api_key)
        st.session_state.agent.start_chat()
    except Exception as e:
        st.error(f"Erreur d'initialisation de l'agent: {e}")

if not st.session_state.agent:
    st.warning("Veuillez configurer la clé API ci-contre pour activer l'assistant.")

# --- Helpers ---
def display_message(role, content):
    with st.chat_message(role):
        st.write(content)

# --- Chat Interface ---

# Display History
for msg in st.session_state.chat_history:
    if msg["role"] == "user":
        display_message("user", msg["content"])
    elif msg["role"] == "assistant":
        display_message("assistant", msg["content"])
    elif msg["role"] == "tool_call":
        with st.chat_message("assistant"):
            st.info(f"🛠️ **Suggestion de Recherche**\n\n**Profil & Souhaits**:\n```json\n{msg['content']}\n```")
    elif msg["role"] == "tool_result":
        with st.chat_message("assistant"):
            st.success(f"✅ Calcul terminé ({len(json.loads(msg['content']))} résultats)")

# Input
if prompt := st.chat_input("Bonjour, qui accompagnez-vous aujourd'hui ?"):
    if not st.session_state.agent:
        st.error("Agent non initialisé.")
        st.stop()
        
    # 1. User Message
    st.session_state.chat_history.append({"role": "user", "content": prompt})
    display_message("user", prompt)
    
    # 2. Agent Response
    with st.spinner("Réflexion en cours..."):
        try:
            response = st.session_state.agent.send_message(prompt)
            
            # Check for Function Calls
            # Check for Function Calls
            # With SDK Auto-Execution, we expect the Agent to handle tool calls internally.
            # We just display the final text functionality or intermediate calls if exposed.
            # In google-genai V1, if auto-exec happens, response.parts usually contains the final answer (role='model').
            # Sometimes it might contain the function call history too?
            # We iterate and show everything.
            
            if response.parts:
                for part in response.parts:
                    # Log Function Calls if present (for transparency)
                    if part.function_call:
                        fc = part.function_call
                        fn_name = fc.name
                        fn_args = dict(fc.args)
                        
                        st.session_state.chat_history.append({
                            "role": "tool_call",
                            "content": json.dumps(fn_args, indent=2, ensure_ascii=False)
                        })
                        # Optional: Show a small toast/status that tool was called
                        st.caption(f"🔧 Utilisation de l'outil: `{fn_name}`")
                            
                    if part.text:
                        st.session_state.chat_history.append({"role": "assistant", "content": part.text})
                        display_message("assistant", part.text)
                        
        except Exception as e:
            st.error(f"Erreur de génération: {e}")
            logger.error(f"Generate Error: {e}", exc_info=True)

