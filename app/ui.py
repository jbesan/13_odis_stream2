# /home/jacques/odis/13_odis/eda/streamlit/ui.py
import streamlit as st
import pandas as pd
from plotly.express import line_polar

import config as cfg
import maps

def display_sidebar(demo_data: dict):
    """Displays the sidebar with location and weight controls."""
    
    st.divider()

    # --- Weights ---
    with st.expander('Pondérations', expanded=False):
        st.select_slider("Education", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_education', 100), 
                        key="ui_poids_education")
        st.select_slider("Projet Pro", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_emploi', 100), 
                        key="ui_poids_emploi")
        st.select_slider("Logement", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_logement', 100), 
                        key="ui_poids_logement")
        st.select_slider("Inclusion", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_inclusion', 100), 
                        key="ui_poids_inclusion")
        st.select_slider("Mobilité", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_mobilité', 100), 
                        key="ui_poids_mobilité")

    # --- Technical Params ---
    with st.expander('Paramètres Résultats'):
        st.radio(
            "Granularité des résultats",
            cfg.VIEW_LEVEL_OPTIONS,
            key='view_level',
            horizontal=True,
            index=cfg.DEFAULT_VIEW_LEVEL
        )
        st.text("\n\n")
        st.select_slider("Décote commune binôme\n\n (en %)", cfg.PENALITE_BINOME_OPTIONS, key="ui_penalite_binome")
        # st.select_slider("Population Minimum", cfg.POP_MIN_OPTIONS, key="ui_pop_min")

    st.divider()

    # --- Export to PDF ---
    if st.session_state.get('processed_gdf') is not None:
        
        if st.button('Export des résultats', icon=':material/picture_as_pdf:', type='secondary'):
            st.cache_data.clear()

def start_over():
    # --- Start over ---
    st.markdown("""
        <style>
            .st-key-btn_recommencer .stButton p {color: #1B4429;}
        </style>
        """
    , unsafe_allow_html=True)
    if st.sidebar.button('Recommencer', type='primary', key='btn_recommencer'):
        st.session_state['processed_gdf'] = None
        st.session_state['form_completed'] = False
        st.switch_page("1_Accueil.py")
        
def render_localisation_form():
    """Renders the UI for the 'Localisation Actuelle' form section."""
    app_data = st.session_state.app_data
    col1, col2 = st.columns(2)
    with col1:
        options_dep = app_data['coddep_set']
        # The index is now correctly derived from the session state, preventing the warning.
        departement_actuel = st.selectbox("Département", options_dep, key="ui_departement")
    with col2:
        communes = app_data['depcom_df'][app_data['depcom_df'].dep_code == departement_actuel]['libgeo'].tolist()
        if st.session_state['ui_commune'] not in communes:
            st.session_state['ui_commune'] = communes[0]
        st.selectbox("Commune", communes, key="ui_commune")

def render_family_form():
    """Renders the UI for the 'Situation familiale' form section."""
    col1, col2 = st.columns(2)
    with col1:
        st.radio("Nombre d'adultes", cfg.NOMBRE_ADULTES_OPTIONS, horizontal=True, key="ui_nb_adultes")
    with col2:
        st.radio("Nombre d'enfants", cfg.NOMBRE_ENFANTS_OPTIONS, horizontal=True, key="ui_nb_enfants")

def render_education_form():
    """Renders the UI for the 'Education' form section."""
    if st.session_state['ui_nb_enfants'] == 0:
        st.info("Aucun enfant n'a été ajouté dans l'onglet 'Situation familiale'.")
    else:
        col1, col2 = st.columns(2)
        for i in range(st.session_state['ui_nb_enfants']):
            col = col1 if i % 2 == 0 else col2
            with col:
                # This widget also needs the index to be set correctly.
                options = cfg.CLASSES_SCOLAIRES
                key = f"ui_classe_enfant_{i}"
                st.selectbox(f'Niveau enfant {i+1}', options, key=key)

