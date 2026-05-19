import streamlit as st
import pandas as pd
import logging
import config as cfg
from core.models import SearchCriterias, CriteriaItem
from utils.data_loader import get_app_data
from ui.components import inject_custom_css
from typing import List, Dict, Any, Optional, Set

# Configure Logging
logger = logging.getLogger("ui.forms")

def render_localisation_form() -> None:
    """Renders the UI for the 'Localisation Actuelle' form section."""
    app_data = get_app_data()
    dept_details = app_data.get('dept_details', {})
    options_dep = app_data['coddep_set']
    
    departement_actuel = st.selectbox(
        "Département", 
        options_dep, 
        key="ui_departement",
        format_func=lambda x: f"{x} - {dept_details.get(x, {}).get('label', x)}" if dept_details else x
    )
    
    communes = app_data['depcom_df'][app_data['depcom_df'].dep_code == departement_actuel]['libgeo'].tolist()
    if st.session_state.get('ui_commune') not in communes:
        st.session_state['ui_commune'] = communes[0]
    st.selectbox("Commune", communes, key="ui_commune")

    st.markdown("---")
    
    force_skip = st.session_state.get("ui_france_search", False)
    
    if force_skip:
        freq_options = ["Pas d'attache particulière"]
        if "ui_freq_retour" not in st.session_state or st.session_state["ui_freq_retour"] != "Pas d'attache particulière":
             st.session_state["ui_freq_retour"] = "Pas d'attache particulière"
        freq_disabled = True
    else:
        freq_options = [
             "1 fois/semaine",
             "1 fois/mois",
             "1 fois/an",
             "Pas d'attache particulière"
        ]
        freq_disabled = False

    st.selectbox(
        "A quelle fréquence pense-t-il/elle revenir dans son lieu de vie actuel ?", 
        options=freq_options,
        key="ui_freq_retour",
        disabled=freq_disabled,
        help="Détermine l'importance de la proximité et des connexions selon le lieu actuel."
    )

def render_family_form() -> None:
    """Renders the UI for the 'Situation familiale' form section."""
    col1, col2 = st.columns(2)
    with col1:
        st.radio("Nombre d'adultes", cfg.NOMBRE_ADULTES_OPTIONS, horizontal=True, key="ui_nb_adultes")
    with col2:
        st.radio("Nombre d'enfants", cfg.NOMBRE_ENFANTS_OPTIONS, horizontal=True, key="ui_nb_enfants")

def render_education_form() -> None:
    """Renders the UI for the 'Education' form section."""
    nb_enfants = st.session_state.get('ui_nb_enfants', 0)
    if nb_enfants == 0:
        st.info("Aucun enfant n'a été ajouté dans l'onglet 'Situation familiale'.")
    else:
        col1, col2 = st.columns(2)
        for i in range(nb_enfants):
            col = col1 if i % 2 == 0 else col2
            with col:
                options = cfg.CLASSES_SCOLAIRES
                key = f"ui_classe_enfant_{i}"
                st.selectbox(f'Niveau enfant {i+1}', options, key=key)

def render_employment_form() -> None:
    """Renders the UI for the 'Emploi & Formation' form section."""
    inject_custom_css()
    app_data = get_app_data()
    col1, col2 = st.columns(2)
    rome_full_index = app_data['rome_index']
    rome_top_index = app_data.get('rome_top_index', rome_full_index) # Fallback to full if missing
    codform_select = app_data['codformations_index']
    
    for i in range(st.session_state.get('ui_nb_adultes', 1)):
        with col1:
            current_selection = st.session_state.get(f"ui_metiers_adult_{i}", [])
            
            available_options = list(rome_top_index.index)
            for code in current_selection:
                if code not in available_options and code in rome_full_index.index:
                    available_options.append(code)
            
            def format_rome_label(code):
                if code in rome_full_index.index:
                    row = rome_full_index.loc[code]
                    label = row['label']
                    count = row.get('total_postes', 0)
                    count_str = f"{int(count):,}".replace(",", " ")
                    return f"{label} [{count_str} postes]"
                return str(code)

            st.multiselect(
                f"Métiers ciblés Adulte {i+1}", 
                available_options, 
                format_func=format_rome_label, 
                key=f"ui_metiers_adult_{i}", 
                help="Recherchez par nom de métier (Référentiel ROME). La liste affiche les métiers les plus demandés en nombre de postes."
            )
        with col2:
            st.multiselect(f"Formations recherchées Adulte {i+1}", codform_select.index, format_func=lambda x: codform_select.loc[x, 'label'], key=f"ui_formations_adult_{i}")

