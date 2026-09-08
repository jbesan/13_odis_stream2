import logging
from typing import List, Optional
import plotly.graph_objects as go
import streamlit as st

import config as cfg
from core.models import (
    CommuneResult,
    SearchResultsData,
)
from core.enrichment_status import (
    is_terminal_enrichment_status,
    is_terminal_refiner_status,
)
from core.postscoring import generate_static_pitch
from agents.utils import (
    is_terminal_graph_run_status,
    odis_get_bg_result,
)
from core import maps_deck

# Sub-module imports & re-exports for complete backward compatibility
from ui.results_actions import (
    pdf_modal,
    render_export_pdf_button,
    share_search_modal,
    render_share_search_button,
    _is_postscoring_ready_for_search,
)
from ui.ccas_dialog import (
    show_ccas_dialog,
    _on_ccas_dialog_dismiss,
)
from ui.ai_analysis_dialog import (
    show_ia_analysis_dialog,
    _on_ia_dialog_dismiss,
    ia_analysis_content,
    _merge_agent_results,
    polling_synthesis_fragment,
    polling_chat_fragment,
    _render_sources_popover,
    _get_or_build_analysis_report,
    _render_initial_analysis_report,
)
from ui.details_dialog import (
    show_details_dialog,
    _on_details_dialog_dismiss,
    _enrichment_status_for_city,
    _should_poll_enrichment,
    sync_background_data,
    _get_jaccueille_salesforce_urls,
    render_jaccueille_housing_info,
    render_associations_enrichment,
    render_inclusion_services_enrichment,
    render_jobs_enrichment,
    polling_associations_fragment,
    polling_inclusion_services_fragment,
    polling_jobs_fragment,
    render_scores_for_category,
)

# Configure Logging
logger = logging.getLogger("ui.results")

__all__ = [
    # Dialogs & dismiss callbacks
    "pdf_modal",
    "share_search_modal",
    "show_ccas_dialog",
    "show_ia_analysis_dialog",
    "show_details_dialog",
    "_on_ccas_dialog_dismiss",
    "_on_ia_dialog_dismiss",
    "_on_details_dialog_dismiss",
    # Trigger and action buttons
    "render_export_pdf_button",
    "render_share_search_button",
    "render_details_trigger_button",
    "render_ai_trigger_button",
    "render_refiner_panel",
    # Readiness and polling helpers
    "_is_postscoring_ready_for_search",
    "_is_hydration_ready_for_city",
    "_is_postscoring_ready_for_city",
    "_enrichment_status_for_city",
    "_should_poll_enrichment",
    "sync_background_data",
    # Detailed sub-renderers
    "_get_jaccueille_salesforce_urls",
    "render_jaccueille_housing_info",
    "render_associations_enrichment",
    "render_inclusion_services_enrichment",
    "render_jobs_enrichment",
    "render_scores_for_category",
    "polling_associations_fragment",
    "polling_inclusion_services_fragment",
    "polling_jobs_fragment",
    "polling_synthesis_fragment",
    "polling_chat_fragment",
    "ia_analysis_content",
    "_render_sources_popover",
    "_get_or_build_analysis_report",
    "_render_initial_analysis_report",
    "_merge_agent_results",
    # Main results listing
    "render_active_dialogs",
    "_display_result_details",
    "_result_highlight_callback",
    "_on_result_feedback",
]


def _is_hydration_ready_for_city(commune: CommuneResult, h: Optional[str]) -> bool:
    """Return True if all data hydrations (jobs, associations, inclusions) for this commune are terminal."""
    if st.session_state.get("immutable_shared_snapshot"):
        return True

    if not h:
        return True

    bg_res = odis_get_bg_result(h)
    if not isinstance(bg_res, dict):
        return False

    codgeo_str = str(commune.codgeo)

    # 1. Jobs enrichment status
    if not (hasattr(commune, "siae_jobs") and getattr(commune, "siae_jobs", None) is not None):
        jobs_status = (
            bg_res.get("jobs_enrichment", {}).get(codgeo_str, {}).get("status")
        )
        if not is_terminal_enrichment_status(jobs_status):
            return False

    # 2. Associations enrichment status
    if not (
        hasattr(commune, "associations_details")
        and getattr(commune, "associations_details", None) is not None
    ):
        assos_status = (
            bg_res.get("association_enrichment_status", {})
            .get(codgeo_str, {})
            .get("status")
        )
        if not is_terminal_enrichment_status(assos_status):
            return False

    # 3. Inclusion services enrichment status
    if not (
        hasattr(commune, "inclusion")
        and getattr(commune.inclusion, "services_detailed", None) is not None
    ):
        inc_status = (
            bg_res.get("inclusion_enrichment_status", {})
            .get(codgeo_str, {})
            .get("status")
        )
        if inc_status is not None and not is_terminal_enrichment_status(inc_status):
            return False

    return True