def render_employment_form():
    """Renders the UI for the 'Projet Professionnel' form section."""
    app_data = st.session_state.app_data
    col1, col2 = st.columns(2)
    codfap_select = app_data['codfap_index'][['Code FAP 341', 'Intitulé FAP 341']].set_index('Code FAP 341')
    codform_select = app_data['codformations_index']
    
    for i in range(st.session_state.ui_nb_adultes):
        with col1:
            st.multiselect(f"Métiers ciblés Adulte {i+1}", codfap_select.index, format_func=lambda x: codfap_select.loc[x, 'Intitulé FAP 341'], key=f"ui_metiers_adult_{i}")
        with col2:
            st.multiselect(f"Formations recherchées Adulte {i+1}", codform_select.index, format_func=lambda x: codform_select.loc[x, 'libformation'], key=f"ui_formations_adult_{i}")

def render_housing_form():
    """Renders the UI for the 'Logement' form section."""
    st.radio('Hébergement à court terme', cfg.HEBERGEMENT_OPTIONS, key="ui_hebergement")
    st.radio('Logement à long terme', cfg.LOGEMENT_OPTIONS, key="ui_logement")

def render_health_form():
    """Renders the UI for the 'Santé' form section."""
    options = ["Aucun", "Hopital", 'Maternité', "Soutien Psychologique & Addictologie"]
    st.radio('Support médical à proximité', options, key="ui_besoin_sante")

def render_other_needs_form():
    """Renders the UI for the 'Autres Besoins' form section."""
    app_data = st.session_state.app_data
    if 'ui_besoins_autres' not in st.session_state:
        st.session_state.ui_besoins_autres = st.session_state['demo_data'].get('besoins_autres', {})

    st.text("Sélectionnez d'autres besoins:")
    col1, col2 = st.columns(2)
    with col1:
        annuaire_inclusion = app_data['annuaire_inclusion']
        cat = st.selectbox('Catégorie', sorted(set(annuaire_inclusion.categorie)), format_func=lambda x: x.replace('-', ' ').capitalize(), index=2)
        service = st.selectbox('Service', sorted(set(annuaire_inclusion[annuaire_inclusion.categorie == cat].service)), format_func=lambda x: x.replace('-', ' ').capitalize(), index=0)
        if st.button('Ajouter'):
            st.session_state.ui_besoins_autres.setdefault(cat, []).append(service)
            st.session_state.ui_besoins_autres[cat] = sorted(list(set(st.session_state.ui_besoins_autres[cat])))
    with col2:
        st.text('Besoins ajoutés:')
        if not st.session_state.ui_besoins_autres:
            st.info('Aucun')
        else:
            for key, values in st.session_state.ui_besoins_autres.items():
                st.markdown(f"**{key.replace('-', ' ').capitalize()}**")
                for value in values:
                    st.markdown(f"&nbsp;&nbsp;&nbsp;- {value.replace('-', ' ').capitalize()}")
        if st.button('Vider', width='stretch'):
            st.session_state.ui_besoins_autres = {}
            st.rerun()

def render_mobility_form():
    """Renders the UI for the 'Mobilité' form section."""
    st.radio('Zone de recherche autour du lieu de vie actuel :', cfg.LOC_DISTANCE_OPTIONS.keys(), format_func=cfg.LOC_DISTANCE_OPTIONS.get, key="ui_loc_distance_km")

def display_input_tabs(demo_data: dict):
    """Displays the main tabs for user input, composed of modular rendering functions."""
    tab_localisation, tab_foyer, tab_edu, tab_emploi, tab_logement, tab_sante, tab_autres, tab_mobilite = st.tabs([
        'Localisation Actuelle', 'Situation familiale', 'Education', 'Projet Professionnel', 'Logement', 'Santé', 'Autres Besoins', 'Mobilité'
    ])
    with tab_localisation:
        render_localisation_form()
    with tab_foyer:
        render_family_form()
    with tab_edu:
        render_education_form()
    with tab_emploi:
        render_employment_form()
    with tab_logement:
        render_housing_form()
    with tab_sante:
        render_health_form()
    with tab_autres:
        render_other_needs_form()
    with tab_mobilite:
        render_mobility_form()


