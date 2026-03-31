import os
import streamlit as st
from streamlit_folium import st_folium
from core import scoring
import config as cfg
from ui import components as ui
from ui import feedback
from utils import common as utils
from core import maps
from core.pdf_generator import generate_pdf_report
import folium as flm
from utils import data_loader
import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping
import logging
import gc
import warnings
from agents.utils import launch_background_scorer, odis_get_bg_result, launch_background_enrichment
from core.models import SearchResultsData
from utils import memory

st.set_page_config(layout="wide")

# Reduce padding around results
st.markdown("""
<style>
    .stMainBlockContainer {
        padding: 2rem;
    }
</style>
""", unsafe_allow_html=True)

# --- Authentication ---
from utils import auth
if not auth.check_password():
    st.stop()
    
# --- Session State Initialization ---
if 'highlighted_result' not in st.session_state:
    st.session_state['highlighted_result'] = [False, None]
if 'fgs_to_show' not in st.session_state:
    st.session_state['fgs_to_show'] = set()
if 'center' not in st.session_state:
    st.session_state['center'] = [46.5, 2.5] # Default France
if 'zoom' not in st.session_state:
    st.session_state['zoom'] = 6
if 'active_ia_city_index' not in st.session_state:
    st.session_state['active_ia_city_index'] = None
if 'active_details_index' not in st.session_state:
    st.session_state['active_details_index'] = None
if 'active_ccas_index' not in st.session_state:
    st.session_state['active_ccas_index'] = None

# --- PDF Modal Logic ---
def on_pdf_dialog_dismiss():
    """Callback to clean up state when the dialog is dismissed."""
    st.session_state.show_pdf_modal = False
    st.session_state.pdf_modal_data = None

@st.dialog("Export des résultats en PDF", on_dismiss=on_pdf_dialog_dismiss)
def pdf_modal():
    logging.info(f"📄 [UI-MODAL] pdf_modal called. show_pdf_modal={st.session_state.get('show_pdf_modal')}")
    # State 1: Loading / Generating
    if 'pdf_modal_data' not in st.session_state or st.session_state.pdf_modal_data is None:
        logging.info("📄 [UI] Opening PDF modal: Starting generation state")
        with st.spinner("Veuillez patienter, nous générons votre document..."):
            try:    
                search_results = st.session_state.get('search_results')
                logging.info(f"📄 [UI-MODAL] search_results present: {search_results is not None}")
                if search_results:
                     logging.info(f"📄 [UI-MODAL] number of results: {len(search_results.results)}")
                
                pdf_bytes = generate_pdf_report(
                    st.session_state, 
                    search_results
                )
                st.session_state.pdf_modal_data = pdf_bytes
                st.rerun() 
            except Exception as e:
                st.error(f"Erreur lors de la génération du PDF : {e}")
                if st.button("Fermer"):
                    st.session_state.show_pdf_modal = False
                    st.rerun()

    # State 2: Download Ready
    else:
        st.success("Votre document est prêt !")
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                label="Télécharger le PDF",
                data=st.session_state.pdf_modal_data,
                file_name="synthese_jaccueille.pdf",
                mime="application/pdf",
                icon=':material/picture_as_pdf:',
                type='primary',
                width='stretch'
            )
        with col2:
            if st.button("Fermer", width="stretch"):
                st.session_state.show_pdf_modal = False
                st.session_state.pdf_modal_data = None
                st.rerun()


# --- PDF Modal Execution (moved to bottom for reliability) ---

# Ensure app_data is initialized
# Ensure app data and session state are initialized
data_loader.ensure_data_initialized()

# DO NOT REMOVE: This makes sure the ui_ form state persists as expected
for k, v in st.session_state.items():
    if str(k).startswith('ui_'):
        st.session_state[k] = v

app_data = data_loader.get_app_data()
search_results: SearchResultsData = st.session_state.get('search_results')

