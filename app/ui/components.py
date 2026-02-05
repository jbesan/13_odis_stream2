
import streamlit as st
import pandas as pd
from plotly.express import line_polar
import geopandas as gpd
import config as cfg
from core.models import ScoringConfig
from core import maps
from typing import Dict, Any, List, Optional
from pathlib import Path
import base64
import logging
from utils.data_loader import ensure_data_initialized
from core.scoring import ScoringEngine

# --- Preservation of New Utils ---
from utils.common import get_asset_path, get_base64_image

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui")

@st.dialog("Centre Communal d'Action Sociale", width="large")
def show_ccas_dialog(codgeo_or_list: Any, structures_df: pd.DataFrame, priority_code: Optional[str] = None, priority_label: Optional[str] = None):
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

@st.dialog(title="Détails du Territoire", width="large")
def show_details_dialog(details: Dict[str, Any]):
    """Displays thematic details for a city in a large modal."""
    if not details:
        st.error("Détails non disponibles.")
        return

    # --- Header ---
    identity = details.get('identity', {})
    st.markdown(f"## 📍 {identity.get('nom', 'Inconnu')}")
    
    with st.container(border=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            pop = identity.get('population', 0)
            if pd.isna(pop) or pop is None:
                pop = 0
            st.metric("Population", f"{int(pop):,}".replace(",", " "), help="Population totale de la commune")
        with col2:
            st.metric("Bassin de Vie", identity.get('bassin_de_vie', 'N/A'), help="Territoire d'influence économique et sociale")
        with col3:
            score_gl = identity.get('score_global')
            if pd.notna(score_gl) and score_gl is not None:
                st.metric("Score Global", f"{float(score_gl)*100:.0f}%", help="Adéquation globale avec votre projet de vie")

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
            with st.container():
                c_label, c_val = st.columns([3, 1])
                with c_label:
                    st.markdown(f"**{s['label']}**")
                    p_val = s['score_normalise']
                    if pd.isna(p_val):
                        p_val = 0.0
                    st.progress(float(max(0.0, min(1.0, p_val))))
                    # Add description/tooltip as caption for better readability
                    if s.get('tooltip'):
                        st.caption(f"_{s['tooltip']}_")
                with c_val:
                    st.markdown(f"### {s['valeur_kpi']}")
                    st.caption(s['unit'] if pd.notna(s['unit']) and s['unit'] != 'None' else "")
            st.markdown("<br>", unsafe_allow_html=True) # Minor spacing

    # --- Tabs ---
    tab_emploi, tab_logement, tab_edu, tab_sante, tab_vie = st.tabs([
        "💼 Emploi & Formation", 
        "🏠 Logement", 
        "🎓 Education", 
        "🏥 Santé", 
        "🤝 Vie Sociale & Inclusion"
    ])

    with tab_emploi:
        emploi_data = details.get('emploi', {})
        c1, c2 = st.columns([1, 1.5], gap="medium")
        
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/work: Marché de l'emploi")
                
                matching_total = emploi_data.get('matching_total', 0)
                if matching_total > 0:
                    st.success(f"**{matching_total} offres en direct** correspondent à votre recherche actuelle.")
                    with st.expander("Détail par métier (Live)", expanded=True):
                        for rome, count in emploi_data.get('matching_jobs_summary', {}).items():
                            st.write(f"• **{rome}** : {count} offre{'s' if count > 1 else ''}")
                else:
                    st.error("Aucune offre en direct ne correspond à votre recherche actuelle.")
                
                with st.expander("Top 10 des métiers recherchés", expanded=False):

                    top_metiers = emploi_data.get('top_metiers', [])
                    if top_metiers:
                        pref_metiers = []
                        # Support both single and list-based session state keys
                        for k in st.session_state:
                            if k.startswith('ui_metiers_adult'):
                                val = st.session_state[k]
                                if isinstance(val, list): pref_metiers.extend(val)
                                elif isinstance(val, str) and val: pref_metiers.append(val)
                        
                        unique_prefs = set(str(p).lower() for p in pref_metiers)
                        for label in top_metiers:
                            is_pref = any(p in label.lower() for p in unique_prefs)
                            icon = "⭐ " if is_pref else ""
                            st.write(f"• {icon}{label}")
                    else:
                        st.info("Pas de données disponibles.")
                
                with st.expander("Formations proposées", expanded=False):
                    formations = emploi_data.get('formations', [])
                    if formations:
                        pref_forms = []
                        # Support both single and list-based session state keys
                        for k in st.session_state:
                            if k.startswith('ui_formations_adult'):
                                val = st.session_state[k]
                                if isinstance(val, list): pref_forms.extend(val)
                                elif isinstance(val, str) and val: pref_forms.append(val)
                                
                        unique_prefs = set(str(p).lower() for p in pref_forms)
                        for label in formations:
                            is_pref = any(p in label.lower() for p in unique_prefs)
                            icon = "⭐ " if is_pref else ""
                            st.write(f"• {icon}{label}")
                    else:
                        st.info("Aucune formation spécifique listée pour ce territoire.")
        
        with c2:
            st.markdown("#### :material/monitoring: Indicateurs Emploi")
            render_scores_for_category('emploi')

    with tab_logement:
        st.markdown("#### :material/home: Indicateurs Logement")
        render_scores_for_category('logement')

    with tab_edu:
        edu_data = details.get('education', {})
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/school: Établissements")
                
                etablissements = edu_data.get('etablissements', {})
                if etablissements:
                    for cat, names in sorted(etablissements.items()):
                        # Deduplicate, remove NaN, and sort
                        items = sorted(list(set([n for n in names if pd.notna(n)])))
                        if items:
                            with st.expander(f"{cat} ({len(items)})", expanded=False):
                                for name in items:
                                    st.write(f"• {name}")
                else:
                    # Fallback to old formations list if any
                    formations = edu_data.get('formations', [])
                    if formations:
                        with st.expander("Liste des établissements", expanded=True):
                            for f in sorted(list(set(formations))):
                                st.write(f"• {f}")
                    else:
                        st.info("Aucune information détaillée sur les établissements.")
        with c2:
            st.markdown("#### :material/analytics: Indicateurs Éducation")
            render_scores_for_category('education')

    with tab_sante:
        sante_data = details.get('sante', {})
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/medical_services: Établissements de Santé")
                etablissements = sante_data.get('etablissements', {})
                if etablissements:
                    for cat, names in sorted(etablissements.items()):
                        # Deduplicate, remove NaN, and sort
                        items = sorted(list(set([n for n in names if pd.notna(n)])))
                        if items:
                            with st.expander(f"{cat} ({len(items)})", expanded=False):
                                for name in items:
                                    st.write(f"• {name}")
                else:
                    st.info("Aucune information détaillée sur les structures de santé.")
        with c2:
            st.markdown("#### :material/medical_services: Indicateurs Santé")
            render_scores_for_category('sante')

    with tab_vie:
        incl_data = details.get('inclusion', {})
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                # 1. Specialized Associations (Refugees) - AT THE TOP
                refugee_assos = incl_data.get('refugee_associations', [])
                if refugee_assos:
                    st.markdown("#### :material/diversity_1: Associations spécialisées nouveaux arrivants")
                    with st.expander("Consulter les associations spécialisées", expanded=False):
                        refugee_df = pd.DataFrame(refugee_assos)
                        # Group by waldec_label for categorization
                        for label, group in refugee_df.groupby('waldec_label'):
                             with st.expander(f"{label} ({len(group)})", expanded=False):
                                    for _, asso in group.iterrows():
                                        st.write(f"**{asso['name']}**")
                                        if pd.notna(asso['description']):
                                            st.caption(asso['description'])
                                        # Link to assoce.fr
                                        url = f"https://www.assoce.fr/waldec/{asso['id']}"
                                        st.markdown(f"🔗 [Voir sur assoce.fr]({url})")
                                        st.markdown("---")
                
                # 2. Services d'Inclusion - NESTED IN EXPANDER
                st.markdown("#### :material/volunteer_activism: Services d'Inclusion")
                with st.expander("Consulter les services disponibles", expanded=False):
                    services_grouped = incl_data.get('services_grouped', {})
                    if services_grouped:
                        for thematique, names in sorted(services_grouped.items()):
                            # Deduplicate, remove NaN, and sort
                            items = sorted(list(set([n for n in names if pd.notna(n)])))
                            if items:
                                with st.expander(f"{thematique} ({len(items)})", expanded=False):
                                    for name in items:
                                        st.write(f"• {name}")
                    else:
                        st.info("Aucun service spécifique référencé.")
                
                # 3. ODIS Associations Directory
                odis_grouped = incl_data.get('odis_associations_grouped', {})
                if odis_grouped:
                    st.markdown("#### :material/groups: Annuaire des Associations ODIS")
                    with st.expander("Consulter l'annuaire complet", expanded=False):
                        for label, assos in sorted(odis_grouped.items()):
                            with st.expander(f"{label} ({len(assos)})", expanded=False):
                                for asso in assos:
                                    st.write(f"**{asso['name']}**")
                                    if asso.get('description'):
                                        # Description is already lowercased in pipeline, good.
                                        st.caption(asso['description'])
                                    # Link to assoce.fr
                                    url = f"https://www.assoce.fr/waldec/{asso['id']}"
                                    st.markdown(f"🔗 [Voir sur assoce.fr]({url})")
                                    st.markdown("---")
        with c2:
            st.markdown("#### :material/diversity_3: Indicateurs Inclusion")
            render_scores_for_category('inclusion')

def open_pdf_modal() -> None:
    """Callback to signal that the PDF modal should be shown."""
    st.session_state['show_pdf_modal'] = True

def display_sidebar(demo_data: Optional[Dict[str, Any]] = None) -> None:
    """Displays the sidebar with location and weight controls."""
    
    with st.sidebar:
 
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
        st.divider()
        # st.info(f"Version: {cfg.VERSION}")

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
        st.switch_page("pages/1_Accueil.py")
        
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
    rome_select = app_data['rome_index']
    codform_select = app_data['codformations_index']
    
    for i in range(st.session_state.ui_nb_adultes):
        with col1:
            st.multiselect(f"Métiers ciblés Adulte {i+1}", rome_select.index, format_func=lambda x: rome_select.loc[x, 'label'], key=f"ui_metiers_adult_{i}", help="Recherchez par nom de métier (Référentiel ROME)")
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
        
    # F-41: Only show housing type selector if 'Location' is selected in either short-term or long-term options
    if st.session_state.get('ui_hebergement') == 'Location' or st.session_state.get('ui_logement') == 'Location':
        
        st.selectbox(
            "Type de logement (affine les loyers)",
            options=list(cfg.HOUSING_TYPE_OPTIONS.keys()),
            format_func=lambda x: cfg.HOUSING_TYPE_OPTIONS[x],
            index=list(cfg.HOUSING_TYPE_OPTIONS.keys()).index("appt_all"),
            key="ui_type_logement",
            help="Permet d'utiliser les loyers spécifiques au type de logement choisi (Source ODACE 2024)"
        )
        
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
    st.text("Sélectionnez vos centres d'intérêt pour identifier les territoires avec un tissu associatif correspondant.")
    
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
    st.subheader("Autres Besoins")
    
    # F-35: FLE Checkbox (Most important need)
    # Initialize from session state or demo data
    if 'ui_inc_service_fle' not in st.session_state:
        current_list = st.session_state.get('ui_inc_services_add_selection', st.session_state['demo_data'].get('inc_services_add_selection', []))
        st.session_state.ui_inc_service_fle = cfg.INC_SERVICE_FLE_SLUG in current_list

    st.checkbox("Apprentissage du Français (FLE)", key="ui_inc_service_fle", help="Recherche de structures FLE (Français Langue Étrangère)")

    st.text("Sélectionnez d'autres services d'inclusion spécifiques.")
    
    # Prepare options: Use the Referentiel loaded in app_data
    inclusion_index = app_data.get('inclusion_services_index', pd.DataFrame())
    socle_keys = set(default_socle)
    socle_keys.add(cfg.INC_SERVICE_FLE_SLUG) # Hide FLE from the multiselect
    
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
    """Renders the UI for the 'Mobilité' form section (Consolidated)."""
    app_data = st.session_state['app_data']
    dept_details = app_data.get('dept_details', {})
    regions_dict = app_data.get('regions_names', {})
    
    # 1. France Métropolitaine Override
    is_france = st.checkbox("France Métropolitaine", key="ui_mobility_france")
    
    st.text("ou")

    # 2. Region & Department Selectors
    col1, col2 = st.columns(2)
    
    # Defaults based on current localization
    current_dept_code = st.session_state.get('ui_departement')
    current_reg_code = dept_details.get(current_dept_code, {}).get('reg_code')
    
    with col1:
        region_codes = sorted(regions_dict.keys())
        try:
            default_reg_idx = region_codes.index(current_reg_code) if current_reg_code in region_codes else 0
        except ValueError:
            default_reg_idx = 0
            
        selected_region_code = st.selectbox(
            "Région",
            region_codes,
            format_func=lambda x: regions_dict.get(x, f"Code {x}"),
            key="ui_mobility_region",
            disabled=is_france,
            index=default_reg_idx
        )
        
    with col2:
        # Filter departments by selected region
        depts_in_region = [
            code for code, details in dept_details.items() 
            if details.get('reg_code') == selected_region_code
        ]
        depts_in_region.sort()
        
        # Options: "Toute la région" + departments
        dept_options = ["Toute la région"] + depts_in_region
        
        # Try to default to current department if it's in the region
        try:
            default_dept_idx = dept_options.index(current_dept_code) if current_dept_code in dept_options else 0
        except ValueError:
            default_dept_idx = 0

        st.selectbox(
            "Département",
            dept_options,
            format_func=lambda x: f"{x} - {dept_details.get(x, {}).get('label', x)}" if x != "Toute la région" else x,
            key="ui_mobility_dept",
            disabled=is_france,
            index=default_dept_idx
        )

def display_input_tabs(demo_data: Optional[Dict[str, Any]] = None) -> None:
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


def create_scoring_config_from_inputs() -> ScoringConfig:
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

    # New Mobility Logic
    if st.session_state.get('ui_mobility_france'):
        loc_search_area = 'france'
        loc_search_code = None
    else:
        selected_dept = st.session_state.get('ui_mobility_dept')
        if selected_dept == "Toute la région":
            loc_search_area = 'region'
            loc_search_code = st.session_state.get('ui_mobility_region')
        else:
            loc_search_area = 'departement'
            loc_search_code = selected_dept

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

    # F-35: Add FLE if checked
    if st.session_state.get('ui_inc_service_fle'):
        if cfg.INC_SERVICE_FLE_SLUG not in inc_services_add_selection_list:
            inc_services_add_selection_list.append(cfg.INC_SERVICE_FLE_SLUG)
    elif cfg.INC_SERVICE_FLE_SLUG in inc_services_add_selection_list:
        # If unchecked but present (e.g. from demo), remove it
        inc_services_add_selection_list = [s for s in inc_services_add_selection_list if s != cfg.INC_SERVICE_FLE_SLUG]

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

    return ScoringConfig(
        poids_emploi=st.session_state['ui_poids_emploi'],
        poids_logement=st.session_state['ui_poids_logement'],
        poids_education=st.session_state['ui_poids_education'],
        poids_inclusion=st.session_state['ui_poids_inclusion'],
        poids_sante=st.session_state['ui_poids_sante'], # NEW
        criteria_weights=criteria_weights, # F-15
        poids_mobilité=st.session_state['ui_poids_mobilité'],
        commune_actuelle=commune_codgeo,
        loc_search_area=loc_search_area,
        loc_search_code=loc_search_code,
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
        type_logement=st.session_state.get('ui_type_logement', 'appartement_toutes')
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
        formations_data=app_data['formations_data'],
        codformations_index=app_data['codformations_index'],
        waldec_index=app_data.get('waldec_index'),
        global_stats={},

        # Ensure all indices and annuaires are passed
        annuaire_ecoles=app_data.get('annuaire_ecoles', pd.DataFrame()),
        annuaire_sante=app_data.get('annuaire_sante', pd.DataFrame()),
        annuaire_inclusion=app_data.get('annuaire_inclusion', pd.DataFrame()),
        inclusion_services_index=app_data.get('inclusion_services_index', pd.DataFrame()),
        refugee_associations_data=app_data['refugee_associations_data'],
        live_jobs_data=app_data['live_jobs_data'],
        odis_asso_mini_data=app_data.get('odis_asso_mini_data', pd.DataFrame())
    )
    
    details = engine.format_city_details(row, config=st.session_state.get('config'))

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
            args=(index,),
            width='stretch',
            key=f'button_top{i+1}',
            type='primary',
            icon=f":material/filter_{i+1}:"
        )

        # Check if this row's index matches the highlighted index
        if is_highlighted and index == highlighted_rank:
            _display_result_details(row)

        

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

        # --- Links ---
        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            if st.button("En savoir plus", key=f"btn_details_comm_{row.name}", width="stretch"):
                _show_details_callback(row.name)
        with c2:
            if st.button("Contact local", key=f"btn_ccas_commune_{row.name}", icon=':material/phone:', type="primary", width="stretch"):
                # For commune: Include binome if present
                targets = [main_code]

                
                # Priority code is the main commune
                show_ccas_dialog(targets, st.session_state['app_data'].get('structures_ccas', pd.DataFrame()), priority_code=main_code, priority_label=row['libgeo'])
        

