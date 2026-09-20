import logging
import urllib.parse
from typing import List, Optional
import streamlit as st
import streamlit.components.v2 as components_v2

from core.models import SearchResultsData, CommuneResult
from core.postscoring import sync_commune_data
from core.pdf_generator import generate_pdf_report
from agents.utils import odis_get_bg_result
from ui.dialog_state import clear_dialog, request_dialog
from ui import ui_telemetry

logger = logging.getLogger("ui.results.actions")

_SHARE_ACTIONS_HTML = """
<div class="odis-share-actions-row">
    <a href="slack://open" id="odis-share-slack-btn" class="odis-share-btn odis-slack-btn" title="Copier le message et ouvrir Slack">
        <span class="odis-share-btn-icon" id="odis-share-slack-icon">
            <img src="app/static/logo-slack.svg" width="20" height="20" alt="Slack" />
        </span>
        <span id="odis-share-slack-text">Partager sur Slack</span>
    </a>
    <a href="mailto:" id="odis-share-email-btn" class="odis-share-btn odis-email-btn" target="_self" title="Envoyer par email">
        <span class="odis-share-btn-icon" id="odis-share-email-icon">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect width="20" height="16" x="2" y="4" rx="2"/>
                <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"/>
            </svg>
        </span>
        <span id="odis-share-email-text">Envoyer par Email</span>
    </a>
</div>
"""

_SHARE_ACTIONS_CSS = """
.odis-share-actions-row {
    display: flex;
    gap: 12px;
    width: 100%;
    box-sizing: border-box;
}
.odis-share-btn {
    flex: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 8px;
    min-height: 2.5rem;
    padding: 0.25rem 0.75rem;
    margin: 0;
    border-radius: 9999px;
    font-family: inherit;
    font-size: 1rem;
    font-weight: 500;
    cursor: pointer;
    text-decoration: none;
    box-sizing: border-box;
    transition: background-color 0.15s ease-in-out, border-color 0.15s ease-in-out, transform 0.1s ease;
}
.odis-share-btn:active {
    transform: scale(0.98);
}
.odis-share-btn-icon {
    display: inline-flex;
    align-items: center;
    justify-content: center;
}
.odis-share-btn-icon img,
.odis-share-btn-icon svg {
    width: 20px;
    height: 20px;
    display: block;
}
.odis-slack-btn {
    background-color: #4A154B;
    color: #FFFFFF !important;
    border: 1px solid #4A154B;
}
.odis-slack-btn:hover {
    background-color: #611f69;
    border-color: #611f69;
    color: #FFFFFF !important;
}
.odis-slack-btn:active {
    background-color: #38103c;
}
.odis-slack-btn,
.odis-slack-btn:visited,
.odis-slack-btn span {
    color: #FFFFFF !important;
}
.odis-email-btn {
    background-color: #FFD700;
    color: #1B4429 !important;
    border: 1px solid #FFD700;
}
.odis-email-btn:hover {
    background-color: #e6c200;
    border-color: #e6c200;
    color: #1B4429 !important;
}
.odis-email-btn:active {
    background-color: #cca300;
}
.odis-email-btn,
.odis-email-btn:visited,
.odis-email-btn span {
    color: #1B4429 !important;
}
"""

_SHARE_ACTIONS_JS = """
export default function(component) {
    const { parentElement, data } = component;
    const slackBtn = parentElement.querySelector("#odis-share-slack-btn");
    const slackLabel = parentElement.querySelector("#odis-share-slack-text");
    const emailBtn = parentElement.querySelector("#odis-share-email-btn");
    const emailLabel = parentElement.querySelector("#odis-share-email-text");

    if (slackBtn) {
        slackBtn.onclick = async function(e) {
            e.preventDefault();
            console.log("[ODIS] Bouton Slack cliqué !");
            const msg = data?.msg || "";
            try {
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    await navigator.clipboard.writeText(msg);
                }
            } catch (err) {
                console.warn("[ODIS] Erreur lors de la copie:", err);
            }
            if (slackLabel) {
                slackLabel.innerText = "Copié ! Ouverture...";
                setTimeout(() => {
                    if (slackLabel) {
                        slackLabel.innerText = "Partager sur Slack";
                    }
                }, 2500);
            }
            console.log("[ODIS] Lancement de slack://open");
            window.location.href = "slack://open";
        };
    }

    if (emailBtn) {
        if (data?.mailto_url) {
            emailBtn.href = data.mailto_url;
        }
        emailBtn.onclick = function(e) {
            e.preventDefault();
            console.log("[ODIS] Bouton Email cliqué !");
            const url = data?.mailto_url || emailBtn.href;
            if (url) {
                if (emailLabel) {
                    emailLabel.innerText = "Ouverture...";
                    setTimeout(() => {
                        if (emailLabel) {
                            emailLabel.innerText = "Envoyer par Email";
                        }
                    }, 2500);
                }
                console.log("[ODIS] Lancement de mailto:", url);
                window.location.href = url;
            } else {
                console.warn("[ODIS] mailto_url manquant");
            }
        };
    }
}
"""

_share_actions_component = components_v2.component(
    "odis_share_actions_buttons",
    html=_SHARE_ACTIONS_HTML,
    css=_SHARE_ACTIONS_CSS,
    js=_SHARE_ACTIONS_JS,
)
_slack_share_component = _share_actions_component


def _on_pdf_modal_dismiss() -> None:
    """Clear the pending PDF dialog request when the modal is dismissed."""
    clear_dialog(st.session_state, "active_pdf_modal")


@st.dialog("Export des résultats en PDF", on_dismiss=_on_pdf_modal_dismiss)
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
                clear_dialog(st.session_state, "active_pdf_modal")
                st.session_state.pdf_modal_data = None
                st.session_state.pop("pdf_modal_warnings", None)
                st.rerun(scope="app")


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
        request_dialog(st.session_state, "active_pdf_modal")
        st.rerun(scope="app")


def _on_share_dialog_dismiss() -> None:
    """Clear the pending share dialog request when the modal is dismissed."""
    clear_dialog(st.session_state, "active_share_dialog")


@st.dialog("Partager cette recherche", on_dismiss=_on_share_dialog_dismiss)
def share_search_modal():
    """Dialog to generate, display, and copy shared permalink URL."""
    config = st.session_state.get("config")
    search_results = st.session_state.get("search_results")

    if not config or not search_results:
        st.error("Aucune recherche active à partager.")
        return

    # Generate or retrieve active share_id
    if not st.session_state.get("active_share_id"):
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
        share_id = st.session_state.get("active_share_id")

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
    mailto_url = f"mailto:?subject={urllib.parse.quote(subject)}&body={urllib.parse.quote(body, safe=':/?=')}"

    _share_actions_component(
        data={"msg": slack_msg, "mailto_url": mailto_url},
        key=f"share_actions_{share_id}",
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
        request_dialog(st.session_state, "active_share_dialog")
        st.rerun(scope="app")
