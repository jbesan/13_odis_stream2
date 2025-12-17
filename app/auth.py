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

    def password_entered():
        """Checks whether a password entered by the user is correct."""
        if "username" in st.session_state and "password" in st.session_state:
            username = st.session_state["username"]
            password = st.session_state["password"]
            
            if verify_credentials(username, password, st.secrets):
                st.session_state["password_correct"] = True
                del st.session_state["password"]  # Don't store password
                del st.session_state["username"]
                return
            
            st.session_state["password_correct"] = False

    # Initialize session state for auth
    if "password_correct" not in st.session_state:
        # First run, show inputs
        st.session_state["password_correct"] = False

    if not st.session_state["password_correct"]:
        # Show input for username and password
        with st.container(width='stretch', horizontal_alignment="center"):
            with st.container(width=300, border=True, horizontal_alignment="center"):
                st.subheader("Authentification")
                st.text_input("Username", key="username")
                st.text_input("Password", type="password", on_change=password_entered, key="password")
                st.text("")
        
        # Determine specific error message
        if "password_correct" in st.session_state and st.session_state["password_correct"] is False:
             # Check if they actually tried to log in (keys exist) - wait, password_entered handles the check
             # If we are here and password_correct is explicitly False, it might mean a failed attempt OR just initialized.
             # But we initialize to False.
             # Let's add a flag for "attempted" to differentiate initial load vs failed login if we want fancy UI,
             # but standard streamlit pattern is simpler.
             # If keys are present in session state but cleared by callback... 
             # Actually, simpler pattern:
             pass

        if "password_correct" in st.session_state and st.session_state["password_correct"] is False:
             # Simplification: Just check if we are re-running and it failed.
             # We can't easily know if it's a failed attempt without an extra flag.
             # Let's just trust the user will see the form again.
             pass
             
        return False
    else:
        # Password correct.
        return True