def render_housing_form() -> None:
    """Renders the UI for the 'Logement' form section."""
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Hébergement cible à court terme")
        current_heb = st.session_state.get('ui_hebergement_cible', [])
        selected_heb = []
        for opt in cfg.HEBERGEMENT_OPTIONS:
            cb_key = f"ui_heb_cb_{opt.replace(' ', '_').lower()}"
            if cb_key not in st.session_state:
                st.session_state[cb_key] = opt in current_heb
            
            if st.checkbox(opt, key=cb_key):
                selected_heb.append(opt)
        
        st.session_state['ui_hebergement_cible'] = selected_heb
        
    with col2:
        st.subheader("Logement cible à long terme")
        st.radio('Logement', cfg.LOGEMENT_OPTIONS, key="ui_logement", label_visibility="hidden")
        
    heb_sel = st.session_state.get('ui_hebergement_cible', [])
    if "Location avec Intermédiation" in heb_sel or st.session_state.get('ui_logement') == 'Location':
        
        housing_type_options = list(cfg.HOUSING_TYPE_OPTIONS.keys())
        if "ui_type_logement" not in st.session_state or st.session_state["ui_type_logement"] not in housing_type_options:
            st.session_state["ui_type_logement"] = "appt_all"

        st.space("small")
        st.selectbox(
            "Si location quel type de logement ?",
            options=housing_type_options,
            format_func=lambda x: cfg.HOUSING_TYPE_OPTIONS[x],
            width=300,
            key="ui_type_logement",
            help="Permet d'utiliser les loyers spécifiques au type de logement choisi (Source 2024)"
        )


def render_health_form() -> None:
    """Renders the UI for the 'Santé' form section."""
    options = ["Aucun", "Hopital", 'Maternité', "Soutien Psychologique & Addictologie"]
    # F-48: Consistent key with config.py
    st.radio('Support médical à proximité', options, key="ui_besoin_sante")


def render_other_needs_form() -> None:
    """Renders the UI for the 'Autres Besoins' (Inclusion) section."""
    inject_custom_css()
    app_data = get_app_data()
    
    col1, col2 = st.columns(2)
    with col2:
        st.subheader("Associations Locales (Solidarité, Loisirs, Culture)")
        st.text("Sélectionnez vos centres d'intérêt pour identifier les territoires avec un tissu associatif correspondant.")
        
        if 'waldec_index' in app_data:
            waldec_index = app_data['waldec_index']
            prefixes = cfg.WALDEC_CATEGORIES
            mask = waldec_index.index.str[:3].isin(prefixes)
            loisirs_df = waldec_index[mask].copy()
            
            options_items = []
            item_map = {}
            for code, row in loisirs_df.iterrows():
                item = CriteriaItem(code=str(code), label=row['label'])
                options_items.append(item)
                count_str = f"{int(row['count']):,}".replace(",", " ")
                item_map[item.code] = f"{item.label.title()} [{count_str} assos]"

            if 'ui_inc_asso_add_selection' not in st.session_state:
                st.session_state.ui_inc_asso_add_selection = st.session_state.get('demo_data', {}).get('inc_asso_add_selection', [])
                
            selected_codes = st.multiselect(
                "Centres d'intérêt",
                options=[item.code for item in options_items],
                format_func=lambda x: item_map.get(x, x),
                key="ui_inc_asso_add_selection_raw", 
                label_visibility="collapsed"
            )
            
            st.session_state.ui_inc_asso_add_selection = [
                next(item for item in options_items if item.code == code)
                for code in selected_codes
            ]
        else:
            st.warning("Référentiel WALDEC non chargé.")
        

    with col1:
        st.subheader("Services d'Inclusion")
        st.text("Sélectionnez des services pertinents pour faciliter leur installation une fois sur place.")
        st.text("Services courants:")

        if 'ui_inc_services_add_selection' not in st.session_state:
            st.session_state.ui_inc_services_add_selection = st.session_state.get('demo_data', {}).get('inc_services_add_selection', cfg.DEFAULT_INC_SERVICES_CORE)

        current_selection = set(st.session_state.ui_inc_services_add_selection)
        checkbox_selection = set()

        for slug, label in cfg.INC_SERVICES_CHECKBOX_MAPPING.items():
            cb_key = f"ui_cb_inc_{slug.replace('-', '_')}"
            
            if cb_key not in st.session_state:
                st.session_state[cb_key] = slug in current_selection
                if slug in cfg.DEFAULT_INC_SERVICES_CORE:
                    st.session_state[cb_key] = True
                else:
                    st.session_state[cb_key] = False
            
            if st.checkbox(label, key=cb_key):
                checkbox_selection.add(slug)

        st.markdown("\n")
        st.text("Services plus spécifiques:")
        
        inclusion_index = app_data.get('inclusion_services_index', pd.DataFrame())
        checkbox_slugs = set(cfg.INC_SERVICES_CHECKBOX_MAPPING.keys())
        
        options_map = {} 
        if not inclusion_index.empty:
            for code, row in inclusion_index.iterrows():
                if code not in checkbox_slugs:
                    label = row['label']
                    options_map[label] = code
        
        options_list = sorted(list(options_map.keys()))
        
        if 'ui_inc_services_multi_only' not in st.session_state:
            initial_multi = []
            if not inclusion_index.empty:
                for s in current_selection:
                    if s in inclusion_index.index and s not in checkbox_slugs:
                        initial_multi.append(inclusion_index.loc[s, 'label'])
            st.session_state.ui_inc_services_multi_only = initial_multi

        selected_labels = st.multiselect(
            "Autres services d'inclusion",
            options=options_list,
            key="ui_inc_services_multi_only",
            help="Recherchez et ajoutez des services spécifiques.",
            label_visibility="collapsed"
        )
        
        final_selection = list(checkbox_selection)
        for label in selected_labels:
            if label in options_map:
                final_selection.append(options_map[label])
        
        st.session_state.ui_inc_services_add_selection = sorted(list(set(final_selection)))
        st.session_state['ui_inc_services_add_selection_map'] = options_map

