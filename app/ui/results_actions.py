import logging
import urllib.parse
from typing import List, Optional
import streamlit as st

from core.models import SearchResultsData, CommuneResult
from core.postscoring import sync_commune_data
from core.pdf_generator import generate_pdf_report
from agents.utils import odis_get_bg_result
from ui import ui_telemetry

logger = logging.getLogger("ui.results.actions")


@st.dialog("Export des résultats en PDF")
def pdf_modal():
    """Dialog to handle PDF generation and download."""
    # State 1: Loading / Generating
    if (
        "pdf_modal_data" not in st.session_state
        or st.session_state.pdf_modal_data is None
    ):
        with st.spinner("Veuillez patienter, nous générons votre document..."):
            search_results = st.session_state.get("search_results")
            pdf_warnings: List[str] = []
            try:
                pdf_bytes = generate_pdf_report(
                    search_results=search_results,
                    config=st.session_state.config,
                    processed_gdf=st.session_state.get("processed_gdf"),
                    generation_warnings=pdf_warnings,
                )
            except Exception:
                logger.error(
                    "PDF export failed",
                    extra={
                        "extra_data": {
                            "operation": "pdf_export",
                            "error_code": "PDF-EXPORT-FAILED",
                        }
                    },
                    exc_info=True,
                )
                st.error(
                    "Impossible de générer le PDF. Réessayez plus tard "
                    "(code : PDF-EXPORT-FAILED)."
                )
                return
            st.session_state.pdf_modal_data = pdf_bytes
            st.session_state["pdf_modal_warnings"] = sorted(set(pdf_warnings))
            ui_telemetry.track_ui_event(
                "export_pdf",
                {"search_hash": search_results.search_hash if search_results else ""},
            )

    # State 2: Download Ready
    if st.session_state.get("pdf_modal_data"):
        pdf_warnings = st.session_state.get("pdf_modal_warnings", [])
        if pdf_warnings:
            st.warning(
                "Votre document est prêt, mais certaines visualisations sont "
                f"indisponibles ({', '.join(pdf_warnings)})."
            )
        else:
            st.success("Votre document est prêt !")
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                label="Télécharger le PDF",
                data=st.session_state.pdf_modal_data,
                file_name="synthese_jaccueille.pdf",
                mime="application/pdf",
                icon=":material/picture_as_pdf:",
                type="primary",
                width="stretch",
            )
        with col2:
            if st.button("Fermer", width="stretch"):
                st.session_state.pdf_modal_data = None
                st.session_state.pop("pdf_modal_warnings", None)
                st.rerun()


def _is_hydration_ready_for_city(commune: CommuneResult, h: Optional[str]) -> bool:
    """Apply available results before checking the commune's hydration state.

    Args:
        commune: Commune whose enrichment data must be published.
        h: Background search key, absent for results without live workers.

    Returns:
        Whether the commune is ready for user actions.
    """
    if st.session_state.get("immutable_shared_snapshot"):
        return True

    if not h:
        return True

    bg_res = odis_get_bg_result(h)
    sync_commune_data(commune, bg_res)
    return commune.commune_results_hydrated


def _is_postscoring_ready_for_search(h: Optional[str]) -> bool:
    """Return True if all background post-scoring tasks for all search results have reached a terminal state."""
    if st.session_state.get("immutable_shared_snapshot"):
        return True

    if not h:
        return True

    search_results: Optional[SearchResultsData] = st.session_state.get("search_results")
    if not search_results or not search_results.results:
        return True

    communes = list(search_results.results)
    if search_results.commune_pressentie:
        communes.append(search_results.commune_pressentie)

    # Check each commune's data hydrations (no refiner check for PDF/Share)
    for commune in communes:
        if not _is_hydration_ready_for_city(commune, h):
            return False

    return True


