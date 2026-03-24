
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
import logging
import string
from utils.data_loader import ensure_data_initialized
from core.scoring import ScoringEngine

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
    # Unique session key for this city's analysis
    h = search_criterias.compute_hash()
    cache_key = f"ia_analysis_{h}_{codgeo}"
    
    if cache_key not in st.session_state['app_data']:
        # F-IA: Automate trigger on open
        with st.spinner(f"Les experts analysent {nom}, veuillez patienter (environ 15 à 30s)..."):
            from agents.utils import run_async_safe
            
            state_dict = {
                "search_criteria": search_criterias.model_dump(),
                "is_interview_complete": True,
                "execution_mode": "full_analysis",
                "focus_city": {"name": nom, "codgeo": codgeo},
                "messages": [{"role": "user", "content": f"Fais une analyse complète pour {nom}."}]
            }
            try:
                final_state = run_async_safe(state_dict)
                syn_msg = final_state.get("messages", [])[-1]["content"] if final_state.get("messages") else "Pas de synthèse générée."
                st.session_state['app_data'][cache_key] = {
                    "synthesis": syn_msg,
                    "chat": []
                }
                # Within a fragment, rerun() only reruns the fragment
                st.rerun()
            except Exception as e:
                st.error(f"Erreur lors de la génération: {str(e)}")

    if cache_key in st.session_state['app_data']:
        data_cache = st.session_state['app_data'][cache_key]
        st.markdown(data_cache["synthesis"])
        
        st.divider()
        st.markdown(f"#### Poser une question sur {nom}")
        
        for msg in data_cache["chat"]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        
        question = st.chat_input(f"Ex: Quelles associations facilitent le logement à {nom} ?", key=f"chat_input_ia_{codgeo}")
        if question:
            data_cache["chat"].append({"role": "user", "content": question})
            with st.chat_message("user"):
                st.markdown(question)
            
            with st.spinner("Recherche de la réponse en cours..."):
                from agents.utils import run_async_safe
                state_dict = {
                    "search_criteria": search_criterias.model_dump(),
                    "is_interview_complete": True,
                    "execution_mode": "specific_ask",
                    "focus_city": {"name": nom, "codgeo": codgeo},
                    "messages": data_cache["chat"]
                }
                try:
                    final_state = run_async_safe(state_dict)
                    answer = final_state.get("messages", [])[-1]["content"] if final_state.get("messages") else "Pas de réponse."
                    data_cache["chat"].append({"role": "assistant", "content": answer})
                    with st.chat_message("assistant"):
                        st.markdown(answer)
                    st.rerun() # Refresh fragment to show answer and clear input
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
    if 'processed_gdf' not in st.session_state or index not in st.session_state.processed_gdf.index:
        st.error("Données de la ville introuvables.")
        return
        
    row = st.session_state.processed_gdf.loc[index]
    # We need to compute details for the specific city
    # In the results page, this is usually done during the list render.
    # We re-calculate it here to ensure it's available for the dialog.
    engine = st.session_state.get('engine')
    if not engine:
        # Fallback to creating one if not in session state (should be there)
        # But safer to error if we are in results list
        st.error("Moteur de recherche non initialisé.")
        return
        
    details = engine.format_city_details(row, config=st.session_state.config)
    
    identity = details.get('identity', {})
    nom = identity.get('nom', 'cette ville')
    codgeo = identity.get('codgeo')
    
    st.header(f"Analyse OD&IS pour {nom}")
    
    search_criterias = st.session_state.config
    ia_analysis_content(nom, codgeo, search_criterias)