def render_other_notes_form() -> None:
    """Renders the UI for entering free-text qualitative notes (F-48 update)."""
    if 'ui_notes_qualitatives' not in st.session_state:
        st.session_state.ui_notes_qualitatives = st.session_state.get('demo_data', {}).get('notes_qualitatives', "")

    st.text("Précisez ici tout élément supplémentaire potentiellement utile pour la recherche (origine culturelle, contexte familial, passions, contraintes spécifiques, etc.).")
    
    st.text_area(
        "Notes qualitatives",
        key="ui_notes_qualitatives",
        height=250, 
        placeholder="Exemple : Famille sud-américaine parlant espagnol, souhaite une zone rurale avec accès à la nature...",
        label_visibility="collapsed"
    )

def render_mobility_form() -> None:
    """Renders the UI for the 'Mobilité' form section (Consolidated)."""
    app_data = get_app_data()
    dept_details = app_data.get('dept_details', {})
    regions_dict = app_data.get('regions_names', {})

    current_dept_code = st.session_state.get('ui_departement')
    current_reg_code = dept_details.get(current_dept_code, {}).get('reg_code')
    
    region_codes = sorted(regions_dict.keys())
    
    if "ui_france_search" not in st.session_state:
        st.session_state["ui_france_search"] = False
    if "ui_region_search" not in st.session_state:
        st.session_state["ui_region_search"] = False
    if "ui_mobility_region" not in st.session_state or st.session_state["ui_mobility_region"] not in region_codes:
        st.session_state["ui_mobility_region"] = current_reg_code if current_reg_code in region_codes else region_codes[0]

    col_reg_1, col_reg_2 = st.columns([3, 1])
    with col_reg_1:
        selected_region_code = st.selectbox(
            "Région",
            region_codes,
            format_func=lambda x: regions_dict.get(x, x),
            key="ui_mobility_region",
            disabled=st.session_state.ui_france_search
        )
    with col_reg_2:
        st.space(20)
        st.checkbox("France Métro.", key="ui_france_search", help="Rechercher sur l'ensemble du territoire.")

    depts_in_region = [
        code for code, details in dept_details.items() 
        if details.get('reg_code') == selected_region_code
    ]
    depts_in_region.sort()
    
    if "ui_mobility_dept" not in st.session_state:
         st.session_state["ui_mobility_dept"] = [current_dept_code] if current_dept_code in depts_in_region else []
    elif isinstance(st.session_state["ui_mobility_dept"], str):
         old_val = st.session_state["ui_mobility_dept"]
         st.session_state["ui_mobility_dept"] = [old_val] if old_val in depts_in_region else []

    col_dept_1, col_dept_2 = st.columns([3, 1])
    with col_dept_2:
        st.space(20)
        st.checkbox(
            "Toute la région", 
            key="ui_region_search", 
            disabled=st.session_state.ui_france_search,
            help="Rechercher dans tous les départements de cette région."
        )
        
    with col_dept_1:
        st.multiselect(
            "Départements",
            depts_in_region,
            format_func=lambda x: f"{x} - {dept_details.get(x, {}).get('label', x)}",
            key="ui_mobility_dept",
            disabled=st.session_state.ui_france_search or st.session_state.ui_region_search,
            placeholder="Sélectionnez un ou plusieurs départements"
        )

    st.divider()
    
    target_options = list(cfg.CITY_SIZE_MAPPING.keys())
    if "ui_target_city_size_label" not in st.session_state:
        default_label = next((l for l in target_options if "Petite Ville" in l), target_options[2])
        st.session_state["ui_target_city_size_label"] = default_label

    with st.container(horizontal=True, width='stretch', horizontal_alignment='center'):
        st.radio(
            "Taille de la ville recherchée",
            options=target_options,
            key="ui_target_city_size_label",
            horizontal=True,
            help="Définit la taille idéale de la commune recherchée. Le score de population sera maximal pour cette catégorie.",
            label_visibility="visible"
        )
    
    selected_label = st.session_state["ui_target_city_size_label"]
    mapping = cfg.CITY_SIZE_MAPPING.get(selected_label, {"mu": cfg.DEFAULT_MU, "sigma": cfg.DEFAULT_SIGMA})
    st.session_state["ui_target_population"] = mapping["mu"]
    st.session_state["ui_target_population_sigma"] = mapping["sigma"]

    