def create_scoring_config_from_inputs() -> cfg.ScoringConfig:
    """Gathers all user inputs from session_state and creates a ScoringConfig object."""
    app_data = st.session_state['app_data']
    
    # Location
    commune_codgeo = app_data['depcom_df'][
        (app_data['depcom_df'].dep_code == st.session_state['ui_departement']) & 
        (app_data['depcom_df'].libgeo == st.session_state['ui_commune'])
    ].index[0]

    # Education
    classe_enfants = [st.session_state[f"ui_classe_enfant_{i}"] for i in range(st.session_state['ui_nb_enfants'])]

    # Employment
    codes_metiers = [st.session_state[f"ui_metiers_adult_{i}"] for i in range(st.session_state['ui_nb_adultes'])]
    codes_formations = [st.session_state[f"ui_formations_adult_{i}"] for i in range(st.session_state['ui_nb_adultes'])]

    return cfg.ScoringConfig(
        poids_emploi=st.session_state['ui_poids_emploi'],
        poids_logement=st.session_state['ui_poids_logement'],
        poids_education=st.session_state['ui_poids_education'],
        poids_inclusion=st.session_state['ui_poids_inclusion'],
        poids_mobilité=st.session_state['ui_poids_mobilité'],
        commune_actuelle=commune_codgeo,
        loc_distance_km=st.session_state['ui_loc_distance_km'],
        nb_adultes=st.session_state['ui_nb_adultes'],
        nb_enfants=st.session_state['ui_nb_enfants'],
        hebergement=st.session_state['ui_hebergement'],
        logement=st.session_state['ui_logement'],
        codes_metiers=codes_metiers,
        codes_formations=codes_formations,
        classe_enfants=classe_enfants,
        besoin_sante=st.session_state['ui_besoin_sante'],
        besoins_autres=st.session_state['ui_besoins_autres'],
        binome_penalty=st.session_state['ui_penalite_binome'] / 100,
        pop_min=st.session_state['ui_pop_min']
    )

def _result_highlight_callback(index: int):
    """Callback to handle highlighting a result."""
    is_highlighted, highlighted_index = st.session_state.highlighted_result
    
    # If the same button is clicked again, un-highlight it
    if is_highlighted and index == highlighted_index:
        st.session_state.highlighted_result = [False, None]
        # st.session_state.center = None # Recenter map
        st.session_state.zoom = None
    else:
        # Highlight the new result
        row = st.session_state.processed_gdf.loc[index]
        st.session_state.highlighted_result = [True, index]
        st.session_state.center = [row.polygon.centroid.y, row.polygon.centroid.x]
        st.session_state.zoom = cfg.DETAIL_MAP_ZOOM

def get_person_accompanied_str():
    if st.session_state.get('ui_nom'):
        return f"de {st.session_state.ui_nom}"
    return "de la personne accompagnée"

def display_results_list():
    """Displays the list of top N results."""
    st.subheader("Meilleurs résultats")
    st.text("Cliquez sur un résultat pour comprendre le détail du score")
    # st.text(f'Voici des localités qui pourraient convenir {get_person_accompanied_str()}.')
    st.markdown('<style> [class*="st-key-button_top"] .stButton button div {text-align:left; width:100%;},</style>', unsafe_allow_html=True)

    top_n = 5
    df = st.session_state.processed_gdf
    is_highlighted, highlighted_index = st.session_state.highlighted_result

    # Pre-build layers for top results to be shown on map
    for index, row in df.head(top_n).iterrows():
        fg_key = f'Top{index + 1}'
        st.session_state.fg_dict_ref[fg_key] = maps.build_top_result_layer(row, index)

    # Display buttons and details
    for index, row in df.head(top_n).iterrows():
        # Adapt title based on the view level
        if st.session_state.get('view_level') == 'Bassins de vie':
            title = f"Top {index+1} | Bassin de vie de {row.libgeo}"
        else:
            title = f"Top {index+1} | {row.libgeo}" + (f" (avec {row.libgeo_binome})" if row.binome else "")

        st.button(
            title,
            on_click=_result_highlight_callback,
            args=(index,),
            width='stretch',
            key=f'button_top{index+1}',
            type='primary'
            # type='primary' if is_highlighted and index == highlighted_index else 'secondary'
        )

        if is_highlighted and index == highlighted_index:
            # For 'Bassin de vie' view, we call the specific details display for aggregated data
            if st.session_state.get('view_level') == 'Bassins de vie':
                _display_bv_result_details(row)
            else:
                _display_result_details(row)

