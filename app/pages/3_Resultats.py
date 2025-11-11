import streamlit as st
from scoring import compute_odis_score
import config as cfg
import ui
import maps
import folium as flm
import data_loader
import geopandas as gpd

# Ensure app_data is initialized
if 'app_data' not in st.session_state:
    st.session_state['app_data'] = data_loader.init_datasets()

@st.cache_data
def run_scoring_pipeline(_df_original, scores_cat, config, _incl_index):
    """Wrapper for the scoring function to enable Streamlit caching."""
    return compute_odis_score(_df_original, scores_cat, config, _incl_index)

def run_search():
    """
    Callback function for the 'Lancer la recherche' button.
    It creates the config, runs the scoring, and updates the session state.
    """
    print('--- Running new search ---')
    config = ui.create_scoring_config_from_inputs()
    st.session_state['config'] = config

    # Run the main scoring pipeline
    odis_scored = run_scoring_pipeline(
        _df_original=st.session_state.app_data['odis'],
        scores_cat=st.session_state.app_data['scores_cat'],
        config=config,
        _incl_index=st.session_state.app_data['incl_index'],
    )

    # Pop the current commune from the results and store it separately
    selected_geo = st.session_state.app_data['odis'].loc[[config.commune_actuelle]].copy()
    
    # Reproject the geometry to a projected CRS before calculating the centroid for accuracy
    # The geometry column is named 'polygon' in this GeoDataFrame
    selected_geo_projected = selected_geo.to_crs(cfg.PROJECTED_CRS)
    
    # Convert centroid back to geographic CRS for map display
    # Create a GeoSeries for the centroid in the projected CRS
    centroid_geo_series_projected = gpd.GeoSeries([selected_geo_projected.geometry.centroid.iloc[0]], crs=cfg.PROJECTED_CRS)
    # Reproject to geographic CRS (EPSG:4326)
    centroid_geo_series_geographic = centroid_geo_series_projected.to_crs('EPSG:4326')
    
    # Extract the geographic coordinates
    final_center_x = centroid_geo_series_geographic.x.iloc[0]
    final_center_y = centroid_geo_series_geographic.y.iloc[0]
    
    odis_scored = odis_scored.drop(config.commune_actuelle, errors='ignore')

    # Sort results by score
    odis_scored = odis_scored.sort_values('weighted_score', ascending=False).reset_index()

    # Reset session state for the new results
    st.session_state['processed_gdf'] = odis_scored
    st.session_state['selected_geo'] = selected_geo
    # Calculate centroid on the reprojected geometry
    st.session_state['center'] = [final_center_y, final_center_x]
    st.session_state['zoom'] = maps.get_map_zoom(config.loc_distance_km)
    st.session_state['fg_dict_ref'] = {}
    st.session_state['highlighted_result'] = [False, None]

# Automatically run the search if not already processed and form is completed
if st.session_state.get('processed_gdf') is None and st.session_state.get('form_completed'):
    run_search()
    st.session_state['form_completed'] = False

# Sidebar
with st.sidebar:
    st.image('./images/logo-jaccueille-singa.png', width=150)
    st.write("")
    st.write("")
    st.markdown(
        """
        <div style='text-align: justify;'>
        Découvrez les 5 communes ou binômes de communes qui répondent le mieux à vos attentes. Les scores vous permettent de comparer facilement leurs atouts.
        </div>
        """,
        unsafe_allow_html=True
    )
    ui.display_sidebar(st.session_state['demo_data'])

def get_person_accompanied_str():
    if st.session_state.get('ui_nom'):
        return f"de {st.session_state.ui_nom}"
    return "de la personne accompagnée"

#Top filter Form
with st.container(border=False, key='top_menu'):
    st.markdown("""
                <style>
                    .st-key-top_menu  {background-color:whitesmoke; padding:30px; border-radius:10px}
                    .stTabs div div button div p {font-size:1rem}
                </style>
                """
                , unsafe_allow_html=True)

    ui.display_main_header(f"Résultats {get_person_accompanied_str()}")

    col_tabs, col_button = st.columns([5,1])
    with col_tabs:
        #  Input Tabs
        ui.display_input_tabs(st.session_state['demo_data'])
        # st.text("Renseignez les informations liées au projet de vie. Vous pouvez les modifier à tout moment.")
    with col_button: 
        with st.container(height="stretch", vertical_alignment="center", horizontal_alignment = "center"):
            st.button(
                "Lancer la recherche" if st.session_state.get("processed_gdf") is None else "Mettre à jour la carte",
                on_click=run_search, type="primary"
            )