def render_org_profile_form() -> None:
    """Renders the organization-specific preamble component (F-54)."""
    org_id = st.session_state.get('ui_org_context')
    if not org_id:
        st.info("Aucun profil d'organisation actif.")
        return
        
    profile = cfg.ORGANIZATION_PROFILES.get(org_id)
    if not profile:
        st.error(f"Profil '{org_id}' non trouvé.")
        return

    st.subheader(f'Vous trouverez ci dessous les paramètres spécifiques pour {profile["name"]} :')
    
    # st.divider()
    
    # --- Strategic Locations Multi-select ---
    app_data = get_app_data()
    zone_type = profile['zone_type']
    
    if zone_type == 'departement':
        label = "Départements"
        options = app_data['coddep_set']
        dept_details = app_data.get('dept_details', {})
        def format_func(x):
            return f"{x} - {dept_details.get(x, {}).get('label', x)}"
    else:
        # Bassin de vie
        label = "Bassins de vie"
        odis = app_data['odis']
        options = sorted(odis['bassin_de_vie'].unique().tolist())
        bv_names = odis.groupby('bassin_de_vie')['libelle_bassin_de_vie'].first().to_dict()
        def format_func(x):
            return f"{x} - {bv_names.get(x, x)}"

    
    # Pre-fill with current session state or profile defaults
    current_selection = st.session_state.get('ui_org_strategic_locations', profile['default_zones'])
    
    # Ensure all current selections are in options (safety)
    valid_selection = [x for x in current_selection if x in options]
    st.write(f"**Zones d'intérêt stratégique ({label})**")

    selected_zones = st.multiselect(
        f"**Zones d'intérêt stratégique ({label})**",
        options=options,
        default=valid_selection,
        format_func=format_func,
        label_visibility="collapsed",
        key="ui_org_strategic_locations_multiselect",
        help="Les communes situées dans ces zones recevront un bonus dans le score final."
    )
    
    # Update the actual session state used by scoring
    st.session_state['ui_org_strategic_locations'] = selected_zones
    
    # --- Criteria Boosts Sliders (F-54 Expansion) ---
    org_defaults = profile.get('defaults', {})
    if 'org_boosts' in org_defaults:
        st.divider()
        st.write("**Boosts de critères spécifiques**")
        st.text("Ajustez l'importance de certains critères clés pour votre organisation (multiplicateur x1 à x5).")
        
        boost_config = org_defaults['org_boosts']
        new_boosts = {}
        
        for criterion_id, default_val in boost_config.items():
            # Get label from config
            label = criterion_id
            score_row = app_data['scores_cat'][app_data['scores_cat']['score'] == criterion_id]
            if not score_row.empty:
                label = score_row.iloc[0]['label']
            
            # Key for session state
            ui_key = f"ui_org_boost_{criterion_id}"
            
            # Ensure session state is initialized if not present
            if ui_key not in st.session_state:
                st.session_state[ui_key] = float(default_val)
            with st.container(horizontal=True):
                # col1, col2 = st.columns([1, 2])
                # with col1:
                st.space(size="medium")
                st.markdown(f"##### {label}")
                # with col2:
                val = st.slider(
                    f"Boost pour : {label}",
                    min_value=1,
                    max_value=5,
                    value=int(st.session_state[ui_key]),
                    step=1,
                    format="x%d",
                    key=f"ui_org_boost_slider_{criterion_id}",
                    width=100,
                    label_visibility="collapsed",
                    on_change=lambda k=ui_key, sk=f"ui_org_boost_slider_{criterion_id}": st.session_state.update({k: float(st.session_state[sk])})
                )
            new_boosts[criterion_id] = float(val)
        
        st.session_state['ui_org_boosts'] = new_boosts
    
    # st.markdown("---")
    # st.caption("Vous pouvez modifier ces paramètres manuellement dans les autres onglets si nécessaire.")

