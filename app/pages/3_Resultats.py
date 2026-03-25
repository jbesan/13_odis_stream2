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
from agents.utils import launch_background_scorer, odis_get_bg_result
from core.models import SearchResultsData

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
if 'fg_dict_ref' not in st.session_state:
    st.session_state['fg_dict_ref'] = {}
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
    # State 1: Loading / Generating
    if 'pdf_modal_data' not in st.session_state or st.session_state.pdf_modal_data is None:
        with st.spinner("Veuillez patienter, nous générons votre document..."):
            try:    
                pdf_bytes = generate_pdf_report(
                    st.session_state, 
                    st.session_state.search_results
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

app_data = st.session_state.app_data

def run_search():
    """
    Callback function for the 'Lancer la recherche' button.
    It orchestrates the new filtering and scoring logic.
    """
    logging.info('--- Running new search with refactored logic ---')
    gc.collect()
    # Clear any previously generated PDF data on new search
    st.session_state['pdf_data'] = None
    st.session_state['map_object'] = None

    from services import telemetry
    telemetry.reset_interaction_id()
    
    config = ui.create_search_criterias_from_inputs()
    st.session_state['config'] = config

    # Get required dataframes from session state
    df_all_communes = st.session_state.app_data['odis']
    df_bv_geo = st.session_state.app_data['bv_geo']
    df_area_geo = st.session_state.app_data['area_geo']
    start_commune = df_all_communes.loc[[config.commune_actuelle.code]]

    # --- Run Scoring Pipeline ---
    # Instantiate the stateless engine with current data
    engine = scoring.ScoringEngine(
        df_all_communes=df_all_communes,
        df_bv_geo=df_bv_geo,
        df_area_geo=df_area_geo,
        scores_cat=st.session_state.app_data['scores_cat'],
        incl_index=st.session_state.app_data['incl_index'],
        associations_data=st.session_state.app_data['associations_data'],
        formations_data=st.session_state.app_data['formations_data'],
        codformations_index=st.session_state.app_data['codformations_index'],
        waldec_index=st.session_state.app_data['waldec_index'],
        global_stats={},
        refugee_associations_data=st.session_state.app_data['refugee_associations_data'],
        live_jobs_data=st.session_state.app_data['live_jobs_data'],
        siae_jobs_data=st.session_state.app_data['siae_jobs_data'],
        annuaire_ecoles=st.session_state.app_data.get('annuaire_ecoles', pd.DataFrame()),
        annuaire_sante=st.session_state.app_data.get('annuaire_sante', pd.DataFrame()),
        annuaire_inclusion=st.session_state.app_data.get('annuaire_inclusion', pd.DataFrame()),
        inclusion_services_index=st.session_state.app_data.get('inclusion_services_index', pd.DataFrame()),
        rome_index=st.session_state.app_data.get('rome_index', pd.DataFrame()),
        bv_data=st.session_state.app_data.get('bv_data')
    )

    processed_gdf = engine.run(
        config=config,
        log_prefix="classic"
    )
    
    # --- Create Standardized Search Results Payload ---
    search_results: SearchResultsData = engine.create_search_results(processed_gdf, config)
    
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
        {"codgeo": str(c.codgeo), "libgeo": c.name, "weighted_score": c.global_score, "details": c.model_dump(exclude={'geometry', 'centroid'})} 
        for c in search_results.results
    ]
        
    h = search_results.search_hash
    st.session_state['active_search_hash'] = h
    
    # Trigger background scorer if result not already present or running
    if odis_get_bg_result(h) is None:
        launch_background_scorer(config, {}, h, top_cities=top_cities_full)
    
    # Calculate center for map
    if not processed_gdf.empty:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            # Use centroid of unioned polygons in projected CRS
            projected_union = processed_gdf.to_crs(cfg.PROJECTED_CRS).union_all()
            avg_centroid = projected_union.centroid
        
        # Project back to 4326 for Folium
        lon, lat = utils.project_point(avg_centroid.x, avg_centroid.y, from_crs=cfg.PROJECTED_CRS, to_crs='EPSG:4326')
        final_center_y, final_center_x = lat, lon
    else:
        # Fallback to current commune
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            # start_commune is already a GeoDataFrame from data_loader
            start_geom = start_commune.geometry.iloc[0]
            if start_geom.x > 180: # Meters
                 start_lon, start_lat = utils.project_point(start_geom.centroid.x, start_geom.centroid.y, from_crs=cfg.PROJECTED_CRS, to_crs='EPSG:4326')
            else:
                 start_lon, start_lat = start_geom.centroid.x, start_geom.centroid.y
                 
        final_center_y, final_center_x = start_lat, start_lon

    st.session_state['selected_geo'] = st.session_state.app_data['odis'].loc[[config.commune_actuelle.code]].copy()
    st.session_state['center'] = [final_center_y, final_center_x]
    st.session_state['zoom'] = maps.get_map_zoom(config.loc_search_area)
    
    # We no longer pre-build Top 5 layers here to avoid Folium serialization issues in session state.
    # They are now rebuilt on the fly in the map rendering block.
    st.session_state['fg_dict_ref'] = {}
    
    st.session_state['fgs_to_show'] = set()
    st.session_state['highlighted_result'] = [False, None]

