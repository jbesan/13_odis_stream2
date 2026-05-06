
import streamlit as st
import logging

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui.components")

def inject_custom_css() -> None:
    """Injects custom CSS for UI refinements (F-48, pills width)."""
    st.markdown("""
        <style>
            /* Target stable BaseWeb tag attributes used by Streamlit */
            [data-baseweb="tag"] {
                max-width: 500px !important;
            }
            /* Alternative stable selector for text inside tags */
            div[data-testid="stMultiSelect"] span {
                max-width: 500px !important;
            }
            /* Custom styling for specific buttons if needed */
            .st-key-btn_recommencer .stButton p {color: white;}
            
            /* Org Badge Styling */
            .org-badge {
                padding: 10px;
                border-radius: 8px;
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                margin-top: 20px;
                text-align: center;
            }
            .org-name {
                font-weight: bold;
                font-size: 0.9em;
                display: block;
            }
            .org-status {
                font-size: 0.75em;
                color: #888;
            }
        </style>
    """, unsafe_allow_html=True)

@st.dialog("Confirmer la réinitialisation")
def confirm_reset_dialog():
    """A confirmation modal before resetting the search and returning home."""
    st.write("Cette action réinitialisera la recherche en cours. Souhaitez-vous vraiment retourner à l'accueil et effacer vos saisies ?")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Oui", width="stretch"):
            st.switch_page("pages/1_Accueil.py")
    with col2:
        if st.button("Annuler", width="stretch"):
            st.rerun()

def start_over() -> None:
    """Renders the 'Back to Home' button with a confirmation dialog if results exist."""
    inject_custom_css()
    if st.button("Retour à l'Accueil", icon=":material/home:", width="stretch", key="btn_recommencer"):
        if 'search_results' in st.session_state:
            confirm_reset_dialog()
        else:
            st.switch_page("pages/1_Accueil.py")

def get_person_accompanied_str() -> str:
    """Returns a string describing the person accompanied, using their name if available."""
    if st.session_state.get('ui_nom'):
        return f"de {st.session_state.ui_nom}"
    return "de la personne accompagnée"

def render_org_badge() -> None:
    """Renders a subtle organization badge in the sidebar."""
    import config as cfg
    org_id = st.session_state.get('ui_org_context')
    if not org_id:
        return
        
    profile = cfg.ORGANIZATION_PROFILES.get(org_id)
    if profile:
        # st.text('Hello')
        st.sidebar.info(f"Mode | {profile['name']}")#, color="blue", width="stretch")


