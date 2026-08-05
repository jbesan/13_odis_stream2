"""Common Streamlit page entry and sidebar conventions."""

from __future__ import annotations

from typing import Optional

import streamlit as st

from services import telemetry
from services.app_session import AppSession
from ui import components
from utils import auth, common


def enter_page(
    page_name: Optional[str],
    *,
    admin_only: bool = False,
    handle_shared_search: bool = False,
    redirect_shared_to_results: bool = False,
) -> None:
    """Apply the common authenticated page lifecycle after page config."""
    if not auth.check_password():
        st.stop()
    AppSession(st.session_state).identity()
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
