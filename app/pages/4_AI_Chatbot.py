
import streamlit as st
import os
import time
from agents.orchestrator import MultiAgentOrchestrator
from agents.state import AgentContext
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
    # st.header("Configuration")
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        st.error("Clé API Google non trouvée en variable d'environnement.")
    
    # st.info("Cet assistant utilise Gemini et vos données ODIS locales.")
    
    
    
    # --- Token Counter Widget ---
    if st.session_state.get("agent"):
        st.subheader("AI Agent")
        
        # Model selection removed as it's now handled per-agent inside gemini_client
        with st.expander("Agents actifs", expanded=False):
            # st.session_state.agent.orchestrator
            for ragent,model in st.session_state.agent.orchestrator.models.items():
                st.text(f"{ragent}: {model}")
        
        with st.expander("📊 Consommation", expanded=False):
            ctx = st.session_state.agent.context
            
            col1, col2 = st.columns(2)
            with col1:
                st.metric("Envoyés", f"{ctx.total_tokens_sent:,}")
            with col2:
                st.metric("Reçus", f"{ctx.total_tokens_received:,}")
            
            # Granular Cost Calculation
            # Gemini 3: $0.50/M in, $3.00/M out
            # Gemini 2.5: $0.10/M in, $0.40/M out
            cost_g3 = (ctx.tokens_g3_input * 0.50 / 1_000_000) + (ctx.tokens_g3_output * 3.00 / 1_000_000)
            cost_g25 = (ctx.tokens_g25_input * 0.10 / 1_000_000) + (ctx.tokens_g25_output * 0.40 / 1_000_000)
            total_cost = cost_g3 + cost_g25
            
            st.metric("Coût estimé", f"{total_cost:.4f} $")
            
            st.text("Détails par modèle:")
            st.write(f"**Gemini 3**: {cost_g3:.4f}$")
            st.text(f"{ctx.tokens_g3_input:,} in / {ctx.tokens_g3_output:,} out")
            st.write(f"**Gemini 2.5**: {cost_g25:.4f}$")
            st.text(f"{ctx.tokens_g25_input:,} in / {ctx.tokens_g25_output:,} out")
    
        # Debug Criteria
        if st.session_state.get("agent"):
            with st.expander("Critères identifiés", expanded=False):
                st.json(st.session_state.agent.context.search_criteria)

        st.divider()
        st.markdown("""
        <style>
            .st-key-btn_recommencer .stButton p {color: #1B4429;}
        </style>
        """,
        unsafe_allow_html=True
        )
        if st.button("Recommencer",type="primary", key="btn_recommencer"):
            st.session_state.chat_history = []
            st.session_state.agent = None
            print("################################## NEW CONVERSATION ##################################")
            st.rerun()

# --- Core logic classes (Inlined) ---
class OdisAgent:
    def __init__(self, api_key: str, model_id: str = "gemini-2.5-flash"):
        self.orchestrator = MultiAgentOrchestrator(api_key)
        self.context = AgentContext()
        self.model_id = model_id

    def start_chat(self, history=None):
        self.context = AgentContext()
        if history:
            self.context.history = list(history)
        return self

    def send_message(self, message: str) -> Any:
        try:
            logger.info(f"🚀 [ODIS_AGENT] Passing message to Orchestrator...")
            response_text = self.orchestrator.process_message(message, self.context)
            
            class MockPart:
                def __init__(self, text): 
                    self.text = text
                    self.function_call = None
            
            class MockCandidate:
                def __init__(self):
                    self.grounding_metadata = None

            class MockResponse:
                def __init__(self, text, active_agent):
                    self.parts = [MockPart(text)]
                    self.candidates = [MockCandidate()]
                    self.active_agent = active_agent
            
            return MockResponse(response_text, self.context.active_agent)
        except Exception as e:
            logger.error(f"Orchestrator Error: {e}")
            raise e

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
                        # Add agent badge to the response if it changed or for clarity
                        agent_badge = f"🏷️ **Agent: {response.active_agent.upper()}**\n\n"
                        st.session_state.chat_history.append({"role": "assistant", "content": f"{agent_badge}{part.text}"})
                        display_message("assistant", f"{agent_badge}{part.text}")
                
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

