import streamlit as st
import pandas as pd
from plotly.express import line_polar
import geopandas as gpd
import config as cfg
import maps
from typing import Dict, Any, List, Optional
from pathlib import Path
import base64
import logging
from scoring import ScoringEngine

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

@st.dialog("Centre Communal d'Action Sociale", width="large")
def show_ccas_dialog(codgeo_or_list: Any, structures_df: pd.DataFrame, priority_code: str = None, priority_label: str = None):
     target_codes = []
     if isinstance(codgeo_or_list, list):
         target_codes = [str(c).strip() for c in codgeo_or_list]
     else:
         target_codes = [str(codgeo_or_list).strip()]

     if not structures_df.empty and 'codgeo' in structures_df.columns:
         # Filter with clean string types
         subset = structures_df[structures_df['codgeo'].isin(target_codes)].copy()
         
         if not subset.empty:
             # Check if priority code is missing (and we have results for others)
             if priority_code:
                 p_code_clean = str(priority_code).strip()
                 if p_code_clean not in subset['codgeo'].values:
                     label = priority_label if priority_label else "la zone sélectionnée"
                     st.markdown(f"⚠️ **{label}** ne dispose pas de structure référencée.")
                     st.caption("Affichage des structures disponibles pour les autres communes de la zone :")
                     st.divider()

             # Sorting: Priority code first, then alphabetical by commune/nom
             if priority_code:
                 p_code_clean = str(priority_code).strip()
                 subset['is_main'] = subset['codgeo'] == p_code_clean
                 subset = subset.sort_values(by=['is_main', 'commune', 'nom'], ascending=[False, True, True])
             
             st.write(f"**Structures trouvées : {len(subset)}**")
             
             for _, struct in subset.iterrows():
                 # Layout: Commune First
                 label = struct['commune'] if pd.notna(struct.get('commune')) else "Commune Inconnue"
                 st.subheader(f"📍 {label}")
                 
                 # Name
                 st.write(f"**{struct['nom']}**")
                 
                 # Address
                 if pd.notna(struct.get('adresse')):
                     st.write(f"{struct['adresse']}")
                 
                 # Contact Info
                 c1, c2 = st.columns(2)
                 with c1:
                     if pd.notna(struct.get('telephone')):
                         st.write(f"📞 {struct['telephone']}")
                 with c2:
                     if pd.notna(struct.get('courriel')):
                         # Simple email link
                         st.markdown(f"✉️ [{struct['courriel']}](mailto:{struct['courriel']})")
                 
                 if pd.notna(struct.get('site_web')):
                     st.markdown(f"🌐 [Site Web]({struct['site_web']})")
                     
                 # Separator AFTER
                 st.markdown("---")
         else:
             st.info("Aucune structure CCAS/CIAS référencée (avec contact) pour cette zone.")
     else:
         st.warning("Données structures non disponibles.")

