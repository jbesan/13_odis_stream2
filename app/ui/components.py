
import streamlit as st
import pandas as pd
import numpy as np
from plotly.express import line_polar
import geopandas as gpd
import config as cfg
from core.models import SearchCriterias, CriteriaItem
from core import maps
from typing import Dict, Any, List, Optional
from pathlib import Path
import base64
import json
import logging
import string
from agents.utils import odis_get_bg_result
from utils.data_loader import ensure_data_initialized, get_app_data
from utils import memory
from core.scoring import ScoringEngine
from typing import Any, Dict, List, Optional, Union
from core.models import SearchCriterias, CommuneResult, CommuneScoreDetail, SearchResultsData

# --- Preservation of New Utils ---
from utils.common import get_asset_path, get_base64_image

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ui")

def inject_custom_css() -> None:
    """Injects custom CSS for UI refinements (F-48, pills width)."""
    st.markdown("""
        <style>
            /* Target stable BaseWeb tag attributes used by Streamlit */
            [data-baseweb="tag"] {
                max-width: 500px !important;
            }
            /* Alternative stable selector for text inside tags */
            div[data-testid="stMultiSelect"] span {
                max-width: 500px !important;
            }
        </style>
    """, unsafe_allow_html=True)

@st.fragment
def ia_analysis_content(nom: str, codgeo: str, search_criterias: Any):
    """Component to display AI synthesis and handle follow-up questions."""
    
    logger.info(f"Starting ia_analysis_content: {nom} {codgeo}")
    # 1. Access Single Source of Truth from unified state
    if 'search_results' not in st.session_state or not st.session_state.search_results:
        st.error("Résultats introuvables.")
        return
        
    results: SearchResultsData = st.session_state.search_results
    commune = results.get_by_code(codgeo)
    if not commune:
        st.error(f"Détails introuvables pour {nom} ({codgeo}).")
        return

    # Initialize chat history in session state for interactivity
    chat_key = f"chat_history_{codgeo}"
    if chat_key not in st.session_state:
        st.session_state[chat_key] = []

    # 2. Trigger analysis if synthesis is missing
    if not commune.odis_synthesis:
        with st.spinner(f"Les experts analysent {nom}, veuillez patienter (environ 15s)..."):
            from agents.utils import run_async_safe
            
            state_dict = {
                "search_criteria": search_criterias.model_dump(),
                "is_interview_complete": True,
                "execution_mode": "full_analysis",
                "focus_city": {"name": nom, "codgeo": codgeo},
                "search_results": results.model_dump(),
                "criteria_hash": st.session_state.get('active_search_hash'),
                "messages": [{"role": "user", "content": f"Fais une analyse complète pour {nom}."}]
            }
            try:
                final_state = run_async_safe(state_dict)
                
                # Selective Merge into session state
                if "search_results" in final_state and final_state["search_results"]:
                    new_state_data = final_state["search_results"]
                    # Handle both dict and SearchResultsData model
                    def _get_field(obj, field, default=None):
                        if isinstance(obj, dict): return obj.get(field, default)
                        return getattr(obj, field, default)
                    
                    # 1. Update Global Brief
                    st.session_state.search_results.odis_brief = _get_field(new_state_data, "odis_brief", st.session_state.search_results.odis_brief)
                    
                    # 2. Find and update the specific focus city
                    new_results = _get_field(new_state_data, "results", [])
                    for city_data in new_results:
                        city_codgeo = _get_field(city_data, "codgeo")
                        if str(city_codgeo) == str(codgeo):
                            commune.odis_synthesis = _get_field(city_data, "odis_synthesis", [])
                            # Expert analysis is a dict, we update it
                            commune.expert_analysis.update(_get_field(city_data, "expert_analysis", {}))
                            new_pitch = _get_field(city_data, "scorer_pitch")
                            if new_pitch:
                                commune.scorer_pitch = new_pitch
                            break
                
                # Fail-safe: only rerun if synthesis was actually populated.
                # If the graph failed silently and returned empty synthesis, avoid
                # an infinite loop by showing an error instead of calling st.rerun().
                if commune.odis_synthesis:
                    st.rerun()
                else:
                    logger.warning(f"⚠️ [IA-DIALOG] Synthesis empty after graph run for {codgeo}. Check synthesizer logs.")
                    st.error("La synthèse n'a pas pu être générée. Veuillez réessayer.")
                    return
            except Exception as e:
                st.error(f"Erreur lors de la génération: {str(e)}")
                return

    # 3. Display Synthesis and Chat History
    # Use the persistent list from the model as the single source of truth
    history = list(commune.odis_synthesis)
    
    for msg in history:
        # with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
    
    # 4. Handle follow-up questions
    question = st.chat_input(f"Ex: Quelles associations facilitent le logement à {nom} ?", key=f"chat_input_ia_{codgeo}")
    if question:
        # Display user message immediately for responsiveness
        with st.chat_message("user"):
            st.markdown(question)
        
        with st.spinner("Recherche de la réponse en cours..."):
            from agents.utils import run_async_safe
            # The graph now handles appending to odis_synthesis internally
            state_dict = {
                "search_criteria": search_criterias.model_dump(),
                "is_interview_complete": True,
                "execution_mode": "specific_ask",
                "focus_city": {"name": nom, "codgeo": codgeo},
                "search_results": st.session_state.search_results.model_dump(),
                "criteria_hash": st.session_state.get('active_search_hash'),
                "messages": history + [{"role": "user", "content": question}]
            }
            try:
                final_state = run_async_safe(state_dict)
                
                # Selective Merge for Chat session
                if "search_results" in final_state and final_state["search_results"]:
                    new_state_data = final_state["search_results"]
                    # Handle both dict and SearchResultsData model
                    def _get_field(obj, field, default=None):
                        if isinstance(obj, dict): return obj.get(field, default)
                        return getattr(obj, field, default)
                        
                    # Update global brief if it evolved
                    st.session_state.search_results.odis_brief = _get_field(new_state_data, "odis_brief", st.session_state.search_results.odis_brief)
                    
                    # Update conversation history for THIS city
                    new_results = _get_field(new_state_data, "results", [])
                    for city_data in new_results:
                        city_codgeo = _get_field(city_data, "codgeo")
                        if str(city_codgeo) == str(codgeo):
                            commune.odis_synthesis = _get_field(city_data, "odis_synthesis", [])
                            break
                            
                # st.rerun() 
            except Exception as e:
                st.error(f"Erreur de l'agent: {str(e)}")


def _on_ia_dialog_dismiss():
    st.session_state.active_ia_city_index = None

def _on_details_dialog_dismiss():
    st.session_state.active_details_index = None

def _on_ccas_dialog_dismiss():
    st.session_state.active_ccas_index = None

