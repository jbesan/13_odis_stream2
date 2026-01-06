
import streamlit as st
import os
import time
from gemini_client import OdisAgent
# from ui import inject_custom_css
import json
import logging
logger = logging.getLogger(__name__)

st.set_page_config(page_title="Assistant ODIS", page_icon="🤖", layout="wide")

# --- Authentication ---
import auth
if not auth.check_password():
    st.stop()

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
    
    st.info("Cet assistant utilise Gemini et vos données ODIS locales.")
    
    # Model Selection
    model_options = {
        "gemini-2.5-flash-lite": "Gemini 2.5 Flash Lite",
        "gemini-2.5-flash": "Gemini 2.5 Flash",
        "gemini-3-flash-preview": "Gemini 3.0 Flash"
    }
    
    selected_model_id = st.selectbox(
        "Modèle Gemini",
        options=list(model_options.keys()),
        format_func=lambda x: model_options[x],
        index=0,
        help="Choisissez le modèle de langage pour l'assistant."
    )
    
    # Handle model change: Reset agent if model ID changes
    if "current_model_id" not in st.session_state:
        st.session_state.current_model_id = selected_model_id
        
    if st.session_state.current_model_id != selected_model_id:
        st.session_state.current_model_id = selected_model_id
        st.session_state.chat_history = []
        st.session_state.agent = None
        st.rerun()

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
        st.session_state.agent = OdisAgent(api_key=api_key, model_id=selected_model_id)
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
    elif msg["role"] == "sources":
        with st.chat_message("assistant"):
            with st.expander("🌐 Sources & Vérifications", expanded=False):
                st.markdown(msg["content"])

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
                    # 1. Handle Function Calls (Maps / ODIS)
                    if part.function_call:
                        fc = part.function_call
                        fn_name = fc.name
                        
                        emoji = "🛠️"
                        if "place" in fn_name.lower() or "route" in fn_name.lower(): emoji = "🗺️"
                        st.toast(f"{emoji} Appel Google Maps : `{fn_name}`", icon=emoji)
                        
                        st.session_state.chat_history.append({
                            "role": "tool_call",
                            "name": fn_name,
                            "content": json.dumps(dict(fc.args), indent=2, ensure_ascii=False)
                        })
                    
                    # 2. Handle Text Response
                    if part.text:
                        st.session_state.chat_history.append({"role": "assistant", "content": part.text})
                        display_message("assistant", part.text)
                
                # 3. Handle Grounding Metadata (Native Search)
                if response.candidates and response.candidates[0].grounding_metadata:
                    meta = response.candidates[0].grounding_metadata
                    if meta.search_entry_point:
                        st.toast("🔍 Grounding Google Search actif", icon="🔍")
                        sources_md = f"🔎 **Sources Google Search** :\n\n{meta.search_entry_point.rendered_content}"
                        st.session_state.chat_history.append({"role": "sources", "content": sources_md})
                        display_message("sources", sources_md)
                    
                    if meta.grounding_chunks:
                        sources_text = "#### Sources consultées :\n"
                        for i, chunk in enumerate(meta.grounding_chunks):
                            if chunk.web:
                                sources_text += f"- [{chunk.web.title}]({chunk.web.uri})\n"
                        
                        st.session_state.chat_history.append({"role": "sources", "content": sources_text})
                        with st.chat_message("assistant"):
                            with st.expander("🌐 Sources & Vérifications", expanded=False):
                                st.markdown(sources_text)
                        
        except Exception as e:
            st.error(f"Erreur de génération: {e}")
            logger.error(f"Generate Error: {e}", exc_info=True)