@st.dialog("Détails du Territoire", width="large")
def show_details_dialog(details: Dict[str, Any]):
    """Displays thematic details for a city in a large modal."""
    if not details:
        st.error("Détails non disponibles.")
        return

    # --- Header ---
    identity = details.get('identity', {})
    st.title(f"📍 {identity.get('nom', 'Inconnu')}")
    col1, col2, col3 = st.columns(3)
    with col1:
        pop = identity.get('population', 0)
        if pd.isna(pop) or pop is None:
            pop = 0
        st.metric("Population", f"{int(pop):,}".replace(",", " "))
    with col2:
        st.metric("Bassin de Vie", identity.get('bassin_de_vie', 'N/A'))
    with col3:
        score_gl = identity.get('score_global')
        if pd.notna(score_gl) and score_gl is not None:
            st.metric("Score Global", f"{float(score_gl)*100:.0f}%")

    # --- Helper to render scores table ---
    def render_scores_for_category(category_key: str):
        scores_dict = details.get('scores', {})
        # Map tab categories to config categories
        # Config cats: emploi, logement, education, santé, inclusion, mobilité
        
        scores = scores_dict.get(category_key, [])
        if not scores:
            st.info("Aucun indicateur spécifique pour cette catégorie.")
            return
        
        # Filter out redundant education presence scores if we have the counts tab
        if category_key == 'education':
            scores = [s for s in scores if not s['label'].startswith('Présence')]

        # Display directly (No Expanders as per V3 request)
        df_scores = pd.DataFrame(scores)
        # Sort by score_normalise desc to show strengths
        df_scores = df_scores.sort_values(by='score_normalise', ascending=False)
        
        for _, s in df_scores.iterrows():
            c_label, c_val = st.columns([3, 1])
            with c_label:
                st.write(f"**{s['label']}**")
                # Progress bar for the normalized score
                # Handle NaN values safely (prevent StreamlitAPIException)
                p_val = s['score_normalise']
                if pd.isna(p_val):
                    p_val = 0.0
                st.progress(float(max(0.0, min(1.0, p_val))))
            with c_val:
                st.write(f" `{s['valeur_kpi']}`")
                st.caption(s['unit'])

    # --- Tabs ---
    tab_emploi, tab_logement, tab_edu, tab_sante, tab_vie = st.tabs([
        "💼 Emploi & Formation", 
        "🏠 Logement", 
        "🎓 Education", 
        "🏥 Santé", 
        "🤝 Vie Sociale & Inclusion"
    ])

    with tab_emploi:
        c1, c2 = st.columns(2)
        emploi_data = details.get('emploi', {})
        
        with c1:
            st.subheader("Marché de l'emploi")
            with st.expander("Top 10 des métiers recherchés", expanded=True):
                top_metiers = emploi_data.get('top_metiers', [])
                if top_metiers:
                    # Highlighting (F-15 / V2)
                    codes_metiers_prefs = []
                    # Flatten user preferences to highlight labels
                    if 'ui_metiers_adult_0' in st.session_state:
                         codes_metiers_prefs.extend(st.session_state.ui_metiers_adult_0)
                    if 'ui_metiers_adult_1' in st.session_state:
                         codes_metiers_prefs.extend(st.session_state.ui_metiers_adult_1)
                    
                    # Resolve labels for prefs to highlight correctly
                    codfap_index = st.session_state.app_data.get('codfap_index')
                    pref_labels = []
                    if codfap_index is not None:
                         for code in codes_metiers_prefs:
                              if code in codfap_index.index:
                                   pref_labels.append(codfap_index.loc[code, 'label'])

                    for job in top_metiers:
                        if job in pref_labels:
                            st.markdown(f"- **{job}** ✨")
                        else:
                            st.markdown(f"- {job}")
                else:
                    st.info("Données non disponibles.")
        
        with c2:
            st.subheader("Formations")
            with st.expander("Centres de formation", expanded=True):
                formations = emploi_data.get('formations', [])
                if formations:
                    # Highlighting
                    codes_form_prefs = []
                    if 'ui_formations_adult_0' in st.session_state:
                        codes_form_prefs.extend(st.session_state.ui_formations_adult_0)
                    if 'ui_formations_adult_1' in st.session_state:
                        codes_form_prefs.extend(st.session_state.ui_formations_adult_1)
                    
                    codform_index = st.session_state.app_data.get('codformations_index')
                    pref_form_labels = []
                    if codform_index is not None:
                        for code in codes_form_prefs:
                            if code in codform_index.index:
                                pref_form_labels.append(codform_index.loc[code, 'label'])

                    for form in formations:
                        if form in pref_form_labels:
                            st.markdown(f"- **{form}** ✨")
                        else:
                            st.markdown(f"- {form}")
                else:
                    st.info("Aucune formation référencée.")

        st.divider()
        st.subheader("Indicateurs Emploi")
        render_scores_for_category('emploi')

    with tab_logement:
        st.subheader("Indicateurs Logement")
        render_scores_for_category('logement')

    with tab_edu:
        edu_data = details.get('education', {})
        counts = edu_data.get('counts', {})
        
        if counts:
            st.subheader("Établissements scolaires")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Maternelles", counts.get('maternelle', 0))
            c2.metric("Elémentaires", counts.get('elementaire', 0))
            c3.metric("Collèges", counts.get('college', 0))
            c4.metric("Lycées", counts.get('lycee', 0))
            st.divider()
            
        st.subheader("Indicateurs Education")
        render_scores_for_category('education')

    with tab_sante:
        st.subheader("Équipements de santé")
        sante_data = details.get('sante', {})
        s_counts = sante_data.get('counts', {})
        
        c1, c2, c3 = st.columns(3)
        with c1:
            st.write("**Maternité**")
            st.write(f"📄 {s_counts.get('maternite', 0)}")
        with c2:
            st.write("**Hôpital**")
            st.write(f"🏥 {s_counts.get('hopital', 0)}")
        with c3:
            st.write("**Addiction / Psy**")
            st.write(f"🧠 {s_counts.get('psy', 0)}")
            
        st.divider()
        st.subheader("Indicateurs Santé")
        render_scores_for_category('santé')

    with tab_vie:
        inclusion_data = details.get('inclusion', {})
        st.subheader("Associations")
        assos = details.get('associations', {})
        if assos:
            st.metric("Total Associations", assos.get('total', 0))
            if assos.get('refugee_count', 0) > 0:
                 st.caption(f"Dont {assos['refugee_count']} orientées aide aux réfugiés/immigrés.")
        
        st.divider()
        st.subheader("Services d'Inclusion")
        services = inclusion_data.get('services', [])
        if services:
            # V2 Logic for labels
            incl_index = st.session_state.app_data.get('inclusion_services_index', pd.DataFrame())
            labels = []
            for s in services:
                if not incl_index.empty and s in incl_index.index:
                    labels.append(incl_index.loc[s, 'label'])
                else:
                    labels.append(s)
            
            # Display sorted
            for label in sorted(labels):
                st.markdown(f"- {label}")
        else:
            st.info("Aucun service spécifique référencé.")

        st.divider()
        st.subheader("Indicateurs Inclusion")
        render_scores_for_category('inclusion')
        st.subheader("Indicateurs Mobilité")
        render_scores_for_category('mobilité')

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
    default_socle = cfg.DEFAULT_INC_SERVICES_CORE
    
    # Initialize session state for this selection if not present
    if 'ui_inc_services_core_selection' not in st.session_state:
        # Default to the recommended list
        st.session_state.ui_inc_services_core_selection = st.session_state['demo_data'].get('inc_services_core_selection', default_socle)

    # Widget hidden as per user request, but state is preserved for scoring.
    # st.multiselect(...) 

    # --- 2. Affinités (Loisirs & Intérêts) ---
    st.subheader("Affinités & Loisirs")
    st.info("Sélectionnez vos centres d'intérêt pour identifier les territoires avec un tissu associatif correspondant.")
    
    # from rna_config import WALDEC_INC_ASSO_ADD_MAPPING
    interest_options = list(cfg.WALDEC_INC_ASSO_ADD_MAPPING.keys())
    
    if 'ui_inc_asso_add_selection' not in st.session_state:
        st.session_state.ui_inc_asso_add_selection = st.session_state['demo_data'].get('inc_asso_add_selection', [])
        
    st.multiselect(
        "Centres d'intérêt",
        options=interest_options,
        key="ui_inc_asso_add_selection"
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
    if 'ui_inc_services_add_selection_flat' not in st.session_state:
        # User config now stores a list of slugs
        current_list = st.session_state.get('ui_inc_services_add_selection', st.session_state['demo_data'].get('inc_services_add_selection', []))
        flat_selection = []
        
        # Create reverse map for initialization: Slug -> Display String
        slug_to_display = {v: k for k, v in options_map.items()}
        
        for slug in current_list:
            if slug in slug_to_display:
                flat_selection.append(slug_to_display[slug])
                
        st.session_state.ui_inc_services_add_selection_flat = flat_selection

    # Widget
    st.multiselect(
        "Services disponibles",
        options=options_list,
        key="ui_inc_services_add_selection_flat",
        help="Recherchez et ajoutez des services spécifiques."
    )
    if st.session_state.ui_inc_services_add_selection_flat:
        st.toggle("Prioritaire", key="ui_priority_other_needs", help="Donne plus de poids à ces besoins spécifiques")
    
    # We store the map in session state so we can use it in create_scoring_config_from_inputs
    # without re-computing it (optimization)
    st.session_state['ui_inc_services_add_selection_map'] = options_map

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
    inc_services_add_selection_list = []
    if 'ui_inc_services_add_selection_flat' in st.session_state:
        flat_selection = st.session_state.ui_inc_services_add_selection_flat
        options_map = st.session_state.get('ui_inc_services_add_selection_map', {})
        
        if options_map:
            for ui_inc_asso_add_selection in flat_selection:
                if ui_inc_asso_add_selection in options_map:
                    slug = options_map[ui_inc_asso_add_selection]
                    inc_services_add_selection_list.append(slug)
        
        # Update session state for compatibility
        st.session_state.ui_inc_services_add_selection = inc_services_add_selection_list
    else:
        # Fallback to existing list if flat not present (e.g. tests or legacy)
        inc_services_add_selection_list = st.session_state.get('ui_inc_services_add_selection', [])

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
        criteria_weights['inc_services_add_scaled'] = 3.0

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
        inc_services_add_selection=inc_services_add_selection_list,
        inc_services_core_selection=st.session_state.get('ui_inc_services_core_selection', []), # NEW
        inc_asso_add_selection=st.session_state.get('ui_inc_asso_add_selection', []), # NEW
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
        
        # Project centroid to 4326 for map centering (On-the-fly)
        # processed_gdf is in EPSG:2154
        centroid_2154 = row.polygon.centroid
        centroid_4326 = gpd.GeoSeries([centroid_2154], crs=cfg.PROJECTED_CRS).to_crs("EPSG:4326").iloc[0]
        
        st.session_state.center = [centroid_4326.y, centroid_4326.x]
        st.session_state.zoom = cfg.DETAIL_MAP_ZOOM

def _show_details_callback(rank: int) -> None:
    """Callback to compute and show city details modal."""
    row = st.session_state.processed_gdf.loc[rank]
    app_data = st.session_state['app_data']
    
    # Initialize ScoringEngine to format details
    engine = ScoringEngine(
        df_all_communes=app_data['odis'],
        df_bv_geo=app_data['bv_geo'],
        df_area_geo=app_data['area_geo'],
        scores_cat=app_data['scores_cat'],
        incl_index=app_data['incl_index'],
        associations_data=app_data['associations_data'],
        bmo_vertical=app_data['bmo_vertical'],
        formations_data=app_data['formations_data'],
        codformations_index=app_data['codformations_index'],
        global_stats={},
        codfap_index=app_data['codfap_index']
    )
    
    details = engine.format_city_details(row)
    show_details_dialog(details)


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
    for i, (index, row) in enumerate(df.head(top_n).iterrows()):
        fg_key = f'Top{i + 1}'
        st.session_state.fg_dict_ref[fg_key] = maps.build_top_result_layer(row, i)

    # Display buttons and details
    for i, (index, row) in enumerate(df.head(top_n).iterrows()):
        title = f"Top {i+1} | {row.libgeo}"

        st.button(
            title,
            on_click=_result_highlight_callback,
            # We pass the index (label) to the callback so .loc[index] works
            args=(index,),
            width='stretch',
            # Key uses relative rank i
            key=f'button_top{i+1}',
            type='primary'
        )

        # Check if this row's index matches the highlighted index
        if is_highlighted and index == highlighted_rank:
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
            target_slugs = set(cfg.DEFAULT_INC_SERVICES_CORE)
            
            # Add user selected specific needs
            if 'ui_inc_services_add_selection' in st.session_state and st.session_state.ui_inc_services_add_selection:
                 target_slugs.update(st.session_state.ui_inc_services_add_selection)
            
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
        st.divider()
        # Using columns for layout
        c1, c2 = st.columns(2)
        with c1:
            if st.button("En savoir plus", key=f"btn_details_bv_{row.name}", width="stretch"):
                _show_details_callback(row.name)
        with c2:
            if st.button("Contact local", key=f"btn_ccas_bv_{row.name}", type="primary", width="stretch"):
                # For BV, pass the list of communes
                target = row['communes'] if 'communes' in row else str(row.name)
                # Ensure we pass the BV code as priority
                bv_code = str(row['bassin_de_vie']) if 'bassin_de_vie' in row else str(row.name)
                show_ccas_dialog(target, st.session_state['app_data'].get('structures_ccas', pd.DataFrame()), priority_code=bv_code, priority_label=row['libgeo'])
        # with c3:
        #      st.link_button("Page OD&IS", row.get('url_odis', '#'), width="stretch")
        # with c4:
        #     st.link_button("Page Wikipedia", row.get('url_wikipedia', '#'), width="stretch")
        

def _display_result_details(row: pd.Series) -> None:
    """Displays the detailed information for a single search result (Commune)."""
    # Use codgeo from column if available, else fallback to index (row.name)
    main_code = str(row['codgeo']) if 'codgeo' in row else str(row.name)
    
    # --- Structures Inclusion (Dialog Trigger) ---
    # We moved the display to the bottom links section or a header button
    
    # ... Skipping inline section ...
    
    # --- Existing Details ---
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
        # st.divider()
        # st.markdown('**Plus d’informations sur cette localité :**')
        # with st.expander('Top 10 des métiers recherchés'):
        #     bmo_vertical = st.session_state.app_data['bmo_vertical']
        #     codfap_index = st.session_state.app_data['codfap_index']
            
        #     commune_metiers = bmo_vertical[bmo_vertical.codgeo == main_code]
            
        #     if not commune_metiers.empty:
        #         merged = commune_metiers.merge(codfap_index, left_on='fap_code', right_index=True, how='left')
        #         merged['label'] = merged['label'].fillna(merged['fap_code'])
                
        #         top_metiers = sorted(merged['label'].unique())
        #         st.markdown("\n".join([f'- {item}' for item in top_metiers]))
        #     else:
        #         st.info("Pas de données disponibles.")
        
        # with st.expander('Formations proposées'):
        #     formations = set(row.get('noms_formations') if row.get('noms_formations') is not None else [])

        #     if formations:
        #         st.markdown("\n".join([f'- {item}' for item in sorted(list(formations))]))
        #     else:
        #         st.info("Pas de données disponibles.")
        
        # with st.expander("Services d'inclusions proposés"):
        #     services_df = st.session_state.app_data['annuaire_inclusion']
        #     incl_index = st.session_state.app_data.get('inclusion_services_index', pd.DataFrame())
            
        #     # Determine Target Slugs for Filtering
        #     target_slugs = set(cfg.DEFAULT_INC_SERVICES_CORE)
            
        #     # Add user selected specific needs
        #     if 'ui_inc_services_add_selection' in st.session_state and st.session_state.ui_inc_services_add_selection:
        #          target_slugs.update(st.session_state.ui_inc_services_add_selection)

        #     commune_services = services_df[
        #         (services_df['codgeo'] == main_code) &
        #         (services_df['categorie'].isin(target_slugs))
        #     ]
            
        #     if not commune_services.empty:
        #         # Get unique slugs found
        #         unique_slugs = sorted(commune_services['categorie'].unique())
                
        #         valid_labels = []
        #         for slug in unique_slugs:
        #             # Lookup label
        #             if not incl_index.empty and slug in incl_index.index:
        #                 try:
        #                     label = incl_index.loc[slug, 'label']
        #                     # If duplicate index
        #                     if isinstance(label, (pd.Series, pd.DataFrame)):
        #                         label = label.iloc[0]
        #                 except:
        #                     label = slug
        #             else:
        #                 label = slug # Fallback
                    
        #             if label:
        #                  valid_labels.append(label)

        #         if valid_labels:
        #              # Deduplicate labels
        #              valid_labels = sorted(list(set(valid_labels)))
        #              st.markdown("\n".join([f'- {label}' for label in valid_labels]))
        #         else:
        #             st.info("Aucun service d'inclusion correspondant aux critères trouvé dans cette commune.")
        #     else:
        #         st.info("Aucun service d'inclusion correspondant aux critères trouvé dans cette commune.")
        
        # --- Links ---
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            if st.button("En savoir plus", key=f"btn_details_comm_{row.name}", icon=':material/analytics:', width="stretch"):
                _show_details_callback(row.name)
        with c2:
            if st.button("Contact local", key=f"btn_ccas_commune_{row.name}", icon=':material/phone:', type="primary", width="stretch"):
                # For commune: Include binome if present
                targets = [main_code]
                if row.get("binome", False) and pd.notna(row.get('codgeo_binome')):
                     targets.append(str(row['codgeo_binome']))
                
                # Priority code is the main commune
                show_ccas_dialog(targets, st.session_state['app_data'].get('structures_ccas', pd.DataFrame()), priority_code=main_code, priority_label=row['libgeo'])
        # with c3:
        #      st.link_button("Page OD&IS", row.get('url_odis', '#'), width="stretch")
        # with c4:
        #     st.link_button("Page Wikipedia", row.get('url_wikipedia', '#'), width="stretch")
        

def _produce_pitch_markdown(row: pd.Series, config: cfg.ScoringConfig, scores_cat: pd.DataFrame) -> str:
    """Generates a summary "pitch" for a result, adapting to commune or bassin de vie."""
    pitch_md = []
    population = f"{row['population']:,.0f}".replace(",", " ")

    # Adapt the intro based on whether it's a bassin de vie or a commune
    libgeo = row.get('libgeo', row.get('libelle_bassin_de_vie', 'Localité'))
    if 'communes' in row and isinstance(row['communes'], list):
        # It's a bassin de vie
        pitch_md.append(f'Le bassin de vie de **{libgeo}** ({population} habitants), composé de **{len(row["communes"])} communes**, présente un bon équilibre pour le projet.')
    else:
        # It's a commune
        pitch_md.append(f'**{libgeo}** ({population} habitants) fait partie du bassin de vie de : **{row.get("libelle_bassin_de_vie", "N/A")}**.  ')

    score_percent = f"{row['weighted_score'] * 100:.0f}%"
    pitch_md.append(f'\nLa correspondance avec le projet est évaluée à **{score_percent}**. ')

    # --- Top contributing criteria (common logic) ---
    all_scores = scores_cat['score'].unique()
    crit_scores_cols = [col for col in row.keys() if col in all_scores]
    weighted_scores = {}
    for col in crit_scores_cols:
        cat = scores_cat[scores_cat.score == col]['cat'].iloc[0]
        # Skip if row doesn't have cat weight or it's 0 (optimization)
        # But we need to use the config object
        cat_weight = getattr(config, f'poids_{cat}', 0)
        
        # F-15: Include criteria-level weights
        base_weight = scores_cat[scores_cat.score == col]['weight'].iloc[0]
        dynamic_multiplier = config.criteria_weights.get(col, 1.0)
        
        total_weight = cat_weight * base_weight * dynamic_multiplier
        
        # New Scoring: The value in 'row[col]' IS the effective score (max of commune & bdv)
        effective_score = row.get(col, 0)
        
        weighted_scores[col] = effective_score * total_weight

    sorted_scores = sorted(weighted_scores.items(), key=lambda item: item[1], reverse=True)

    if any(s > 0 for s in weighted_scores.values()):
        pitch_md.append(f"\nCette localité se distingue par :")
        count = 0
        for score_col, weighted_val in sorted_scores:
            if weighted_val > 0 and count < 5:
                # Robust lookup
                details_rows = scores_cat[scores_cat.score == score_col]
                if not details_rows.empty:
                    score_details = details_rows.iloc[0]
                    pitch_md.append(f'- {score_details["score_affichage"]}')
                    count += 1

    return "\n".join(pitch_md)
