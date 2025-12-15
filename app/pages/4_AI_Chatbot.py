
import streamlit as st
import os
import time
from gemini_client import OdisAgent
# from ui import inject_custom_css
import json

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
            # In V1 SDK, response.parts might contain function_call
            if response.parts:
                for part in response.parts:
                    if part.function_call:
                        fc = part.function_call
                        fn_name = fc.name
                        fn_args = dict(fc.args)
                        
                        # Store Tool Call in History
                        st.session_state.chat_history.append({
                            "role": "tool_call",
                            "content": json.dumps(fn_args, indent=2, ensure_ascii=False)
                        })
                        
                        # Show Confirmation UI
                        with st.chat_message("assistant"):
                            st.info(f"🛠️ **L'assistant souhaite effectuer un calcul ODIS**")
                            st.json(fn_args)
                            st.write("Confirmez-vous le lancement de la recherche ? (Cela peut prendre quelques secondes)")
                            
                            # We can't pause execution here easily in Streamlit script flow 
                            # without rerun or using a callback form.
                            # But we are in a loop.
                            # Hack: We Auto-execute for now? No, User wanted safe-guards.
                            # Solution: We define a state 'pending_tool_confirmation'
                            
                            st.session_state.pending_tool_call = {
                                "name": fn_name,
                                "args": fn_args
                            }
                            st.session_state.last_response_parts = response.parts
                            st.rerun()
                            
                    elif part.text:
                        st.session_state.chat_history.append({"role": "assistant", "content": part.text})
                        display_message("assistant", part.text)
                        
        except Exception as e:
            st.error(f"Erreur de génération: {e}")

# --- Pending Confirmation Handling ---
if "pending_tool_call" in st.session_state and st.session_state.pending_tool_call:
    tool_call = st.session_state.pending_tool_call
    
    with st.chat_message("assistant"):
        st.warning("⚠️ Confirmation requise")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Confirmer & Lancer"):
                st.session_state.pending_tool_call = None # Clear immediately to avoid loop if rerun happens differently
                
                with st.status("🔄 Exécution de le recherche...", expanded=True) as status:
                    try:
                        st.write("Calcul des scores...")
                        # Execute Tool
                        result = st.session_state.agent.execute_tool_local(
                            tool_call["name"], 
                            tool_call["args"]
                        )
                        st.write(f"Trouvé {len(result)} résultats.")
                        status.update(label="✅ Recherche terminée", state="complete", expanded=False)
                        
                        # Add Result to History
                        st.session_state.chat_history.append({
                            "role": "tool_result",
                            "content": json.dumps(result, default=str) # simplified
                        })
                        
                        # Feed back to Agent
                        final_response = st.session_state.agent.send_tool_response(
                            tool_call["name"],
                            result
                        )
                        
                        # Cleanup State
                        st.session_state.pending_tool_call = None
                        
                        # Display Final Response
                        if final_response.text:
                            st.session_state.chat_history.append({"role": "assistant", "content": final_response.text})
                        
                        st.rerun()
                        
                    except Exception as e:
                        st.error(f"Erreur lors de l'exécution: {e}")
                        
        with col2:
            if st.button("❌ Refuser"):
                st.session_state.pending_tool_call = None
                st.session_state.chat_history.append({"role": "assistant", "content": "(Action annulée par l'utilisateur)"})
                st.rerun()