def _is_postscoring_ready_for_city(commune: CommuneResult, h: Optional[str]) -> bool:
    """Return True if all background post-scoring tasks for this commune have reached a terminal state."""
    if getattr(commune, "odis_synthesis", None) or getattr(commune, "analysis_report", None):
        return True

    if st.session_state.get("immutable_shared_snapshot"):
        return False

    if not h:
        return True

    # 1. Verify data hydrations are complete
    if not _is_hydration_ready_for_city(commune, h):
        return False

    bg_res = odis_get_bg_result(h)
    if not isinstance(bg_res, dict):
        return False

    # 2. Refiner status (pitches & briefing)
    refiner_status = bg_res.get("status_refiner")
    if not is_terminal_refiner_status(refiner_status):
        return False

    codgeo_str = str(commune.codgeo)

    # 3. Automated city analysis status (if enabled)
    if cfg.is_auto_analyse_top_cities_enabled():
        auto_run = odis_get_bg_result(f"analysis_{h}_{codgeo_str}")
        if isinstance(auto_run, dict):
            auto_status = auto_run.get("status")
            if not is_terminal_graph_run_status(auto_status):
                return False

    return True


@st.fragment(run_every=2.0)
def render_details_trigger_button(commune: CommuneResult, h: Optional[str]) -> bool:
    """Renders the 'En savoir plus' button with up-to-date hydration state in-place."""
    ready = _is_hydration_ready_for_city(commune, h)
    btn_label = "En savoir plus" if ready else "En savoir plus (Préparation...)"
    btn_disabled = not ready

    if st.button(
        btn_label,
        key=f"btn_details_comm_{commune.codgeo}",
        icon=":material/data_exploration:",
        type="primary",
        width="stretch",
        disabled=btn_disabled,
    ):
        st.session_state.active_details_index = commune.codgeo
        show_details_dialog(commune.codgeo)

    return ready


@st.fragment(run_every=2.0)
def render_ai_trigger_button(commune: CommuneResult, h: Optional[str]) -> bool:
    """Renders the AI Analysis trigger button with up-to-date state in-place."""
    has_analysis = bool(
        getattr(commune, "analysis_report", None)
        or getattr(commune, "odis_synthesis", None)
    )
    ready = _is_postscoring_ready_for_city(commune, h)
    immutable_snapshot = bool(st.session_state.get("immutable_shared_snapshot"))

    if has_analysis:
        btn_label = "Consulter l'Analyse Avancée" if immutable_snapshot else "Analyse Avancée"
        btn_disabled = False
    elif immutable_snapshot:
        btn_label = "Analyse Avancée (non réalisée)"
        btn_disabled = True
    elif not ready:
        btn_label = "Analyse Avancée (Préparation...)"
        btn_disabled = True
    else:
        btn_label = "Analyse Avancée"
        btn_disabled = False

    if st.button(
        btn_label,
        key=f"btn_ia_comm_{commune.codgeo}",
        icon=":material/wand_stars:",
        width="stretch",
        disabled=btn_disabled,
    ):
        st.session_state.active_ia_city_index = commune.codgeo
        show_ia_analysis_dialog(commune.codgeo)

    return ready


@st.fragment(run_every=2.0)
def render_refiner_panel(commune: CommuneResult, h: Optional[str]) -> bool:
    """Render one stable refiner-panel state and return whether it is final.

    The panel deliberately stays in a processing state until the refiner has a
    terminal result. This avoids replacing a deterministic summary while the
    user is reading it. A terminal failure falls back to the deterministic top
    three contributors instead of leaving an empty AI-shaped gap.
    """
    if st.session_state.get("immutable_shared_snapshot"):
        if commune.refiner_pitch:
            st.markdown(commune.refiner_pitch)
        else:
            st.markdown(generate_static_pitch(commune))
        return True

    sync_background_data(commune, h)
    if commune.refiner_pitch:
        st.markdown(commune.refiner_pitch)
        return True

    bg_res = odis_get_bg_result(h) if h else None
    refiner_status = bg_res.get("status_refiner") if isinstance(bg_res, dict) else None
    if is_terminal_refiner_status(refiner_status):
        st.markdown(generate_static_pitch(commune))
        return True

    st.info("Analyse des points forts en cours...")
    st.caption("La synthèse personnalisée apparaîtra ici lorsqu'elle sera prête.")
    return False


