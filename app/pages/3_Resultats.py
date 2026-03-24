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
if st.session_state.get('show_pdf_modal'):
    
    def on_dialog_dismiss():
        """Callback to clean up state when the dialog is dismissed."""
        st.session_state.show_pdf_modal = False
        st.session_state.pdf_modal_data = None

    @st.dialog("Export des résultats en PDF", on_dismiss=on_dialog_dismiss)
    def pdf_modal():
        # State 1: Loading / Generating
        if 'pdf_modal_data' not in st.session_state or st.session_state.pdf_modal_data is None:
            with st.spinner("Veuillez patienter, nous générons votre document..."):
                try:    
                    pdf_bytes = generate_pdf_report(
                        st.session_state, 
                        st.session_state.processed_gdf.copy()
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
                    type='primary'
                )
            with col2:
                if st.button("Fermer", width="stretch"):
                    st.session_state.show_pdf_modal = False
                    st.session_state.pdf_modal_data = None
                    st.rerun()

# Trigger PDF Modal
if st.session_state.get('show_pdf_modal'):
    pdf_modal()

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
        # Extract criteria vs weights from the SearchCriterias
        criteria_keys = ['commune_actuelle', 'loc_search_area', 'situation_famille', 'nb_enfants', 'besoin_emploi', 'besoin_sante', 'inc_services_add_selection']
        full_config = config.model_dump()
        
        search_criteria = {k: full_config.get(k) for k in criteria_keys if k in full_config}
        weights = {k: v for k, v in full_config.items() if k.startswith('poids_')}
        
        top_5_results = []
        top_5_breakdown = {}
        top_cities_full = []
        for commune in search_results.top_communes:
            idx = commune.codgeo
            top_5_breakdown[str(idx)] = {
                "libgeo": commune.name,
                "scores": {cat: [s.model_dump() for s in items] for cat, items in commune.scores.items()}
            }
            top_5_results.append(
                {"codgeo": str(idx), "libgeo": commune.name, "score": commune.global_score} 
            )
            
            # For Background AI Agent
            top_cities_full.append({
                "codgeo": str(idx),
                "libgeo": commune.name,
                "weighted_score": commune.global_score,
                "details": commune.model_dump()
            })
        
        telemetry.log_search_complete(
            criteria=search_criteria,
            weights=weights,
            results=top_5_results,
            breakdown=top_5_breakdown,
            source_flow='classic'
        )
    except Exception as tel_e:
        logging.warning(f"Failed to log search telemetry: {tel_e}")
        
    h = search_results.search_hash
    
    # Trigger background scorer if result not already present or running
    if odis_get_bg_result(h) is None:
        launch_background_scorer(config, {}, h, top_cities=top_cities_full)
    
    st.session_state['active_search_hash'] = h

    # Calculate center for map
    if not processed_gdf.empty:
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            projected_centroids = processed_gdf.to_crs('EPSG:2154').centroid
            avg_centroid_projected = projected_centroids.union_all().centroid
        
        center_x, center_y = utils.project_point(avg_centroid_projected.x, avg_centroid_projected.y, from_crs='EPSG:2154', to_crs='EPSG:4326')
        final_center_y, final_center_x = center_y, center_x
    else:
        # Fallback to current commune
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            start_commune_projected = start_commune.to_crs('EPSG:2154')
            start_centroid = start_commune_projected.centroid.iloc[0]
            
        center_x, center_y = utils.project_point(start_centroid.x, start_centroid.y, from_crs='EPSG:2154', to_crs='EPSG:4326')
        final_center_x, final_center_y = center_x, center_y

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
    if st.session_state.get('processed_gdf') is not None:
        # Filter out current city from the recommended list (Top 5)
        config = st.session_state.get('config')
        current_codgeo = config.commune_actuelle.code if config and hasattr(config.commune_actuelle, 'code') else (config.commune_actuelle if config else None)
        
        # Create a display-only GDF excluding the current city
        if current_codgeo and current_codgeo in st.session_state.processed_gdf.index:
            display_gdf = st.session_state.processed_gdf.drop(index=current_codgeo)
        else:
            display_gdf = st.session_state.processed_gdf

        if display_gdf.empty:
            st.warning("Aucun résultat ne correspond à vos critères de recherche.")
        else:
            ui.display_results_list(display_gdf=display_gdf)

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
        
        # Base layer with all scored communes
        if not st.session_state.processed_gdf.empty:
            st.session_state['fg_dict_ref']['Scores'], colormap = maps.build_scores_layer(st.session_state['processed_gdf'])

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

        if config:
            target_codgeos = set(st.session_state.get('unaggregated_gdf', gpd.GeoDataFrame()).index.tolist())

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
        
        if show_top_5:
            # Rebuild Top 5 layers on the fly to avoid Folium object exhaustion in session state
            for i, (idx, row) in enumerate(st.session_state.processed_gdf.head(5).iterrows()):
                name = f'Top{i+1}'
                st.session_state['fg_dict_ref'][name] = maps.build_top_result_layer(row, i)
                fgs_to_show.add(name)
            st.session_state["zoom"] = None
        elif is_highlighted:
            # Rebuild ONLY the highlighted layer
            row = st.session_state.processed_gdf.iloc[highlighted_index]
            name = f'Top{highlighted_index + 1}'
            st.session_state['fg_dict_ref'][name] = maps.build_top_result_layer(row, highlighted_index)
            fgs_to_show.add(name)

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

# To debug criteria scoring
# try:
#     st.dataframe(st.session_state.processed_gdf, column_order=sorted(st.session_state.processed_gdf.columns))
# except:
#     pass
