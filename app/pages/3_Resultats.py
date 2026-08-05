import streamlit as st
from streamlit_folium import st_folium
from core import scoring
import config as cfg
from ui import components as ui
from ui import forms as ui_forms
from ui import results as ui_results
from ui import feedback
from utils import common as utils
from core import maps
import folium as flm
from utils import data_loader
import pandas as pd
import logging
import gc
from agents.utils import odis_get_bg_result
from core.postscoring import launch_post_scoring_tasks
from core.models import SearchResultsData

logger = logging.getLogger(__name__)

st.set_page_config(page_title="OD&IS", page_icon="👋", layout="wide")

# Reduce padding around results
st.markdown(
    """
<style>
    .stMainBlockContainer {
        padding: 2rem;
    }
</style>
""",
    unsafe_allow_html=True,
)

# --- Authentication ---
from utils import auth
from services import telemetry

if not auth.check_password():
    st.stop()

telemetry.log_page_view("Resultats")


# --- Session State Initialization ---
if "highlighted_result" not in st.session_state:
    st.session_state["highlighted_result"] = [False, None]
if "fgs_to_show" not in st.session_state:
    st.session_state["fgs_to_show"] = set()
if "center" not in st.session_state:
    st.session_state["center"] = [46.5, 2.5]  # Default France
if "zoom" not in st.session_state:
    st.session_state["zoom"] = 6
if "active_ia_city_index" not in st.session_state:
    st.session_state["active_ia_city_index"] = None
if "active_details_index" not in st.session_state:
    st.session_state["active_details_index"] = None
if "active_ccas_index" not in st.session_state:
    st.session_state["active_ccas_index"] = None


# --- PDF Modal Execution (moved to bottom for reliability) ---

# --- Shared Search Restoration (Direct link access) ---
# This runs after authentication and before choosing the data tier. An immutable
# snapshot must never rescore itself against today's datasets just to render.
if "search" in st.query_params:
    from services import share_service

    share_service.restore_shared_search_from_query_params()

if "share_error" in st.session_state and st.session_state.share_error:
    err_msg = st.session_state.pop("share_error")
    st.toast(err_msg, icon="⚠️")
    st.error(f"⚠️ {err_msg}")

is_immutable_snapshot = bool(st.session_state.get("immutable_shared_snapshot"))
is_editing_snapshot = bool(st.session_state.get("shared_snapshot_editing"))

# Immutable display only needs the light reference tier used by the criteria
# adapter. Editing/forking or a normal search uses the full scoring tier.
with st.spinner("Chargement des indicateurs et données territoriales..."):
    app_data = data_loader.ensure_data_initialized(
        load_heavy=not is_immutable_snapshot or is_editing_snapshot
    )

search_results: SearchResultsData = st.session_state.get("search_results")


