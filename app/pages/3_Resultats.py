import logging
import pandas as pd
import streamlit as st

from core import maps_deck
from core.models import SearchResultsData
from services.app_session import AppSession
from services.search_controller import SearchController
from ui import forms as ui_forms
from ui import page_shell
from ui import results as ui_results
from ui import map_vector
from ui.form_state import FormState
from utils import data_loader

logger = logging.getLogger(__name__)

st.set_page_config(page_title="OD&IS", page_icon="👋", layout="wide")

# Full-bleed native MapGL map with a small number of stable, page-owned
# overlay selectors.  Keep the style-only payload outside the normal Markdown
# flow so it does not leave a layout element behind when pages are switched.
st.html(
    """
    <style>
    /* Activate the full-bleed layout only while the Results page is mounted. */
    [data-testid="stMain"]:has([class*="st-key-top_pills_bar"]),
    [data-testid="stMain"]:has([class*="st-key-top_pills_bar"])
    [data-testid="stMainBlockContainer"] {
        position: relative !important;
        overflow: hidden !important;
    }
    [data-testid="stMain"]:has([class*="st-key-top_pills_bar"])
    [data-testid="stMainBlockContainer"] {
        width: 100% !important;
        max-width: none !important;
        height: 100vh !important;
        min-height: 100vh !important;
        padding: 0 !important;
        margin: 0 !important;
        isolation: isolate;
    }
    [data-testid="stAppViewBlockContainer"]:has([class*="st-key-top_pills_bar"])
    > div:first-child {
        padding-top: 0 !important;
    }

    /* Native MapGL is rendered by components.v1.html inside stIFrame. */
    [data-testid="stMain"]:has([class*="st-key-top_pills_bar"])
    div[data-testid="stElementContainer"]:has(> iframe[data-testid="stIFrame"]) {
        position: absolute !important;
        inset: 0 !important;
        width: 100% !important;
        height: 100vh !important;
        min-height: 100vh !important;
        padding: 0 !important;
        margin: 0 !important;
        z-index: 0 !important;
    }
    [data-testid="stMain"]:has([class*="st-key-top_pills_bar"])
    div[data-testid="stElementContainer"]:has(> iframe[data-testid="stIFrame"])
    > iframe[data-testid="stIFrame"] {
        position: absolute !important;
        inset: 0 !important;
        width: 100% !important;
        height: 100% !important;
        min-height: 0 !important;
        border: 0 !important;
    }

    /* Shared-search notice: visible, but it does not consume map height. */
    div[class*="st-key-results_snapshot_notice"] {
        position: absolute !important;
        top: 1rem !important;
        left: 50% !important;
        transform: translateX(-50%) !important;
        width: min(38rem, calc(100% - 2rem)) !important;
        z-index: 10 !important;
        pointer-events: none;
    }
    div[class*="st-key-results_snapshot_notice"] [data-testid="stAlert"] {
        pointer-events: auto;
        margin: 0 !important;
    }

    /* Floating controls. Their own keyed blocks are the only positioned nodes. */
    div[class*="st-key-top_pills_bar"] {
        position: absolute !important;
        top: 1rem !important;
        right: 1rem !important;
        left: auto !important;
        width: fit-content !important;
        max-width: calc(100% - 2rem) !important;
        padding: 0.35rem 0.5rem !important;
        z-index: 30 !important;
        background: rgba(255, 255, 255, 0.5) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border-radius: 999px !important;
        box-shadow: 0 0.5rem 1.5rem rgba(0, 0, 0, 0.14), 0 0 0 1px rgba(0, 0, 0, 0.06) !important;
    }
    div[class*="st-key-top_pills_bar"] [data-testid="stHorizontalBlock"] {
        width: fit-content !important;
        max-width: 100% !important;
    }

    /* Legend stays below the result panel and explains the choropleth scale. */
    div[class*="st-key-legend_floating_box"] {
        position: absolute !important;
        right: 1rem !important;
        left: auto !important;
        top: auto !important;
        bottom: 2rem !important;
        width: min(24rem, calc(100% - 2rem)) !important;
        max-width: calc(100% - 2rem) !important;
        box-sizing: border-box !important;
        padding: 0.75rem 1rem !important;
        z-index: 25 !important;
        background: rgba(255, 255, 255, 0.5) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border-radius: 0.9rem !important;
        box-shadow: 0 0.75rem 2rem rgba(0, 0, 0, 0.16), 0 0 0 1px rgba(0, 0, 0, 0.07) !important;
    }

    div[class*="st-key-results_floating_panel"] {
        position: absolute !important;
        top: 1rem !important;
        left: 1rem !important;
        width: min(26rem, calc(100% - 2rem)) !important;
        max-width: calc(100% - 2rem) !important;
        max-height: calc(100vh - 2rem) !important;
        box-sizing: border-box !important;
        overflow-x: hidden !important;
        overflow-y: auto !important;
        padding: 1rem !important;
        z-index: 20 !important;
        background: rgba(255, 255, 255, 1.0) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        border-radius: 1rem !important;
        box-shadow: 0 1.25rem 2.5rem rgba(0, 0, 0, 0.2), 0 0 0 1px rgba(0, 0, 0, 0.08) !important;
    }
    div[class*="st-key-results_floating_panel"]::-webkit-scrollbar {
        width: 5px;
    }
    div[class*="st-key-results_floating_panel"]::-webkit-scrollbar-thumb {
        background: rgba(0, 0, 0, 0.22);
        border-radius: 4px;
    }

    @media (max-width: 900px) {
        div[class*="st-key-top_pills_bar"] {
            top: 0.75rem !important;
            right: 0.75rem !important;
            left: 0.75rem !important;
            width: auto !important;
            overflow-x: auto !important;
        }
        div[class*="st-key-results_floating_panel"] {
            top: 4.5rem !important;
            left: 0.75rem !important;
            width: calc(100% - 1.5rem) !important;
            max-width: calc(100% - 1.5rem) !important;
            max-height: calc(100vh - 12rem) !important;
        }
        div[class*="st-key-legend_floating_box"] {
            right: 0.75rem !important;
            bottom: 0.75rem !important;
            width: calc(100% - 1.5rem) !important;
            max-width: calc(100% - 1.5rem) !important;
        }
    }
    .odis-map-legend {
        display: grid;
        gap: 0.6rem;
        color: #374151;
        font-size: 0.78rem;
        line-height: 1;
        margin-bottom: 1rem !important;
    }
    .odis-map-legend-title {
        color: #1b4429;
        font-weight: 700;
    }
    .odis-map-legend-scale {
        display: grid;
        grid-template-columns: auto 1fr auto;
        gap: 0.5rem;
        align-items: center;
    }
    .odis-map-legend-gradient {
        display: block;
        height: 0.65rem;
        min-width: 8rem;
        border-radius: 999px;
    }
    .odis-map-legend-range,
    .odis-map-legend-markers {
        display: flex;
        gap: 0.75rem;
        flex-wrap: wrap;
    }
    .odis-map-legend-range {
        justify-content: space-between;
        color: #6b7280;
    }
    .odis-map-legend-markers {
        padding-top: 0.15rem;
        border-top: 1px solid rgba(27, 68, 41, 0.12);
    }
    .odis-map-legend-marker-item {
        display: inline-flex;
        gap: 0.3rem;
        align-items: center;
        white-space: nowrap;
    }
    .odis-map-legend-marker {
        width: 0.65rem;
        height: 0.65rem;
        border: 1px solid rgba(27, 68, 41, 0.45);
        border-radius: 50%;
    }
    </style>
    """,
)