@st.dialog(title=" ", width="large", on_dismiss=_on_ia_dialog_dismiss)
def show_ia_analysis_dialog(index: Any):
    """Displays AI synthesis and chat for a city in a modal."""
    if 'search_results' not in st.session_state or not st.session_state.search_results or not st.session_state.search_results.get_by_code(index):
        st.error("Données de la ville introuvables.")
        return
        
    commune = st.session_state.search_results.get_by_code(index)
    
    nom = commune.name
    codgeo = commune.codgeo
    
    st.header(f"Analyse OD&IS pour {nom}")
    
    search_criterias = st.session_state.config
    ia_analysis_content(nom, codgeo, search_criterias)

@st.fragment(run_every=2.0)
def ai_pitch_container(main_code: str, h: str):
    # 1. Try unified state first (Single source of truth)
    if 'search_results' in st.session_state and st.session_state.search_results:
        commune = st.session_state.search_results.get_by_code(main_code)
        if commune and commune.scorer_pitch:
            st.markdown(commune.scorer_pitch)
            return

    # 2. Fallback to background store with back-sync
    from agents.utils import odis_get_bg_result
    scorer_res = odis_get_bg_result(h)
    
    if scorer_res is None:
        st.info("✨ _Récupération des points forts pour cette ville..._")
    else:
        pitch_for_city = ""
        if isinstance(scorer_res, dict) and "pitches" in scorer_res:
            pitches_data = scorer_res["pitches"]
            if isinstance(pitches_data, dict) and "pitches" in pitches_data:
                pitch_for_city = pitches_data["pitches"].get(main_code, "")
        elif isinstance(scorer_res, str):
             pitch_for_city = scorer_res
             
        if pitch_for_city:
            # Sync back to unified state for persistence
            if 'search_results' in st.session_state:
                c = st.session_state.search_results.get_by_code(main_code)
                if c and not c.scorer_pitch:
                    c.scorer_pitch = pitch_for_city
                    # Also update current_geo if needed
                    if st.session_state.search_results.current_geo and st.session_state.search_results.current_geo.codgeo == main_code:
                         st.session_state.search_results.current_geo.scorer_pitch = pitch_for_city
                    # st.rerun()
            st.markdown(pitch_for_city)

def sync_background_data(commune: CommuneResult, h: Optional[str]):
    """
    Syncs both enrichment (associations) and pitches from the background store 
    back into the CommuneResult model for persistence.
    """
    if not h: return
    
    from agents.utils import odis_get_bg_result
    bg_res = odis_get_bg_result(h)
    if not isinstance(bg_res, dict): return
    
    # 1. Sync Enrichment (Associations)
    if 'enrichment' in bg_res:
        enrich_data = bg_res['enrichment'].get(str(commune.codgeo))
        if enrich_data and not commune.inclusion.asso_inclusion_list_by_cat:
            logging.debug(f"✨ [SYNC] Associations sync for {commune.codgeo}")
            commune.inclusion.asso_refugee_list = enrich_data.get('refugee', [])
            commune.inclusion.asso_refugee_count = len(commune.inclusion.asso_refugee_list)
            commune.inclusion.asso_inclusion_list_by_cat = enrich_data.get('inclusion', {})
            commune.inclusion.asso_inclusion_count = sum(len(l) for l in commune.inclusion.asso_inclusion_list_by_cat.values())
            
    # 2. Sync Pitches (AI analysis)
    if 'pitches' in bg_res and not commune.scorer_pitch:
        pitches_data = bg_res['pitches']
        if isinstance(pitches_data, dict) and "pitches" in pitches_data:
            pitch_for_city = pitches_data["pitches"].get(str(commune.codgeo))
            if pitch_for_city:
                logging.debug(f"✨ [SYNC] Pitch sync for {commune.codgeo}")
                commune.scorer_pitch = pitch_for_city

@st.dialog("Centre Communal d'Action Sociale", width="large", on_dismiss=_on_ccas_dialog_dismiss)
def show_ccas_dialog(index: Any):
    if 'search_results' not in st.session_state or not st.session_state.search_results or not st.session_state.search_results.get_by_code(index):
         st.error("Données de la ville introuvables.")
         return
         
    commune = st.session_state.search_results.get_by_code(index)
    codgeo = commune.codgeo
    libgeo = commune.name
    structures_df = get_app_data().get('structures_ccas', pd.DataFrame())
    
    target_codes = [codgeo.strip()]
    # Optional logic for binome if needed (fallback to df_all_communes)
    df_all = get_app_data().get('odis', pd.DataFrame())
    if codgeo in df_all.index:
        row = df_all.loc[codgeo]
        if 'binome' in row and row['binome'] and 'codgeo_binome' in row:
            target_codes.append(str(row['codgeo_binome']).strip())

    if not structures_df.empty and 'codgeo' in structures_df.columns:
        # Filter with clean string types
        subset = structures_df[structures_df['codgeo'].isin(target_codes)].copy()
        
        if not subset.empty:
             # For ccas, we just show them all for the commune/binome
            #  st.subheader(f"Contacts locaux pour {libgeo}")
             
             for _, struct in subset.iterrows():
                 st.divider()
                 # Layout: Commune First
                 label = struct['commune'] if pd.notna(struct.get('commune')) else libgeo
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
                     
        else:
             st.info(f"Aucune structure CCAS/CIAS référencée (avec contact) pour {libgeo}.")
    else:
        st.warning("Données structures non disponibles.")

