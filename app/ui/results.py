import streamlit as st
import pandas as pd
import logging
import config as cfg
from core.models import CommuneResult, CommuneScoreDetail, SearchResultsData, SearchCriterias
from utils.data_loader import get_app_data
from agents.utils import odis_get_bg_result, launch_background_city_analysis
from ui.components import inject_custom_css
from typing import List, Optional, Any
import plotly.graph_objects as go
from core import maps

# Configure Logging
logger = logging.getLogger("ui.results")

# --- Dialog Dismiss Callbacks (Necessary for modular UI state management) ---
def _on_ia_dialog_dismiss():
    st.session_state.active_ia_city_index = None

def _on_details_dialog_dismiss():
    st.session_state.active_details_index = None

def _on_ccas_dialog_dismiss():
    st.session_state.active_ccas_index = None

def ia_analysis_content(nom: str, codgeo: str, search_criterias: Any):
    """Component to display AI synthesis and handle follow-up questions."""
    
    try:
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

        # Use a unique key for background tracking
        h = st.session_state.get('active_search_hash')
        task_key = f"analysis_{h}_{codgeo}"

        def _merge_agent_results(final_state_results):
            """Helper to merge graph state results back into session state."""
            if not final_state_results: return
            
            # 🧪 SOTA: Robust merging with type checking to prevent page-level crashes
            def _get_field(obj, field, default=None):
                if isinstance(obj, dict): return obj.get(field, default)
                return getattr(obj, field, default)
            
            # 1. Update Global Brief
            st.session_state.search_results.odis_brief = _get_field(final_state_results, "odis_brief", st.session_state.search_results.odis_brief)
            
            # 2. Find and update the specific focus city
            new_results = _get_field(final_state_results, "results", [])
            for city_data in new_results:
                city_codgeo = _get_field(city_data, "codgeo")
                if str(city_codgeo) == str(codgeo):
                    new_synth = _get_field(city_data, "odis_synthesis", [])
                    if new_synth:
                        commune.odis_synthesis = new_synth
                    
                    # Expert analysis is a dict, we update it
                    expert_data = _get_field(city_data, "expert_analysis", {})
                    if expert_data and isinstance(expert_data, dict):
                        commune.expert_analysis.update(expert_data)
                    
                    new_pitch = _get_field(city_data, "scorer_pitch")
                    if new_pitch:
                        commune.scorer_pitch = new_pitch
                    break

        # 2. Trigger analysis if synthesis is missing
        if not commune.odis_synthesis:
            # Polling Fragment for Initial Synthesis
            @st.fragment(run_every=3.0)
            def polling_synthesis_fragment():
                status_data = odis_get_bg_result(task_key)
                
                if not status_data:
                    # First run: start the thread
                    launch_background_city_analysis(nom, codgeo, search_criterias, results, h)
                    st.caption("Lancement de la synthèse...")
                elif status_data.get("status") == "running":
                    st.caption("Préparation de la synthèse (~30s)...")
                elif status_data.get("status") == "error":
                    st.error(f"Erreur d'analyse : {status_data.get('error')}")
                    if st.button("Réessayer"):
                        del st.session_state.odis_bg_store[task_key]
                        st.rerun(scope="fragment")
                elif status_data.get("status") == "done":
                    # Success! Merge and rerun the whole component
                    _merge_agent_results(status_data.get("result"))
                    st.rerun(scope="fragment")

            polling_synthesis_fragment()
            return # Hide rest of UI until synthesis is ready

        # 3. Display Synthesis and Chat History
        history = list(commune.odis_synthesis)
        
        # Container for chat history
        history_container = st.container()
        with history_container:
            for msg in history:
                with st.chat_message(msg["role"]):
                    st.markdown(msg["content"])
        
        # Check if a follow-up chat task is running
        chat_task_key = f"chat_active_flag_{codgeo}"
        
        # 4. Handle follow-up questions
        question = st.chat_input(f"Ex: Quelles associations facilitent le logement à {nom} ?", key=f"chat_input_ia_{codgeo}")
        
        if question:
            # Note: the actual graph run will return the full history including this message
            launch_background_city_analysis(nom, codgeo, search_criterias, results, h, messages=history + [{"role": "user", "content": question}])
            st.session_state[chat_task_key] = True 
            st.rerun(scope="fragment")

        # Shared Polling Fragment for Follow-up Chat
        if st.session_state.get(chat_task_key):
            @st.fragment(run_every=3.0)
            def polling_chat_fragment():
                status_data = odis_get_bg_result(task_key)
                
                if status_data and status_data.get("status") == "done":
                    # Check if the result is NEW (i.e. has more messages than current history)
                    # We merge and then disable the polling
                    _merge_agent_results(status_data.get("result"))
                    del st.session_state[chat_task_key]
                    st.rerun(scope="fragment")
                elif status_data and status_data.get("status") == "error":
                    st.error(f"Erreur de l'agent : {status_data.get('error')}")
                    del st.session_state[chat_task_key]
                else:
                    with st.chat_message("assistant"):
                        st.write("✨ _Recherche de la réponse en cours (Job Hunter / Scouts)..._")
            
            polling_chat_fragment()
            
    except Exception as e:
        st.error(f"⚠️ Une erreur est survenue lors de l'affichage de l'analyse : {str(e)}")
        logger.error(f"❌ [PAGE-CRASH] ia_analysis_content: {e}", exc_info=True)

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