def _display_bv_result_details(row: pd.Series):
    """Displays the detailed aggregated information for a 'bassin de vie'."""
    with st.container(border=True):
        # --- Pitch ---
        pitch = _produce_pitch_markdown(row)
        st.markdown(pitch)

        # --- Radar Chart ---
        cat_scores = row[[col for col in row.index if col.endswith('_cat_score')]]
        cat_scores.rename(lambda x: x.split('_')[0].capitalize(), inplace=True)
        fig = line_polar(theta=cat_scores.index, r=cat_scores.values * 100, line_close=True, range_r=[0, 100])
        fig.update_traces(fill='toself')
        fig.update_layout(margin=dict(l=50, r=50, t=50, b=50))
        st.plotly_chart(fig, width='stretch', config=None)
        st.caption('Plus le critère s’approche du bord, plus il est attractif.')

        # --- Additional Info ---
        st.divider()
        st.markdown('**Plus d’informations sur ce bassin de vie :**')
        with st.expander('Top 10 des métiers recherchés'):
            top_metiers = set(row.be_libfap_top if row.be_libfap_top is not None else [])
            if top_metiers:
                st.markdown("\n".join([f'- {item}' for item in sorted(list(top_metiers))]))
            else:
                st.info("Pas de données disponibles.")
        
        with st.expander('Formations proposées'):
            formations = set(row.noms_formations if row.noms_formations is not None else [])
            if formations:
                st.markdown("\n".join([f'- {item}' for item in sorted(list(formations))]))
            else:
                st.info("Pas de données disponibles.")
        
        with st.expander("Services d'inclusions proposés"):
            services_df = st.session_state.app_data['annuaire_inclusion']
            bv_services = services_df[services_df.codgeo.isin(row.communes)]

            if not bv_services.empty:
                any_service_found = False
                for cat, group in bv_services.groupby('categorie', observed=True):
                    # Filter out empty/placeholder services within the group
                    valid_services = group[group.service != '-'].copy()
                    
                    if not valid_services.empty:
                        any_service_found = True
                        # Get unique, capitalized service names and join them
                        services_list_str = ", ".join(
                            valid_services['service'].str.replace('-', ' ').str.capitalize().unique()
                        )
                        st.markdown(f"**{cat.replace('-', ' ').capitalize()}**: {services_list_str}")

                if not any_service_found:
                    st.info("Pas de services d'inclusion répertoriés dans ce bassin de vie.")
            else:
                st.info("Pas de services d'inclusion répertoriés dans ce bassin de vie.")

        # --- Links ---
        st.markdown(f"[Page OD&IS]({row.get('url_odis', '#')}) | [Page Wikipedia]({row.get('url_wikipedia', '#')})")