def render_weight_profile_form() -> None:
    """Renders the UI for selecting the weighting profile and expert weights adjustment."""
    def _update_weights_from_profile():
        profile = st.session_state.ui_weight_profile
        if profile == "Profil personnalisé":
            st.session_state.ui_expert_weights = True
        elif profile in cfg.WEIGHT_PROFILES:
            st.session_state.ui_expert_weights = False
            weights = cfg.WEIGHT_PROFILES[profile]
            for key, value in weights.items():
                st.session_state[f"ui_{key}"] = value
        
        st.session_state['processed_gdf'] = None
        st.session_state['search_results'] = None

    weight_profiles = list(cfg.WEIGHT_PROFILES.keys()) + ["Profil personnalisé"]
    if "ui_weight_profile" not in st.session_state or st.session_state["ui_weight_profile"] not in weight_profiles:
        st.session_state["ui_weight_profile"] = weight_profiles[0]

    if "ui_expert_weights" not in st.session_state:
        st.session_state.ui_expert_weights = (st.session_state["ui_weight_profile"] == "Profil personnalisé")

    st.text('Pour améliorer la pertinence des résultats de la recherche, vous pouvez ajuster les poids des différentes catégories de critères de recherche en utilisant soit un profil de pondération (recommandé) soit une pondération sur-mesure.')

    col1, col2 = st.columns(2)
    with col1:
        st.selectbox(
            "Profil de pondération",
            options=weight_profiles,
            key="ui_weight_profile",
            on_change=_update_weights_from_profile
        )
    
    def _invalidate_results():
        st.session_state['processed_gdf'] = None
        st.session_state['search_results'] = None

    with col2:
        weight_keys = ["ui_poids_education", "ui_poids_emploi", "ui_poids_logement", "ui_poids_inclusion", "ui_poids_sante", "ui_poids_mobilite"]
        
        # Add Territory weight if org context is present
        org_context = st.session_state.get('ui_org_context')
        if org_context:
            weight_keys.append("ui_poids_territoire")

        for p_key in weight_keys:
            if p_key not in st.session_state:
                st.session_state[p_key] = 0.5

        format_pct = lambda x: f"{int(x*100)}%"
        
        sliders_disabled = not st.session_state.get('ui_expert_weights', False)

        st.select_slider("Education", cfg.POIDS_OPTIONS, 
                        format_func=format_pct,
                        disabled=sliders_disabled,
                        key="ui_poids_education", on_change=_invalidate_results)
        st.select_slider("Projet Pro", cfg.POIDS_OPTIONS, 
                        format_func=format_pct,
                        disabled=sliders_disabled,
                        key="ui_poids_emploi", on_change=_invalidate_results)
        st.select_slider("Logement", cfg.POIDS_OPTIONS, 
                        format_func=format_pct,
                        disabled=sliders_disabled,
                        key="ui_poids_logement", on_change=_invalidate_results)
        st.select_slider("Inclusion", cfg.POIDS_OPTIONS, 
                        format_func=format_pct,
                        disabled=sliders_disabled,
                        key="ui_poids_inclusion", on_change=_invalidate_results)
        st.select_slider("Santé", cfg.POIDS_OPTIONS, 
                        format_func=format_pct,
                        disabled=sliders_disabled,
                        key="ui_poids_sante", on_change=_invalidate_results)
        st.select_slider("Mobilité", cfg.POIDS_OPTIONS, 
                        format_func=format_pct,
                        disabled=sliders_disabled,
                        key="ui_poids_mobilite", on_change=_invalidate_results)
        
        if org_context:
            st.select_slider("Territoire", cfg.POIDS_OPTIONS,
                            format_func=format_pct,
                            disabled=sliders_disabled,
                            key="ui_poids_territoire", on_change=_invalidate_results)