def run_search():
    """
    Callback function for the 'Lancer la recherche' button.
    It orchestrates the new filtering and scoring logic.
    """
    logging.info('--- Running new search with refactored logic ---')
    gc.collect()
    # Clear any previously generated PDF data on new search
    st.session_state['pdf_data'] = None

    from services import telemetry
    telemetry.reset_interaction_id()
    
    config = ui.create_search_criterias_from_inputs()
    st.session_state['config'] = config

    # Get required dataframes from global cached app_data
    app_data = data_loader.get_app_data()
    df_all_communes = app_data['odis']
    df_bv_geo = app_data['bv_geo']
    df_area_geo = app_data['area_geo']
    start_commune = df_all_communes.loc[[config.commune_actuelle.code]]

    # --- Run Scoring Pipeline (Optimized) ---
    # Instantiate the stateless engine with current data
    engine = scoring.ScoringEngine(
        df_all_communes=df_all_communes,
        df_odis_geo=app_data.get('odis_geo'),
        df_bv_geo=df_bv_geo,
        df_area_geo=df_area_geo,
        scores_cat=app_data['scores_cat'],
        incl_index=app_data['incl_index'],
        associations_data=app_data['associations_data'],
        formations_data=app_data['formations_data'],
        codformations_index=app_data['codformations_index'],
        waldec_index=app_data['waldec_index'],
        global_stats={},
        refugee_associations_data=app_data['refugee_associations_data'],
        live_jobs_data=app_data['live_jobs_data'],
        siae_jobs_data=app_data['siae_jobs_data'],
        annuaire_ecoles=app_data.get('annuaire_ecoles', pd.DataFrame()),
        annuaire_sante=app_data.get('annuaire_sante', pd.DataFrame()),
        annuaire_inclusion=app_data.get('annuaire_inclusion', pd.DataFrame()),
        inclusion_services_index=app_data.get('inclusion_services_index', pd.DataFrame()),
        rome_index=app_data.get('rome_index', pd.DataFrame()),
        bv_data=app_data.get('bv_data')
    )

    # 1. Clear old heavy results from session state (Centralized pattern)
    
    memory.clear_search_state()

    # 2. Run optimized scoring (returns model and pruned GDF)
    search_results, processed_gdf = engine.run_optimized(config)
    
    # --- State Update ---
    st.session_state['processed_gdf'] = processed_gdf
    st.session_state['unaggregated_gdf'] = processed_gdf # Single view now
    st.session_state['engine'] = engine
    st.session_state['search_results'] = search_results
    
    # --- Unified Telemetry Logging (BigQuery) ---
    try:
        telemetry.log_search_complete(config, search_results, source_flow='classic')
    except Exception as tel_e:
        logging.warning(f"Failed to log search telemetry: {tel_e}")
        
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
    st.session_state['active_search_hash'] = h
    
    # Trigger background scorer and enrichment if result not already present or running
    if odis_get_bg_result(h) is None:
        launch_background_scorer(config, {}, h, top_cities=top_cities_full)
        # Background enrichment for detailed associations (SOTA Pattern)
        target_codgeos = [c['codgeo'] for c in top_cities_full]
        launch_background_enrichment(engine, target_codgeos, h)
    
    # Calculate center for map (Use starting commune as anchor - more efficient & robust)
    if not start_commune.empty and 'centroid_lon' in start_commune.columns:
        c_lon = start_commune['centroid_lon'].iloc[0]
        c_lat = start_commune['centroid_lat'].iloc[0]
        
        # Project from 2154 to 4326 for Folium if needed
        if pd.notna(c_lon) and c_lon > 180:
             lon, lat = utils.project_point(c_lon, c_lat, from_crs=cfg.PROJECTED_CRS, to_crs='EPSG:4326')
        else:
             lon, lat = c_lon, c_lat
        final_center_y, final_center_x = lat, lon
    else:
        # Fallback to absolute default
        final_center_y, final_center_x = cfg.DEFAULT_MAP_CENTER

    st.session_state['selected_geo'] = data_loader.get_app_data()['odis'].loc[[config.commune_actuelle.code]].copy()
    st.session_state['center'] = [final_center_y, final_center_x]
    st.session_state['zoom'] = maps.get_map_zoom(config.loc_search_area)
    
    # We no longer pre-build Top 5 layers here to avoid Folium serialization issues in session state.
    # They are now rebuilt on the fly in the map rendering block.
    st.session_state['fgs_to_show'] = set()
    st.session_state['highlighted_result'] = [False, None]


def open_pdf_modal() -> None:
    """Callback to signal that the PDF modal should be shown."""
    logging.info("🎯 [UI] 'Exporter résultats' button clicked. Setting show_pdf_modal=True")
    st.session_state['show_pdf_modal'] = True

# Automatically run the search if not already processed and form is completed
if st.session_state.get('processed_gdf') is None and st.session_state.get('form_completed'):
    run_search()
    st.session_state['form_completed'] = False

