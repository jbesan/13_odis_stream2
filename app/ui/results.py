import logging
import time
from typing import Optional
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

import config as cfg
from core.models import (
    CommuneResult,
    SearchResultsData,
)
from core.enrichment_status import is_terminal_refiner_status
from core.postscoring import generate_static_pitch
from agents.utils import (
    launch_background_city_analysis,
    odis_get_bg_result,
)
from core import maps_deck
from ui import ui_telemetry

# Sub-module imports & re-exports for complete backward compatibility
from ui.results_actions import (
    pdf_modal,
    render_export_pdf_button,
    share_search_modal,
    render_share_search_button,
    _is_postscoring_ready_for_search,
    _is_hydration_ready_for_city,
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
from core.postscoring import (
    sync_commune_data,
    sync_search_results_data,
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
    "sync_commune_data",
    "sync_search_results_data",
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


def _is_postscoring_ready_for_city(commune: CommuneResult, h: Optional[str]) -> bool:
    """Return True if prerequisite background post-scoring tasks for this commune are ready."""
    if getattr(commune, "odis_synthesis", None) or getattr(
        commune, "analysis_report", None
    ):
        return True

    if st.session_state.get("immutable_shared_snapshot"):
        return False

    if not h:
        return True

    # 1. Verify data hydrations are complete
    if not _is_hydration_ready_for_city(commune, h):
        return False

    # 2. Refiner status (pitches & briefing)
    if isinstance(getattr(commune, "refiner_pitch", None), str) and bool(
        getattr(commune, "refiner_pitch", None)
    ):
        return True

    bg_res = odis_get_bg_result(h)
    if not isinstance(bg_res, dict):
        return False

    refiner_status = bg_res.get("status_refiner")
    if not is_terminal_refiner_status(refiner_status):
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


def _is_auto_analysis_planned(commune: CommuneResult, h: Optional[str]) -> bool:
    """Return whether the post-scoring coordinator owns this city's analysis."""
    if not h:
        return False
    bg_res = odis_get_bg_result(h)
    if not isinstance(bg_res, dict):
        return False
    steps = bg_res.get("auto_analysis_steps")
    if not isinstance(steps, dict):
        return False
    step = steps.get(commune.codgeo) or steps.get(str(commune.codgeo))
    return isinstance(step, dict) and step.get("status") in {"waiting", "dispatched"}


@st.fragment(run_every=2.0)
def render_ai_trigger_button(commune: CommuneResult, h: Optional[str]) -> bool:
    """Renders the AI Analysis trigger button with up-to-date state in-place.

    Self-refreshing fragment that automatically transitions from preparation to ready,
    reflects live background execution states, and opens the modal dialog on click.
    """
    has_analysis = bool(
        getattr(commune, "analysis_report", None)
        or getattr(commune, "odis_synthesis", None)
    )
    immutable_snapshot = bool(st.session_state.get("immutable_shared_snapshot"))

    # 1. Snapshot mode: strictly read-only
    if immutable_snapshot:
        if has_analysis:
            if st.button(
                "Consulter l'Analyse Avancée",
                key=f"btn_ia_comm_{commune.codgeo}",
                icon=":material/wand_stars:",
                width="stretch",
                disabled=False,
            ):
                st.session_state["active_ia_city_index"] = commune.codgeo
                show_ia_analysis_dialog(commune.codgeo)
            return True
        else:
            st.button(
                "Analyse Avancée (non réalisée)",
                key=f"btn_ia_comm_{commune.codgeo}",
                icon=":material/wand_stars:",
                width="stretch",
                disabled=True,
            )
            return False

    # 2. Live mode: Check background task state
    task_key = f"analysis_{h}_{commune.codgeo}" if h else f"analysis_{commune.codgeo}"
    status_data = odis_get_bg_result(task_key) if h else None
    status = status_data.get("status") if isinstance(status_data, dict) else None

    # 3. Completed state (in memory or freshly finished in bg store)
    if has_analysis or status == "done":
        just_completed = bool(status == "done" and not has_analysis and status_data)
        if just_completed:
            _merge_agent_results(
                status_data.get("result"), str(commune.codgeo), commune
            )

        # Notify via toast once per city
        toasted_set = st.session_state.setdefault("ia_analysis_toasted", set())
        if commune.codgeo not in toasted_set:
            toasted_set.add(commune.codgeo)
            st.toast(
                f"Analyse Avancée pour {commune.name} disponible",
                icon="✨",
                duration="long",
            )

        # If dialog is currently open for this city, refresh page to reveal full synthesis
        if just_completed and st.session_state.get("active_ia_city_index") == commune.codgeo:
            st.rerun()

        if st.button(
            "Analyse Avancée",
            key=f"btn_ia_comm_{commune.codgeo}",
            icon=":material/wand_stars:",
            width="stretch",
            disabled=False,
        ):
            st.session_state["active_ia_city_index"] = commune.codgeo
            ui_telemetry.track_ui_event(
                "run_ia_analysis", {"codgeo": commune.codgeo, "name": commune.name}
            )
            show_ia_analysis_dialog(commune.codgeo)
        return True

    # 4. Error / Timeout / Cancelled -> Retry state
    if status in {"error", "timeout", "cancelled"}:
        if st.button(
            "Analyse Avancée [Échec - Réessayer ?]",
            key=f"btn_ia_comm_{commune.codgeo}",
            icon=":material/error:",
            width="stretch",
        ):
            if h and st.session_state.get("search_results"):
                st.session_state.setdefault(
                    "ia_analysis_launch_toasted", set()
                ).discard(commune.codgeo)
                st.session_state.setdefault("ia_analysis_toasted", set()).discard(
                    commune.codgeo
                )
                launch_background_city_analysis(
                    nom=commune.name,
                    codgeo=commune.codgeo,
                    search_criterias=st.session_state.get("config"),
                    search_results=st.session_state.get("search_results"),
                    h=h,
                    username=st.session_state.get("username", "unknown"),
                    organization_id=getattr(st.session_state.get("org"), "id", None),
                    retry=True,
                    trigger="city_card_retry",
                )
                st.toast(
                    f"Analyse Avancée pour {commune.name} lancée...",
                    icon="🧠",
                    duration="short",
                )
                st.rerun()
        return False

    # 5. Running state
    if status == "running":
        start_time = (
            status_data.get("start_time", 0) if isinstance(status_data, dict) else 0
        )
        elapsed = time.time() - start_time if start_time else 0
        btn_label = (
            "Analyse Avancée [Lancement...]"
            if elapsed < 1.0
            else "Analyse Avancée [En cours...]"
        )
        st.button(
            btn_label,
            key=f"btn_ia_comm_{commune.codgeo}",
            icon=":material/wand_stars:",
            width="stretch",
            disabled=True,
        )
        return False

    # 6. Postscoring readiness check (hydration + refiner)
    ready = _is_postscoring_ready_for_city(commune, h)
    if not ready:
        st.button(
            "Analyse Avancée [Préparation...]",
            key=f"btn_ia_comm_{commune.codgeo}",
            icon=":material/wand_stars:",
            width="stretch",
            disabled=True,
        )
        return False

    # 7. Auto-analysis planned by PostScoringRun coordinator
    if _is_auto_analysis_planned(commune, h):
        st.button(
            "Analyse Avancée [Lancement...]",
            key=f"btn_ia_comm_{commune.codgeo}",
            icon=":material/wand_stars:",
            width="stretch",
            disabled=True,
        )
        return False

    # 8. Ready for manual launch
    can_launch = bool(ready and h and st.session_state.get("search_results"))
    if (
        st.button(
            "Analyse Avancée",
            key=f"btn_ia_comm_{commune.codgeo}",
            icon=":material/wand_stars:",
            width="stretch",
            disabled=not can_launch,
        )
        and can_launch
    ):
        st.session_state["active_ia_city_index"] = commune.codgeo
        ui_telemetry.track_ui_event(
            "run_ia_analysis", {"codgeo": commune.codgeo, "name": commune.name}
        )
        launch_background_city_analysis(
            nom=commune.name,
            codgeo=commune.codgeo,
            search_criterias=st.session_state.get("config"),
            search_results=st.session_state.get("search_results"),
            h=h,
            username=st.session_state.get("username", "unknown"),
            organization_id=getattr(st.session_state.get("org"), "id", None),
            trigger="user_modal",
        )
        st.toast(
            f"Analyse Avancée pour {commune.name} lancée...",
            icon="🧠",
            duration="short",
        )
        st.rerun()
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

    bg_res = odis_get_bg_result(h) if h else None
    if bg_res:
        sync_commune_data(commune, bg_res)
    if commune.refiner_pitch:
        st.markdown(commune.refiner_pitch)
        return True

    refiner_status = bg_res.get("status_refiner") if isinstance(bg_res, dict) else None
    if is_terminal_refiner_status(refiner_status):
        st.markdown(generate_static_pitch(commune))
        return True

    st.info("Analyse des points forts en cours...")
    # st.caption("La synthèse personnalisée apparaîtra ici lorsqu'elle sera prête.")
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

    with st.container(key="city_result_card", border=True):
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
        st.markdown(
            '<style> [class*="st-key-btn_ia"] .stButton button { background-color: #F5D819; color: #1B4429; } </style>',
            unsafe_allow_html=True,
        )

        # st.space("small")
        c1, c2 = st.columns(2)
        # c1, c2, c3 = st.columns(3)
        with c1:
            render_details_trigger_button(commune, h)
        with c2:
            if not cfg.is_ai_free_mode(st.session_state.get("org")):
                render_ai_trigger_button(commune, h)
        # with c3:
        if st.button(
            "Contact local",
            key=f"btn_ccas_commune_{commune.codgeo}",
            icon=":material/phone:",
            type="tertiary",
            width="stretch",
            wrap=True,
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

        # --- Radar Chart with Comparison ---
        # st.space("small")
        # all_cats = [
        #     "emploi",
        #     "logement",
        #     "education",
        #     "sante",
        #     "inclusion",
        #     "mobilite",
        #     "territoire",
        # ]
        # cat_map = {
        #     "emploi": "employment",
        #     "logement": "housing",
        #     "education": "education",
        #     "sante": "health",
        #     "inclusion": "inclusion",
        #     "mobilite": "mobility",
        #     "territoire": "territoire",
        # }

        # config = st.session_state.get("config")
        # if config and hasattr(config, "active_categories") and config.active_categories:
        #     active_cats = [
        #         cat
        #         for cat in all_cats
        #         if cat in config.active_categories or cat in ["mobilite", "territoire"]
        #     ]
        # else:
        #     active_cats = all_cats

        # def get_radar_data(c: CommuneResult, active_cats: List[str]):
        #     label_map = {
        #         "emploi": "Emploi",
        #         "logement": "Logement",
        #         "education": "Éducation",
        #         "sante": "Santé",
        #         "inclusion": "Inclusion",
        #         "mobilite": "Mobilité",
        #         "territoire": "Territoire",
        #     }
        #     labels = [label_map.get(cat, cat.capitalize()) for cat in active_cats]

        #     vals = []
        #     for cat in active_cats:
        #         attr_name = cat_map.get(cat, cat)
        #         data = getattr(c, attr_name, None)
        #         if data and hasattr(data, "cat_score"):
        #             val = float(data.cat_score) if data.cat_score is not None else 0.0
        #             vals.append(val * 100)
        #         else:
        #             vals.append(0.0)

        #     if vals:
        #         vals.append(vals[0])
        #         labels.append(labels[0])
        #     return labels, vals

        # labels_target, vals_target = get_radar_data(commune, active_cats)

        # search_results: SearchResultsData = st.session_state.get("search_results")

        # fig = go.Figure()

        # # Add trace for target city (Green)
        # fig.add_trace(
        #     go.Scatterpolar(
        #         r=vals_target,
        #         theta=labels_target,
        #         fill="toself",
        #         name=libgeo,
        #         fillcolor="rgba(0, 98, 104, 0.5)",
        #         line=dict(color="#006268"),
        #         hovertemplate="%{theta}: %{r:.0f}/100<extra></extra>",
        #     )
        # )

        # # Add trace for current city (Blue) if available
        # if search_results and search_results.current_geo:
        #     _, vals_current = get_radar_data(search_results.current_geo, active_cats)
        #     current_name = search_results.current_geo.name or "Votre ville"

        #     st.text(
        #         f"Comparaison avec {current_name}",
        #         help=f"Comparaison des profils : la zone verte représente **{commune.name}**, la zone bleue **{current_name}**. Une plus grande surface indique une meilleure adéquation avec vos critères.",
        #     )

        #     fig.add_trace(
        #         go.Scatterpolar(
        #             r=vals_current,
        #             theta=labels_target,
        #             fill="toself",
        #             name=current_name,
        #             fillcolor="rgba(31, 119, 180, 0.4)",
        #             line=dict(color="#1f77b4"),
        #             hovertemplate="%{theta}: %{r:.0f}/100<extra></extra>",
        #         )
        #     )

        #     fig.update_layout(
        #         polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        #         showlegend=True,
        #         legend=dict(
        #             orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1
        #         ),
        #         margin=dict(l=50, r=50, t=50, b=50),
        #     )

        #     st.plotly_chart(fig, width="stretch", height=300, config=None)

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
