"""Common Streamlit page entry and sidebar conventions."""

from __future__ import annotations

import os
from typing import Optional

import streamlit as st

from services import telemetry
from services.app_session import AppSession
from ui import components
from ui.idle_sleep import inject_idle_disconnect
from utils import auth, common


IDLE_DISCONNECT_TIMEOUT_MINUTES = 10


def _mount_idle_disconnect_when_configured() -> None:
    """Mount the production inactivity guard without coupling it to auth logic."""
    is_cloud_run = os.environ.get("K_SERVICE") is not None
    enabled = os.environ.get("ODIS_IDLE_DISCONNECT_ENABLED", "True").lower() in (
        "true",
        "1",
        "yes",
    )
    if is_cloud_run and enabled:
        inject_idle_disconnect(timeout_minutes=IDLE_DISCONNECT_TIMEOUT_MINUTES)


def _inject_common_styles() -> None:
    """Inject global CSS rules for sidebar primary buttons (J'accueille dark green text #1B4429)."""
    st.markdown(
        """
        <style>
            [data-testid="stSidebar"] button[kind="primary"] p,
            [data-testid="stSidebar"] button[kind="primary"] span,
            [data-testid="stSidebar"] button[kind="primary"] div,
            [data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] p,
            [data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] span,
            [data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] div {
                color: #1B4429 !important;
            }
            [data-testid="stSidebar"] button[kind="primary"] svg,
            [data-testid="stSidebar"] button[data-testid="stBaseButton-primary"] svg {
                fill: #1B4429 !important;
                color: #1B4429 !important;
            }
        </style>
        """,
        unsafe_allow_html=True,
    )


def enter_page(
    page_name: Optional[str],
    *,
    admin_only: bool = False,
    handle_shared_search: bool = False,
    redirect_shared_to_results: bool = False,
) -> None:
    """Apply the common authenticated page lifecycle after page config."""
    authenticated = auth.check_password()
    # Mount on the login screen too: an abandoned unauthenticated tab also owns
    # a WebSocket and would otherwise keep a Cloud Run request active.
    _mount_idle_disconnect_when_configured()
    if not authenticated:
        st.stop()
    _inject_common_styles()
    AppSession(st.session_state).identity()
    auth.get_login_session_id()
    if admin_only and not auth.is_admin():
        st.error("🔒 Accès refusé : cette page est réservée aux administrateurs.")
        st.stop()

    if handle_shared_search and "search" in st.query_params:
        from services import share_service

        restored = share_service.restore_shared_search_from_query_params()
        if restored and redirect_shared_to_results:
            st.switch_page("pages/3_Resultats.py")

    error = st.session_state.pop("share_error", None)
    if error:
        st.toast(error, icon="⚠️")
        st.error(f"⚠️ {error}")

    if page_name:
        telemetry.log_page_view(page_name)


def render_sidebar_logo() -> None:
    """Render the standard organization logo when available."""
    logo_path = common.get_asset_path("logo-jaccueille-singa.png")
    logo_b64 = common.get_base64_image(logo_path)
    if logo_b64:
        st.markdown(
            f'<img src="data:image/png;base64,{logo_b64}" width="150" '
            'style="margin-bottom: 20px;">',
            unsafe_allow_html=True,
        )


def render_primary_sidebar_actions(
    *, show_home: bool = False, show_feedback: bool = False
) -> None:
    if show_home:
        components.start_over()
    if show_feedback:
        from ui import feedback

        feedback.render_feedback_button()


def render_account_sidebar_actions(*, show_admin: bool = True) -> None:
    st.divider()
    if show_admin:
        components.render_admin_sidebar_link()
    components.render_sources_sidebar_link()
    components.render_logout_sidebar_button()