page_shell.enter_page("Resultats", handle_shared_search=True)


# --- Session/controller convention ---
app_session = AppSession(st.session_state)
app_session.ensure_result_view()
search_controller = SearchController(app_session)


# --- PDF Modal Execution (moved to bottom for reliability) ---

is_immutable_snapshot = bool(st.session_state.get("immutable_shared_snapshot"))
is_editing_snapshot = bool(st.session_state.get("shared_snapshot_editing"))

# A snapshot is self-contained. Only a live search or an explicit fork loads
# the complete release, including the referentials dataset.
if is_immutable_snapshot and not is_editing_snapshot:
    data_loader.initialize_session_state()
    app_data = None
else:
    with st.spinner("Chargement des indicateurs et données territoriales..."):
        app_data = data_loader.ensure_data_initialized()

# This page deliberately does not render the form except inside the dialog.
# Keep native widget keys alive across full Results-page reruns so Streamlit's
# multipage cleanup cannot turn an unsaved draft back into defaults.
if not is_immutable_snapshot or is_editing_snapshot:
    FormState(st.session_state).preserve_widgets_across_steps()

search_results: SearchResultsData = st.session_state.get("search_results")


def run_search() -> None:
    """Collect the draft and delegate the complete lifecycle to the controller."""
    complete_data = data_loader.ensure_data_initialized()
    config = ui_forms.create_search_criterias_from_inputs(complete_data)
    search_controller.execute(config, complete_data)


def prepare_search_criteria_editor(complete_data: dict) -> None:
    """Restore the active search exactly once before opening its editor."""
    active_config = st.session_state.get("config")
    if active_config is None:
        return
    FormState(st.session_state).prepare_editor(
        active_config,
        source_hash=active_config.compute_hash(),
        app_data=complete_data,
    )


