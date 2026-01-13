import streamlit as st
from streamlit_folium import st_folium
from core import scoring
import config as cfg
from ui import components as ui
from utils import common as utils
from core import maps
from core.pdf_generator import generate_pdf_report
import folium as flm
from utils import data_loader
import geopandas as gpd
import logging
import gc
import warnings

st.set_page_config(layout="wide")

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
                pdf_bytes = generate_pdf_report(
                    st.session_state, 
                    st.session_state.processed_gdf
                )
                st.session_state.pdf_modal_data = pdf_bytes
                st.rerun() # Rerun to update the dialog's content to the download state
        
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
                if st.button("Fermer"):
                    on_dialog_dismiss()
                    st.rerun()

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

    config = ui.create_scoring_config_from_inputs()
    st.session_state['config'] = config

    # Get required dataframes from session state
    df_all_communes = st.session_state.app_data['odis']
    df_bv_geo = st.session_state.app_data['bv_geo']
    df_area_geo = st.session_state.app_data['area_geo']
    start_commune = df_all_communes.loc[[config.commune_actuelle]]

    # --- Run Scoring Pipeline ---
    # Instantiate the stateless engine with current data
    engine = scoring.ScoringEngine(
        df_all_communes=df_all_communes,
        df_bv_geo=df_bv_geo,
        df_area_geo=df_area_geo,
        scores_cat=st.session_state.app_data['scores_cat'],
        incl_index=st.session_state.app_data['incl_index'],
        associations_data=st.session_state.app_data['associations_data'],
        bmo_vertical=st.session_state.app_data['bmo_vertical'],
        formations_data=st.session_state.app_data['formations_data'],
        codformations_index=st.session_state.app_data['codformations_index'],
        waldec_index=st.session_state.app_data['waldec_index'],
        global_stats={},
        refugee_associations_data=st.session_state.app_data['refugee_associations_data'],
        live_jobs_data=st.session_state.app_data['live_jobs_data']
    )

    processed_gdf = engine.run(
        config=config,
        log_prefix="classic"
    )
    unaggregated_gdf = processed_gdf # Single view now, they are identical

    # --- State Update ---
    st.session_state['processed_gdf'] = processed_gdf
    st.session_state['unaggregated_gdf'] = unaggregated_gdf
    
    # --- Logging ---
    # Logging is now handled inside engine.run(log_prefix="classic")
    
    # Calculate center for map
    if not processed_gdf.empty:
        # Compute centroid of all results
        # Use EPSG:2154 for accurate centroid, then back to 4326
        # Optimization: use the mean of centroids if the dataset is large
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
            projected_centroids = processed_gdf.to_crs('EPSG:2154').centroid
            avg_centroid_projected = projected_centroids.union_all().centroid
        
        center_x, center_y = utils.project_point(avg_centroid_projected.x, avg_centroid_projected.y, from_crs='EPSG:2154', to_crs='EPSG:4326')
        final_center_y, final_center_x = center_y, center_x
    else:
        # Fallback to current commune
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*array with ndim > 0 to a scalar is deprecated.*")
            start_commune_projected = start_commune.to_crs('EPSG:2154')
            start_centroid = start_commune_projected.centroid.iloc[0]
            
        center_x, center_y = utils.project_point(start_centroid.x, start_centroid.y, from_crs='EPSG:2154', to_crs='EPSG:4326')
        final_center_x = center_x
        final_center_y = center_y

    st.session_state['selected_geo'] = st.session_state.app_data['odis'].loc[[config.commune_actuelle]].copy()
    st.session_state['center'] = [final_center_y, final_center_x]
    st.session_state['zoom'] = maps.get_map_zoom(config.loc_search_area)
    st.session_state['fg_dict_ref'] = {}
    st.session_state['fgs_to_show'] = set()
    st.session_state['highlighted_result'] = [False, None]

# Automatically run the search if not already processed and form is completed
if st.session_state.get('processed_gdf') is None and st.session_state.get('form_completed'):
    run_search()
    st.session_state['form_completed'] = False

# --- UI LAYOUT ---

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
    with st.container(border=False, height='stretch', vertical_alignment="bottom"):
        ui.display_sidebar(st.session_state['demo_data'])
        ui.start_over()

