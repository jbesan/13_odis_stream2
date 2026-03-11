import streamlit as st
from datetime import datetime, timezone
from services.telemetry import get_interaction_id
from google.cloud import bigquery
import os
import logging

logger = logging.getLogger(__name__)

DATASET_ID = "odis_logs"
TABLE_ID = "user_feedback"

def _submit_to_bq(feedback_type, comment):
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and not os.getenv("GCP_PROJECT"):
         logger.warning("No Google Cloud Project found. Skipping BQ feedback insert.")
         # Return True so the UI doesn't block local dev
         return True
    try:
        client = bigquery.Client()
        table_ref = f"{client.project}.{DATASET_ID}.{TABLE_ID}"
        interaction_id = get_interaction_id()
        username = st.session_state.get('username', 'unknown')
        
        row = {
            "interaction_id": interaction_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "username": username,
            "feedback_type": feedback_type,
            "comment": comment
        }
        errors = client.insert_rows_json(table_ref, [row])
        if errors:
            logger.error(f"BQ Feedback Insert Error: {errors}")
            return False
        return True
    except Exception as e:
        logger.error(f"Failed to submit feedback: {str(e)}")
        return False

@st.dialog("💬 Donner mon avis")
def feedback_dialog():
    st.write("Merci de nous aider à améliorer l'outil en mode test ! (N'incluez pas de données personnelles).")
    feedback_type = st.selectbox("Type de retour", ["Bug", "Question", "Suggestion"], key="fb_type")
    comment = st.text_area("Votre message", height=150, key="fb_comment")
    
    if st.button("Envoyer", type="primary"):
        if not comment.strip():
            st.error("Le message ne peut pas être vide.")
        else:
            with st.spinner("Envoi..."):
                success = _submit_to_bq(feedback_type, comment)
            if success:
                st.success("Merci ! Votre retour a bien été enregistré. Vous pouvez fermer cette fenêtre.")
            else:
                st.error("Erreur lors de l'envoi. Veuillez réessayer plus tard.")

def render_feedback_button():
    """Renders the feedback button, usually in the sidebar."""
    if st.button("💬 Donner mon avis", use_container_width=True):
        feedback_dialog()