def _produce_pitch_markdown(row: pd.Series, config: ScoringConfig, scores_cat: pd.DataFrame) -> str:
    """Generates a summary "pitch" for a result, adapting to commune or bassin de vie."""
    pitch_md = []
    population = f"{row['population']:,.0f}".replace(",", " ")

    # It's a commune
    libgeo = row.get('libgeo', row.get('libelle_bassin_de_vie', 'Localité'))
    pitch_md.append(f'**{libgeo}** ({population} habitants) fait partie du bassin de vie de : **{row.get("libelle_bassin_de_vie", "N/A")}**.  ')

    score_percent = f"{row['weighted_score'] * 100:.0f}%"
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
        
        # Robust handling of NaN or None
        def safe_get(key):
            v = row.get(key, 0)
            return v if pd.notna(v) else 0

        effective_score = safe_get(col)
        weighted_scores[col] = effective_score * total_weight

    sorted_scores = sorted(weighted_scores.items(), key=lambda item: item[1], reverse=True)

    if any(s > 0 for s in weighted_scores.values()):
        pitch_md.append(f"\nCette localité se distingue par :")
        count = 0
        for score_col, weighted_val in sorted_scores:
            if weighted_val > 0 and count < 5:
                # Robustly find label
                details = scores_cat[scores_cat.score == score_col]
                if not details.empty:
                    label = details.iloc[0]["score_affichage"]
                    pitch_md.append(f'- {label}')
                    count += 1

    return "\n".join(pitch_md)