@st.dialog(
    "Modifier les critères de recherche",
    width="large",
    icon=":material/edit:",
    on_dismiss="rerun",
)
def edit_search_criteria_dialog(complete_data: dict) -> None:
    """Edit widget state without rerunning the results page or PyDeck map."""
    ui_forms.display_input_tabs(complete_data)
    with st.container(horizontal=True, horizontal_alignment="right"):
        if st.button(
            "Relancer la recherche",
            type="primary",
            icon=":material/search:",
            key="rerun_search_from_criteria_editor",
        ):
            run_search()
            st.rerun()


# Submit from the form always replaces a prior result with the current draft.
if st.session_state.get("form_completed"):
    run_search()
    st.session_state["form_completed"] = False

# --- UI LAYOUT ---


def action_buttons_container_static(h: str):
    ui_results.render_export_pdf_button(h)
    ui_results.render_share_search_button(
        h=h, button_text="Partager les résultats", key_prefix="sidebar_share"
    )


# Sidebar
with st.sidebar:
    page_shell.render_sidebar_logo()

    st.write("")
    st.markdown(
        "Découvrez les lieux de vie correspondant le mieux au projet renseigné. Les scores vous permettent de comparer facilement leurs atouts.",
        unsafe_allow_html=True,
    )
    st.divider()

    # --- Action de modification des critères ---
    if not is_immutable_snapshot or is_editing_snapshot:
        if st.button(
            "Modifier la recherche",
            width="stretch",
            type="primary",
            icon=":material/edit:",
            key="open_results_criteria_editor",
        ):
            prepare_search_criteria_editor(app_data)
            edit_search_criteria_dialog(app_data)
    else:
        if st.button(
            "Modifier les critères",
            width="stretch",
            type="primary",
            key="fork_shared_snapshot",
            icon=":material/edit:",
        ):
            search_controller.begin_snapshot_edit()
            st.rerun()


    # --- Export to PDF & Partager ---
    if st.session_state.get("search_results") is not None:
        h = st.session_state.search_results.search_hash
        # Deterministic results are immediately shareable/exportable. Optional
        # providers must not hold these actions in a permanent loading state.
        action_buttons_container_static(h)

    st.divider()
    # --- Navigation / Actions secondaires ---
    page_shell.render_primary_sidebar_actions(show_home=True, show_feedback=True)
    page_shell.render_account_sidebar_actions()


# The custom results layout does not call display_results_list(), so dispatch
# active result dialogs explicitly on the full rerun triggered by each action.
ui_results.render_active_dialogs()

# Global Pitch (Strategic intro + Loading state)
# if st.session_state.get('search_results'):
#     h = st.session_state.search_results.search_hash
# @st.fragment(run_every=3.0)
# def global_pitch_container(h: str):
#     ui_results.render_global_pitch(h)
# global_pitch_container(h)

