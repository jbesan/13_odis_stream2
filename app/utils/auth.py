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
from utils.oidc_policy import normalize_domain, normalize_email

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


def _normalized_mapping(mapping: Dict[str, str], key_normalizer) -> Dict[str, str]:
    """Normalize a config mapping without allowing a malformed key to match."""
    normalized: Dict[str, str] = {}
    for key, value in mapping.items():
        normalized_key = key_normalizer(key)
        if normalized_key and isinstance(value, str) and value.strip():
            normalized[normalized_key] = value.strip()
    return normalized


def resolve_org_for_oidc(email: str) -> Optional[Org]:
    """Authorize an OIDC identity and return its configured organization.

    OIDC verifies that the identity exists. This application-level check decides
    whether that identity may access OD&IS and which organization context it
    may use. Every successful identity must be explicitly authorized and map to
    an existing profile; no generic fallback organization is permitted.
    """
    normalized_email = normalize_email(email)
    if not normalized_email:
        return None

    domain = normalized_email.rsplit("@", maxsplit=1)[1]
    allowed_domains = {
        normalized_domain
        for raw_domain in cfg.OIDC_ALLOWED_DOMAINS
        if (normalized_domain := normalize_domain(raw_domain))
    }
    allowed_emails = {
        normalized_allowed_email
        for raw_email in cfg.OIDC_ALLOWED_EMAILS
        if (normalized_allowed_email := normalize_email(raw_email))
    }
    email_mapping = _normalized_mapping(cfg.OIDC_EMAIL_ORG_MAPPING, normalize_email)
    domain_mapping = _normalized_mapping(cfg.OIDC_DOMAIN_ORG_MAPPING, normalize_domain)

    if normalized_email not in allowed_emails and domain not in allowed_domains:
        return None

    # A specifically authorized email may override the organization assigned to
    # its otherwise authorized domain. Both paths still require a known profile.
    org_id = email_mapping.get(normalized_email) or domain_mapping.get(domain)
    if not org_id:
        return None
    return cfg.ORGANIZATION_PROFILES.get(org_id)


def _clear_authenticated_context() -> None:
    """Remove identity and data that could belong to the prior identity."""
    st.session_state.clear()


def _render_oidc_denied() -> None:
    """Show a generic denial without exposing allowlist membership."""
    st.error("🔒 Votre compte n'est pas autorisé à accéder à OD&IS.")
    if st.button("Se déconnecter", key="oidc_denied_logout"):
        st.logout()


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

    Streamlit OIDC authenticates the identity; this function separately enforces
    OD&IS authorization and organization assignment.

    Returns:
        True if the user is authenticated (or local dev bypass is active), False otherwise.
    """
    # Detect Cloud Run environment or forced auth flag
    is_cloud_run = os.environ.get("K_SERVICE") is not None
    force_auth = os.environ.get("ODIS_FORCE_AUTH", "False").lower() in (
        "true",
        "1",
        "yes",
    )

    # 1. Local dev bypass (no Cloud Run, no forced auth)
    if not is_cloud_run and not force_auth:
        if "user" not in st.session_state or "org" not in st.session_state:
            org = cfg.ORGANIZATION_PROFILES.get("jaccueille")
            st.session_state["user"] = User(
                username=LOCAL_DEV_USERNAME, org_id="jaccueille"
            )
            st.session_state["org"] = org
            st.session_state["username"] = LOCAL_DEV_USERNAME
        st.session_state["password_correct"] = True
        st.session_state["auth_method"] = "local"
        return True

    # Initialize auth state
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    user_obj = getattr(st, "user", None)
    oidc_logged_in = bool(user_obj and getattr(user_obj, "is_logged_in", False))

    # 2. A legacy/local identity has no Streamlit OIDC state to re-check. OIDC
    # identities are re-authorized on every page entry so an existing session
    # cannot retain access after an allowlist or organization mapping change.
    if st.session_state.get("password_correct"):
        has_complete_identity = (
            st.session_state.get("user") is not None
            and st.session_state.get("org") is not None
        )
        auth_method = st.session_state.get("auth_method")
        if has_complete_identity and auth_method in {"local", "legacy"}:
            return True
        if auth_method == "oidc" and not oidc_logged_in:
            _clear_authenticated_context()
        elif not oidc_logged_in:
            logger.warning("Authenticated session has no verifiable identity context.")
            _clear_authenticated_context()

    # 3. OIDC: Streamlit has authenticated the identity; OD&IS now authorizes
    # it against the Secret Manager policy and resolves a known organization.
    if oidc_logged_in:
        email = getattr(user_obj, "email", None)
        normalized_email = normalize_email(email)
        org = resolve_org_for_oidc(email or "")
        if not normalized_email or not org:
            logger.warning(
                "OIDC-authenticated identity denied by authorization policy."
            )
            _clear_authenticated_context()
            _render_oidc_denied()
            return False
        st.session_state["password_correct"] = True
        st.session_state["auth_method"] = "oidc"
        st.session_state["username"] = normalized_email
        st.session_state["user"] = User(username=normalized_email, org_id=org.id)
        st.session_state["org"] = org
        return True

    # 4. Show login UI (Google OIDC + legacy form)
    with st.container(width="stretch", horizontal_alignment="center"):
        with st.container(width=400, border=True, horizontal_alignment="center"):
            st.subheader("Accès ODIS")
            st.info(
                "👋 Bienvenue ! Veuillez vous connecter avec l'une des méthodes ci-dessous."
            )

            # --- OPTION A : Google Workspace (OIDC) ---
            if st.button(
                "🔑 Se connecter avec Google", type="primary", width="stretch"
            ):
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
                        st.session_state["auth_method"] = "legacy"
                        st.session_state["user"] = User(
                            username=username, org_id=org_id
                        )
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