def _result_highlight_callback(index: int) -> None:
    """Callback to handle highlighting a result by its index in the top results."""
    search_results: SearchResultsData = st.session_state.get("search_results")
    if not search_results:
        return

    if index == -1:
        if not search_results.commune_pressentie:
            return
        commune = search_results.commune_pressentie
    else:
        if index < 0 or index >= len(search_results.results):
            return
        commune = search_results.results[index]

    is_highlighted, highlighted_rank = st.session_state.get(
        "highlighted_result", [False, None]
    )

    # If the same button is clicked again, un-highlight it
    if is_highlighted and index == highlighted_rank:
        st.session_state["highlighted_result"] = [False, None]
        st.session_state["zoom"] = None
        st.session_state["center"] = st.session_state.get(
            "initial_center", list(cfg.DEFAULT_MAP_CENTER)
        )
    else:
        st.session_state["highlighted_result"] = [True, index]
        c_pt = maps_deck._get_geom(
            commune, "centroid", gdf_context=st.session_state.get("processed_gdf")
        )
        if c_pt:
            st.session_state["center"] = [c_pt.y, c_pt.x]
        st.session_state["zoom"] = cfg.DETAIL_MAP_ZOOM


def _on_result_feedback(cid: str, c_name: str, score: float, fb_key: str) -> None:
    """Callback for st.feedback to submit relevance directly to BQ."""
    val = st.session_state.get(fb_key)
    if val is not None:
        # Avoid duplicate submission for the same selection state during reruns/fragment updates
        submission_key = f"last_submitted_{fb_key}"
        if st.session_state.get(submission_key) == val:
            return

        try:
            from ui.feedback import _submit_to_bq
            import json

            context = json.dumps({"codgeo": cid, "libgeo": c_name, "score": score})
            # st.feedback values are 0-4 (5 faces), we map to 1-5 for BQ
            if _submit_to_bq("Result Relevance", str(val + 1), context=context):
                st.session_state[submission_key] = val
                logger.info(f"✨ Feedback submitted for {c_name} ({cid}): {val + 1}")
        except Exception as e:
            logger.error(f"Failed to submit result feedback: {e}")


def render_active_dialogs() -> None:
    """Open any result dialog requested by a button on the current rerun."""
    active_ia_index = st.session_state.get("active_ia_city_index")
    if active_ia_index is not None:
        show_ia_analysis_dialog(active_ia_index)

    active_details_index = st.session_state.get("active_details_index")
    if active_details_index is not None:
        show_details_dialog(active_details_index)

    active_ccas_index = st.session_state.get("active_ccas_index")
    if active_ccas_index is not None:
        show_ccas_dialog(active_ccas_index)