@st.fragment(run_every=3.0)
def ai_pitch_container(main_code: str, h: str):
    """Module-level fragment to avoid redefinition issues and use global results."""
    from agents.utils import odis_get_bg_result
    scorer_res = odis_get_bg_result(h)
    pitch_for_city = ""
    
    if scorer_res is None:
        # Still running in background
        st.info("✨ _Récupération des points forts pour cette ville..._")
    else:
        if isinstance(scorer_res, dict) and "pitches" in scorer_res:
            pitch_for_city = scorer_res["pitches"].get(main_code, "")
        elif isinstance(scorer_res, str):
            pitch_for_city = scorer_res
            
        if pitch_for_city:
            st.markdown(pitch_for_city)

@st.dialog("Centre Communal d'Action Sociale", width="large", on_dismiss=_on_ccas_dialog_dismiss)
def show_ccas_dialog(index: Any):
    if 'processed_gdf' not in st.session_state or index not in st.session_state.processed_gdf.index:
         st.error("Données de la ville introuvables.")
         return
         
    row = st.session_state.processed_gdf.loc[index]
    codgeo = str(row['codgeo']) if 'codgeo' in row else str(index)
    libgeo = row.get('libgeo', 'cette ville')
    structures_df = st.session_state['app_data'].get('structures_ccas', pd.DataFrame())
    
    target_codes = [codgeo.strip()]
    # Include binome if present
    if row.get('binome') and row.get('codgeo_binome'):
        target_codes.append(str(row['codgeo_binome']).strip())

    if not structures_df.empty and 'codgeo' in structures_df.columns:
        # Filter with clean string types
        subset = structures_df[structures_df['codgeo'].isin(target_codes)].copy()
        
        if not subset.empty:
             # For ccas, we just show them all for the commune/binome
             st.subheader(f"Contacts locaux pour {libgeo}")
             
             for _, struct in subset.iterrows():
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
                     
                 # Separator AFTER
                 st.markdown("---")
        else:
             st.info(f"Aucune structure CCAS/CIAS référencée (avec contact) pour {libgeo}.")
    else:
        st.warning("Données structures non disponibles.")