@st.dialog(title="Détails du Territoire", width="large", on_dismiss=_on_details_dialog_dismiss)
def show_details_dialog(index: Any):
    """Displays thematic details for a city in a large modal."""
    if 'search_results' not in st.session_state or not st.session_state.search_results or not st.session_state.search_results.get_by_code(index):
        st.error("Données de la ville introuvables.")
        return
        
    commune = st.session_state.search_results.get_by_code(index)
    
    if not commune:
        st.error("Détails non disponibles.")
        return

    # --- Header ---
    st.markdown(f"## 📍 {commune.name} (code INSEE: {commune.codgeo})")
    
    # Active search hash for background enrichment (SOTA Pattern)
    h = st.session_state.get('active_search_hash')
    
    # Sync background results into model if available
    sync_background_data(commune, h)
    
    with st.container(border=False):
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Population", f"{commune.population:,}".replace(",", " "), help="Population totale de la commune")
        with col2:
            st.metric("Bassin de Vie", commune.name_bdv, help="Territoire d'influence économique et sociale")
        with col3:
            st.metric("Score Global", f"{commune.global_score*100:.1f}%", help="Adéquation globale avec votre projet de vie")

    # --- Helper to render scores table ---
    def render_scores_for_category(category_key: str):
        # category_key: emploi, logement, education, sante, inclusion, mobilite
        scores: List[CommuneScoreDetail] = commune.scores.get(category_key, [])
        if not scores:
            st.info("Aucun indicateur spécifique pour cette catégorie.")
            return
        
        # Filter out redundant education presence scores if we have the counts tab
        if category_key == 'education':
            scores = [s for s in scores if not s.label.startswith('Présence')]

        # Sort by score_normalise desc to show strengths
        scores = sorted(scores, key=lambda x: x.score_normalise, reverse=True)
        
        with st.container(border=False):
            for s in scores:
                c_label, c_val = st.columns([3, 1])
                with c_label:
                    st.markdown(f"**{s.label}**")
                    p_val = s.score_normalise
                    st.progress(float(max(0.0, min(1.0, p_val))))
                with c_val:
                    val_display = s.valeur_kpi
                    if isinstance(val_display, (int, float)) and pd.notna(val_display):
                         if isinstance(val_display, int) and val_display > 1000:
                             st.markdown(f"### {val_display:,}".replace(",", " "))
                         else:
                             st.markdown(f"### {val_display}")
                    else:
                         st.markdown(f"### {val_display}")
                    
                    st.caption(s.unit if s.unit and s.unit != 'None' else "")
            st.markdown("<br>", unsafe_allow_html=True) # Minor spacing

    # --- Tabs ---
    tab_emploi, tab_logement, tab_edu, tab_sante, tab_vie = st.tabs([
        "💼 Emploi & Formation", 
        "🏠 Logement", 
        "🎓 Éducation", 
        "🏥 Santé", 
        "🤝 Vie Sociale & Inclusion"
    ])

    with tab_emploi:
        employment_data = commune.employment
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/work: Opportunités")
                
                live_total = employment_data.standard_jobs_total
                matching_total = employment_data.standard_jobs_matching_total
                
                if live_total > 0:
                    st.info(f"**{live_total} postes** à pourvoir actuellement dans le bassin de vie.")
                    if matching_total > 0:
                        st.success(f"**{matching_total} correspondances** directes avec votre projet !")
                
                with st.expander("Métiers les plus recherchés", expanded=False):
                    top_professions = employment_data.top_professions
                    if top_professions:
                        for m in top_professions:
                            st.write(f"• {m}")
                    else:
                        st.write("Pas de données détaillées.")
                
                matching_siae = employment_data.inclusive_jobs_matching_summary
                if matching_siae:
                    with st.expander(f"Offres par les SIAE correspondant au projet ({employment_data.inclusive_jobs_matching_total})", expanded=True):
                        for label, count in matching_siae.items():
                            st.write(f"• **{label}** : {count} offre{'s' if count > 1 else ''}")
                elif employment_data.inclusive_jobs_total > 0:
                    with st.expander(f"Toutes les offres par les SIAE locales ({employment_data.inclusive_jobs_total})", expanded=False):
                        for label, count in employment_data.inclusive_jobs_summary.items():
                            st.write(f"• **{label}** : {count} offre{'s' if count > 1 else ''}")
                
                with st.expander("Formations proposées", expanded=False):
                    training_programs = employment_data.training_programs
                    if training_programs:
                        pref_forms = []
                        for k in st.session_state:
                            if k.startswith('ui_formations_adult'):
                                val = st.session_state[k]
                                if isinstance(val, list): pref_forms.extend(val)
                                elif isinstance(val, str) and val: pref_forms.append(val)
                        unique_prefs = set(str(p).lower() for p in pref_forms)
                        for label in training_programs:
                            is_pref = any(p in label.lower() for p in unique_prefs)
                            icon = "⭐ " if is_pref else ""
                            st.write(f"• {icon}{label}")
                    else:
                        st.info("Aucune formation spécifique listée pour ce territoire.")
        
        with c2:
            st.markdown("#### :material/monitoring: Indicateurs Emploi")
            render_scores_for_category('emploi')

    with tab_logement:
        housing_data = commune.housing
        c1, c2 = st.columns([1, 1], gap="medium")
        with c2:
            st.markdown("#### :material/home: Indicateurs Logement")
            render_scores_for_category('logement')
        with c1:
            st.markdown("#### :material/info: Données Complémentaires")
            j_count = housing_data.host_count
            if j_count > 0:
                 st.info(f"**{int(j_count)} accueillants** J'Accueille identifiés dans le bassin de vie.")

    with tab_edu:
        education_data = commune.education
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/school: Établissements")
                facility_details = education_data.facility_details
                if facility_details:
                    for cat, names in sorted(facility_details.items()):
                        items = sorted(list(set([n for n in names if pd.notna(n)])))
                        if items:
                            with st.expander(f"{cat} ({len(items)})", expanded=False):
                                for name in items:
                                    st.write(f"• {name}")
                else:
                    st.info("Aucune information détaillée sur les établissements.")
        with c2:
            st.markdown("#### :material/analytics: Indicateurs Éducation")
            render_scores_for_category('education')

    with tab_sante:
        health_data = commune.health
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/medical_services: Établissements de Santé")
                facility_details = health_data.facility_details
                if facility_details:
                    for cat, names in sorted(facility_details.items()):
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
        inclusion_data = commune.inclusion
        c1, c2 = st.columns([1, 1], gap="medium")
        with c1:
            with st.container(border=False):
                st.markdown("#### :material/volunteer_activism: Services d'Inclusion")
                with st.expander("Consulter les services disponibles", expanded=False):
                    services_grouped = inclusion_data.services_grouped
                    if services_grouped:
                        for thematique, names in sorted(services_grouped.items()):
                            items = sorted(list(set([n for n in names if pd.notna(n)])))
                            if items:
                                with st.expander(f"{thematique} ({len(items)})", expanded=False):
                                    for name in items:
                                        st.write(f"• {name}")
                    else:
                        st.info("Aucun service spécifique référencé.")
                
                st.markdown("#### :material/groups: Associations de l'inclusion")
                
                @st.fragment(run_every=3.0)
                def associations_polling_fragment():
                    # Local reference to data
                    inc_data = commune.inclusion
                    
                    # 1. Background Sync (if data is missing)
                    if h and not inc_data.asso_inclusion_list_by_cat:
                        bg_res = odis_get_bg_result(h)
                        if isinstance(bg_res, dict) and 'enrichment' in bg_res:
                            enrich_data = bg_res['enrichment'].get(str(commune.codgeo))
                            if enrich_data:
                                logging.info(f"✨ [FRAGMENT] Data arrived for {commune.codgeo}, updating UI")
                                inc_data.asso_refugee_list = enrich_data.get('refugee', [])
                                inc_data.asso_refugee_count = len(inc_data.asso_refugee_list)
                                inc_data.asso_inclusion_list_by_cat = enrich_data.get('inclusion', {})
                                inc_data.asso_inclusion_count = sum(len(l) for l in inc_data.asso_inclusion_list_by_cat.values())
                                # No st.rerun() here to avoid closing/resetting the dialog tabs

                    # 2. Render UI
                    if inc_data.asso_inclusion_count > 0:
                        st.info(f"**{inc_data.asso_inclusion_count} associations** actives identifiées dans le bassin de vie.")
                        if inc_data.asso_refugee_count > 0:
                            st.success(f"**{inc_data.asso_refugee_count} association(s)** spécifiquement dédiée(s) aux réfugiés.")
                        

                        # Display Refugee associations from the model (secondary list)  
                        if inclusion_data.asso_refugee_list:
                            with st.expander("Intégration des réfugiés & migrants", expanded=True):
                                # Sort by local preference if needed (already sorted in scoring.py)
                                for asso in inclusion_data.asso_refugee_list:
                                    name = str(asso.get('name', 'Inconnu'))
                                    id_val = asso.get('id', '')
                                    url = f"https://www.assoce.fr/waldec/{id_val}" if id_val else "#"
                                    desc = str(asso.get('description', '')).strip()
                                    
                                    cat_label = asso.get('waldec_label', '')
                                    cat_str = f" ({cat_label})" if cat_label else ""
                                    
                                    if desc:
                                        st.markdown(f"**{name}**{cat_str}: {desc} [En savoir plus]({url})")
                                    else:
                                        st.markdown(f"**{name}**{cat_str}: [En savoir plus]({url})")

                        # Display other categories
                        if inc_data.asso_inclusion_list_by_cat:
                            # with st.expander("Répartition par catégorie", expanded=False):
                            for cat, asso_list in sorted(inc_data.asso_inclusion_list_by_cat.items()):
                                with st.expander(f"**{cat}** ({len(asso_list)})", expanded=False):
                                    for asso in asso_list:
                                        name = str(asso.get('name', 'Inconnu'))
                                        id_val = asso.get('id', '')
                                        url = f"https://www.assoce.fr/waldec/{id_val}" if id_val else "#"
                                        desc = str(asso.get('description', '')).strip()
                                        
                                        if desc:
                                            st.markdown(f"**{name}**: {desc} [En savoir plus]({url})")
                                        else:
                                            st.markdown(f"**{name}**: [En savoir plus]({url})")
                    elif h and (not odis_get_bg_result(h) or 'enrichment' not in odis_get_bg_result(h)):
                        with st.status("Récupération des associations détaillées...", expanded=True):
                            st.write("Nous interrogeons BigQuery pour obtenir la liste complète des associations locales.")
                    else:
                        st.info("Aucune association détaillée répertoriée pour ce territoire.")
                
                # Call the fragment
                associations_polling_fragment()
                
                
                
                # If we still want to allow dynamic search as a fallback/expansion, 
                # keep it but make it optional or moved to the Scout agent.
                # For now, let's keep the UI focused on the pre-computed data.
        with c2:
            st.markdown("#### :material/diversity_3: Indicateurs Inclusion")
            render_scores_for_category('inclusion')



        # def clear_processed_gdf():
        #     st.session_state['processed_gdf'] = None