def display_input_tabs() -> None:
    """Displays the main tabs for user input, composed of modular rendering functions."""
    inject_custom_css()
    
    tab_names = ['Localisation', 'Situation familiale', 'Education', 'Projet Professionnel', 'Logement', 'Santé', 'Inclusion', 'Autres', 'Profil']
    
    org_id = st.session_state.get('ui_org_context')
    if org_id:
        profile = cfg.ORGANIZATION_PROFILES.get(org_id)
        if profile:
            tab_names.insert(0, profile['name'])
    
    tabs = st.tabs(tab_names)
    
    current_tab_idx = 0
    if org_id:
        with tabs[current_tab_idx]:
            render_org_profile_form()
        current_tab_idx += 1
        
    with tabs[current_tab_idx]:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.markdown("**Localisation actuelle**")
            render_localisation_form()
        with col2:
            st.markdown("**Zone de recherche**")
            render_mobility_form()
    current_tab_idx += 1
    
    with tabs[current_tab_idx]:
        render_family_form()
    current_tab_idx += 1
    
    with tabs[current_tab_idx]:
        render_education_form()
    current_tab_idx += 1
    
    with tabs[current_tab_idx]:
        render_employment_form()
    current_tab_idx += 1
    
    with tabs[current_tab_idx]:
        render_housing_form()
    current_tab_idx += 1
    
    with tabs[current_tab_idx]:
        render_health_form()
    current_tab_idx += 1
    
    with tabs[current_tab_idx]:
        render_other_needs_form()
    current_tab_idx += 1
    
    with tabs[current_tab_idx]:
        render_other_notes_form()
    current_tab_idx += 1
    
    with tabs[current_tab_idx]:
        render_weight_profile_form()