# Main results & full-screen map layout
if st.session_state.get("processed_gdf") is not None:
    config = st.session_state.get("config")
    search_results = st.session_state.get("search_results")
    h = search_results.search_hash if search_results else None
    snapshot_mode = bool(st.session_state.get("immutable_shared_snapshot"))
    current_map_context = st.session_state.get("snapshot_current_map_context")
    if not isinstance(current_map_context, pd.DataFrame):
        current_map_context = st.session_state.processed_gdf

    # Default zoom if not set
    if st.session_state.get("zoom") is None:
        st.session_state["zoom"] = maps_deck.get_map_zoom(
            config.loc_search_area if config else "departement"
        )

    # 1. Floating Box 1: pastilles de couches (top-right)
    selected_ids = set()
    with st.container(key="top_pills_bar", horizontal=True, vertical_alignment="center"):
        st.space('xxsmall')
        st.text("  Afficher: ")
        pill_specs = [
            # ("🥇 Top 5", "top_5"),
        ]
        if not snapshot_mode:
            pill_specs.append(("🏛️ Mairies", "mairie"))
            if config:
                if config.nb_enfants > 0:
                    pill_specs.append(("🎓 Éducation", "edu"))
                if getattr(config, "besoin_sante", []):
                    pill_specs.append(("🏥 Santé", "sante"))
                if config.inc_services_selection:
                    pill_specs.append(("🤝 Inclusion", "inc"))

        pill_options = [label for label, _ in pill_specs]
        pill_id_map = dict(pill_specs)
        existing_pills = st.session_state.get("map_layers_pills")
        if not isinstance(existing_pills, list):
            st.session_state["map_layers_pills"] = [pill_options[0]] if pill_options else []
        else:
            st.session_state["map_layers_pills"] = [
                pill for pill in existing_pills if pill in pill_options
            ]
        if pill_options:
            selected_pills = st.pills(
                "Afficher sur la carte :",
                pill_options,
                selection_mode="multi",
                key="map_layers_pills",
                label_visibility="collapsed",
            )
        else:
            selected_pills = []
        selected_ids = {pill_id_map[p] for p in (selected_pills or []) if p in pill_id_map}
        # show_top_5 = "top_5" in selected_ids
        show_top_5 = True

    # 2. Floating Box 2: choropleth legend (bottom-left)
    legend_markers = []
    if show_top_5:
        legend_markers.append(("#D63E2A", "Top 5"))
        if search_results and search_results.commune_pressentie:
            legend_markers.append(("#F5D819", "Ville souhaitée"))
    if not snapshot_mode:
        if "mairie" in selected_ids:
            legend_markers.append(("#F5D819", "Mairies"))
        if "edu" in selected_ids:
            legend_markers.append(("#22C55E", "Écoles"))
        if "sante" in selected_ids:
            legend_markers.append(("#3B82F6", "Santé"))
        if "inc" in selected_ids:
            legend_markers.append(("#A855F7", "Inclusion"))

    with st.container(key="legend_floating_box"):
        st.markdown(
            maps_deck.build_choropleth_legend_html(legend_markers),
            unsafe_allow_html=True,
        )

    is_highlighted, highlighted_index = st.session_state.highlighted_result

    # 3. Floating Box 3: Volet de résultats (Top 5 + Accordéon à gauche)
    with st.container(key="results_floating_panel", border=False):
        if search_results and search_results.results:
            bg_res = ui_results.odis_get_bg_result(h) if h else None
            if bg_res:
                for c in search_results.results:
                    ui_results.sync_background_data(c, h)
                if search_results.commune_pressentie:
                    ui_results.sync_background_data(search_results.commune_pressentie, h)
                if "odis_brief" in bg_res and st.session_state.get("config"):
                    brief_val = bg_res["odis_brief"]
                    if brief_val and st.session_state.config.odis_brief != brief_val:
                        st.session_state.config.odis_brief = brief_val

            st.subheader("Meilleurs Résultats")
            if not is_highlighted:
                st.caption("👇 Cliquez sur une ville pour afficher les détails.", text_alignment="center", width="stretch")
            st.html(
                '<style> [class*="st-key-btn_top"] .stButton button div, [class*="st-key-btn_top"] .stButton button p { justify-content: flex-start !important; text-align: left !important; width: 100%; } </style>',
            )
            # A. Ville Souhaitée (if present)
            if search_results.commune_pressentie:
                p_commune = search_results.commune_pressentie
                is_active = is_highlighted and highlighted_index == -1
                btn_type = "primary" if is_active else "secondary"
                score_pct = f"{p_commune.global_score * 100:.0f}/100"

                st.button(
                    f"**{score_pct}** - {p_commune.name}",
                    help=f"Ville Souhaitée : {p_commune.name}",
                    key="btn_top_pressentie",
                    type="secondary",
                    width="stretch",
                    on_click=ui_results._result_highlight_callback,
                    args=(-1,),
                )
                if is_active:
                    with st.container(border=True):
                        ui_results._display_result_details(p_commune)
                    st.write("")

            # B. Top 5 Results (Vertical list)
            for i, c in enumerate(search_results.results[:5]):
                is_active = is_highlighted and highlighted_index == i
                btn_type = "primary" if is_active else "secondary"
                score_pct = f"{c.global_score * 100:.0f}/100"
                st.button(
                    f"**{score_pct}** - {c.name}",
                    help=f"Top {i+1} : {c.name}",
                    key=f"btn_top_{i+1}",
                    # type=btn_type,
                    icon=f":material/counter_{i+1}:",
                    type="primary",
                    width="stretch",
                    on_click=ui_results._result_highlight_callback,
                    args=(i,),
                )
                if is_active:
                    # with st.container(border=True):
                    ui_results._display_result_details(c)
                    st.write("")

            

    # 3. Main Full-Screen Vector Map (Background canvas)
    # Offset center slightly to the right to leave space for left overlay panel
    zoom_current = st.session_state.get("zoom", 6) or 6
    offset_lon = -0.1 * (2 ** max(0, 6 - zoom_current))

    try:
        map_vector.render_vector_map(
            gdf_scores=st.session_state.processed_gdf,
            center=st.session_state.get("center"),
            zoom=st.session_state.get("zoom"),
            search_results=search_results,
            config=config,
            pois_df=app_data.get("pois") if (app_data and not snapshot_mode) else None,
            selected_ids=selected_ids,
            highlighted_rank=highlighted_index if is_highlighted else None,
            show_top_5=show_top_5,
            current_map_context=current_map_context,
            center_offset_lon=offset_lon,
            inclusion_services_index=app_data.get("inclusion_services_index") if app_data else None,
            height=1500,
        )
    except Exception as e:
        st.error(f"Erreur d'affichage de la carte: {e}")
        logger.error(f"❌ [MAP-ERROR] map_vector: {e}")