# --- UI LAYOUT
@st.fragment(run_every=3.0)
def export_pdf_container(h: str):
    """Module-level fragment to avoid redefinition issues."""
    if not h:
        return
        
    scorer_res = odis_get_bg_result(h)
    # scorer_res can be dict (success) or str (error)
    scorer_done = scorer_res is not None and not isinstance(scorer_res, str)
    
    if scorer_done:
        st.button(
            "Exporter résultats", 
            on_click=open_pdf_modal,
            icon=':material/picture_as_pdf:',
            type='secondary',
            width="stretch"
        )
    elif isinstance(scorer_res, str) and "⚠️" in scorer_res:
        st.error(scorer_res)
    else:
        # Still running
        col1, col2 = st.columns([1, 4])
        with col1:
            st.spinner("")
        with col2:
            st.button(
                "Patience...", 
                disabled=True,
                icon=':material/picture_as_pdf:',
                type='secondary',
                width="stretch"
            )

# Sidebar
with st.sidebar:
    
    logo_path = utils.get_asset_path('logo-jaccueille-singa.png')
    logo_b64 = utils.get_base64_image(logo_path)
    if logo_b64:
        st.markdown(f'<img src="data:image/png;base64,{logo_b64}" width="150" style="margin-bottom: 20px;">', unsafe_allow_html=True)
    else:
        st.error("Logo not found")
    st.write("")
    st.markdown("Découvrez les lieux de vie correspondant le mieux au projet renseigné. Les scores vous permettent de comparer facilement leurs atouts.", unsafe_allow_html=True)

    # --- Retour à l'Accueil ---    
    ui.start_over()

    # --- Bouton Feedback ---
    feedback.render_feedback_button()

    # --- Export to PDF ---
    if st.session_state.get('processed_gdf') is not None:
        active_h = st.session_state.get('active_search_hash')
        if not active_h:
            config = st.session_state.get('config')
            active_h = config.compute_hash() if config else None
        
        export_pdf_container(active_h)
    
    st.divider()

    # --- Weights --- (MOVED TO TOP FILTER FORM)
    
    

# Top filter Form
with st.container(border=False, key='top_menu'):
    st.markdown("""
    <style>
        .st-key-top_menu {background-color:whitesmoke; padding:20px; border-radius:10px} 
        .st-key-top_menu h2 {padding:0px} 
        .stTabs div div button div p {font-size:1rem}
    </style>
    """, unsafe_allow_html=True)

    col_tabs, col_button = st.columns([5,1])
    with col_tabs:
        st.markdown(f"## Projet de vie {ui.get_person_accompanied_str()}")    
    with col_button: 
        with st.container(height="stretch", horizontal_alignment="center", vertical_alignment="center"):
            st.button("Lancer la recherche", on_click=run_search, type="primary")
    with st.expander('Modifier les critères de recherche', expanded=False):
        ui.display_input_tabs(st.session_state['demo_data'])
    
# Main two sections: results and map
col_map, col_results = st.columns([3, 2])

with col_results:
    if st.session_state.get('search_results') is not None:
        ui.display_results_list() # No args needed, it uses session_state.search_results internally