@st.dialog("Confirmer la réinitialisation")
def confirm_reset_dialog():
    # st.warning("⚠️ Cette action réinitialisera tous vos critères de recherche.")
    st.write("Cette action réinitialisera la recherche en cours. Souhaitez-vous vraiment retourner à l'accueil et effacer vos saisies ?")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Oui", width="stretch"):
            st.switch_page("pages/1_Accueil.py")
    with col2:
        if st.button("Annuler", width="stretch"):
            st.rerun()

def start_over() -> None:
    # --- Start over ---
    st.markdown("""
        <style>
            .st-key-btn_recommencer .stButton p {color: white;}
        </style>
        """
    , unsafe_allow_html=True)
    if st.button("Retour à l'Accueil", icon=":material/home:", width="stretch", key="btn_recommencer"):
        if 'search_results' in st.session_state:
            confirm_reset_dialog()
        else:
            st.switch_page("pages/1_Accueil.py")

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
                # This widget also needs the index to be set correctly.
                options = cfg.CLASSES_SCOLAIRES
                key = f"ui_classe_enfant_{i}"
                st.selectbox(f'Niveau enfant {i+1}', options, key=key)
                # st.toggle("Prioritaire", key=f"ui_priority_edu_{i}", help="Donne plus de poids à ce critère")

def render_employment_form() -> None:
    """Renders the UI for the 'Emploi & Formation' form section."""
    inject_custom_css()
    app_data = get_app_data()
    col1, col2 = st.columns(2)
    rome_full_index = app_data['rome_index']
    rome_top_index = app_data.get('rome_top_index', rome_full_index) # Fallback to full if missing
    codform_select = app_data['codformations_index']
    
    for i in range(st.session_state.ui_nb_adultes):
        with col1:
            # F-47: Use rome_top_index but ensure currently selected codes are in options
            current_selection = st.session_state.get(f"ui_metiers_adult_{i}", [])
            
            # Combine top index with current selection to avoid "Value not in options" errors
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
            
            # F-15: Priority Toggle
            # st.toggle("Prioritaire", key=f"ui_priority_job_adult_{i}", help="Donne plus de poids à la recherche d'emploi pour cet adulte")

def render_housing_form() -> None:
    """Renders the UI for the 'Logement' form section."""
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Hébergement cible à court terme")
        
        # Initialize checkboxes from aggregate state if they don't exist
        # This ensures demo scenarios are correctly reflected in the checkboxes
        current_heb = st.session_state.get('ui_hebergement_cible', [])
        selected_heb = []
        
        # Streamlit doesn't have a built-in Checkbox Group, so we loop through options
        for opt in cfg.HEBERGEMENT_OPTIONS:
            cb_key = f"ui_heb_cb_{opt.replace(' ', '_').lower()}"
            if cb_key not in st.session_state:
                st.session_state[cb_key] = opt in current_heb
            
            if st.checkbox(opt, key=cb_key):
                selected_heb.append(opt)
        
        # Update aggregate state for scoring
        st.session_state['ui_hebergement_cible'] = selected_heb
        
        # st.toggle("Prioritaire", key="ui_priority_hebergement", help="Donne plus de poids à ce critère")
    with col2:
        st.subheader("Logement cible à long terme")
        st.radio('Logement', cfg.LOGEMENT_OPTIONS, key="ui_logement", label_visibility="hidden")
        # st.toggle("Prioritaire", key="ui_priority_logement", help="Donne plus de poids à ce critère")
        
    # F-41: Only show housing type selector if 'Location' or 'Location avec Intermédiation' is selected
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
            help="Permet d'utiliser les loyers spécifiques au type de logement choisi (Source ODACE 2024)"
        )
        
    # F-15: Priority Toggle (Removed global toggle)

def render_health_form() -> None:
    """Renders the UI for the 'Santé' form section."""
    options = ["Aucun", "Hopital", 'Maternité', "Soutien Psychologique & Addictologie"]
    st.radio('Support médical à proximité', options, key="ui_besoin_sante")
    # if st.session_state.ui_besoin_sante != "Aucun":
        # st.toggle("Prioritaire", key="ui_priority_sante", help="Donne plus de poids à ce critère")