def _display_result_details(row: pd.Series):
    """Displays the detailed information for a single highlighted result."""
    with st.container(border=True):
        # --- Pitch ---
        pitch = _produce_pitch_markdown(row)
        st.markdown(pitch)

        # --- Radar Chart ---
        cat_scores = row[[col for col in row.index if col.endswith('_cat_score')]]
        cat_scores.rename(lambda x: x.split('_')[0].capitalize(), inplace=True)
        fig = line_polar(theta=cat_scores.index, r=cat_scores.values * 100, line_close=True, range_r=[0, 100])
        fig.update_traces(fill='toself')
        fig.update_layout(margin=dict(l=50, r=50, t=50, b=50))
        st.plotly_chart(fig, width='stretch', config=None)
        st.caption('Plus le critère s’approche du bord, plus il est attractif.')

        # --- Additional Info ---
        st.divider()
        st.markdown('**Plus d’informations sur cette localité :**')
        with st.expander('Top 10 des métiers recherchés'):
            top_metiers = set(row.be_libfap_top if row.be_libfap_top is not None else [])
            if top_metiers:
                st.markdown("\n".join([f'- {item}' for item in sorted(list(top_metiers))]))
            else:
                st.info("Pas de données disponibles.")
        
        with st.expander('Formations proposées'):
            formations = set(row.noms_formations if row.noms_formations is not None else [])
            if row.binome:
                binome_row = st.session_state.app_data['odis'].loc[row.codgeo_binome]
                formations.update(binome_row.noms_formations if binome_row.noms_formations is not None else [])
            if formations:
                st.markdown("\n".join([f'- {item}' for item in sorted(list(formations))]))
            else:
                st.info("Pas de données disponibles.")
        
        with st.expander("Services d'inclusions proposés"):
            services_df = st.session_state.app_data['annuaire_inclusion']
            commune_services = services_df[services_df.codgeo == row.codgeo]
            
            if not commune_services.empty:
                any_service_found = False
                for cat, group in commune_services.groupby('categorie', observed=True):
                    # Filter out empty/placeholder services within the group
                    valid_services = group[group.service != '-'].copy()
                    
                    if not valid_services.empty:
                        any_service_found = True
                        # Get unique, capitalized service names and join them
                        services_list_str = ", ".join(
                            valid_services['service'].str.replace('-', ' ').str.capitalize().unique()
                        )
                        st.markdown(f"**{cat.replace('-', ' ').capitalize()}**: {services_list_str}")
                
                if not any_service_found:
                    st.info("Pas de services d'inclusion répertoriés dans cette commune.")
            else:
                st.info("Pas de services d'inclusion répertoriés dans cette commune.")

        # --- Links ---
        st.markdown(f"[Page OD&IS]({row.get('url_odis', '#')}) | [Page Wikipedia]({row.get('url_wikipedia', '#')})")

def _produce_pitch_markdown(row: pd.Series) -> str:
    """Generates a summary "pitch" for a result, adapting to commune or bassin de vie."""
    config = st.session_state.config
    scores_cat = st.session_state.app_data['scores_cat']
    
    pitch_md = []
    population = f"{row['population']:,.0f}".replace(",", " ")

    # Adapt the intro based on whether it's a bassin de vie or a commune
    if 'communes' in row and isinstance(row['communes'], list):
        # It's a bassin de vie
        pitch_md.append(f'Le bassin de vie de **{row["libgeo"]}** ({population} habitants), composé de **{len(row["communes"])} communes**, présente un bon équilibre pour le projet.')
    else:
        # It's a commune
        pitch_md.append(f'**{row["libgeo"]}** ({population} habitants) fait partie de l\'EPCI : **{row["epci_nom"]}**.  ')

    score_percent = f"{row['weighted_score'] * 100:.0f}%"
    if row.get("binome", False):
        pitch_md.append(f'\nEn [binôme](https://www.google.com "Lorsque des communes sont proposées en binômes, c’est qu’ensemble elles correspondent au projet de vie.") avec sa voisine **{row["libgeo_binome"]}**, la correspondance avec le projet est évaluée à **{score_percent}**. ')
    else:
        pitch_md.append(f'\nLa correspondance avec le projet est évaluée à **{score_percent}**. ')

    # --- Top contributing criteria (common logic) ---
    all_scores = scores_cat['score'].unique()
    crit_scores_cols = [col for col in row.keys() if col in all_scores]
    weighted_scores = {}
    for col in crit_scores_cols:
        cat = scores_cat[scores_cat.score == col]['cat'].iloc[0]
        weight = getattr(config, f'poids_{cat}', 0)
        
        penalty = config.binome_penalty if row.get("binome", False) and col + '_binome' in row.index else 0
        
        score_commune = row.get(col, 0)
        score_binome = row.get(col + '_binome', 0) * (1 - penalty)
        effective_score = max(score_commune or 0, score_binome or 0)
        
        weighted_scores[col] = effective_score * weight

    sorted_scores = sorted(weighted_scores.items(), key=lambda item: item[1], reverse=True)

    if any(s > 0 for s in weighted_scores.values()):
        pitch_md.append(f"\nCette localité se distingue par :")
        count = 0
        for score_col, weighted_val in sorted_scores:
            if weighted_val > 0 and count < 5:
                score_details = scores_cat[scores_cat.score == score_col].iloc[0]
                pitch_md.append(f'- {score_details["score_affichage"]}')
                count += 1

    return "\n".join(pitch_md)