@st.dialog(title="Détails du Territoire", width="large", on_dismiss=_on_details_dialog_dismiss)
def show_details_dialog(index: Any):
    """Displays thematic details for a city in a large modal."""
    if 'processed_gdf' not in st.session_state or index not in st.session_state.processed_gdf.index:
        st.error("Données de la ville introuvables.")
        return
        
    row = st.session_state.processed_gdf.loc[index]
    engine = st.session_state.get('engine')
    if not engine:
        st.error("Moteur de recherche non initialisé.")
        return
        
    details = engine.format_city_details(row, config=st.session_state.config)
    
    if not details:
        st.error("Détails non disponibles.")
        return

    # --- Header ---
    identity = details.get('identity', {})
    st.markdown(f"## 📍 {identity.get('nom', 'Inconnu')} (code INSEE: {identity.get('codgeo', 'N/A')})")
    
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
                st.metric("Score Global", f"{float(score_gl)*100:.1f}%", help="Adéquation globale avec votre projet de vie")

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

        # Sort by score_normalise desc to show strengths (Directly on the list to preserve types)
        scores = sorted(scores, key=lambda x: x.get('score_normalise', 0.0), reverse=True)
        
        for s in scores:
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
                    # Format value as string to be safe, but keep it as-is if it's already an int
                    val_display = s['valeur_kpi']
                    if isinstance(val_display, (int, float)) and pd.notna(val_display):
                         # If it's a large integer (like population), add spaces for readability
                         if isinstance(val_display, int) and val_display > 1000:
                             st.markdown(f"### {val_display:,}".replace(",", " "))
                         else:
                             st.markdown(f"### {val_display}")
                    else:
                         st.markdown(f"### {val_display}")
                    
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
                    with st.expander(f"Offres correspondant au projet ({matching_total})", expanded=True):
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
                        
                # --- New SIAE Jobs (F-39) ---
                siae_data = emploi_data.get('siae', {})
                if siae_data and siae_data.get('total', 0) > 0:
                    # st.divider()
                    # st.markdown("#### :material/volunteer_activism: Offres d'Inclusion (SIAE)")
                    
                    matching_siae = siae_data.get('matching_summary', {})
                    if matching_siae:
                        # st.info(f"**{sum(matching_siae.values())} offres d'insertion** correspondent à votre recherche.")
                        with st.expander(f"Offres par les SIAE correspondant au projet ({sum(matching_siae.values())})", expanded=True):
                            for label, count in matching_siae.items():
                                st.write(f"• **{label}** : {count} offre{'s' if count > 1 else ''}")
                    else:
                        # st.write(f"{siae_data['total']} opportunités d'insertion identifiées dans d'autres domaines.")
                        with st.expander(f"Toutes les offres par les SIAE locales ({siae_data['total']})", expanded=False):
                            for label, count in siae_data.get('summary', {}).items():
                                st.write(f"• **{label}** : {count} offre{'s' if count > 1 else ''}")
                
                
                
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
        logement_data = details.get('logement', {})
        c1, c2 = st.columns([1, 1], gap="medium")
        with c2:
            st.markdown("#### :material/home: Indicateurs Logement")
            render_scores_for_category('logement')
        with c1:
            st.markdown("#### :material/info: Données Complémentaires")
            # Show J'Accueille host count if available
            j_count = logement_data.get('jaccueille_count', 0)
            if j_count > 0:
                 st.info(f"**{int(j_count)} accueillants** J'Accueille identifiés dans le bassin de vie.")

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
                
                # 3. ODIS Associations Directory (Refactored to RAG Categories)
                st.markdown("#### :material/groups: Associations de l'inclusion")
                
                # Fetch associations from BQ via RNARagService
                rna_service = st.session_state.get('rna_rag_service')
                codgeo = identity.get('codgeo')
                
                if rna_service and codgeo:
                    with st.spinner("Chargement des associations..."):
                        try:
                            # F-48: Expand search to the entire "bassin de vie"
                            bv_id = identity.get('bassin_de_vie')
                            odis = st.session_state.app_data.get('odis')
                            if odis is not None and bv_id:
                                # Get all codgeos belonging to the same bassin de vie
                                codgeos_in_bv = odis[odis['bassin_de_vie'] == bv_id].index.tolist()
                                if codgeo not in codgeos_in_bv:
                                    codgeos_in_bv.append(codgeo)
                                assos_raw = rna_service.get_associations_by_codgeo(codgeos_in_bv)
                            else:
                                # Fallback to single commune if BV not found
                                assos_raw = rna_service.get_associations_by_codgeo([codgeo])

                            if assos_raw:
                                # Separate Refugee-focused from others
                                refugee_assos_from_rag = [a for a in assos_raw if a.get('is_refugee_focused')]
                                other_assos_from_rag = [a for a in assos_raw if not a.get('is_refugee_focused')]

                                # Re-group others by primary_category
                                grouped_assos = {}
                                for a in other_assos_from_rag:
                                    cat = a.get('primary_category') or "Autres"
                                    if cat not in grouped_assos:
                                        grouped_assos[cat] = []
                                    grouped_assos[cat].append(a)
                                
                                # 1. Display Refugee Focused Associations (if any)
                                if refugee_assos_from_rag:
                                    with st.expander("Intégration des réfugiés & migrants", expanded=False):
                                        # Sort refugee associations by name for readability
                                        refugee_assos_from_rag = sorted(refugee_assos_from_rag, key=lambda x: str(x['name']))
                                        for asso in refugee_assos_from_rag:
                                            name = string.capwords(str(asso['name']).lower())
                                            url = f"https://www.assoce.fr/waldec/{asso['id']}"
                                            desc = str(asso['description']).strip() if pd.notna(asso.get('description')) else ""
                                            
                                            if desc.lower() in ["nan", "none", "null"]:
                                                desc = ""
                                            
                                            if desc:
                                                if len(desc) > 200:
                                                    desc = desc[:200].strip() + "..."
                                                desc = desc[0].upper() + desc[1:] if len(desc) > 1 else desc
                                                st.markdown(f"**{name}**: {desc} [En savoir plus]({url})")
                                            else:
                                                st.markdown(f"**{name}**: [En savoir plus]({url})")
                                
                                for cat, list_assos in sorted(grouped_assos.items()):
                                    with st.expander(f"{cat} ({len(list_assos)})", expanded=False):
                                        for asso in list_assos:
                                            name = string.capwords(str(asso['name']).lower())
                                            # st.write(f"**{name}**")
                                            
                                            # Link to assoce.fr
                                            url = f"https://www.assoce.fr/waldec/{asso['id']}"
                                            
                                            desc = str(asso['description']).strip() if pd.notna(asso.get('description')) else ""
                                            if desc.lower() in ["nan", "none", "null"]:
                                                desc = ""
                                            
                                            if desc:
                                                if len(desc) > 200:
                                                    desc = desc[:200].strip() + "..."
                                                # Capitalize first letter of description for better look
                                                desc = desc[0].upper() + desc[1:] if len(desc) > 1 else desc
                                                st.markdown(f"**{name}**: {desc} [En savoir plus]({url})")
                                            else:
                                                st.markdown(f"**{name}**: [En savoir plus]({url})")
                            else:
                                st.info("Aucune association répertoriée pour cette commune.")
                        except Exception as e:
                            st.warning(f"Impossible de charger les associations : {e}")
                else:
                    st.warning("Le service de recherche d'associations n'est pas disponible.")
        with c2:
            st.markdown("#### :material/diversity_3: Indicateurs Inclusion")
            render_scores_for_category('inclusion')



        # def clear_processed_gdf():
        #     st.session_state['processed_gdf'] = None