def render_other_needs_form() -> None:
    """Renders the UI for the 'Autres Besoins' (Inclusion) section (Refactored F-13/F-48)."""
    inject_custom_css()
    app_data = get_app_data()
    
    col1, col2 = st.columns(2)
    with col2:
        # --- 1. Affinités (Loisirs & Intérêts) ---
        st.subheader("Associations Locales (Solidarité, Loisirs, Culture)")
        st.text("Sélectionnez vos centres d'intérêt pour identifier les territoires avec un tissu associatif correspondant.")
        
        # Load pre-enriched waldec_index
        if 'waldec_index' in get_app_data():
            waldec_index = get_app_data()['waldec_index']
            # Prefixes from config
            prefixes = cfg.WALDEC_CATEGORIES
            
            # Filter for Loisirs categories
            # waldec_index is already indexed by 'code' and sorted by count DESC
            mask = waldec_index.index.str[:3].isin(prefixes)
            loisirs_df = waldec_index[mask].copy()
            
            # Prepare options: CriteriaItem for consistency
            options_items = []
            item_map = {}
            
            for code, row in loisirs_df.iterrows():
                item = CriteriaItem(code=str(code), label=row['label'])
                options_items.append(item)
                count_str = f"{int(row['count']):,}".replace(",", " ")
                item_map[item.code] = f"{item.label.title()} [{count_str} assos]"

            if 'ui_inc_asso_add_selection' not in st.session_state:
                st.session_state.ui_inc_asso_add_selection = st.session_state['demo_data'].get('inc_asso_add_selection', [])
                
            selected_codes = st.multiselect(
                "Centres d'intérêt",
                options=[item.code for item in options_items],
                format_func=lambda x: item_map.get(x, x),
                key="ui_inc_asso_add_selection_raw", 
                label_visibility="collapsed"
            )
            
            # Sync with the main selection
            st.session_state.ui_inc_asso_add_selection = [
                next(item for item in options_items if item.code == code)
                for code in selected_codes
            ]
        else:
            st.warning("Référentiel WALDEC non chargé.")

    with col1:
        # --- 2. Besoins d'Analyse (Inclusion Services) ---
        st.subheader("Services d'Inclusion")
        st.text("Sélectionnez des services pertinents pour faciliter leur installation une fois sur place.")
        st.text("Services courants:")

        # Initialize global selection if not present
        if 'ui_inc_services_add_selection' not in st.session_state:
            # Default includes the core services according to F-48
            st.session_state.ui_inc_services_add_selection = st.session_state['demo_data'].get('inc_services_add_selection', cfg.DEFAULT_INC_SERVICES_CORE)

        current_selection = set(st.session_state.ui_inc_services_add_selection)
        checkbox_selection = set()

        # Render explicit checkboxes from mapping in config
        for slug, label in cfg.INC_SERVICES_CHECKBOX_MAPPING.items():
            cb_key = f"ui_cb_inc_{slug.replace('-', '_')}"
            
            # Initialize individual checkbox state from global selection
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
        
        # Prepare options: Filter out services already present in checkboxes
        inclusion_index = app_data.get('inclusion_services_index', pd.DataFrame())
        checkbox_slugs = set(cfg.INC_SERVICES_CHECKBOX_MAPPING.keys())
        
        options_map = {} # Display String -> Slug
        if not inclusion_index.empty:
            for code, row in inclusion_index.iterrows():
                if code not in checkbox_slugs:
                    label = row['label']
                    options_map[label] = code
        
        options_list = sorted(list(options_map.keys()))
        
        # Use a separate state for the multiselect to avoid conflict with the global merge
        if 'ui_inc_services_multi_only' not in st.session_state:
            # Filter the current selection to only keep those NOT in checkboxes
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
        
        # Merge Checkboxes + Multiselect into the final state used for scoring
        final_selection = list(checkbox_selection)
        for label in selected_labels:
            if label in options_map:
                final_selection.append(options_map[label])
        
        st.session_state.ui_inc_services_add_selection = sorted(list(set(final_selection)))
        
        # Store map for label recovery in results page if needed
        st.session_state['ui_inc_services_add_selection_map'] = options_map

def render_other_notes_form() -> None:
    """Renders the UI for entering free-text qualitative notes (F-48 update)."""
    if 'ui_notes_qualitatives' not in st.session_state:
        st.session_state.ui_notes_qualitatives = st.session_state['demo_data'].get('notes_qualitatives', "")

    # st.subheader("Autres informations")
    st.text("Précisez ici tout élément supplémentaire potentiellement utile pour la recherche (origine culturelle, contexte familial, passions, contraintes spécifiques, etc.).")
    
    st.text_area(
        "Notes qualitatives",
        key="ui_notes_qualitatives",
        height=250,  # "Grande zone" as requested by user
        placeholder="Exemple : Famille sud-américaine parlant espagnol, souhaite une zone rurale avec accès à la nature...",
        label_visibility="collapsed"
    )

def render_mobility_form() -> None:
    """Renders the UI for the 'Mobilité' form section (Consolidated)."""
    app_data = get_app_data()
    dept_details = app_data.get('dept_details', {})
    regions_dict = app_data.get('regions_names', {})

    # Defaults based on current localization
    current_dept_code = st.session_state.get('ui_departement')
    current_reg_code = dept_details.get(current_dept_code, {}).get('reg_code')
    
    # 1. France & Region Selectors
    region_codes = sorted(regions_dict.keys())
    
    # Ensure session states are initialized
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

    # 2. Region Checkbox & Department Multiselect
    depts_in_region = [
        code for code, details in dept_details.items() 
        if details.get('reg_code') == selected_region_code
    ]
    depts_in_region.sort()
    
    if "ui_mobility_dept" not in st.session_state:
         # Initialize as a list for multiselect
         st.session_state["ui_mobility_dept"] = [current_dept_code] if current_dept_code in depts_in_region else []
    elif isinstance(st.session_state["ui_mobility_dept"], str):
         # Migration: if it was a selection from previous version, convert to list if valid
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

    if st.session_state.ui_france_search:
        st.info("💡 Recherche sur l'ensemble du territoire métropolitain.")
    elif st.session_state.ui_region_search:
        st.info(f"💡 Recherche sur toute la région {regions_dict.get(selected_region_code)}.")

    # 2. Target City Size (F-50 Refactored)
    st.divider()
    
    target_options = list(cfg.CITY_SIZE_MAPPING.keys())
    # Initialize label from current numeric mu if possible, else default to "Petite Ville"
    if "ui_target_city_size_label" not in st.session_state:
        # Default target index is 2 for "Petite Ville"
        default_label = next((l for l in target_options if "Petite Ville" in l), target_options[2])
        st.session_state["ui_target_city_size_label"] = default_label

    # Centering logic using columns (standard Streamlit pattern)
    # _, col_center, _ = st.columns([1, 6, 1])
    # with col_center:
    with st.container(horizontal=True, width='stretch', horizontal_alignment='center'):
        st.radio(
            "Taille de la ville recherchée",
            options=target_options,
            key="ui_target_city_size_label",
            horizontal=True,
            help="Définit la taille idéale de la commune recherchée. Le score de population sera maximal pour cette catégorie.",
            label_visibility="visible"
        )
    
    # Sync numeric values for scoring engine compatibility
    selected_label = st.session_state["ui_target_city_size_label"]
    mapping = cfg.CITY_SIZE_MAPPING.get(selected_label, {"mu": cfg.DEFAULT_MU, "sigma": cfg.DEFAULT_SIGMA})
    st.session_state["ui_target_population"] = mapping["mu"]
    st.session_state["ui_target_population_sigma"] = mapping["sigma"]
    # st.caption(f"Tolérance : +/- {st.session_state['ui_target_population_sigma']:,} hab.".replace(",", " "))