@st.fragment(run_every=2.0)
def render_export_pdf_button(h: str):
    """Export results once post-scoring completes, updating in-place."""
    if not h or not st.session_state.get("search_results"):
        return

    ready = _is_postscoring_ready_for_search(h)
    btn_label = "Exporter résultats" if ready else "Exporter résultats (Préparation...)"
    btn_disabled = not ready

    if st.button(
        btn_label,
        icon=":material/picture_as_pdf:",
        type="secondary",
        width="stretch",
        key=f"pdf_btn_{h}",
        disabled=btn_disabled,
    ):
        pdf_modal()


@st.dialog("Partager cette recherche")
def share_search_modal():
    """Dialog to generate, display, and copy shared permalink URL."""
    config = st.session_state.get("config")
    search_results = st.session_state.get("search_results")

    if not config or not search_results:
        st.error("Aucune recherche active à partager.")
        return

    # Generate or retrieve active share_id
    if (
        "active_share_id" not in st.session_state
        or not st.session_state.active_share_id
    ):
        with st.spinner("Génération du lien de partage..."):
            from services import share_service

            try:
                share_id = share_service.save_shared_search(
                    config=config,
                    search_results=search_results,
                    processed_gdf=st.session_state.get("processed_gdf"),
                    selected_geo=st.session_state.get("selected_geo"),
                    data_release=st.session_state.get("active_data_release"),
                    map_center=st.session_state.get("center"),
                    map_zoom=st.session_state.get("zoom"),
                )
            except RuntimeError as exc:
                st.error(str(exc))
                return
            st.session_state["active_share_id"] = share_id
    else:
        share_id = st.session_state.active_share_id

    # Construct public shareable URL
    base_url = "https://myapp.fr"
    try:
        headers = (
            st.context.headers
            if hasattr(st, "context") and hasattr(st.context, "headers")
            else {}
        )
        host = headers.get("host") or headers.get("Host")
        if host:
            scheme = (
                "https"
                if "localhost" not in host and "127.0.0.1" not in host
                else "http"
            )
            base_url = f"{scheme}://{host}"
    except Exception as exc:
        logger.debug("Failed to detect base_url from st.context.headers: %s", exc)

    permalink = f"{base_url}/?search={share_id}"

    st.markdown(
        "Le lien ci-dessous permet de retrouver les résultats de cette recherche tels quels sans relancer les calculs associés. Ces résultats sont accessibles pendant **1 an** (prolongé à chaque consultation)."
    )

    st.code(permalink, language=None)

    subject = "Résultats de recherche OD&IS"
    body = f"Voici le lien pour accéder aux résultats de la recherche : {permalink}"
    slack_msg = f"Voici les résultats de notre recherche OD&IS : {permalink}"

    slack_share_url = f"https://slack.com/app_redirect?channel=&message={urllib.parse.quote(slack_msg)}"
    mailto_url = f"mailto:?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body, safe=':/?=')}"

    col1, col2 = st.columns(2)
    with col1:
        st.link_button(
            "Partager sur Slack",
            slack_share_url,
            icon=":material/chat:",
            width="stretch",
        )
    with col2:
        st.link_button(
            "Envoyer par Email",
            mailto_url,
            icon=":material/mail:",
            type="primary",
            width="stretch",
        )


@st.fragment(run_every=2.0)
def render_share_search_button(
    h: str = "",
    button_text: str = "Partager la recherche",
    key_prefix: str = "share_btn",
    width: str = "stretch",
):
    """Share results once post-scoring completes, updating in-place."""
    if (
        not h
        and "search_results" in st.session_state
        and st.session_state.search_results
    ):
        h = st.session_state.search_results.background_key

    if not st.session_state.get("search_results"):
        return

    ready = _is_postscoring_ready_for_search(h)
    btn_label = button_text if ready else f"{button_text} (Préparation...)"
    btn_disabled = not ready

    if st.button(
        btn_label,
        icon=":material/share:",
        type="secondary",
        width=width,
        key=f"{key_prefix}_{h}",
        disabled=btn_disabled,
    ):
        share_search_modal()