def create_search_criterias_from_inputs() -> SearchCriterias:
    """Gathers all user inputs from session_state and creates a SearchCriterias object."""
    app_data = get_app_data()
    
    # Location
    dept_code = st.session_state.get('ui_departement', cfg.DEMO_DATA_DEFAULT['departement_actuel'])
    commune_lib = st.session_state.get('ui_commune', cfg.DEMO_DATA_DEFAULT['commune_actuelle'])
    
    commune_codgeo = app_data['depcom_df'][
        (app_data['depcom_df'].dep_code == dept_code) & 
        (app_data['depcom_df'].libgeo == commune_lib)
    ].index[0]
    
    commune_actuelle = CriteriaItem(code=str(commune_codgeo), label=str(commune_lib))

    # New Mobility Logic (F-53)
    if st.session_state.get('ui_france_search'):
        loc_search_area = 'france'
        loc_search_code = []
    elif st.session_state.get('ui_region_search'):
        loc_search_area = 'region'
        loc_search_code = [st.session_state.get('ui_mobility_region')]
    else:
        loc_search_area = 'departement'
        selected_depts = st.session_state.get('ui_mobility_dept', [])
        loc_search_code = selected_depts if isinstance(selected_depts, list) else [selected_depts]

    # Education
    nb_enfants = st.session_state.get('ui_nb_enfants', 0)
    classe_enfants = [st.session_state.get(f"ui_classe_enfant_{i}") for i in range(nb_enfants)]

    # Employment (Enrich with CriteriaItem)
    nb_adultes = st.session_state.get('ui_nb_adultes', 1)
    rome_index = app_data.get('rome_index', pd.DataFrame())
    codes_metiers = []
    for i in range(nb_adultes):
        raw_codes = st.session_state.get(f"ui_metiers_adult_{i}", [])
        if not isinstance(raw_codes, list): raw_codes = [raw_codes] if raw_codes else []
        enriched_list = []
        for code in raw_codes:
            label = rome_index.loc[code, 'label'] if not rome_index.empty and code in rome_index.index else str(code)
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_metiers.append(enriched_list)
        
    form_index = app_data.get('codformations_index', pd.DataFrame())
    codes_formations = []
    for i in range(nb_adultes):
        raw_codes = st.session_state.get(f"ui_formations_adult_{i}", [])
        if not isinstance(raw_codes, list): raw_codes = [raw_codes] if raw_codes else []
        enriched_list = []
        for code in raw_codes:
            label = form_index.loc[code, 'label'] if not form_index.empty and code in form_index.index else str(code)
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_formations.append(enriched_list)

    # F-48 Logic: Consolidate Inclusion Services (Checkbox + Multiselect)
    inc_index = app_data.get('inclusion_services_index', pd.DataFrame())
    
    # Get lists from both UI fields (consolidated in UI but might be split in mocked session state)
    inc_services_add = st.session_state.get('ui_inc_services_add_selection', [])
    inc_services_core = st.session_state.get('ui_inc_services_core_selection', [])
    
    # Handle both list and set types gracefully
    all_inc_services: Set[str] = set()
    if isinstance(inc_services_add, (list, set)): all_inc_services.update(inc_services_add)
    elif isinstance(inc_services_add, dict): all_inc_services.update(inc_services_add.keys()) # Robustness for test mocks
    
    if isinstance(inc_services_core, (list, set)): all_inc_services.update(inc_services_core)
    elif isinstance(inc_services_core, dict): all_inc_services.update(inc_services_core.keys())

    inc_services_mapped = []
    for code in sorted(list(all_inc_services)):
        # Recover label
        if not inc_index.empty and code in inc_index.index:
            val = inc_index.loc[code, 'label']
            label = str(val.iloc[0] if isinstance(val, pd.Series) else val)
        else:
            label = str(code)
        inc_services_mapped.append(CriteriaItem(code=str(code), label=label))

    # F-15: Compute Criteria Weights
    criteria_weights = {}
    
    # Education Priorities
    edu_map = {
        'Crèche / Assistante Maternelle': 'edu_petite_enfance_scaled',
        'Maternelle': 'edu_maternelle_scaled',
        'Elémentaire': 'edu_elementaire_scaled',
        'Collège': 'edu_college_scaled',
        'Lycée': 'edu_lycee_scaled'
    }
    for i in range(nb_enfants):
        level = st.session_state.get(f"ui_classe_enfant_{i}")
        is_priority = st.session_state.get(f"ui_priority_edu_{i}", False)
        if is_priority and level in edu_map:
            criteria_weights[edu_map[level]] = 3.0
            
    # Employment Priorities (F-15)
    for i in range(nb_adultes):
        if st.session_state.get(f"ui_priority_job_adult_{i}", False):
            criteria_weights[f'met_match_adult{i+1}_scaled'] = 3.0
            
    # Housing Priorities (F-15)
    heb_sel = st.session_state.get('ui_hebergement_cible', [])
    if st.session_state.get("ui_priority_hebergement", False):
        if "Location avec Intermédiation" in heb_sel:
             criteria_weights['heb_loc_iml_scaled'] = 3.0
             criteria_weights['log_vac_scaled'] = 3.0
        if "Centres d'Hébergement (CHRS, CPH)" in heb_sel:
             criteria_weights['heb_centres_heb_scaled'] = 3.0
        if "Foyers & Pensions de Famille" in heb_sel:
             criteria_weights['heb_foyers_scaled'] = 3.0
        if "Chez l'habitant" in heb_sel:
             criteria_weights['heb_asso_habitant_scaled'] = 3.0
             criteria_weights['heb_jaccueille_score'] = 3.0
             criteria_weights['log_occup_scaled'] = 3.0

    if st.session_state.get("ui_priority_logement", False):
        if st.session_state.get('ui_logement') == 'Logement Social':
             criteria_weights['log_soc_inoc_scaled'] = 3.0
        else:
             criteria_weights['log_vac_scaled'] = 3.0
            
    # Health Priority
    if st.session_state.get("ui_priority_sante", False):
        criteria_weights['sante_structures_scaled'] = 3.0
        
    # Other Needs Priority (F-15)
    if st.session_state.get("ui_priority_other_needs", False):
        criteria_weights['inc_services_incl_scaled'] = 3.0



    # Enrich Inclusion Associations (WALDEC Logic F-48)
    waldec_index = app_data.get('waldec_index', pd.DataFrame())
    inc_assos_mapped = []
    for item in st.session_state.get('ui_inc_asso_add_selection', []):
        if isinstance(item, CriteriaItem):
            inc_assos_mapped.append(item)
        elif isinstance(item, str):
            val = waldec_index.loc[item, 'label'] if not waldec_index.empty and item in waldec_index.index else item
            label = str(val.iloc[0] if isinstance(val, pd.Series) else val)
            inc_assos_mapped.append(CriteriaItem(code=str(item), label=label))

    # Type Logement Enrich
    type_log = None
    ui_type_log = st.session_state.get('ui_type_logement', 'appt_all')
    if ui_type_log in cfg.HOUSING_TYPE_OPTIONS:
        type_log = CriteriaItem(code=ui_type_log, label=cfg.HOUSING_TYPE_OPTIONS[ui_type_log])

    # Weights & Profile
    profile = st.session_state.get('ui_weight_profile', 'Équilibré')
    
    # Population mapping (Respect session state if present, fallback to label mapping)
    selected_city_label = st.session_state.get("ui_target_city_size_label")
    mapping = cfg.CITY_SIZE_MAPPING.get(selected_city_label, {"mu": cfg.DEFAULT_MU, "sigma": cfg.DEFAULT_SIGMA})
    target_pop = st.session_state.get("ui_target_population", mapping["mu"])
    target_sigma = st.session_state.get("ui_target_population_sigma", mapping["sigma"])
    
    # Mobility weights based on freq_retour
    freq = st.session_state.get('ui_freq_retour', "1 fois/mois")
    if freq == "1 fois/semaine":
        criteria_weights['mob_epci_scaled'] = 2.0
        criteria_weights['mob_dist_current_loc_scaled'] = 2.0
    elif freq == "1 fois/mois":
        criteria_weights['mob_epci_scaled'] = 1.0
        criteria_weights['mob_dist_current_loc_scaled'] = 1.0
    elif freq == "1 fois/an":
        criteria_weights['mob_epci_scaled'] = 0.5
        criteria_weights['mob_dist_current_loc_scaled'] = 0.5
    
    return SearchCriterias(
        weight_profile=profile,
        poids_emploi=st.session_state.get('ui_poids_emploi', 0.5),
        poids_logement=st.session_state.get('ui_poids_logement', 0.5),
        poids_education=st.session_state.get('ui_poids_education', 0.5),
        poids_inclusion=st.session_state.get('ui_poids_inclusion', 0.5),
        poids_sante=st.session_state.get('ui_poids_sante', 0.5),
        poids_mobilite=st.session_state.get('ui_poids_mobilite', 0.5),
        criteria_weights=criteria_weights,
        
        target_population=target_pop,
        target_population_sigma=target_sigma,
        
        commune_actuelle=commune_actuelle,
        loc_search_area=loc_search_area,
        loc_search_code=loc_search_code,
        nb_adultes=nb_adultes,
        nb_enfants=nb_enfants,
        hebergement_cible=heb_sel,
        logement=st.session_state.get('ui_logement', 'Location'),
        type_logement=type_log,
        
        freq_retour=freq,
        
        codes_metiers=codes_metiers,
        codes_formations=codes_formations,
        classe_enfants=classe_enfants,
        besoin_sante=st.session_state.get('ui_besoin_sante', 'Aucun'),
        
        # F-48: Consolidated inclusion services field
        inc_services_core_selection=[],
        inc_services_add_selection=inc_services_mapped,
        inc_asso_add_selection=inc_assos_mapped,
        notes_qualitatives=[st.session_state.get('ui_notes_qualitatives', "")] if st.session_state.get('ui_notes_qualitatives') else [],
        
        # Org Specifics
        org_context=st.session_state.get('ui_org_context'),
        org_strategic_locations=st.session_state.get('ui_org_strategic_locations', []),
        org_strategic_locations_type=st.session_state.get('ui_org_strategic_locations_type', 'departement'),
        org_boosts=st.session_state.get('ui_org_boosts', {}),
        poids_territoire=st.session_state.get('ui_poids_territoire', 0.5)
    )