@st.dialog("Confirmer la réinitialisation")
def confirm_reset_dialog():
    # st.warning("⚠️ Cette action réinitialisera tous vos critères de recherche.")
    st.write("Cette action réinitialisera tous vos critères de recherche. Souhaitez-vous vraiment retourner à l'accueil et effacer vos saisies ?")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Oui", width="stretch"):
            # 1. Radical cleanup: clear EVERYTHING except heavy datasets, auth, and essential UI state
            to_preserve = {'app_data', '_data_hash', 'rna_rag_service', 'rna_rag_status', 'password_correct', 'username', 'highlighted_result', 'config'}
            all_keys = list(st.session_state.keys())
            for k in all_keys:
                if k not in to_preserve:
                    del st.session_state[k]

            # 2. Specific cleanup inside app_data (clear city analysis cache)
            if 'app_data' in st.session_state:
                ia_keys = [k for k in st.session_state['app_data'].keys() if str(k).startswith('ia_analysis_')]
                for k in ia_keys:
                    del st.session_state['app_data'][k]

            # 3. Redirect to home
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
        confirm_reset_dialog()
        
def render_localisation_form() -> None:
    """Renders the UI for the 'Localisation Actuelle' form section."""
    app_data = st.session_state.app_data
    dept_details = app_data.get('dept_details', {})
    options_dep = app_data['coddep_set']
    
    departement_actuel = st.selectbox(
        "Département", 
        options_dep, 
        key="ui_departement",
        format_func=lambda x: f"{x} - {dept_details.get(x, {}).get('label', x)}" if dept_details else x
    )
    
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
                # st.toggle("Prioritaire", key=f"ui_priority_edu_{i}", help="Donne plus de poids à ce critère")