@st.fragment(run_every=3.0)
def ai_pitch_container(main_code: str, h: str):
    # 1. Try unified state first (Single source of truth)
    if 'search_results' in st.session_state and st.session_state.search_results:
        commune = st.session_state.search_results.get_by_code(main_code)
        if commune and commune.scorer_pitch:
            st.markdown(commune.scorer_pitch)
            return

    # 2. Fallback to background store with back-sync
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
                    st.rerun(scope="fragment")
            st.markdown(pitch_for_city)

def sync_background_data(commune: CommuneResult, h: Optional[str]):
    """
    Syncs both enrichment (associations) and pitches from the background store 
    back into the CommuneResult model for persistence.
    """
    if not h: return
    
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
                                st.rerun(scope="fragment")

                    # 2. Render UI
                    if inc_data.asso_inclusion_count > 0:
                        st.info(f"**{inc_data.asso_inclusion_count} associations** actives identifiées dans le bassin de vie.")
                        if inc_data.asso_refugee_count > 0:
                            st.success(f"**{inc_data.asso_refugee_count} association(s)** spécifiquement dédiée(s) aux réfugiés.")
                        

                        # Display Refugee associations from the model (secondary list)  
                        if inc_data.asso_refugee_list:
                            with st.expander("Intégration des réfugiés & migrants", expanded=True):
                                # Sort by local preference if needed (already sorted in scoring.py)
                                for asso in inc_data.asso_refugee_list:
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
                
        with c2:
            st.markdown("#### :material/diversity_3: Indicateurs Inclusion")
            render_scores_for_category('inclusion')

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

def _on_result_feedback(cid: str, c_name: str, score: float, fb_key: str) -> None:
    """Callback for st.feedback to submit relevance directly to BQ."""
    val = st.session_state.get(fb_key)
    if val is not None:
        # Avoid duplicate submission for the same selection state during reruns/fragment updates
        submission_key = f"last_submitted_{fb_key}"
        if st.session_state.get(submission_key) == val:
            return
            
        try:
            from ui.feedback import _submit_to_bq
            import json
            context = json.dumps({"codgeo": cid, "libgeo": c_name, "score": score})
            # st.feedback values are 0-4 (5 faces), we map to 1-5 for BQ
            if _submit_to_bq("Result Relevance", str(val + 1), context=context):
                st.session_state[submission_key] = val
                logger.info(f"✨ Feedback submitted for {c_name} ({cid}): {val + 1}")
        except Exception as e:
            logger.error(f"Failed to submit result feedback: {e}")

def display_results_list(display_gdf: Optional[pd.DataFrame] = None) -> None:
    """Renders the list of search results or the detailed view for the highlighted result."""
    h = st.session_state.get('active_search_hash')
    search_results: SearchResultsData = st.session_state.get('search_results')
    
    if not search_results or not search_results.results:
        st.info("Aucun résultat à afficher.")
        return

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
            if st.button("Analyse Avancée", key=f"btn_ia_comm_{commune.codgeo}", icon=':material/bolt:', width="content", type="primary"):
                st.session_state.active_ia_city_index = commune.codgeo
                st.rerun()

        # --- Radar Chart with Comparison ---
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
        
        search_results: SearchResultsData = st.session_state.get('search_results')
        
        fig = go.Figure()

        # Add trace for target city (Green)
        fig.add_trace(go.Scatterpolar(
            r=vals_target,
            theta=labels_target,
            fill='toself',
            name=libgeo,
            fillcolor='rgba(0, 98, 104, 0.5)', 
            line=dict(color='#006268'),
            hovertemplate='%{theta}: %{r:.1f}%<extra></extra>'
        ))

        # Add trace for current city (Blue) if available
        if search_results and search_results.current_geo:
            _, vals_current = get_radar_data(search_results.current_geo, active_cats)
            current_name = search_results.current_geo.name or "Actuel"
            
            fig.add_trace(go.Scatterpolar(
                r=vals_current,
                theta=labels_target,
                fill='toself',
                name=current_name,
                fillcolor='rgba(31, 119, 180, 0.4)',
                line=dict(color='#1f77b4'),
                hovertemplate='%{theta}: %{r:.1f}%<extra></extra>'
            ))

        fig.update_layout(
            polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
            margin=dict(l=50, r=50, t=50, b=50)
        )
        
        st.plotly_chart(fig, use_container_width=True, config=None)
        
        current_geo = search_results.current_geo if search_results else None
        current_label = current_geo.name if current_geo else "votre ville"
        st.caption(f"**Comparaison des profils** : la zone verte représente **{commune.name}**, la zone bleue **{current_label}**. Une plus grande surface indique une meilleure adéquation avec vos critères.", text_alignment="center")
        st.space('small')

        c1, c2 = st.columns(2)
        with c1:
            if st.button("En savoir plus", key=f"btn_details_comm_{commune.codgeo}", width="stretch"):
                st.session_state.active_details_index = commune.codgeo
                st.rerun()
        with c2:
            if st.button("Contact local", key=f"btn_ccas_commune_{commune.codgeo}", icon=':material/phone:', type="secondary", width="stretch"):
                st.session_state.active_ccas_index = commune.codgeo
                st.rerun()
                
        st.divider()
        with st.container(horizontal=True, horizontal_alignment="center", key=f"faces_feedback_container_{commune.codgeo}"):
            st.text("Évaluez la pertinence de ce résultat")
            fb_key = f"fb_result_{commune.codgeo}"
            st.feedback("faces", key=fb_key, on_change=_on_result_feedback, args=(commune.codgeo, commune.name, commune.global_score, fb_key), width="content")