def run_search():
    """
    Callback function for the 'Lancer la recherche' button.
    It orchestrates the new filtering and scoring logic.
    """
    logging.info("--- Running new search with refactored logic ---")
    gc.collect()

    # Clear any previously generated PDF data and share ID on new search
    st.session_state["pdf_data"] = None
    st.session_state["pdf_modal_data"] = None
    st.session_state["active_share_id"] = None
    st.session_state["immutable_shared_snapshot"] = False
    st.session_state.pop("shared_snapshot_editing", None)
    st.session_state.pop("shared_snapshot_version", None)
    st.session_state.pop("shared_snapshot_data_release", None)
    st.session_state.pop("shared_snapshot_created_at", None)
    st.session_state.pop("shared_snapshot_has_map", None)
    st.session_state.pop("snapshot_current_map_context", None)

    from services import telemetry

    telemetry.reset_interaction_id()

    # A new search always owns the complete bundle, including menu metrics and
    # scoring inputs. This is already warm in the usual case.
    app_data = data_loader.ensure_data_initialized(load_heavy=True)
    config = ui_forms.create_search_criterias_from_inputs(app_data)
    st.session_state["config"] = config

    # Get required dataframes from global cached app_data
    # Capture the release identity at execution time. It travels with any
    # immutable shared snapshot produced from this search.
    st.session_state["active_data_release"] = data_loader.get_data_mtime()
    df_all_communes = app_data["odis"]
    df_bv_geo = app_data["bv_geo"]
    start_commune = df_all_communes.loc[[config.commune_actuelle.code]]

    # --- Run Scoring Pipeline (Optimized) ---
    # Instantiate the stateless engine with current data
    engine = scoring.ScoringEngine(
        df_all_communes=df_all_communes,
        df_bv_geo=df_bv_geo,
        scores_cat=app_data["scores_cat"],
        incl_index=app_data["incl_index"],
        associations_data=app_data["associations_data"],
        formations_data=app_data["formations_data"],
        codformations_index=app_data["codformations_index"],
        waldec_index=app_data["waldec_index"],
        global_stats={},
        refugee_associations_data=app_data["refugee_associations_data"],
        live_jobs_data=app_data["live_jobs_data"],
        live_jobs_coverage=app_data.get("live_jobs_coverage", pd.DataFrame()),
        siae_jobs_data=app_data["siae_jobs_data"],
        siae_jobs_coverage=app_data.get("siae_jobs_coverage", pd.DataFrame()),
        annuaire_ecoles=app_data.get("annuaire_ecoles", pd.DataFrame()),
        annuaire_sante=app_data.get("annuaire_sante", pd.DataFrame()),
        annuaire_inclusion=app_data.get("annuaire_inclusion", pd.DataFrame()),
        inclusion_services_index=app_data.get(
            "inclusion_services_index", pd.DataFrame()
        ),
        rome_index=app_data.get("rome_index", pd.DataFrame()),
        bv_data=app_data.get("bv_data"),
    )

    # 1. Run optimized scoring (returns model and pruned GDF)
    search_results, processed_gdf = engine.run_optimized(config, log_prefix="classic")

    # 3. 🧪 SOTA: Lightweight Geometry Hydration (Raw WKB)
    # Join raw WKB bytes from odis_geo (pd.Series indexed by codgeo) onto results.
    # Decoding to Shapely happens JIT in maps.py — never here.
    odis_geo = app_data.get("odis_geo")
    if odis_geo is not None and not odis_geo.empty:
        logging.info(
            f"💾 [HYDRATION] Attaching WKB geometries for {len(processed_gdf)} results..."
        )
        processed_gdf = processed_gdf.join(odis_geo.rename("polygon"), how="left")

    # --- State Update ---
    st.session_state["processed_gdf"] = processed_gdf
    st.session_state["unaggregated_gdf"] = processed_gdf
    st.session_state["engine"] = engine
    st.session_state["search_results"] = search_results

    # --- Unified Telemetry & Logging is now handled in background (launch_post_scoring_tasks) ---

    # Prepare cities for background AI agents
    top_cities_full = [
        {
            "codgeo": str(c.codgeo),
            "libgeo": c.name,
            "weighted_score": c.global_score,
            "scores": c.scores,
            # "details": c.model_dump(include={
            #     'population', 'scores', 'employment', 'housing',
            #     'education', 'health', 'inclusion', 'mobility',
            #     'codgeo_bdv', 'name_bdv'
            # })
        }
        for c in search_results.results
    ]

    h = search_results.search_hash
    st.session_state["active_search_hash"] = h

    # Trigger all background tasks via unified orchestrator (SOTA Pattern)
    if odis_get_bg_result(h) is None:
        launch_post_scoring_tasks(engine, config, search_results, h)

    # Calculate center for map (Use Top 5 Average Centroid - much better UX for distant searches)
    # Stateful Centering: Only reset the map center if this is a NEW search.
    # This prevents the map from "snapping back" during heartbeats or sidebar interactions.
    if st.session_state.get("last_centered_hash") != h:
        top_5_results = search_results.results[:5]
        if top_5_results:
            odis_df = app_data["odis"]
            top_codgeos = [str(c.codgeo) for c in top_5_results]
            top_data = odis_df.loc[odis_df.index.isin(top_codgeos)]

            if not top_data.empty and "centroid_lon" in top_data.columns:
                # Average EPSG:2154 coordinates
                avg_x = top_data["centroid_lon"].mean()
                avg_y = top_data["centroid_lat"].mean()

                # Project from Lambert-93 (2154) to Lat/Lon (4326) for Folium
                lon, lat = utils.project_point(
                    avg_x, avg_y, from_crs=cfg.PROJECTED_CRS, to_crs="EPSG:4326"
                )
                final_center_y, final_center_x = lat, lon
            else:
                final_center_y, final_center_x = cfg.DEFAULT_MAP_CENTER
        else:
            final_center_y, final_center_x = cfg.DEFAULT_MAP_CENTER

        st.session_state["center"] = [final_center_y, final_center_x]
        st.session_state["zoom"] = maps.get_map_zoom(config.loc_search_area)
        st.session_state["last_centered_hash"] = h
        st.session_state["selected_geo"] = app_data["odis"].loc[
            [config.commune_actuelle.code]
        ].copy()

    # We no longer pre-build Top 5 layers here to avoid Folium serialization issues in session state.
    # They are now rebuilt on the fly in the map rendering block.
    st.session_state["fgs_to_show"] = set()
    st.session_state["highlighted_result"] = [False, None]