# Main two sections: results and map
col_results, col_map = st.columns([2, 3])

### Results Column
with col_results:
    if st.session_state.get('processed_gdf') is not None:
        ui.display_results_list()

### Map Column
with col_map:
    from streamlit_folium import st_folium
    if st.session_state.get('processed_gdf') is not None:
        # Base layer with all scored communes
        st.session_state['fg_dict_ref']['Scores'], colormap = maps.build_scores_layer(st.session_state['processed_gdf'])
        st.session_state['fgs_to_show'].add('Scores')

        col1, col2 = st.columns([1,4], vertical_alignment='center')
        with col1:
            st.text("Afficher:")
        with col2:
            with st.container(key='display_toggles'):
                st.markdown('<style>.st-key-display_toggles {gap:0rem}</style>',unsafe_allow_html=True)
                if st.checkbox("Les 5 meilleurs résultats sur la carte"):
                    for key, value in st.session_state["fg_dict_ref"].items():
                        if key.startswith("Top"):
                            st.session_state['fgs_to_show'].add(key)
                    st.session_state["zoom"] = None           
                elif st.session_state["highlighted_result"][0]: # A result is highlighted, keep it visible
                    st.session_state['fgs_to_show'] = {k for k in st.session_state['fgs_to_show'] if not k.startswith('Top')}
                    fg_key = f'Top{st.session_state["highlighted_result"][1] + 1}'
                    st.session_state['fgs_to_show'].add(fg_key)
                else: # Clear all top results highlights
                    st.session_state['fgs_to_show'] = {k for k in st.session_state['fgs_to_show'] if not k.startswith('Top')}
            
                # We add additional informational layers
                legend_items = []
                config = st.session_state['config']
                target_codgeos = set(st.session_state['processed_gdf'].codgeo.tolist())

                # ECOLES
                if config.nb_enfants > 0 and st.checkbox('Établissements scolaires'):
                    st.session_state['fg_dict_ref']['fg_ecoles'] = maps.build_ecoles_layer(st.session_state.app_data['annuaire_ecoles'], target_codgeos, config)
                    st.session_state['fgs_to_show'].add('fg_ecoles')
                    legend_items.append({'color': 'green', 'icon': 'pencil', 'text': 'Écoles'})
                else:
                    st.session_state['fgs_to_show'].discard('fg_ecoles')

                # SANTE
                if config.besoin_sante != "Aucun" and st.checkbox('Établissements de santé'):
                    st.session_state['fg_dict_ref']['fg_sante'] = maps.build_sante_layer(st.session_state.app_data['annuaire_sante'], target_codgeos, config)
                    st.session_state['fgs_to_show'].add('fg_sante')
                    legend_items.append({'color': 'blue', 'icon': 'plus', 'text': 'Santé'})
                else:
                    st.session_state['fgs_to_show'].discard('fg_sante')

                # SERVICES INCLUSION
                if config.besoins_autres and st.checkbox("Services d'inclusion"):
                    st.session_state['fg_dict_ref']['fg_services'] = maps.build_services_layer(st.session_state.app_data['annuaire_inclusion'], target_codgeos, config)
                    st.session_state['fgs_to_show'].add('fg_services')
                    legend_items.append({'color': 'purple', 'icon': 'heart', 'text': 'Inclusion'})
                else:
                    st.session_state['fgs_to_show'].discard('fg_services')

                # Légende
                legend = maps.build_legend(legend_items)

        # Affichage de la carte (toujours en dernier)
        # Base Map
        m = maps.create_base_map(st.session_state["center"], st.session_state["zoom"])
        if legend_items:
            m.get_root().html.add_child(flm.Element(legend))

        # FeatureGroups
        fgs_to_add = [
            st.session_state['fg_dict_ref'][name] 
            for name in sorted(list(st.session_state['fgs_to_show'])) # Sort to ensure consistent layer order
            if name in st.session_state['fg_dict_ref']
        ]

        st_folium(
            m,
            zoom=st.session_state["zoom"],
            center=st.session_state["center"],
            feature_group_to_add=fgs_to_add,
            key="odis_scored_map",
            width='stretch',
            returned_objects=[],
        )
        st.markdown('<style>.stCustomComponentV1   {border-radius:10px}</style>', unsafe_allow_html=True) # Rounded corners for the map widget


    
