import streamlit as st
import hmac
import os
from ui.idle_sleep import inject_idle_sleep

def verify_credentials(username, password, secrets):
    """
    Verifies username and password against the secrets dictionary.
    Returns True if valid, False otherwise.
    """
    if "passwords" not in secrets:
        return False
        
    if username in secrets["passwords"]:
        # Secure comparison
        if hmac.compare_digest(password, secrets["passwords"][username]):
            return True
            
    return False

def check_password():
    """
    Returns `True` if the user had a correct password.
    Skips authentication and Idle Sleep when running locally.
    """
    # Detect Cloud Run environment
    is_cloud_run = os.environ.get("K_SERVICE") is not None
    
    # 1. Skip if not running on Cloud Run (Local Dev)
    if not is_cloud_run:
        # Default user for local logging/telemetry
        if "username" not in st.session_state:
            st.session_state["username"] = "jacques-local"
        st.session_state["password_correct"] = True
        return True

    # 2. Initialize session state for auth
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    if not st.session_state["password_correct"]:
        # Show input for username and password
        with st.container(width='stretch', horizontal_alignment="center"):
            with st.container(width=300, border=True, horizontal_alignment="center"):
                st.subheader("Authentification")
                
                with st.form("login_form"):
                    username = st.text_input("Username", autocomplete="username")
                    password = st.text_input("Password", type="password", autocomplete="current-password")
                    submit = st.form_submit_button("Se connecter", use_container_width=True)
                    
                    if submit:
                        if verify_credentials(username, password, st.secrets):
                            st.session_state["password_correct"] = True
                            st.session_state["username"] = username
                            st.rerun()
                        else:
                            st.error("❌ Identifiants incorrects")
                            st.session_state["password_correct"] = False
             
        return False
    else:
        # Password correct.
        st.sidebar.warning("ATTENTION: L'application est en phase de test. Vos interactions sont collectées pour améliorer l'outil. Merci d'anonymiser au maximum vos saisies libres.")
        
        # 3. Inject Idle Sleep monitor (10 mins timeout) - ONLY on Cloud Run
        inject_idle_sleep(timeout_minutes=10)
        
        return True