# Automatically run the search if not already processed and form is completed
if st.session_state.get("processed_gdf") is None and st.session_state.get(
    "form_completed"
):
    run_search()
    st.session_state["form_completed"] = False

# --- UI LAYOUT ---


def action_buttons_container_static(h: str):
    ui_results.render_export_pdf_button(h)
    ui_results.render_share_search_button(
        h=h, button_text="Partager", key_prefix="sidebar_share"
    )


# Sidebar
with st.sidebar:
    logo_path = utils.get_asset_path("logo-jaccueille-singa.png")
    logo_b64 = utils.get_base64_image(logo_path)
    if logo_b64:
        st.markdown(
            f'<img src="data:image/png;base64,{logo_b64}" width="150" style="margin-bottom: 20px;">',
            unsafe_allow_html=True,
        )
    else:
        st.error("Logo not found")

    st.write("")
    st.markdown(
        "Découvrez les lieux de vie correspondant le mieux au projet renseigné. Les scores vous permettent de comparer facilement leurs atouts.",
        unsafe_allow_html=True,
    )
    st.divider()
    # --- Retour à l'Accueil ---
    ui.start_over()

    # --- Bouton Feedback ---
    feedback.render_feedback_button()

    # --- Export to PDF & Partager ---
    if st.session_state.get("search_results") is not None:
        h = st.session_state.search_results.search_hash
        # Deterministic results are immediately shareable/exportable. Optional
        # providers must not hold these actions in a permanent loading state.
        action_buttons_container_static(h)

    st.divider()

    # --- Dashboard Admin Analytics & Sources ---
    ui.render_admin_sidebar_link()
    ui.render_sources_sidebar_link()
    ui.render_logout_sidebar_button()

    # --- Weights --- (MOVED TO TOP FILTER FORM)


# Top filter Form
with st.container(border=False, key="top_menu"):
    st.markdown(
        """
    <style>
        .st-key-top_menu {background-color:whitesmoke; padding:20px; border-radius:10px} 
        .st-key-top_menu h2 {padding:0px} 
        .stTabs div div button div p {font-size:1rem}
    </style>
    """,
        unsafe_allow_html=True,
    )

    col_tabs, col_btn_search = st.columns([5.0, 1.0])
    with col_tabs:
        st.markdown(f"## Projet de vie {ui.get_person_accompanied_str()}")

    # Compute if criteria have changed since the last search. A shared snapshot
    # is immutable: re-running it is an explicit fork onto current data, even
    # when the criteria hash has not changed.
    current_config = ui_forms.create_search_criterias_from_inputs(app_data)
    last_results = st.session_state.get("search_results")
    snapshot_mode = bool(st.session_state.get("immutable_shared_snapshot"))
    if last_results is not None and not snapshot_mode:
        disable_search = current_config.compute_hash() == last_results.search_hash
    else:
        disable_search = False

    with col_btn_search:
        st.button(
            "Recalculer sur les données actuelles" if snapshot_mode else "Rechercher",
            on_click=run_search,
            type="primary",
            disabled=disable_search,
            width="stretch",
            icon=":material/search:"
        )

    # with col_btn_share:
    #     if st.session_state.get("search_results"):
    #         ui_results.render_share_search_button(
    #             h=st.session_state.search_results.search_hash,
    #             button_text="Partager",
    #             key_prefix="top_share",
    #         )
    if snapshot_mode:
        release = st.session_state.get("shared_snapshot_data_release", "inconnue")
        st.info(
            "Vous consultez un instantané partagé et immuable "
            f"(release de données : `{release}`). Modifier les critères puis "
            "recalculer crée une nouvelle recherche avec les données actuelles."
        )
        if not st.session_state.get("shared_snapshot_editing"):
            if st.button(
                "Modifier les critères pour créer une nouvelle recherche",
                key="fork_shared_snapshot",
                icon=":material/edit:",
            ):
                st.session_state["shared_snapshot_editing"] = True
                st.rerun()

    if not snapshot_mode or st.session_state.get("shared_snapshot_editing"):
        with st.expander("🔎 Modifier les critères de recherche", expanded=False):
            ui_forms.display_input_tabs(app_data)


