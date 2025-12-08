import streamlit as st
import pandas as pd
from plotly.express import line_polar

import config as cfg
import maps
from typing import Dict, Any, List, Optional
from pathlib import Path
import base64
import logging
import scoring

def get_image_path(filename: str) -> str:
    """Returns the absolute path to an image file, robust to launch directory."""
    # Assumes images are in 'images/' subdirectory relative to this script
    current_dir = Path(__file__).parent.resolve()
    return str(current_dir / "images" / filename)

def get_base64_image(image_path: str) -> str:
    """Encodes an image to base64 for embedding in HTML."""
    try:
        with open(image_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode()
    except Exception as e:
        logging.error(f"Could not load image {image_path}: {e}")
        return ""

def open_pdf_modal() -> None:
    """Callback to signal that the PDF modal should be shown."""
    st.session_state['show_pdf_modal'] = True

def display_sidebar(demo_data: Dict[str, Any]) -> None:
    """Displays the sidebar with location and weight controls."""
    
    st.divider()

    # --- Weights ---
    with st.expander('Pondérations', expanded=False):
        # F-15: Profile Selector
        def _update_weights_from_profile():
            profile = st.session_state.ui_weight_profile
            if profile in cfg.WEIGHT_PROFILES:
                weights = cfg.WEIGHT_PROFILES[profile]
                for key, value in weights.items():
                    # Update session state keys for sliders (e.g. ui_poids_education)
                    st.session_state[f"ui_{key}"] = value
            
            st.session_state['processed_gdf'] = None
        

        st.selectbox(
            "Profil de Priorité",
            options=list(cfg.WEIGHT_PROFILES.keys()),
            key="ui_weight_profile",
            on_change=_update_weights_from_profile,
            index=0 # Default to Balanced
        )
        
        st.select_slider("Education", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_education', 50), 
                        key="ui_poids_education")
        st.select_slider("Projet Pro", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_emploi', 50), 
                        key="ui_poids_emploi")
        st.select_slider("Logement", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_logement', 50), 
                        key="ui_poids_logement")
        st.select_slider("Inclusion", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_inclusion', 50), 
                        key="ui_poids_inclusion")
        st.select_slider("Santé", cfg.POIDS_OPTIONS, # NEW
                        value=st.session_state.get('ui_poids_sante', 50), # NEW
                        key="ui_poids_sante") # NEW
        st.select_slider("Mobilité", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_mobilité', 50), 
                        key="ui_poids_mobilité")

    def clear_processed_gdf():
        st.session_state['processed_gdf'] = None

    # --- Technical Params ---
    with st.expander('Paramètres Résultats'):
        st.radio(
            "Granularité des résultats",
            cfg.VIEW_LEVEL_OPTIONS,
            key='view_level',
            horizontal=True,
            index=cfg.DEFAULT_VIEW_LEVEL,
            on_change=clear_processed_gdf
        )
        st.text("\n\n")
        st.select_slider("Décote commune binôme\n\n (en %)", cfg.PENALITE_BINOME_OPTIONS, key="ui_binome_penalty", value=st.session_state.get('ui_binome_penalty', 50))
        st.select_slider("Population minimum", cfg.POP_MIN_OPTIONS, key="ui_pop_min", value=st.session_state.get('ui_pop_min', 1000))

    st.divider()

    # --- Export to PDF ---
    if st.session_state.get('processed_gdf') is not None:
        st.button(
            "Générer le PDF", 
            on_click=open_pdf_modal,
            icon=':material/picture_as_pdf:',
            type='secondary'
        )

def start_over() -> None:
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
        
def render_localisation_form() -> None:
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

def render_family_form() -> None:
    """Renders the UI for the 'Situation familiale' form section."""
    col1, col2 = st.columns(2)
    with col1:
        st.radio("Nombre d'adultes", cfg.NOMBRE_ADULTES_OPTIONS, horizontal=True, key="ui_nb_adultes")
    with col2:
        st.radio("Nombre d'enfants", cfg.NOMBRE_ENFANTS_OPTIONS, horizontal=True, key="ui_nb_enfants")

def render_education_form() -> None:
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
                st.toggle("Prioritaire", key=f"ui_priority_edu_{i}", help="Donne plus de poids à ce critère")

def render_employment_form() -> None:
    """Renders the UI for the 'Projet Professionnel' form section."""
    app_data = st.session_state.app_data
    col1, col2 = st.columns(2)
    codfap_select = app_data['codfap_index']
    codform_select = app_data['codformations_index']
    
    for i in range(st.session_state.ui_nb_adultes):
        with col1:
            st.multiselect(f"Métiers ciblés Adulte {i+1}", codfap_select.index, format_func=lambda x: codfap_select.loc[x, 'label'], key=f"ui_metiers_adult_{i}")
        with col2:
            st.multiselect(f"Formations recherchées Adulte {i+1}", codform_select.index, format_func=lambda x: codform_select.loc[x, 'label'], key=f"ui_formations_adult_{i}")
            
            # F-15: Priority Toggle
            st.toggle("Prioritaire", key=f"ui_priority_job_adult_{i}", help="Donne plus de poids à la recherche d'emploi pour cet adulte")

def render_housing_form() -> None:
    """Renders the UI for the 'Logement' form section."""
    col1, col2 = st.columns(2)
    with col1:
        st.radio('Hébergement cible à court terme', cfg.HEBERGEMENT_OPTIONS, key="ui_hebergement")
        st.toggle("Prioritaire", key="ui_priority_hebergement", help="Donne plus de poids à ce critère")
    with col2:
        st.radio('Logement cible à long terme', cfg.LOGEMENT_OPTIONS, key="ui_logement")
        st.toggle("Prioritaire", key="ui_priority_logement", help="Donne plus de poids à ce critère")
        
    # F-15: Priority Toggle (Removed global toggle)

def render_health_form() -> None:
    """Renders the UI for the 'Santé' form section."""
    options = ["Aucun", "Hopital", 'Maternité', "Soutien Psychologique & Addictologie"]
    st.radio('Support médical à proximité', options, key="ui_besoin_sante")
    if st.session_state.ui_besoin_sante != "Aucun":
        st.toggle("Prioritaire", key="ui_priority_sante", help="Donne plus de poids à ce critère")

def render_other_needs_form() -> None:
    """Renders the UI for the 'Inclusion' form section (F-13)."""
    app_data = st.session_state.app_data
    
    # --- 1. Socle Administratif (Hidden but Active) ---
    # st.subheader("Socle Administratif")
    # st.info("Sélectionnez les services institutionnels essentiels pour vous.")
    
    # Pre-defined list from PRD/Config
    default_socle = cfg.DEFAULT_SOCLE_ADMIN
    
    # Initialize session state for this selection if not present
    if 'ui_socle_admin_selection' not in st.session_state:
        # Default to the recommended list
        st.session_state.ui_socle_admin_selection = st.session_state['demo_data'].get('socle_admin_selection', default_socle)

    # Widget hidden as per user request, but state is preserved for scoring.
    # st.multiselect(...) 

    # --- 2. Affinités (Loisirs & Intérêts) ---
    st.subheader("Affinités & Loisirs")
    st.info("Sélectionnez vos centres d'intérêt pour identifier les territoires avec un tissu associatif correspondant.")
    
    # from rna_config import WALDEC_INTERESTS_MAPPING
    interest_options = list(cfg.WALDEC_INTERESTS_MAPPING.keys())
    
    if 'ui_affinite_selection' not in st.session_state:
        st.session_state.ui_affinite_selection = st.session_state['demo_data'].get('affinite_selection', [])
        
    st.multiselect(
        "Centres d'intérêt",
        options=interest_options,
        key="ui_affinite_selection"
    )

    # --- 3. Autres Besoins (Refactored) ---
    st.subheader("Autres Besoins Spécifiques")
    st.text("Sélectionnez d'autres services d'inclusion spécifiques.")
    
    # Prepare options: Use the Referentiel loaded in app_data
    inclusion_index = app_data.get('inclusion_services_index', pd.DataFrame())
    socle_keys = set(default_socle)
    
    options_map = {} # Display String -> Slug (Nom)
    options_list = []
    
    if not inclusion_index.empty:
        for code, row in inclusion_index.iterrows():
            # Filter out if in socle (optional, depending on if socle uses same codes)
            # The user said "use Nom as the code and use the resulting label as options"
            if code not in socle_keys:
                display_str = row['label']
                options_list.append(display_str)
                options_map[display_str] = code
            
    options_list.sort()
    
    # Initialize flat selection state from existing list state (if any, e.g. from demo data)
    if 'ui_besoins_autres_flat' not in st.session_state:
        # User config now stores a list of slugs
        current_list = st.session_state.get('ui_besoins_autres', st.session_state['demo_data'].get('besoins_autres', []))
        flat_selection = []
        
        # Create reverse map for initialization: Slug -> Display String
        slug_to_display = {v: k for k, v in options_map.items()}
        
        for slug in current_list:
            if slug in slug_to_display:
                flat_selection.append(slug_to_display[slug])
                
        st.session_state.ui_besoins_autres_flat = flat_selection

    # Widget
    st.multiselect(
        "Services disponibles",
        options=options_list,
        key="ui_besoins_autres_flat",
        help="Recherchez et ajoutez des services spécifiques."
    )
    if st.session_state.ui_besoins_autres_flat:
        st.toggle("Prioritaire", key="ui_priority_other_needs", help="Donne plus de poids à ces besoins spécifiques")
    
    # We store the map in session state so we can use it in create_scoring_config_from_inputs
    # without re-computing it (optimization)
    st.session_state['ui_besoins_autres_map'] = options_map

def render_mobility_form() -> None:
    """Renders the UI for the 'Mobilité' form section."""
    st.radio('Zone de recherche autour du lieu de vie actuel :', cfg.LOC_DISTANCE_OPTIONS.keys(), format_func=cfg.LOC_DISTANCE_OPTIONS.get, key="ui_loc_distance_km")

def display_input_tabs(demo_data: Dict[str, Any]) -> None:
    """Displays the main tabs for user input, composed of modular rendering functions."""
    tab_localisation, tab_foyer, tab_edu, tab_emploi, tab_logement, tab_sante, tab_autres, tab_mobilite = st.tabs([
        'Localisation Actuelle', 'Situation familiale', 'Education', 'Projet Professionnel', 'Logement', 'Santé', 'Inclusion', 'Mobilité'
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
        (
            app_data['depcom_df'].dep_code == st.session_state['ui_departement']
        ) & 
        (
            app_data['depcom_df'].libgeo == st.session_state['ui_commune']
        )
    ].index[0]

    # Education
    classe_enfants = [st.session_state[f"ui_classe_enfant_{i}"] for i in range(st.session_state['ui_nb_enfants'])]

    # Employment
    codes_metiers = [st.session_state[f"ui_metiers_adult_{i}"] for i in range(st.session_state['ui_nb_adultes'])]
    codes_formations = [st.session_state[f"ui_formations_adult_{i}"] for i in range(st.session_state['ui_nb_adultes'])]

    # Process Autres Besoins from Flat List (F-13 UI Update)
    # Process Autres Besoins from Flat List (F-13 UI Update)
    besoins_autres_list = []
    if 'ui_besoins_autres_flat' in st.session_state:
        flat_selection = st.session_state.ui_besoins_autres_flat
        options_map = st.session_state.get('ui_besoins_autres_map', {})
        
        if options_map:
            for item in flat_selection:
                if item in options_map:
                    slug = options_map[item]
                    besoins_autres_list.append(slug)
        
        # Update session state for compatibility
        st.session_state.ui_besoins_autres = besoins_autres_list
    else:
        # Fallback to existing list if flat not present (e.g. tests or legacy)
        besoins_autres_list = st.session_state.get('ui_besoins_autres', [])

    # F-15: Compute Criteria Weights
    criteria_weights = {}
    
    # Education Priorities
    edu_map = {
        'Crêche / Assistante Maternelle': 'edu_petite_enfance_scaled',
        'Maternelle': 'edu_maternelle_scaled',
        'Elémentaire': 'edu_elementaire_scaled',
        'Collège': 'edu_college_scaled',
        'Lycée': 'edu_lycee_scaled'
    }
    for i in range(st.session_state['ui_nb_enfants']):
        level = st.session_state.get(f"ui_classe_enfant_{i}")
        is_priority = st.session_state.get(f"ui_priority_edu_{i}", False)
        if is_priority and level in edu_map:
            criteria_weights[edu_map[level]] = 3.0
            
    # Employment Priorities (F-15)
    for i in range(st.session_state['ui_nb_adultes']):
        if st.session_state.get(f"ui_priority_job_adult_{i}", False):
            # Boost the match score for this adult
            criteria_weights[f'met_match_adult{i+1}_scaled'] = 3.0
            # Also boost the general employment availability? Maybe not, keep it specific.
            
    # Housing Priorities (F-15)
    # Housing Priorities (F-15)
    # 1. Hebergement Priority
    if st.session_state.get("ui_priority_hebergement", False):
        if st.session_state.get('ui_hebergement') == "Chez l'habitant":
             criteria_weights['log_occup_scaled'] = 3.0
        else:
             # Default: Location -> Vacancy rate (or maybe we should boost general vacancy?)
             # Let's stick to boosting vacancy as a proxy for availability
             criteria_weights['log_vac_scaled'] = 3.0

    # 2. Logement Priority
    if st.session_state.get("ui_priority_logement", False):
        if st.session_state.get('ui_logement') == 'Logement Social':
             criteria_weights['log_soc_inoc_scaled'] = 3.0
        else:
             # Default: Location -> Vacancy rate
             criteria_weights['log_vac_scaled'] = 3.0
            
    # Health Priority
    if st.session_state.get("ui_priority_sante", False):
        criteria_weights['sante_structures_scaled'] = 3.0
        
    # Other Needs Priority (F-15)
    if st.session_state.get("ui_priority_other_needs", False):
        # Maps to the new Extra Services score
        criteria_weights['inc_extra_services_score'] = 3.0

    return cfg.ScoringConfig(
        poids_emploi=st.session_state['ui_poids_emploi'],
        poids_logement=st.session_state['ui_poids_logement'],
        poids_education=st.session_state['ui_poids_education'],
        poids_inclusion=st.session_state['ui_poids_inclusion'],
        poids_sante=st.session_state['ui_poids_sante'], # NEW
        criteria_weights=criteria_weights, # F-15
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
        besoins_autres=besoins_autres_list,
        socle_admin_selection=st.session_state.get('ui_socle_admin_selection', []), # NEW
        affinite_selection=st.session_state.get('ui_affinite_selection', []), # NEW
        binome_penalty=st.session_state.get('ui_binome_penalty', 50) / 100,
        pop_min=st.session_state.get('ui_pop_min', 1000)
    )

def _result_highlight_callback(rank: int) -> None:
    """Callback to handle highlighting a result."""
    is_highlighted, highlighted_rank = st.session_state.highlighted_result
    
    # If the same button is clicked again, un-highlight it
    if is_highlighted and rank == highlighted_rank:
        st.session_state.highlighted_result = [False, None]
        st.session_state.zoom = None
    else:
        # Highlight the new result
        row = st.session_state.processed_gdf.loc[rank]
        st.session_state.highlighted_result = [True, rank]
        st.session_state.center = [row.polygon.centroid.y, row.polygon.centroid.x]
        st.session_state.zoom = cfg.DETAIL_MAP_ZOOM

def get_person_accompanied_str() -> str:
    if st.session_state.get('ui_nom'):
        return f"de {st.session_state.ui_nom}"
    return "de la personne accompagnée"

def display_results_list() -> None:
    """Displays the list of top N results."""
    st.subheader("Meilleurs résultats")
    st.text("Cliquez sur un résultat pour comprendre le détail du score")
    st.markdown('<style> [class*="st-key-button_top"] .stButton button div {text-align:left; width:100%;},</style>', unsafe_allow_html=True)

    top_n = 5
    df = st.session_state.processed_gdf
    is_highlighted, highlighted_rank = st.session_state.highlighted_result

    # Pre-build layers for top results to be shown on map
    for rank, row in df.head(top_n).iterrows():
        fg_key = f'Top{rank + 1}'
        st.session_state.fg_dict_ref[fg_key] = maps.build_top_result_layer(row, rank)

    # Display buttons and details
    for rank, row in df.head(top_n).iterrows():
        # Adapt title based on the view level
        if st.session_state.get('view_level') == 'Bassins de vie':
            title = f"Top {rank+1} | Bassin de vie de {row.libgeo}"
        else:
            title = f"Top {rank+1} | {row.libgeo}" + (f" (avec {row.libgeo_binome})" if row.binome else "")

        st.button(
            title,
            on_click=_result_highlight_callback,
            args=(rank,),
            width='stretch',
            key=f'button_top{rank+1}',
            type='primary'
        )

        if is_highlighted and rank == highlighted_rank:
            # For 'Bassin de vie' view, we call the specific details display for aggregated data
            if st.session_state.get('view_level') == 'Bassins de vie':
                _display_bv_result_details(row)
            else:
                _display_result_details(row)

def _display_bv_result_details(row: pd.Series) -> None:
    """Displays the detailed aggregated information for a 'bassin de vie'."""
    with st.container(border=True):
        # --- Pitch ---
        pitch = _produce_pitch_markdown(row, st.session_state.config, st.session_state.app_data['scores_cat'])
        st.markdown(pitch)

        # --- Radar Chart ---
        cat_scores = row[[col for col in row.index if col.endswith('_cat_score')]]
        cat_scores.rename(lambda x: x.split('_')[0].capitalize(), inplace=True)
        fig = line_polar(theta=cat_scores.index, r=cat_scores.values * 100, line_close=True, range_r=[0, 100])
        fig.update_traces(fill='toself', hovertemplate='%{theta}: %{r:.0f}%<extra></extra>')
        fig.update_layout(margin=dict(l=50, r=50, t=50, b=50))
        st.plotly_chart(fig, width='stretch', config=None)
        st.caption('Plus le critère s’approche du bord, plus il est attractif.')

        # --- Additional Info ---
        st.divider()
        st.markdown('**Plus d’informations sur ce bassin de vie :**')
        with st.expander('Top 10 des métiers recherchés'):
            bmo_vertical = st.session_state.app_data['bmo_vertical']
            codfap_index = st.session_state.app_data['codfap_index']
            
            # For BV, we need to aggregate across all communes in the BV
            # Or better, we can just look up by BV code if we had it in bmo_vertical.
            # But bmo_vertical is by codgeo.
            # Actually, the user wants "count will be based on the Bassin d'Emploi of the commune".
            # So all communes in the same BE have the same top metiers.
            # We can just pick one commune from the BV and get its top metiers.
            # Or we can aggregate. But since they are identical per BE, picking one is fine.
            
            # Get one commune code from the list
            if 'communes' in row and row['communes']:
                sample_codgeo = row['communes'][0]
                commune_metiers = bmo_vertical[bmo_vertical.codgeo == sample_codgeo]
                
                if not commune_metiers.empty:
                    # Join with labels
                    # codfap_index has 'Code' and 'Libellé' (or similar, need to check data_loader)
                    # data_loader says: codfap_index = pd.read_csv(..., dtype=str)
                    # Let's assume it has 'Code' and 'Libellé' as per build.py logic
                    
                    # Actually, let's look at how it was loaded in data_loader.
                    # It's just a raw CSV load.
                    # We need to ensure we have the right columns.
                    
                    # Merge
                    merged = commune_metiers.merge(codfap_index, left_on='fap_code', right_index=True, how='left')
                    merged['label'] = merged['label'].fillna(merged['fap_code'])
                    
                    top_metiers = sorted(merged['label'].unique())
                    st.markdown("\n".join([f'- {item}' for item in top_metiers]))
                else:
                    st.info("Pas de données disponibles.")
            else:
                st.info("Pas de données disponibles.")
        
        with st.expander('Formations proposées'):
            formations = set(row.get('noms_formations') if row.get('noms_formations') is not None else [])
            if formations:
                st.markdown("\n".join([f'- {item}' for item in sorted(list(formations))]))
            else:
                st.info("Pas de données disponibles.")
        
        with st.expander("Services d'inclusions proposés"):
            services_df = st.session_state.app_data['annuaire_inclusion']
            incl_index = st.session_state.app_data.get('inclusion_services_index', pd.DataFrame())
            
            # Determine Target Slugs for Filtering
            target_slugs = set(cfg.DEFAULT_SOCLE_ADMIN)
            
            # Add user selected specific needs
            if 'ui_besoins_autres' in st.session_state and st.session_state.ui_besoins_autres:
                 target_slugs.update(st.session_state.ui_besoins_autres)
            
            # Filter services for this BV
            # Ensure codgeo matching is robust (str vs category)
            # row.communes is a list of codgeos for the BV
            bv_services = services_df[
                (services_df['codgeo'].isin(row.communes)) & 
                (services_df['categorie'].isin(target_slugs))
            ]

            if not bv_services.empty:
                # The 'categorie' column in annuaire_inclusion comes from 'type' in pois.parquet
                # which is now the clean slug (e.g. 'mobilite--permis-de-conduire')
                # We want to display: "- Human Readable Label"
                
                # Get unique slugs found
                unique_slugs = sorted(bv_services['categorie'].unique())
                
                valid_labels = []
                for slug in unique_slugs:
                    # Lookup label
                    if not incl_index.empty and slug in incl_index.index:
                        try:
                            label = incl_index.loc[slug, 'label']
                            # If duplicate index, loc returns Series/DataFrame
                            if isinstance(label, (pd.Series, pd.DataFrame)):
                                label = label.iloc[0]
                        except:
                            label = slug
                    else:
                        label = slug # Fallback
                    
                    if label:
                         valid_labels.append(label)
                
                if valid_labels:
                     # Deduplicate labels just in case multiple slugs map to same label
                     valid_labels = sorted(list(set(valid_labels)))
                     st.markdown("\n".join([f'- {label}' for label in valid_labels]))
                else:
                    st.info("Aucun service d'inclusion correspondant aux critères trouvé dans ce bassin de vie.")

            else:
                st.info("Aucun service d'inclusion correspondant aux critères trouvé dans ce bassin de vie.")

        # --- Links ---
        st.markdown(f"[Page OD&IS]({row.get('url_odis', '#')}) | [Page Wikipedia]({row.get('url_wikipedia', '#')})")


def _display_result_details(row: pd.Series) -> None:
    """Displays the detailed information for a single highlighted result."""
    with st.container(border=True):
        # --- Pitch ---
        pitch = _produce_pitch_markdown(row, st.session_state.config, st.session_state.app_data['scores_cat'])
        st.markdown(pitch)

        # --- Radar Chart ---
        cat_scores = row[[col for col in row.index if col.endswith('_cat_score')]]
        cat_scores.rename(lambda x: x.split('_')[0].capitalize(), inplace=True)
        fig = line_polar(theta=cat_scores.index, r=cat_scores.values * 100, line_close=True, range_r=[0, 100])
        fig.update_traces(fill='toself', hovertemplate='%{theta}: %{r:.0f}%<extra></extra>')
        fig.update_layout(margin=dict(l=50, r=50, t=50, b=50))
        st.plotly_chart(fig, width='stretch', config=None)
        st.caption('Plus le critère s’approche du bord, plus il est attractif.')

        # --- Additional Info ---
        st.divider()
        st.markdown('**Plus d’informations sur cette localité :**')
        with st.expander('Top 10 des métiers recherchés'):
            bmo_vertical = st.session_state.app_data['bmo_vertical']
            codfap_index = st.session_state.app_data['codfap_index']
            
            commune_metiers = bmo_vertical[bmo_vertical.codgeo == row.name]
            
            if not commune_metiers.empty:
                merged = commune_metiers.merge(codfap_index, left_on='fap_code', right_index=True, how='left')
                merged['label'] = merged['label'].fillna(merged['fap_code'])
                
                top_metiers = sorted(merged['label'].unique())
                st.markdown("\n".join([f'- {item}' for item in top_metiers]))
            else:
                st.info("Pas de données disponibles.")
        
        with st.expander('Formations proposées'):
            formations = set(row.get('noms_formations') if row.get('noms_formations') is not None else [])
            if row.get('binome'):
                binome_row = st.session_state.app_data['odis'].loc[row.codgeo_binome]
                formations.update(binome_row.get('noms_formations') if binome_row.get('noms_formations') is not None else [])
            if formations:
                st.markdown("\n".join([f'- {item}' for item in sorted(list(formations))]))
            else:
                st.info("Pas de données disponibles.")
        
        with st.expander("Services d'inclusions proposés"):
            services_df = st.session_state.app_data['annuaire_inclusion']
            incl_index = st.session_state.app_data.get('inclusion_services_index', pd.DataFrame())
            
            # Determine Target Slugs for Filtering
            target_slugs = set(cfg.DEFAULT_SOCLE_ADMIN)
            
            # Add user selected specific needs
            if 'ui_besoins_autres' in st.session_state and st.session_state.ui_besoins_autres:
                 target_slugs.update(st.session_state.ui_besoins_autres)

            commune_services = services_df[
                (services_df['codgeo'] == row.name) &
                (services_df['categorie'].isin(target_slugs))
            ]
            
            if not commune_services.empty:
                # Get unique slugs found
                unique_slugs = sorted(commune_services['categorie'].unique())
                
                valid_labels = []
                for slug in unique_slugs:
                    # Lookup label
                    if not incl_index.empty and slug in incl_index.index:
                        try:
                            label = incl_index.loc[slug, 'label']
                            # If duplicate index
                            if isinstance(label, (pd.Series, pd.DataFrame)):
                                label = label.iloc[0]
                        except:
                            label = slug
                    else:
                        label = slug # Fallback
                    
                    if label:
                         valid_labels.append(label)

                if valid_labels:
                     # Deduplicate labels
                     valid_labels = sorted(list(set(valid_labels)))
                     st.markdown("\n".join([f'- {label}' for label in valid_labels]))
                else:
                    st.info("Aucun service d'inclusion correspondant aux critères trouvé dans cette commune.")
            else:
                st.info("Aucun service d'inclusion correspondant aux critères trouvé dans cette commune.")

        # --- Links ---
        st.markdown(f"[Page OD&IS]({row.get('url_odis', '#')}) | [Page Wikipedia]({row.get('url_wikipedia', '#')})")

def _produce_pitch_markdown(row: pd.Series, config: cfg.ScoringConfig, scores_cat: pd.DataFrame) -> str:
    """Generates a summary "pitch" for a result, adapting to commune or bassin de vie."""
    pitch_md = []
    population = f"{row['population']:,.0f}".replace(",", " ")

    # Adapt the intro based on whether it's a bassin de vie or a commune
    if 'communes' in row and isinstance(row['communes'], list):
        # It's a bassin de vie
        pitch_md.append(f'Le bassin de vie de **{row["libgeo"]}** ({population} habitants), composé de **{len(row["communes"])} communes**, présente un bon équilibre pour le projet.')
    else:
        # It's a commune
        pitch_md.append(f'**{row["libgeo"]}** ({population} habitants) fait partie du bassin de vie de : **{row["libelle_bassin_de_vie"]}**.  ')

    score_percent = f"{row['weighted_score'] * 100:.0f}%"
    if row.get("binome", False):
        pitch_md.append(f'\nEn [binôme](https://www.google.com "Lorsque des communes sont proposed en binômes, c’est qu’ensemble elles correspondent au projet de vie.") avec sa voisine **{row["libgeo_binome"]}**, la correspondance avec le projet est évaluée à **{score_percent}**. ')
    else:
        pitch_md.append(f'\nLa correspondance avec le projet est évaluée à **{score_percent}**. ')

    # --- Top contributing criteria (common logic) ---
    all_scores = scores_cat['score'].unique()
    crit_scores_cols = [col for col in row.keys() if col in all_scores]
    weighted_scores = {}
    for col in crit_scores_cols:
        cat = scores_cat[scores_cat.score == col]['cat'].iloc[0]
        cat_weight = getattr(config, f'poids_{cat}', 0)
        
        # F-15: Include criteria-level weights
        base_weight = scores_cat[scores_cat.score == col]['weight'].iloc[0]
        dynamic_multiplier = config.criteria_weights.get(col, 1.0)
        
        total_weight = cat_weight * base_weight * dynamic_multiplier
        
        penalty = config.binome_penalty if row.get("binome", False) and col + '_binome' in row.index else 0
        
        score_commune = row.get(col, 0)
        score_binome = row.get(col + '_binome', 0) * (1 - penalty)
        effective_score = max(score_commune or 0, score_binome or 0)
        
        weighted_scores[col] = effective_score * total_weight

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