def open_pdf_modal() -> None:
    """Callback to signal that the PDF modal should be shown."""
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
        st.subheader("Carte des résultats")
        
        # Define config early to avoid NameError
        config = st.session_state.get('config')
        
        # Safety check for zoom
        if st.session_state.get("zoom") is None:
            st.session_state["zoom"] = maps.get_map_zoom(config.loc_search_area if config else 'departement')

        
        # Initialize map
        m = maps.create_base_map(st.session_state["center"], st.session_state["zoom"])
        
        # Base layer with all scored communes (we still need the full GDF for the background layer)
        if not st.session_state.processed_gdf.empty:
            st.session_state['fg_dict_ref']['Scores'], colormap = maps.build_scores_layer(st.session_state['processed_gdf'])
        
        # 1. ADD BLUE LAYER FOR CURRENT COMMUNE (from SearchResultsData.current_geo)
        search_results: SearchResultsData = st.session_state.get('search_results')
        if search_results and search_results.current_geo and search_results.current_geo.geometry:
            fg_current = flm.FeatureGroup(name="Ma position")
            poly_4326 = gpd.GeoSeries([search_results.current_geo.geometry], crs=cfg.PROJECTED_CRS).to_crs("EPSG:4326").iloc[0]
            flm.GeoJson(
                mapping(poly_4326),
                style_function=lambda x: {"fillColor": 'blue', "fillOpacity": 0.4, "stroke": True, "color": "blue", "weight": 2},
                tooltip=f"Actuel: {search_results.current_geo.name}"
            ).add_to(fg_current)
            fg_current.add_to(m)

        fgs_to_show = {'Scores'}
        legend_items = []
        
        config = st.session_state.get('config')
        
        # Build dynamic options for pills
        pill_options = ["🏠 Top 5"]
        if config:
            if config.nb_enfants > 0:
                pill_options.append("🎓 Éducation")
            if config.besoin_sante != "Aucun":
                pill_options.append("🏥 sante")
            if config.inc_services_add_selection:
                pill_options.append("🤝 Inclusion")
        
        # Display pills
        selected_layers = st.pills(
            "Afficher sur la carte :", 
            pill_options, 
            selection_mode="multi", 
            default=["🏠 Top 5"],
            key="map_layers_pills",
            label_visibility="collapsed"
        )
        
        # Map selections to boolean flags
        show_top_5 = "🏠 Top 5" in selected_layers
        show_ecoles = "🎓 Éducation" in selected_layers
        show_sante = "🏥 sante" in selected_layers
        show_inclusion = "🤝 Inclusion" in selected_layers

        if config and search_results:
            target_codgeos = {str(c.codgeo) for c in search_results.results}

            if show_ecoles:
                st.session_state.fg_dict_ref['fg_ecoles'] = maps.build_ecoles_layer(st.session_state.app_data['pois'], target_codgeos, config)
                fgs_to_show.add('fg_ecoles')
                legend_items.append({'color': 'green', 'icon': 'pencil', 'text': 'Écoles'})
            if show_sante:
                st.session_state.fg_dict_ref['fg_sante'] = maps.build_sante_layer(st.session_state.app_data['pois'], target_codgeos, config)
                fgs_to_show.add('fg_sante')
                legend_items.append({'color': 'blue', 'icon': 'plus', 'text': 'sante'})
            if show_inclusion:
                st.session_state.fg_dict_ref['fg_services'] = maps.build_services_layer(st.session_state.app_data['pois'], target_codgeos, config)
                fgs_to_show.add('fg_services')
                legend_items.append({'color': 'purple', 'icon': 'heart', 'text': 'Inclusion'})
        is_highlighted, highlighted_index = st.session_state.highlighted_result
        
        if show_top_5 and search_results:
            # Rebuild Top 5 layers on the fly from SearchResultsData.results
            for i, commune in enumerate(search_results.results):
                if commune.geometry:
                    name = f'Top{i+1}'
                    # Re-use existing map builder by creating a dummy Series if needed, or refine map builder
                    # For now, let's create a minimal Series to satisfy build_top_result_layer
                    row_data = pd.Series({
                        'polygon': commune.geometry,
                        'libgeo': commune.name
                    })
                    st.session_state['fg_dict_ref'][name] = maps.build_top_result_layer(row_data, i)
                    fgs_to_show.add(name)
            st.session_state["zoom"] = None
        elif is_highlighted and search_results:
            # Rebuild ONLY the highlighted layer
            commune = search_results.results[highlighted_index]
            if commune.geometry:
                name = f'Top{highlighted_index + 1}'
                row_data = pd.Series({
                    'polygon': commune.geometry,
                    'libgeo': commune.name
                })
                st.session_state['fg_dict_ref'][name] = maps.build_top_result_layer(row_data, highlighted_index)
                fgs_to_show.add(name)
                # No automatic re-centering as per user request
                
            if search_results.current_geo and search_results.current_geo.geometry:
                row_actuel = pd.Series({
                    'polygon': search_results.current_geo.geometry,
                    'libgeo': search_results.current_geo.name
                })
                st.session_state['fg_dict_ref']['current_loc'] = maps.build_current_loc_layer(row_actuel)
                fgs_to_show.add('current_loc')
                legend_items.append({'color': 'blue', 'icon': 'home', 'text': 'Ma position'})

        st.session_state.fgs_to_show = fgs_to_show

        if legend_items:
            legend = maps.build_legend(legend_items)
            m.get_root().html.add_child(flm.Element(legend))

        fgs_to_add = [
            st.session_state['fg_dict_ref'][name] 
            for name in sorted(list(st.session_state['fgs_to_show']))
            if name in st.session_state['fg_dict_ref']
        ]
        map_key = "odis_scored_map_" + "_".join(sorted(list(st.session_state.fgs_to_show)))

        # Manually add all visible layers to the map object
        # This is the most stable way to ensure they appear in the UI and PDF
        for fg in fgs_to_add:
            fg.add_to(m)
                
        st.session_state['map_object'] = m


        try:
            # Adding explicit height back as it's a known fix for Streamlit column collapses
            st_folium(
                m,
                zoom=st.session_state["zoom"],
                center=st.session_state["center"],
                feature_group_to_add=fgs_to_add,
                # layer_control=True,
                key=map_key,
                # height=500,
                use_container_width=True,
                returned_objects=[],
            )
        except Exception as e:
            st.error(f"Erreur d'affichage de la carte: {e}")
            logging.error(f"❌ [MAP-ERROR] st_folium failed: {e}")
            import traceback
            logging.error(traceback.format_exc())
        st.markdown('<style>.stCustomComponentV1 {border-radius:10px}</style>', unsafe_allow_html=True)

# --- PDF Modal Execution (at the end to ensure all state is initialized) ---
if st.session_state.get('show_pdf_modal'):
    pdf_modal()

# Do not remove, useful to debug states
with st.expander("Debug", expanded=False):
    try:
        st.json(search_results.results)
        st.json(search_results.current_geo)
    except:
        pass