# Top filter Form
with st.container(border=False, key='top_menu'):
    st.markdown("<style>.st-key-top_menu {background-color:whitesmoke; padding:30px; border-radius:10px} .stTabs div div button div p {font-size:1rem}</style>", unsafe_allow_html=True)
    st.subheader(f"Projet de vie {ui.get_person_accompanied_str()}")
    col_tabs, col_button = st.columns([5,1])
    with col_tabs:
        ui.display_input_tabs(st.session_state['demo_data'])
    with col_button: 
        with st.container(height="stretch", horizontal_alignment="center", vertical_alignment="center"):
            st.button("Lancer la recherche", on_click=run_search, type="primary")

# Main two sections: results and map
col_map, col_results  = st.columns([3, 2])

with col_results:
    if st.session_state.get('processed_gdf') is not None:
        if st.session_state.processed_gdf.empty:
            st.warning("Aucun résultat ne correspond à vos critères de recherche.")
        else:
            ui.display_results_list()

with col_map:
    if st.session_state.get('processed_gdf') is not None:
        st.subheader("Cartographie des résultats")
        m = maps.create_base_map(st.session_state["center"], st.session_state["zoom"])
        
        # Base layer with all scored communes
        if not st.session_state.processed_gdf.empty:
            st.session_state['fg_dict_ref']['Scores'], colormap = maps.build_scores_layer(st.session_state['processed_gdf'])

        fgs_to_show = {'Scores'}
        legend_items = []
        
        cols = st.columns(5, vertical_alignment="center")
        with cols[0]:
            st.text("Afficher:")
        with cols[1]:
            show_top_5 = st.toggle("Top 5", key="show_top_5_toggle", value=True)
        
        config = st.session_state.get('config')
        if config:
            target_codgeos = set(st.session_state.get('unaggregated_gdf', gpd.GeoDataFrame()).index.tolist())
            with cols[2]:
                show_ecoles = st.toggle('Éducation', key='show_ecoles_toggle', disabled=(config.nb_enfants == 0))
            with cols[3]:
                show_sante = st.toggle('Santé', key='show_sante_toggle', disabled=(config.besoin_sante == "Aucun"))
            with cols[4]:
                show_inclusion = st.toggle("Inclusion", key='show_inclusion_toggle', disabled=(not config.inc_services_add_selection))

            if show_ecoles:
                st.session_state.fg_dict_ref['fg_ecoles'] = maps.build_ecoles_layer(st.session_state.app_data['pois'], target_codgeos, config)
                fgs_to_show.add('fg_ecoles')
                legend_items.append({'color': 'green', 'icon': 'pencil', 'text': 'Écoles'})
            if show_sante:
                st.session_state.fg_dict_ref['fg_sante'] = maps.build_sante_layer(st.session_state.app_data['pois'], target_codgeos, config)
                fgs_to_show.add('fg_sante')
                legend_items.append({'color': 'blue', 'icon': 'plus', 'text': 'Santé'})
            if show_inclusion:
                st.session_state.fg_dict_ref['fg_services'] = maps.build_services_layer(st.session_state.app_data['pois'], target_codgeos, config)
                fgs_to_show.add('fg_services')
                legend_items.append({'color': 'purple', 'icon': 'heart', 'text': 'Inclusion'})

        is_highlighted, highlighted_index = st.session_state.highlighted_result
        if show_top_5:
            for i in range(5):
                fgs_to_show.add(f'Top{i + 1}')
            st.session_state["zoom"] = None
        elif is_highlighted:
            fgs_to_show.add(f'Top{highlighted_index + 1}')

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

        # Manually add all visible layers to the map object so it's complete for the PDF export
        for fg in fgs_to_add:
            fg.add_to(m)
        
        # flm.LayerControl().add_to(m)

        st.session_state['map_object'] = m

        st_folium(
            m,
            zoom=st.session_state["zoom"],
            center=st.session_state["center"],
            feature_group_to_add=fgs_to_add,
            key=map_key,
            width='stretch',
            returned_objects=[],
        )
        st.markdown('<style>.stCustomComponentV1 {border-radius:10px}</style>', unsafe_allow_html=True)