def render_weight_profile_form() -> None:
    """Renders the UI for selecting the weighting profile and expert weights adjustment."""
    def _update_weights_from_profile():
        profile = st.session_state.ui_weight_profile
        if profile in cfg.WEIGHT_PROFILES:
            weights = cfg.WEIGHT_PROFILES[profile]
            for key, value in weights.items():
                # Update session state keys for sliders (e.g. ui_poids_education)
                st.session_state[f"ui_{key}"] = value
        
        # Reset results if weights change
        st.session_state['processed_gdf'] = None

    weight_profiles = list(cfg.WEIGHT_PROFILES.keys())
    if "ui_weight_profile" not in st.session_state or st.session_state["ui_weight_profile"] not in weight_profiles:
        st.session_state["ui_weight_profile"] = weight_profiles[0]

    st.text('Pour améliorer la pertinence des résultats de la recherche, vous pouvez ajuster les poids des différentes catégories de critères de recherche en utilisant soit un profil pré-défini (recommandé) soit une pondération sur-mesure.')

    col1, col2 = st.columns(2)
    with col1:
        st.selectbox(
            "Profils prédéfinis",
            options=weight_profiles,
            key="ui_weight_profile",
            on_change=_update_weights_from_profile
        )
        # New "Expert Mode" toggle
        st.toggle("Profil personalisé", key="ui_expert_weights", value=False)
    
    with col2:
       
        
        # if st.session_state.get('ui_expert_weights'):
            # st.info("Ajustez finement l'importance de chaque catégorie.")
        
        # F-48: Fix slider warnings by removing 'value' and managing via key + session state
        for p_key in ["ui_poids_education", "ui_poids_emploi", "ui_poids_logement", "ui_poids_inclusion", "ui_poids_sante", "ui_poids_mobilite"]:
            if p_key not in st.session_state:
                st.session_state[p_key] = 50

        st.select_slider("Education", cfg.POIDS_OPTIONS, 
                        disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_education", on_change=lambda: [st.session_state.setdefault('processed_gdf', None), st.session_state.setdefault('search_results', None)])
        st.select_slider("Projet Pro", cfg.POIDS_OPTIONS, 
                        disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_emploi", on_change=lambda: [st.session_state.setdefault('processed_gdf', None), st.session_state.setdefault('search_results', None)])
        st.select_slider("Logement", cfg.POIDS_OPTIONS, 
                        disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_logement", on_change=lambda: [st.session_state.setdefault('processed_gdf', None), st.session_state.setdefault('search_results', None)])
        st.select_slider("Inclusion", cfg.POIDS_OPTIONS, 
                        disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_inclusion", on_change=lambda: [st.session_state.setdefault('processed_gdf', None), st.session_state.setdefault('search_results', None)])
        st.select_slider("Santé", cfg.POIDS_OPTIONS, 
                        disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_sante", on_change=lambda: [st.session_state.setdefault('processed_gdf', None), st.session_state.setdefault('search_results', None)])
        st.select_slider("Mobilité", cfg.POIDS_OPTIONS, 
                        disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_mobilite", on_change=lambda: [st.session_state.setdefault('processed_gdf', None), st.session_state.setdefault('search_results', None)])
        # else:
        #     st.caption("Utilisez un profil prédéfini ci-dessus ou activez le mode personnalisé pour un réglage fin.")

def display_input_tabs() -> None:
    """Displays the main tabs for user input, composed of modular rendering functions."""
    inject_custom_css()
    
    tab_localisation, tab_foyer, tab_edu, tab_emploi, tab_logement, tab_sante, tab_autres, tab_notes, tab_profile = st.tabs([
        'Localisation', 'Situation familiale', 'Education', 'Projet Professionnel', 'Logement', 'Santé', 'Inclusion', 'Autres', 'Profil'
    ])
    with tab_localisation:
        col1, col2 = st.columns([1, 2])
        with col1:
            st.markdown("**Localisation actuelle**")
            render_localisation_form()
        with col2:
            st.markdown("**Zone de recherche**")
            render_mobility_form()
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
    with tab_notes:
        render_other_notes_form()
    with tab_profile:
        render_weight_profile_form()


from core.models import SearchCriterias, CriteriaItem
import config as cfg

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
        # Multiselect returns a list of codes
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

    # Process Autres Besoins from Flat List (F-13 UI Update)
    inc_services_add_selection_list = []
    if 'ui_inc_services_add_selection_flat' in st.session_state:
        flat_selection = st.session_state.ui_inc_services_add_selection_flat
        options_map = st.session_state.get('ui_inc_services_add_selection_map', {})
        
        if options_map:
            for item_label in flat_selection:
                if item_label in options_map:
                    slug = options_map[item_label]
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
    for i in range(nb_enfants):
        level = st.session_state.get(f"ui_classe_enfant_{i}")
        is_priority = st.session_state.get(f"ui_priority_edu_{i}", False)
        if is_priority and level in edu_map:
            criteria_weights[edu_map[level]] = 3.0
            
    # Employment Priorities (F-15)
    for i in range(nb_adultes):
        if st.session_state.get(f"ui_priority_job_adult_{i}", False):
            # Boost the match score for this adult
            criteria_weights[f'met_match_adult{i+1}_scaled'] = 3.0
            
    # Housing Priorities (F-15)
    # 1. Hebergement Priority (F-42)
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
        criteria_weights['inc_services_incl_scaled'] = 3.0

    # Enrich Inclusion Services
    inc_index = app_data.get('inclusion_services_index', pd.DataFrame())
    inc_services_mapped = []
    for code in inc_services_add_selection_list:
        label = inc_index.loc[code, 'label'] if not inc_index.empty and code in inc_index.index else str(code)
        inc_services_mapped.append(CriteriaItem(code=str(code), label=str(label)))

    # Enrich Inclusion Associations
    inc_assos_mapped = []
    for item in st.session_state.get('ui_inc_asso_add_selection', []):
        if isinstance(item, CriteriaItem):
            inc_assos_mapped.append(item)
        elif isinstance(item, str):
            # Try to find the code in waldec_index by label (Backward compatibility for tests)
            waldec_index = app_data.get('waldec_index', pd.DataFrame())
            code_str = "000"
            if not waldec_index.empty:
                # Find the first entry that matches the label
                matches = waldec_index[waldec_index['label'] == item]
                if not matches.empty:
                    code_str = str(matches.index[0])
            inc_assos_mapped.append(CriteriaItem(code=code_str, label=str(item)))


    # Type Logement Enrich
    type_log = None
    ui_type_log = st.session_state.get('ui_type_logement', 'appt_all')
    if ui_type_log in cfg.HOUSING_TYPE_OPTIONS:
        type_log = CriteriaItem(code=ui_type_log, label=cfg.HOUSING_TYPE_OPTIONS[ui_type_log])

    # Weights & Profile
    profile = st.session_state.get('ui_weight_profile', 'Équilibré')
    
    # Population mapping from Label
    selected_city_label = st.session_state.get("ui_target_city_size_label")
    mapping = cfg.CITY_SIZE_MAPPING.get(selected_city_label, {"mu": cfg.DEFAULT_MU, "sigma": cfg.DEFAULT_SIGMA})
    target_pop = mapping["mu"]
    target_sigma = mapping["sigma"]
    
    # Adjust mobility weights based on freq_retour
    freq = st.session_state.get('ui_freq_retour', "Pas d'attache particulière")
    if freq == "1 fois/semaine":
        criteria_weights['mob_epci_scaled'] = 3.0
        criteria_weights['mob_dist_current_loc_scaled'] = 3.0
    elif freq == "1 fois/mois":
        criteria_weights['mob_epci_scaled'] = 2.0
        criteria_weights['mob_dist_current_loc_scaled'] = 2.0
    elif freq == "1 fois/an":
        criteria_weights['mob_epci_scaled'] = 1.0
        criteria_weights['mob_dist_current_loc_scaled'] = 1.0
    
    return SearchCriterias(
        weight_profile=profile,
        poids_emploi=st.session_state.get('ui_poids_emploi', 50) / 100.0,
        poids_logement=st.session_state.get('ui_poids_logement', 50) / 100.0,
        poids_education=st.session_state.get('ui_poids_education', 50) / 100.0,
        poids_inclusion=st.session_state.get('ui_poids_inclusion', 50) / 100.0,
        poids_sante=st.session_state.get('ui_poids_sante', 50) / 100.0,
        poids_mobilite=st.session_state.get('ui_poids_mobilite', 50) / 100.0,
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
        
        inc_services_add_selection=inc_services_mapped,
        inc_asso_add_selection=inc_assos_mapped,
        notes_qualitatives=[st.session_state.get('ui_notes_qualitatives', "")] if st.session_state.get('ui_notes_qualitatives') else []
    )

def _result_highlight_callback(index: int) -> None:
    """Callback to handle highlighting a result by its index in the top results."""
    search_results: SearchResultsData = st.session_state.get('search_results')
    if not search_results or index >= len(search_results.results):
        return

    is_highlighted, highlighted_rank = st.session_state.highlighted_result
    
    # If the same button is clicked again, un-highlight it
    if is_highlighted and index == highlighted_rank:
        st.session_state.highlighted_result = [False, None]
        st.session_state.zoom = None
    else:
        commune = search_results.results[index]
        st.session_state.highlighted_result = [True, index]
        c_pt = maps._get_geom(commune, 'centroid', gdf_context=st.session_state.processed_gdf)
        if c_pt:
            st.session_state.center = [c_pt.y, c_pt.x]
        st.session_state.zoom = cfg.DETAIL_MAP_ZOOM


def get_person_accompanied_str() -> str:
    if st.session_state.get('ui_nom'):
        return f"de {st.session_state.ui_nom}"
    return "de la personne accompagnée"

def display_results_list(display_gdf: Optional[pd.DataFrame] = None) -> None:
    """Renders the list of search results or the detailed view for the highlighted result."""
    h = st.session_state.get('active_search_hash')
    search_results: SearchResultsData = st.session_state.get('search_results')
    
    if not search_results or not search_results.results:
        st.info("Aucun résultat à afficher.")
        return
        
    # SOTA Global Sync Fragment: Hydrates all results in the background
    @st.fragment(run_every=4.0)
    def global_sync_fragment():
        if h and 'search_results' in st.session_state:
            results = st.session_state.search_results.results
            for c in results:
                # Proactively sync both pitches and associations
                sync_background_data(c, h)

    global_sync_fragment()

    # Handle Active Dialogs (at page/list rendering level)
    if st.session_state.get('active_ia_city_index') is not None:
        show_ia_analysis_dialog(st.session_state.active_ia_city_index)
        
    if st.session_state.get('active_details_index') is not None:
        show_details_dialog(st.session_state.active_details_index)
        
    if st.session_state.get('active_ccas_index') is not None:
        show_ccas_dialog(st.session_state.active_ccas_index)

    st.markdown('<style> [class*="st-key-button_top"] .stButton button div, [class*="st-key-button_top"] .stButton button p { justify-content: flex-start !important; text-align: left !important; width: 100%; } </style>', unsafe_allow_html=True)

    is_highlighted, highlighted_rank = st.session_state.highlighted_result

    # Display buttons and details
    for i, commune in enumerate(search_results.results):
        title = f"**{commune.global_score * 100:.1f}%**  |  {commune.name}"

        st.button(
            title,
            on_click=_result_highlight_callback,
            args=(i,),
            width='stretch',
            key=f'button_top{i+1}',
            type='primary',
            icon=f":material/counter_{i+1}:"
        )

        # Check if this row's index matches the highlighted index
        if is_highlighted and i == highlighted_rank:
            _display_result_details(commune)

        

def _display_result_details(commune: CommuneResult) -> None:
    """Displays the detailed information for a single search result (Commune)."""
    h = st.session_state.get('active_search_hash')
    
    with st.container(border=True):
        # --- Pitch ---
        population = f"{commune.population:,}".replace(",", " ")
        libgeo = commune.name
        score_percent = f"{commune.global_score * 100:.1f}%"
        
        st.markdown(f"**{libgeo}** ({population} habitants) fait partie du bassin de vie de : **{commune.name_bdv}**.  \nLa correspondance avec le projet est évaluée à **{score_percent}**.")
        
        # Sync background results into model if available
        sync_background_data(commune, h)
        
        # --- AI Pitch Fragment ---
        ai_pitch_container(commune.codgeo, h)
        
        # F-IA: AI Dialog Trigger (Session State based)
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown('<style> [class*="st-key-btn_ia"] .stButton button { background-color: #F5D819; color: #1B4429; } </style>', unsafe_allow_html=True)
            if st.button("Analyse Complète OD&IS", key=f"btn_ia_comm_{commune.codgeo}", icon=':material/bolt:', width="content", type="primary"):
                st.session_state.active_ia_city_index = commune.codgeo
                st.rerun()

        # --- Radar Chart with Comparison ---
        # 1. Determine active categories based on user criteria stored in config
        all_cats = ['emploi', 'logement', 'education', 'sante', 'inclusion', 'mobilite']
        cat_map = {
            'emploi': 'employment',
            'logement': 'housing',
            'education': 'education',
            'sante': 'health',
            'inclusion': 'inclusion',
            'mobilite': 'mobility'
        }
        
        config = st.session_state.get('config')
        if config and hasattr(config, 'active_categories') and config.active_categories:
            active_cats = [cat for cat in all_cats if cat in config.active_categories]
        else:
            active_cats = all_cats

        def get_radar_data(c: CommuneResult, active_cats: List[str]):
            labels = [cat.capitalize() if cat not in ['sante', 'mobilite'] else cat.replace('e', 'é') for cat in active_cats]
            # Precise labels for radar
            label_map = {
                'emploi': 'Emploi',
                'logement': 'Logement',
                'education': 'Éducation',
                'sante': 'Santé',
                'inclusion': 'Inclusion',
                'mobilite': 'Mobilité'
            }
            labels = [label_map.get(cat, cat.capitalize()) for cat in active_cats]
            
            vals = []
            for cat in active_cats:
                attr_name = cat_map.get(cat, cat)
                data = getattr(c, attr_name, None)
                if data and hasattr(data, 'cat_score'):
                    val = float(data.cat_score) if data.cat_score is not None else 0.0
                    vals.append(val * 100)
                else:
                    vals.append(0.0)
            
            if vals:
                vals.append(vals[0])
                labels.append(labels[0])
            return labels, vals

        labels_target, vals_target = get_radar_data(commune, active_cats)
        
        # Current City Data
        config = st.session_state.get('config')
        current_codgeo = config.commune_actuelle.code if config and hasattr(config.commune_actuelle, 'code') else (config.commune_actuelle if config else None)
        search_results: SearchResultsData = st.session_state.get('search_results')
        current_geo = search_results.current_geo if search_results else None
        
        # Initialize Figure
        import plotly.graph_objects as go
        fig = go.Figure()

        # Add trace for target city (Green)
        fig.add_trace(go.Scatterpolar(
            r=vals_target,
            theta=labels_target,
            fill='toself',
            name=libgeo,
            fillcolor='rgba(0, 98, 104, 0.5)', # Semi-transparent green
            line=dict(color='#006268'),
            hovertemplate='%{theta}: %{r:.1f}%<extra></extra>'
        ))

        # Add trace for current city (Blue) if available
        if search_results and search_results.current_geo:
            _, vals_current = get_radar_data(search_results.current_geo, active_cats)
            current_name = search_results.current_geo.name or "Actuel"
            
            fig.add_trace(go.Scatterpolar(
                r=vals_current,
                theta=labels_target, # Use same theta labels
                fill='toself',
                name=current_name,
                fillcolor='rgba(31, 119, 180, 0.4)', # Semi-transparent blue
                line=dict(color='#1f77b4'),
                hovertemplate='%{theta}: %{r:.1f}%<extra></extra>'
            ))

        fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=50, r=50, t=50, b=50)
        )
        
        st.plotly_chart(fig, width="stretch", config=None)
        
        current_label = current_geo.name if current_geo else "votre ville"
        st.caption(f"**Comparaison des profils** : la zone verte représente **{commune.name}**, la zone bleue **{current_label}**. Une plus grande surface indique une meilleure adéquation avec vos critères.", text_alignment="center")
        st.space('small')
        # --- Links ---
        c1, c2 = st.columns(2)
        # st.divider()
        

             
        with c1:
            if st.button("En savoir plus", key=f"btn_details_comm_{commune.codgeo}", width="stretch"):
                st.session_state.active_details_index = commune.codgeo
                st.rerun()
        with c2:
            if st.button("Contact local", key=f"btn_ccas_commune_{commune.codgeo}", icon=':material/phone:', type="secondary", width="stretch"):
                st.session_state.active_ccas_index = commune.codgeo
                st.rerun()
                
        # --- Feedback ---
        st.divider()
        col1, col2 = st.columns([3,2])
        with col1:
            st.text("Évaluez la pertinence de ce résultat :", text_alignment='right', width='stretch')
                
        with col2:
            fb_key = f"fb_result_{commune.codgeo}_{h}"
            
            def _on_result_feedback(cid, c_name, score):
                val = st.session_state.get(f"fb_result_{cid}_{h}")
                if val is not None:
                    try:
                        from ui.feedback import _submit_to_bq
                        context = json.dumps({"codgeo": cid, "libgeo": c_name, "score": score})
                        _submit_to_bq("Result Relevance", str(val + 1), context=context)
                        st.toast(f"Merci pour votre évaluation de {c_name} !")
                    except Exception as e:
                        logger.error(f"Failed to submit result feedback: {e}")
                    
            st.feedback("faces", key=fb_key, on_change=_on_result_feedback, args=(commune.codgeo, commune.name, commune.global_score))

        

def _produce_pitch_markdown(commune: CommuneResult, config: SearchCriterias) -> str:
    """Generates a summary "pitch" for a result, adapting to commune or bassin de vie."""
    pitch_md = []
    population = f"{commune.population:,}".replace(",", " ")

    # It's a commune
    libgeo = commune.name
    pitch_md.append(f'**{libgeo}** ({population} habitants) fait partie du bassin de vie de : **{commune.name_bdv}**.  ')

    score_percent = f"{commune.global_score * 100:.1f}%"
    pitch_md.append(f'\nLa correspondance avec le projet est évaluée à **{score_percent}**. ')

    # --- Top contributing criteria (from CommuneScoreDetail) ---
    all_scores = []
    for cat, details in commune.scores.items():
        all_scores.extend(details)
    
    # Sort by score_normalise desc
    sorted_scores = sorted(all_scores, key=lambda x: x.score_normalise, reverse=True)

    if sorted_scores:
        pitch_md.append(f"\nCette localité se distingue par :")
        count = 0
        for s in sorted_scores:
            if s.score_normalise > 0.4 and count < 4: # Only show significant points
                val_display = s.valeur_kpi
                if isinstance(val_display, (int, float)):
                    if isinstance(val_display, int) and val_display > 1000:
                        val_display = f"{val_display:,}".replace(",", " ")
                
                unit_str = f" {s.unit}" if s.unit and s.unit != 'None' else ""
                pitch_md.append(f"\n- **{s.label}** : {val_display}{unit_str}")
                count += 1

    return "".join(pitch_md)
