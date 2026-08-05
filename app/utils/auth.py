import streamlit as st
import hmac
import os
import hashlib
import secrets
import json
import logging
from typing import Dict, Any, Optional
from core.models import User, Org
import config as cfg

logger = logging.getLogger(__name__)

# Constant for fallback local development user
LOCAL_DEV_USERNAME = "jacques-local"


def hash_password(password: str, iterations: int = 20000) -> str:
    """Hashes a password using PBKDF2-HMAC-SHA256 with a random salt.

    Format: pbkdf2_sha256$iterations$salt$hash

    Args:
        password: The plain text password to hash.
        iterations: The number of hashing iterations to perform.

    Returns:
        The formatted string representing the hashed password containing the algorithm,
        iterations, salt, and key hash.
    """
    salt = secrets.token_hex(16)
    pw_bytes = password.encode("utf-8")
    salt_bytes = salt.encode("utf-8")
    hash_bytes = hashlib.pbkdf2_hmac("sha256", pw_bytes, salt_bytes, iterations)
    return f"pbkdf2_sha256${iterations}${salt}${hash_bytes.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    """Verifies a password against a PBKDF2-HMAC-SHA256 hash.

    Args:
        password: The plain text password to verify.
        hashed: The PBKDF2 hash to compare against.

    Returns:
        True if the password matches the hash, False otherwise.
    """
    if not hashed or not hashed.startswith("pbkdf2_sha256$"):
        return False
    try:
        parts = hashed.split("$")
        if len(parts) != 4:
            return False
        _, iterations_str, salt, hash_hex = parts
        iterations = int(iterations_str)
        pw_bytes = password.encode("utf-8")
        salt_bytes = salt.encode("utf-8")
        hash_bytes = hashlib.pbkdf2_hmac("sha256", pw_bytes, salt_bytes, iterations)
        return hmac.compare_digest(hash_bytes.hex(), hash_hex)
    except Exception as e:
        logger.error(f"Error verifying password: {e}")
        return False


def load_users_config() -> Dict[str, Any]:
    """Loads user credential dictionary from environment variable or Streamlit secrets.

    Returns:
        A dictionary mapping usernames to user details like password hash and org ID.
        Example: {"username": {"password_hash": "...", "org_id": "..."}}
    """
    config_str = os.environ.get("ODIS_USERS_CONFIG")
    if not config_str:
        try:
            config_str = st.secrets.get("ODIS_USERS_CONFIG")
        except Exception:
            pass

    if not config_str:
        return {}

    try:
        data = json.loads(config_str)
        return data.get("users", {})
    except Exception as e:
        logger.error(f"Error loading ODIS_USERS_CONFIG: {e}")
        return {}


def verify_credentials(
    username: str, password: str, secrets_dict: Optional[Dict[str, Any]] = None
) -> bool:
    """Verifies username and password against credentials config.

    Supports backward compatibility for testing using secrets_dict injection.

    Args:
        username: The username trying to authenticate.
        password: The password to check.
        secrets_dict: Optional dictionary containing backward compatible credentials.

    Returns:
        True if credentials are valid, False otherwise.
    """
    # 1. Use the injected/provided secrets dict if it has passwords mapping (backward compatibility)
    if secrets_dict and "passwords" in secrets_dict:
        if username in secrets_dict["passwords"]:
            hashed = secrets_dict["passwords"][username]
            # If the secret in test is plaintext, do secure check; if pbkdf2, verify properly
            if hashed.startswith("pbkdf2_sha256$"):
                return verify_password(password, hashed)
            return hmac.compare_digest(password, hashed)
        return False

    # 2. Standard flow: load from environment config
    users_config = load_users_config()
    if username in users_config:
        user_data = users_config[username]
        pw_hash = user_data.get("password_hash")
        if pw_hash:
            return verify_password(password, pw_hash)

    return False


def resolve_org_for_oidc(email: str) -> Org:
    """Resolves the Org profile for an OIDC-authenticated email.

    Reads org mappings from st.secrets (written by generate_secrets.py at startup),
    then falls back to config defaults.

    Args:
        email: The authenticated OIDC email address (already verified by Streamlit).

    Returns:
        The mapped Org profile. Defaults to a generic Org if no mapping found.
    """
    org_id = None
    if email:
        email_lower = email.lower()

        # Read mappings from st.secrets (populated by generate_secrets.py)
        try:
            email_mapping = dict(st.secrets.get("auth", {}).get("email_org_mapping", {}))
            if email_lower in {k.lower() for k in email_mapping}:
                org_id = next(v for k, v in email_mapping.items() if k.lower() == email_lower)
        except Exception:
            pass

        # Fall back to domain mapping
        if not org_id and "@" in email_lower:
            domain = email_lower.split("@")[-1]
            try:
                domain_mapping = dict(st.secrets.get("auth", {}).get("domain_org_mapping", {}))
                if domain in {k.lower() for k in domain_mapping}:
                    org_id = next(v for k, v in domain_mapping.items() if k.lower() == domain)
            except Exception:
                pass

        # Final fallback to config
        if not org_id:
            org_id = cfg.OIDC_DOMAIN_ORG_MAPPING.get(email_lower.split("@")[-1] if "@" in email_lower else "")

    if not org_id:
        org_id = "default"

    org = cfg.ORGANIZATION_PROFILES.get(org_id)
    if not org:
        org = Org(
            id=org_id,
            name=org_id.capitalize(),
            zone_type="departement",
            default_zones=[],
        )
    return org


def get_login_session_id() -> str:
    """Retrieves or generates a unique login_session_id for the current user session."""
    if "login_session_id" not in st.session_state:
        st.session_state["login_session_id"] = str(secrets.token_hex(16))
    return st.session_state["login_session_id"]


def is_admin(username: Optional[str] = None) -> bool:
    """Checks if the given or current session user is an administrator."""
    if not username:
        try:
            username = st.session_state.get("username")
        except Exception:
            pass
    if not username:
        return False
    admin_users = getattr(cfg, "ADMIN_USERS", set())
    return username in admin_users


def check_password() -> bool:
    """Checks if the user has authenticated.

    Delegates OIDC authorization entirely to Streamlit's native [auth] secrets.toml
    enforcement. Custom logic is limited to org resolution and legacy form login.

    Returns:
        True if the user is authenticated (or local dev bypass is active), False otherwise.
    """
    # Detect Cloud Run environment or forced auth flag
    is_cloud_run = os.environ.get("K_SERVICE") is not None
    force_auth = os.environ.get("ODIS_FORCE_AUTH", "False").lower() in ("true", "1", "yes")

    # 1. Local dev bypass (no Cloud Run, no forced auth)
    if not is_cloud_run and not force_auth:
        if "user" not in st.session_state or "org" not in st.session_state:
            org = cfg.ORGANIZATION_PROFILES.get("jaccueille")
            st.session_state["user"] = User(username=LOCAL_DEV_USERNAME, org_id="jaccueille")
            st.session_state["org"] = org
            st.session_state["username"] = LOCAL_DEV_USERNAME
        st.session_state["password_correct"] = True
        return True

    # Initialize auth state
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    # 2. Short-circuit only for a complete authenticated session.  Search resets
    # deliberately keep identity, but a partial/stale session must re-enter the
    # OIDC resolution path rather than silently operating without organization
    # defaults or access context.
    if st.session_state.get("password_correct"):
        if st.session_state.get("user") is not None and st.session_state.get("org") is not None:
            return True
        logger.warning(
            "Authenticated flag found without complete user/org context; "
            "re-establishing authentication context."
        )
        st.session_state["password_correct"] = False

    # 3. OIDC: if st.user.is_logged_in is True, Streamlit has already enforced
    #    allowed_emails / allowed_domains from secrets.toml — just resolve org.
    user_obj = getattr(st, "user", None)
    if user_obj and getattr(user_obj, "is_logged_in", False):
        email = getattr(user_obj, "email", None)
        org = resolve_org_for_oidc(email or "")
        st.session_state["password_correct"] = True
        st.session_state["username"] = email
        st.session_state["user"] = User(username=email, org_id=org.id)
        st.session_state["org"] = org
        return True

    # 4. Show login UI (Google OIDC + legacy form)
    with st.container(width="stretch", horizontal_alignment="center"):
        with st.container(width=400, border=True, horizontal_alignment="center"):
            st.subheader("Accès ODIS")
            st.info("👋 Bienvenue ! Veuillez vous connecter avec l'une des méthodes ci-dessous.")

            # --- OPTION A : Google Workspace (OIDC) ---
            if st.button("🔑 Se connecter avec Google", type="primary", width="stretch"):
                st.login("google")
                st.stop()

            st.markdown(
                "<div style='text-align: center; color: gray; margin: 15px 0;'>--- ou via vos identifiants classiques ---</div>",
                unsafe_allow_html=True,
            )

            # --- OPTION B : Form Login (Legacy) ---
            with st.form("login_form"):
                username = st.text_input(
                    "Identifiant (Email / Nom d'utilisateur)",
                    autocomplete="username",
                )
                password = st.text_input(
                    "Mot de passe",
                    type="password",
                    autocomplete="current-password",
                )
                submit = st.form_submit_button("Se connecter", width="stretch")

                if submit:
                    if not username or not password:
                        st.error("❌ Veuillez remplir tous les champs.")
                    elif verify_credentials(username, password):
                        users_config = load_users_config()
                        user_data = users_config[username]
                        org_id = user_data["org_id"]
                        org = cfg.ORGANIZATION_PROFILES.get(org_id) or Org(
                            id=org_id,
                            name=org_id.capitalize(),
                            zone_type="departement",
                            default_zones=[],
                        )
                        st.session_state["password_correct"] = True
                        st.session_state["user"] = User(username=username, org_id=org_id)
                        st.session_state["org"] = org
                        st.session_state["username"] = username
                        st.rerun()
                    else:
                        st.error("❌ Identifiants incorrects.")
    return False


def logout() -> None:
    """Logs out the current user session.

    Supports both native Streamlit OIDC authentication (st.logout()) and direct email/password login.
    Clears all session state variables and redirects to the home/login page.
    """
    st.session_state.clear()

    user_obj = getattr(st, "user", None)
    if user_obj and getattr(user_obj, "is_logged_in", False):
        st.logout()
    else:
        try:
            st.switch_page("pages/1_Accueil.py")
        except Exception:
            st.rerun()
