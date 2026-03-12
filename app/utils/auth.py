import streamlit as st
import hmac

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
    """Returns `True` if the user had a correct password."""

    # Initialize session state for auth
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
        st.warning("L'application est en phase de test. Vos interactions sont collectées pour améliorer l'outil. Merci d'anonymiser au maximum vos saisies libres.", icon="⚠️")
        return True