def _display_result_details(commune: CommuneResult) -> None:
    """Displays the detailed information for a single search result (Commune)."""
    h = st.session_state.get("active_search_hash")

    with st.container(key='city_result_card', border=True):
        # --- Pitch ---
        population = f"{commune.population:,}".replace(",", " ")
        libgeo = commune.name
        score_val = f"{commune.global_score * 100:.0f}/100"

        st.markdown(
            f"**{libgeo}** ({population} habitants) fait partie du bassin de vie de : **{commune.name_bdv}**.  Le score de cette recherche est de **{score_val}**."
        )

        # The narrative stays a processing panel until it reaches one final
        # state (AI result or deterministic fallback). It never replaces text
        # that was already shown as a provisional summary.
        render_refiner_panel(commune, h)

        st.space("small")
        c1, c2 = st.columns(2)
        with c1:
            render_details_trigger_button(commune, h)
        with c2:
            if st.button(
                "Contact local",
                key=f"btn_ccas_commune_{commune.codgeo}",
                icon=":material/phone:",
                type="secondary",
                width="stretch",
                disabled=bool(st.session_state.get("immutable_shared_snapshot")),
                help=(
                    "Les coordonnées locales en direct ne font pas partie de "
                    "cet instantané partagé."
                    if st.session_state.get("immutable_shared_snapshot")
                    else None
                ),
            ):
                st.session_state.active_ccas_index = commune.codgeo
                show_ccas_dialog(commune.codgeo)

        # F-IA: AI Dialog Trigger (Session State based)
        if not cfg.is_ai_free_mode():
            st.markdown(
                '<style> [class*="st-key-btn_ia"] .stButton button { background-color: #F5D819; color: #1B4429; } </style>',
                unsafe_allow_html=True,
            )

            render_ai_trigger_button(commune, h)

        # --- Radar Chart with Comparison ---
        st.space("small")
        all_cats = [
            "emploi",
            "logement",
            "education",
            "sante",
            "inclusion",
            "mobilite",
            "territoire",
        ]
        cat_map = {
            "emploi": "employment",
            "logement": "housing",
            "education": "education",
            "sante": "health",
            "inclusion": "inclusion",
            "mobilite": "mobility",
            "territoire": "territoire",
        }

        config = st.session_state.get("config")
        if config and hasattr(config, "active_categories") and config.active_categories:
            active_cats = [
                cat
                for cat in all_cats
                if cat in config.active_categories or cat in ["mobilite", "territoire"]
            ]
        else:
            active_cats = all_cats

        def get_radar_data(c: CommuneResult, active_cats: List[str]):
            label_map = {
                "emploi": "Emploi",
                "logement": "Logement",
                "education": "Éducation",
                "sante": "Santé",
                "inclusion": "Inclusion",
                "mobilite": "Mobilité",
                "territoire": "Territoire",
            }
            labels = [label_map.get(cat, cat.capitalize()) for cat in active_cats]

            vals = []
            for cat in active_cats:
                attr_name = cat_map.get(cat, cat)
                data = getattr(c, attr_name, None)
                if data and hasattr(data, "cat_score"):
                    val = float(data.cat_score) if data.cat_score is not None else 0.0
                    vals.append(val * 100)
                else:
                    vals.append(0.0)

            if vals:
                vals.append(vals[0])
                labels.append(labels[0])
            return labels, vals

        labels_target, vals_target = get_radar_data(commune, active_cats)

        search_results: SearchResultsData = st.session_state.get("search_results")

        fig = go.Figure()

        # Add trace for target city (Green)
        fig.add_trace(
            go.Scatterpolar(
                r=vals_target,
                theta=labels_target,
                fill="toself",
                name=libgeo,
                fillcolor="rgba(0, 98, 104, 0.5)",
                line=dict(color="#006268"),
                hovertemplate="%{theta}: %{r:.0f}/100<extra></extra>",
            )
        )

        # Add trace for current city (Blue) if available
        if search_results and search_results.current_geo:
            _, vals_current = get_radar_data(search_results.current_geo, active_cats)
            current_name = search_results.current_geo.name or "Votre ville"

            st.text(
                f"Comparaison avec {current_name}",
                help=f"Comparaison des profils : la zone verte représente **{commune.name}**, la zone bleue **{current_name}**. Une plus grande surface indique une meilleure adéquation avec vos critères.",
            )

            fig.add_trace(
                go.Scatterpolar(
                    r=vals_current,
                    theta=labels_target,
                    fill="toself",
                    name=current_name,
                    fillcolor="rgba(31, 119, 180, 0.4)",
                    line=dict(color="#1f77b4"),
                    hovertemplate="%{theta}: %{r:.0f}/100<extra></extra>",
                )
            )

            fig.update_layout(
                polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
                showlegend=True,
                legend=dict(
                    orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
                ),
                margin=dict(l=50, r=50, t=50, b=50),
            )

            st.plotly_chart(fig, width="stretch", height=300, config=None)

        st.divider()
        with st.container(
            horizontal=True,
            horizontal_alignment="center",
            key=f"faces_feedback_container_{commune.codgeo}",
        ):
            st.text("Évaluez la pertinence de ce résultat")
            fb_key = f"fb_result_{commune.codgeo}"
            st.feedback(
                "faces",
                key=fb_key,
                on_change=_on_result_feedback,
                args=(commune.codgeo, commune.name, commune.global_score, fb_key),
                width="content",
            )