with col_map:
    if st.session_state.get('processed_gdf') is not None:
        # 1. Map View State (Driven by st.session_state)
        # Defining variables early to avoid NameError
        config = st.session_state.get('config')
        search_results = st.session_state.get('search_results')
        
        # Default zoom if not set
        if st.session_state.get("zoom") is None:
            st.session_state["zoom"] = maps.get_map_zoom(config.loc_search_area if config else 'departement')

        # 2. Base Map Initialization (🧪 SOTA: Stable object for st-folium)
        # We pass None/None to the constructor to ensure the base object is stable.
        m = maps.create_base_map(None, None)
        
        # 3. Build Consolidated Dynamic FeatureGroup
        fg_dynamic = flm.FeatureGroup(name="ODIS_Dynamic_Layers")
        
        # A. Scores & Current Location
        if not st.session_state.processed_gdf.empty:
            fg_scores, _ = maps.build_scores_layer(st.session_state['processed_gdf'])
            # Logic: Instead of nesting FGs, add children of the generated layers to fg_dynamic if possible
            # for child in fg_scores._children.values(): 
            # actually nesting should work, but let's be flat if it helps
            fg_scores.add_to(fg_dynamic)

            if search_results and search_results.current_geo:
                maps.build_current_loc_layer(search_results.current_geo).add_to(fg_dynamic)

        # B. User-selected Layers (Pills)
        pill_options = [{"id": "top_5", "label": "🥇 Top 5"}]
        if config:
            if config.nb_enfants > 0: pill_options.append({"id": "edu", "label": "🎓 Éducation"})
            if config.besoin_sante != "Aucun": pill_options.append({"id": "sante", "label": "🏥 Santé"})
            if config.inc_services_add_selection: pill_options.append({"id": "inc", "label": "🤝 Inclusion"})
        
        selected_objs = st.pills("Afficher sur la carte :", pill_options, selection_mode="multi", 
                                default=[pill_options[0]], format_func=lambda x: x["label"],
                                key="map_layers_pills", label_visibility="collapsed")
        
        selected_ids = {obj["id"] for obj in selected_objs} if selected_objs else set()
        legend_items = [{'color': 'red', 'text': 'Top 5', 'icon':'circle'}]

        # C. POI & Top 5 Rendering
        if config and search_results:
            target_codgeos = {str(c.codgeo) for c in search_results.results}
            
            # Additional Layers
            if "edu" in selected_ids:
                maps.build_ecoles_layer(data_loader.get_app_data()['pois'], target_codgeos, config).add_to(fg_dynamic)
                legend_items.append({'color': 'green', 'icon': 'pencil', 'text': 'Écoles'})
            if "sante" in selected_ids:
                maps.build_sante_layer(data_loader.get_app_data()['pois'], target_codgeos, config).add_to(fg_dynamic)
                legend_items.append({'color': 'blue', 'icon': 'plus', 'text': 'Santé'})
            if "inc" in selected_ids:
                maps.build_services_layer(data_loader.get_app_data()['pois'], target_codgeos, config).add_to(fg_dynamic)
                legend_items.append({'color': 'purple', 'icon': 'heart', 'text': 'Inclusion'})
            
            # Top 5 & Highlights
            show_top_5 = "top_5" in selected_ids
            is_highlighted, highlighted_index = st.session_state.highlighted_result

            if show_top_5:
                for i, commune in enumerate(search_results.results[:5]):
                    maps.build_top_result_layer(commune, i).add_to(fg_dynamic)

            if is_highlighted:
                commune = search_results.results[highlighted_index]
                maps.build_top_result_layer(commune, highlighted_index).add_to(fg_dynamic)

        # 4. Legend Rendering (Added to the stable map object)
        if legend_items:
            legend_html = maps.build_legend(legend_items)
            m.get_root().html.add_child(flm.Element(legend_html))

        # 5. Final Incremental Rendering
        try:
            st_folium(
                m,
                center=st.session_state.get("center"),
                zoom=st.session_state.get("zoom"),
                feature_group_to_add=fg_dynamic,
                key="odis_main_map",
                use_container_width=True,
                returned_objects=[]
            )
        except Exception as e:
            st.error(f"Erreur d'affichage de la carte: {e}")
            logging.error(f"❌ [MAP-ERROR] st_folium: {e}")
        
        st.markdown('<style>.stCustomComponentV1 {border-radius:10px}</style>', unsafe_allow_html=True)

# --- PDF Modal Execution (at the end to ensure all state is initialized) ---
if st.session_state.get('show_pdf_modal'):
    pdf_modal()

# Do not remove, useful to debug states
# Detect Cloud Run environment
is_cloud_run = os.environ.get("K_SERVICE") is not None

# 1. Skip if not running on Cloud Run (Local Dev)
# if not is_cloud_run:
#     with st.expander("Debug", expanded=False):
        # try:
        # 🧪 SOTA: Drop geometry columns to avoid 'pyarrow.lib.ArrowTypeError'
        # Streamlit's Arrow serialization doesn't support GeoPandas objects in st.dataframe
        # debug_df = st.session_state.get('processed_gdf')
        # if debug_df is not None:
        #     st.text(f"Lignes: {len(debug_df)}")
        #     st.text(f"colonnes: {len(debug_df.columns)}")
        #     mem_usage = debug_df.memory_usage(deep=True).sum() / (1024 * 1024)
        #     st.text(f"Mémoire RAM: {mem_usage:.2f} Mo")                
        # st.dataframe(st.session_state['processed_gdf'].drop(columns=['polygon', 'centroid'], errors='ignore'))
        # st.json(search_results.results)

        # except:
        #     pass