def render_employment_form() -> None:
    """Renders the UI for the 'Emploi & Formation' form section."""
    inject_custom_css()
    app_data = st.session_state.app_data
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
        if "ui_type_logement" in st.session_state and st.session_state["ui_type_logement"] in housing_type_options:
            default_housing_idx = housing_type_options.index(st.session_state["ui_type_logement"])
        else:
            default_housing_idx = housing_type_options.index("appt_all")
        st.markdown("\n\n")
        st.selectbox(
            "Si location quel type de logement ?",
            options=housing_type_options,
            format_func=lambda x: cfg.HOUSING_TYPE_OPTIONS[x],
            index=default_housing_idx,
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
    app_data = st.session_state.app_data
    
    col1, col2 = st.columns(2)
    with col2:
        # --- 1. Affinités (Loisirs & Intérêts) ---
        st.subheader("Associations Locales (Solidarité, Loisirs, Culture)")
        st.text("Sélectionnez vos centres d'intérêt pour identifier les territoires avec un tissu associatif correspondant.")
        
        # Load pre-enriched waldec_index
        if 'waldec_index' in st.session_state.app_data:
            waldec_index = st.session_state.app_data['waldec_index']
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
    app_data = st.session_state['app_data']
    dept_details = app_data.get('dept_details', {})
    regions_dict = app_data.get('regions_names', {})
    is_france = False

    # 1. Region & Department Selectors
    # Defaults based on current localization
    current_dept_code = st.session_state.get('ui_departement')
    current_reg_code = dept_details.get(current_dept_code, {}).get('reg_code')
    
    region_codes = ['france'] + sorted(regions_dict.keys())
    
    # F-48: Fix st.selectbox warning by aligning index with session_state if present
    if "ui_mobility_region" in st.session_state and st.session_state["ui_mobility_region"] in region_codes:
        default_reg_idx = region_codes.index(st.session_state["ui_mobility_region"])
    else:
        try:
            # Default to current region, if current_reg_code is not in dict, use first option (France)
            default_reg_idx = region_codes.index(current_reg_code) if current_reg_code in region_codes else 0
        except ValueError:
            default_reg_idx = 0
            
    selected_region_code = st.selectbox(
        "Région",
        region_codes,
        format_func=lambda x: regions_dict.get(x, "France Métropolitaine") if x != 'france' else "France Métropolitaine",
        key="ui_mobility_region",
        index=default_reg_idx
    )
    
    is_france = (selected_region_code == 'france')

    # Filter departments by selected region
    if not is_france:
        depts_in_region = [
            code for code, details in dept_details.items() 
            if details.get('reg_code') == selected_region_code
        ]
        depts_in_region.sort()
        
        # Options: "Toute la région" + departments
        dept_options = ["Toute la région"] + depts_in_region
        
        # F-48: Fix st.selectbox warning by aligning index with session_state if present
        if "ui_mobility_dept" in st.session_state and st.session_state["ui_mobility_dept"] in dept_options:
            default_dept_idx = dept_options.index(st.session_state["ui_mobility_dept"])
        else:
            try:
                default_dept_idx = dept_options.index(current_dept_code) if current_dept_code in dept_options else 0
            except ValueError:
                default_dept_idx = 0

        st.selectbox(
            "Département",
            dept_options,
            format_func=lambda x: f"{x} - {dept_details.get(x, {}).get('label', x)}" if x != "Toute la région" else x,
            key="ui_mobility_dept",
            index=default_dept_idx
        )
    else:
        st.info("Recherche sur l'ensemble du territoire métropolitain.")

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
    if "ui_weight_profile" in st.session_state and st.session_state["ui_weight_profile"] in weight_profiles:
        default_profile_idx = weight_profiles.index(st.session_state["ui_weight_profile"])
    else:
        default_profile_idx = 0

    st.text('Pour améliorer la pertinence des résultats de la recherche, vous pouvez ajuster les poids des différentes catégories de critères de recherche en utilisant soit un profil pré-défini (recommandé) soit une pondération sur-mesure.')

    col1, col2 = st.columns(2)
    with col1:
        st.selectbox(
            "Profils prédéfinis",
            options=weight_profiles,
            key="ui_weight_profile",
            on_change=_update_weights_from_profile,
            index=default_profile_idx
        )
        # New "Expert Mode" toggle
        st.toggle("Profil personalisé", key="ui_expert_weights", value=False)
    
    with col2:
       
        
        # if st.session_state.get('ui_expert_weights'):
            # st.info("Ajustez finement l'importance de chaque catégorie.")
        
        st.select_slider("Education", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_education', 50), disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_education", on_change=lambda: st.session_state.setdefault('processed_gdf', None))
        st.select_slider("Projet Pro", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_emploi', 50), disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_emploi", on_change=lambda: st.session_state.setdefault('processed_gdf', None))
        st.select_slider("Logement", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_logement', 50), disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_logement", on_change=lambda: st.session_state.setdefault('processed_gdf', None))
        st.select_slider("Inclusion", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_inclusion', 50), disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_inclusion", on_change=lambda: st.session_state.setdefault('processed_gdf', None))
        st.select_slider("Santé", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_sante', 50), disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_sante", on_change=lambda: st.session_state.setdefault('processed_gdf', None))
        st.select_slider("Mobilité", cfg.POIDS_OPTIONS, 
                        value=st.session_state.get('ui_poids_mobilité', 50), disabled=not st.session_state.get('ui_expert_weights'),
                        key="ui_poids_mobilité", on_change=lambda: st.session_state.setdefault('processed_gdf', None))
        # else:
        #     st.caption("Utilisez un profil prédéfini ci-dessus ou activez le mode personnalisé pour un réglage fin.")

def display_input_tabs(demo_data: Optional[Dict[str, Any]] = None) -> None:
    """Displays the main tabs for user input, composed of modular rendering functions."""
    inject_custom_css()
    
    tab_localisation, tab_foyer, tab_edu, tab_emploi, tab_logement, tab_sante, tab_autres, tab_notes, tab_profile = st.tabs([
        'Localisation', 'Situation familiale', 'Education', 'Projet Professionnel', 'Logement', 'Santé', 'Inclusion', 'Autres', 'Profil'
    ])
    with tab_localisation:
        col1, col2 = st.columns(2)
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
    app_data = st.session_state['app_data']
    
    # Location
    commune_codgeo = app_data['depcom_df'][
        (app_data['depcom_df'].dep_code == st.session_state['ui_departement']) & 
        (app_data['depcom_df'].libgeo == st.session_state['ui_commune'])
    ].index[0]
    
    libgeo = st.session_state['ui_commune']
    commune_actuelle = CriteriaItem(code=str(commune_codgeo), label=str(libgeo))

    # New Mobility Logic
    selected_region = st.session_state.get('ui_mobility_region')
    if selected_region == 'france':
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

    # Employment (Enrich with CriteriaItem)
    rome_index = app_data.get('rome_index', pd.DataFrame())
    codes_metiers = []
    for i in range(st.session_state['ui_nb_adultes']):
        raw_codes = st.session_state[f"ui_metiers_adult_{i}"]
        enriched_list = []
        for code in raw_codes:
            label = rome_index.loc[code, 'label'] if not rome_index.empty and code in rome_index.index else str(code)
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_metiers.append(enriched_list)
        
    form_index = app_data.get('codformations_index', pd.DataFrame())
    codes_formations = []
    for i in range(st.session_state['ui_nb_adultes']):
        raw_codes = st.session_state[f"ui_formations_adult_{i}"]
        enriched_list = []
        for code in raw_codes:
            label = form_index.loc[code, 'label'] if not form_index.empty and code in form_index.index else str(code)
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_formations.append(enriched_list)

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
    # session_state['ui_inc_asso_add_selection'] should be a list of CriteriaItem
    # but for tests, it might be a list of strings
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
                    # In current logic, item.code is just the first match
                    code_str = str(matches.index[0])
            inc_assos_mapped.append(CriteriaItem(code=code_str, label=str(item)))


    # Type Logement Enrich
    type_log = None
    ui_type_log = st.session_state.get('ui_type_logement', 'appartement_toutes')
    if ui_type_log in cfg.HOUSING_TYPE_OPTIONS:
        type_log = CriteriaItem(code=ui_type_log, label=cfg.HOUSING_TYPE_OPTIONS[ui_type_log])

    return SearchCriterias(
        poids_emploi=st.session_state['ui_poids_emploi'],
        poids_logement=st.session_state['ui_poids_logement'],
        poids_education=st.session_state['ui_poids_education'],
        poids_inclusion=st.session_state['ui_poids_inclusion'],
        poids_sante=st.session_state['ui_poids_sante'],
        poids_mobilité=st.session_state['ui_poids_mobilité'],
        criteria_weights=criteria_weights,
        
        commune_actuelle=commune_actuelle,
        loc_search_area=loc_search_area,
        loc_search_code=loc_search_code,
        nb_adultes=st.session_state['ui_nb_adultes'],
        nb_enfants=st.session_state['ui_nb_enfants'],
        hebergement_cible=heb_sel,
        logement=st.session_state['ui_logement'],
        type_logement=type_log,
        
        codes_metiers=codes_metiers,
        codes_formations=codes_formations,
        classe_enfants=classe_enfants,
        besoin_sante=st.session_state['ui_besoin_sante'],
        
        inc_services_add_selection=inc_services_mapped,
        inc_asso_add_selection=inc_assos_mapped,
        notes_qualitatives=[st.session_state.get('ui_notes_qualitatives', "")] if st.session_state.get('ui_notes_qualitatives') else []
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
    st.session_state.active_details_index = rank
    st.rerun()

def _show_ia_dialog_callback(rank: int) -> None:
    """Callback to compute and show AI analysis modal."""
    st.session_state.active_ia_city_index = rank
    st.rerun()

def _show_ccas_dialog_callback(rank: int) -> None:
    """Callback to compute and show CCAS dialog."""
    st.session_state.active_ccas_index = rank
    st.rerun()

def get_person_accompanied_str() -> str:
    if st.session_state.get('ui_nom'):
        return f"de {st.session_state.ui_nom}"
    return "de la personne accompagnée"

def display_results_list(display_gdf: Optional[pd.DataFrame] = None) -> None:
    """Renders the list of search results or the detailed view for the highlighted result."""
    gdf = display_gdf if display_gdf is not None else st.session_state.get('processed_gdf')
    if gdf is None or gdf.empty:
        st.info("Aucun résultat à afficher.")
        return

    # Handle Active Dialogs (at page/list rendering level)
    if st.session_state.get('active_ia_city_index') is not None:
        show_ia_analysis_dialog(st.session_state.active_ia_city_index)
        
    if st.session_state.get('active_details_index') is not None:
        show_details_dialog(st.session_state.active_details_index)
        
    if st.session_state.get('active_ccas_index') is not None:
        show_ccas_dialog(st.session_state.active_ccas_index)

    st.subheader("Meilleurs résultats")
    st.text("Cliquez sur un résultat pour comprendre le détail du score")
    st.markdown('<style> [class*="st-key-button_top"] .stButton button div, [class*="st-key-button_top"] .stButton button p { justify-content: flex-start !important; text-align: left !important; width: 100%; } </style>', unsafe_allow_html=True)

    top_n = 5
    df = gdf
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
        population = f"{row['population']:,.0f}".replace(",", " ")
        libgeo = row.get('libgeo', row.get('libelle_bassin_de_vie', 'Localité'))
        score_percent = f"{row['weighted_score'] * 100:.1f}%"
        
        st.markdown(f"**{libgeo}** ({population} habitants) fait partie du bassin de vie de : **{row.get('libelle_bassin_de_vie', 'N/A')}**.  \nLa correspondance avec le projet est évaluée à **{score_percent}**.")
        
        search_criterias = st.session_state.config
        h = search_criterias.compute_hash()
        
        # --- AI Pitch Fragment ---
        ai_pitch_container(main_code, h)
        
        # F-IA: AI Dialog Trigger (Session State based)
        col1, col2, col3 = st.columns([1, 2, 1])
        with col2:
            st.markdown('<style> [class*="st-key-btn_ia"] .stButton button { background-color: #F5D819; color: #1B4429; } </style>', unsafe_allow_html=True)
            if st.button("Analyse Complète OD&IS", key=f"btn_ia_comm_{row.name}", icon=':material/bolt:', width="content", type="primary"):
                st.session_state.active_ia_city_index = row.name
                st.rerun()

        # --- Radar Chart with Comparison ---
        def get_radar_data(row_data):
            cols = [col for col in row_data.index if col.endswith('_cat_score')]
            vals = list(row_data[cols].values * 100)
            labels = [col.split('_')[0].capitalize() for col in cols]
            # Close the loop
            if vals:
                vals.append(vals[0])
                labels.append(labels[0])
            return labels, vals

        labels_target, vals_target = get_radar_data(row)
        
        # Current City Data
        config = st.session_state.get('config')
        current_codgeo = config.commune_actuelle.code if config and hasattr(config.commune_actuelle, 'code') else (config.commune_actuelle if config else None)
        
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
        if current_codgeo and current_codgeo in st.session_state.processed_gdf.index:
            row_current = st.session_state.processed_gdf.loc[current_codgeo]
            _, vals_current = get_radar_data(row_current)
            
            fig.add_trace(go.Scatterpolar(
                r=vals_current,
                theta=labels_target, # Use same theta labels
                fill='toself',
                name=f"Actuel ({config.commune_actuelle.label})",
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
        st.caption(f"**Comparaison des profils** : la zone verte représente **{libgeo}**, la zone bleue représente la **{config.commune_actuelle.label}**. Une plus grande surface indique une meilleure adéquation avec vos critères.")

        # --- Links ---
        # st.divider()
        

             
        c1, c2 = st.columns(2)
        with c1:
            if st.button("En savoir plus", key=f"btn_details_comm_{row.name}", width="stretch"):
                st.session_state.active_details_index = row.name
                st.rerun()
        with c2:
            if st.button("Contact local", key=f"btn_ccas_commune_{row.name}", icon=':material/phone:', type="secondary", width="stretch"):
                st.session_state.active_ccas_index = row.name
                st.rerun()
                
        # --- Feedback ---
        st.divider()
        st.caption("Évaluez la pertinence de ce résultat :")
        fb_key = f"fb_result_{main_code}_{h}"
        
        def _on_result_feedback(cid, c_name, score):
            val = st.session_state.get(f"fb_result_{cid}_{h}")
            if val is not None:
                import json
                try:
                    from ui.feedback import _submit_to_bq
                    context = json.dumps({"codgeo": cid, "libgeo": c_name, "score": score})
                    _submit_to_bq("Result Relevance", str(val + 1), context=context)
                    st.toast(f"Merci pour votre évaluation de {c_name} !")
                except Exception as e:
                    logger.error(f"Failed to submit result feedback: {e}")
                    
        st.feedback("stars", key=fb_key, on_change=_on_result_feedback, args=(main_code, libgeo, row.get('weighted_score')))

        

def _produce_pitch_markdown(row: pd.Series, config: SearchCriterias, scores_cat: pd.DataFrame) -> str:
    """Generates a summary "pitch" for a result, adapting to commune or bassin de vie."""
    pitch_md = []
    population = f"{row['population']:,.0f}".replace(",", " ")

    # It's a commune
    libgeo = row.get('libgeo', row.get('libelle_bassin_de_vie', 'Localité'))
    pitch_md.append(f'**{libgeo}** ({population} habitants) fait partie du bassin de vie de : **{row.get("libelle_bassin_de_vie", "N/A")}**.  ')

    score_percent = f"{row['weighted_score'] * 100:.1f}%"
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
