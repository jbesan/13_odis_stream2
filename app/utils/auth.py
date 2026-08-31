import streamlit as st
import os
import secrets
import logging
from typing import Dict, Optional
from core.models import User, Org
import config as cfg
from utils.oidc_policy import normalize_domain, normalize_email

logger = logging.getLogger(__name__)

# Constant for fallback local development user
LOCAL_DEV_USERNAME = "jacques-local"


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
        get_login_session_id()
        st.session_state["password_correct"] = True
        st.session_state["auth_method"] = "local"
        return True

    # Initialize auth state
    if "password_correct" not in st.session_state:
        st.session_state["password_correct"] = False

    user_obj = getattr(st, "user", None)
    oidc_logged_in = bool(user_obj and getattr(user_obj, "is_logged_in", False))

    # 2. Re-verify active sessions. Local bypass identities have no OIDC state;
    # OIDC identities are re-authorized on every page entry so an existing session
    # cannot retain access after an allowlist or organization mapping change.
    if st.session_state.get("password_correct"):
        has_complete_identity = (
            st.session_state.get("user") is not None
            and st.session_state.get("org") is not None
        )
        auth_method = st.session_state.get("auth_method")
        if has_complete_identity and auth_method == "local":
            get_login_session_id()
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
        get_login_session_id()
        return True

    # 4. Show login UI (Google Workspace OIDC)
    with st.container(width="stretch", horizontal_alignment="center"):
        with st.container(width=400, border=True, horizontal_alignment="center"):
            st.subheader("Accès ODIS")
            st.info(
                "👋 Bienvenue ! Veuillez vous connecter avec votre compte Google Workspace."
            )
            if st.button(
                "🔑 Se connecter avec Google", type="primary", width="stretch"
            ):
                st.login("google")
                st.stop()
    return False


def logout() -> None:
    """Logs out the current user session.

    Supports both native Streamlit OIDC authentication (st.logout()) and local dev session clear.
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