# Global Pitch (Strategic intro + Loading state)
# if st.session_state.get('search_results'):
#     h = st.session_state.search_results.search_hash
# @st.fragment(run_every=3.0)
# def global_pitch_container(h: str):
#     ui_results.render_global_pitch(h)
# global_pitch_container(h)

# Main two sections: results and map
col_map, col_results = st.columns([2, 1])

with col_results:
    if st.session_state.get("search_results") is not None:
        # st.subheader("Meilleurs résultats")
        st.space()

        # The deterministic score is ready at this point. AI and external
        # enrichment update individual details, but never gate Top-5 review.
        h = st.session_state.search_results.search_hash
        ui_results.display_results_list()

        with st.container(height=40, vertical_alignment="center", border=False):
            st.caption(
                "Cliquez sur un résultat ⬆ pour le détail du score",
                text_alignment="center",
                width="stretch",
            )

with col_map:
    if st.session_state.get("processed_gdf") is not None:
        # 1. Map View State (Driven by search_results.search_hash)
        config = st.session_state.get("config")
        search_results = st.session_state.get("search_results")
        h = search_results.search_hash if search_results else None
        snapshot_mode = bool(st.session_state.get("immutable_shared_snapshot"))
        current_map_context = st.session_state.get("snapshot_current_map_context")
        if not isinstance(current_map_context, pd.DataFrame):
            current_map_context = st.session_state.processed_gdf

        # Default zoom if not set
        if st.session_state.get("zoom") is None:
            st.session_state["zoom"] = maps.get_map_zoom(
                config.loc_search_area if config else "departement"
            )

        # 2. Fresh Map Instance (Mandatory for st-folium React mount)
        m = maps.create_base_map(
            st.session_state.get("center"), st.session_state.get("zoom")
        )

        # 3. Build Dynamic FeatureGroup (Freshly built on every rerun to prevent Folium ReferenceErrors)
        fg_scores = flm.FeatureGroup(name="Scores (Chaleur)")
        if not st.session_state.processed_gdf.empty:
            compiled_fg, _ = maps.build_scores_layer(st.session_state["processed_gdf"])
            compiled_fg.add_to(fg_scores)

            if search_results and search_results.current_geo:
                maps.build_current_loc_layer(
                    search_results.current_geo,
                    gdf_context=current_map_context,
                ).add_to(fg_scores)
        elif snapshot_mode:
            st.info(
                "Cet ancien instantané ne contient pas la géométrie de la carte. "
                "Les résultats affichés restent ceux qui ont été partagés."
            )

        fg_scores.add_to(m)

        # 4. Build Transient Group (Pills & Top 5)
        fg_dynamic = flm.FeatureGroup(name="ODIS_Dynamic_Layers")
        # B. User-selected Layers (Pills)
        pill_options = [{"id": "top_5", "label": "🥇 Top 5"}]
        if config and not snapshot_mode:
            if config.nb_enfants > 0:
                pill_options.append({"id": "edu", "label": "🎓 Éducation"})
            if getattr(config, "besoin_sante", []):
                pill_options.append({"id": "sante", "label": "🏥 Santé"})
            if config.inc_services_selection:
                pill_options.append({"id": "inc", "label": "🤝 Inclusion"})

        with st.container(horizontal=True, horizontal_alignment="center"):
            st.text("Afficher")
            selected_objs = st.pills(
                "Afficher sur la carte :",
                pill_options,
                selection_mode="multi",
                default=[pill_options[0]],
                format_func=lambda x: x["label"],
                key="map_layers_pills",
                label_visibility="collapsed",
            )

        selected_ids = {obj["id"] for obj in selected_objs} if selected_objs else set()
        legend_items = [{"color": "red", "text": "Top 5", "icon": "circle"}]
        if search_results and search_results.commune_pressentie:
            legend_items.append({"color": "yellow", "text": "Ville Souhaitée"})

        # C. POI & Top 5 Rendering. POI layers are live reference data, so they
        # are intentionally omitted from an immutable shared snapshot.
        if config and search_results:
            target_codgeos = {str(c.codgeo) for c in search_results.results}
            if search_results.commune_pressentie:
                target_codgeos.add(str(search_results.commune_pressentie.codgeo))

            if not snapshot_mode:
                pois = app_data["pois"]
                # Always-on Mairie layer
                maps.build_mairies_layer(pois, target_codgeos).add_to(m)
                legend_items.append(
                    {"color": "#F5D819", "text": "Mairie (BPE)", "icon": "circle"}
                )

                if "edu" in selected_ids:
                    maps.build_ecoles_layer(pois, target_codgeos, config).add_to(
                        fg_dynamic
                    )
                    legend_items.append(
                        {"color": "green", "icon": "pencil", "text": "Écoles"}
                    )
                if "sante" in selected_ids:
                    maps.build_sante_layer(pois, target_codgeos, config).add_to(
                        fg_dynamic
                    )
                    legend_items.append(
                        {"color": "blue", "icon": "plus", "text": "Santé"}
                    )
                if "inc" in selected_ids:
                    maps.build_services_layer(pois, target_codgeos, config).add_to(
                        fg_dynamic
                    )
                    legend_items.append(
                        {"color": "purple", "icon": "heart", "text": "Inclusion"}
                    )

            show_top_5 = "top_5" in selected_ids
            is_highlighted, highlighted_index = st.session_state.highlighted_result

            if show_top_5:
                for i, commune_result in enumerate(search_results.results[:5]):
                    maps.build_top_result_layer(
                        commune_result, i, gdf_context=st.session_state.processed_gdf
                    ).add_to(fg_dynamic)
                if search_results.commune_pressentie:
                    maps.build_top_result_layer(
                        search_results.commune_pressentie,
                        -1,
                        gdf_context=st.session_state.processed_gdf,
                    ).add_to(fg_dynamic)

            if is_highlighted:
                if highlighted_index == -1:
                    commune_result = search_results.commune_pressentie
                else:
                    commune_result = search_results.results[highlighted_index]
                if commune_result:
                    maps.build_top_result_layer(
                        commune_result,
                        highlighted_index,
                        gdf_context=st.session_state.processed_gdf,
                    ).add_to(fg_dynamic)

        # 4. Legend Rendering
        if legend_items:
            legend_html = maps.build_legend(legend_items)
            fg_dynamic.add_child(flm.Element(legend_html))

        # 5. Final Rendering (Streamlit Integration)
        try:
            st_folium(
                m,
                center=st.session_state.get("center"),
                zoom=st.session_state.get("zoom"),
                feature_group_to_add=fg_dynamic,
                key="odis_main_map",
                width="content",
                returned_objects=[],
            )
        except Exception as e:
            st.error(f"Erreur d'affichage de la carte: {e}")
            logging.error(f"❌ [MAP-ERROR] st_folium: {e}")

        st.markdown(
            "<style>.stCustomComponentV1 {border-radius:10px}</style>",
            unsafe_allow_html=True,
        )

    # st.caption(f"⚠️ Seules les {cfg.MAX_MAP_POLYGONS} meilleures communes sont affichées")

# Do not remove, useful to debug states
# Detect Cloud Run environment
# is_cloud_run = os.environ.get("K_SERVICE") is not None
# st.dataframe(st.session_state.processed_gdf[["ter_insecurite_scaled"]])

# 1. Skip if not running on Cloud Run (Local Dev)
# if not is_cloud_run:
#     with st.expander("Debug", expanded=False):
#         # try:
#         # 🧪 SOTA: Drop geometry columns to avoid 'pyarrow.lib.ArrowTypeError'
#         # Streamlits Arrow serialization doesn't support GeoPandas objects in st.dataframe
#         debug_df = st.session_state.get('processed_gdf')
#         if debug_df is not None:
#             st.text(f"Lignes: {len(debug_df)}")
#     st.text(f"colonnes: {len(debug_df.columns)}")
#     mem_usage = debug_df.memory_usage(deep=True).sum() / (1024 * 1024)
#     st.text(f"Mémoire RAM: {mem_usage:.2f} Mo")
# st.dataframe(st.session_state['processed_gdf'].drop(columns=['polygon', 'centroid'], errors='ignore'))
# st.json(search_results.results)

# except:
#     pass
