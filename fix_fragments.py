import re

with open("app/ui/results.py", "r") as f:
    text = f.read()

# 1. Insert module level fragments
module_fragments = """# --- Module Level Fragments for Stability ---
def _merge_agent_results(final_state_results, codgeo: str, commune: CommuneResult):
    \"\"\"Helper to merge graph state results back into session state.\"\"\"
    if not final_state_results: return
    
    # 🧪 SOTA: Robust merging with type checking to prevent page-level crashes
    def _get_field(obj, field, default=None):
        if isinstance(obj, dict): return obj.get(field, default)
        return getattr(obj, field, default)
    
    # 1. Update Global Brief
    import streamlit as st
    st.session_state.search_results.odis_brief = _get_field(final_state_results, "odis_brief", st.session_state.search_results.odis_brief)
    
    # 2. Find and update the specific focus city
    new_results = _get_field(final_state_results, "results", [])
    for city_data in new_results:
        city_codgeo = _get_field(city_data, "codgeo")
        if str(city_codgeo) == str(codgeo):
            new_synth = _get_field(city_data, "odis_synthesis", [])
            if new_synth:
                commune.odis_synthesis = new_synth
            
            expert_data = _get_field(city_data, "expert_analysis", {})
            if expert_data and isinstance(expert_data, dict):
                commune.expert_analysis.update(expert_data)
            
            new_pitch = _get_field(city_data, "scorer_pitch")
            if new_pitch:
                commune.scorer_pitch = new_pitch
            break

@st.fragment(run_every=2.0)
def polling_synthesis_fragment(task_key: str, nom: str, codgeo: str, search_criterias: Any, results: SearchResultsData, h: str, commune: CommuneResult):
    from agents.utils import odis_get_bg_result, launch_background_city_analysis
    status_data = odis_get_bg_result(task_key)
    if not status_data:
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
        _merge_agent_results(status_data.get("result"), codgeo, commune)
        st.rerun(scope="fragment")

@st.fragment(run_every=2.0)
def polling_chat_fragment(task_key: str, chat_task_key: str, codgeo: str, commune: CommuneResult):
    from agents.utils import odis_get_bg_result
    status_data = odis_get_bg_result(task_key)
    
    if status_data and status_data.get("status") == "done":
        _merge_agent_results(status_data.get("result"), codgeo, commune)
        del st.session_state[chat_task_key]
        st.rerun(scope="fragment")
    elif status_data and status_data.get("status") == "error":
        st.error(f"Erreur de l'agent : {status_data.get('error')}")
        del st.session_state[chat_task_key]
    else:
        with st.chat_message("assistant"):
            st.write("✨ _Recherche de la réponse en cours (Job Hunter / Scouts)..._")

@st.fragment(run_every=3.0)
def associations_polling_fragment(commune: CommuneResult, h: Optional[str]):
    from agents.utils import odis_get_bg_result
    inc_data = commune.inclusion
    import logging
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

    if inc_data.asso_inclusion_count > 0:
        st.info(f"**{inc_data.asso_inclusion_count} associations** actives identifiées dans le bassin de vie.")
        if inc_data.asso_refugee_count > 0:
            st.success(f"**{inc_data.asso_refugee_count} association(s)** spécifiquement dédiée(s) aux réfugiés.")

        if inc_data.asso_refugee_list:
            with st.expander("Intégration des réfugiés & migrants", expanded=True):
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

def ia_analysis_content"""

text = text.replace("def ia_analysis_content", module_fragments)

# 2. Cleanup `ia_analysis_content` internals
old_synth = r'''        def _merge_agent_results\(final_state_results\):.*?return # Hide rest of UI until synthesis is ready'''
new_synth = r'''        # 2. Trigger analysis if synthesis is missing
        if not commune.odis_synthesis:
            polling_synthesis_fragment(task_key, nom, codgeo, search_criterias, results, h, commune)
            return # Hide rest of UI until synthesis is ready'''
text = re.sub(old_synth, new_synth, text, flags=re.DOTALL)

old_chat = r'''        # Shared Polling Fragment for Follow-up Chat.*?polling_chat_fragment\(\)'''
new_chat = r'''        # Shared Polling Fragment for Follow-up Chat
        if st.session_state.get(chat_task_key):
            polling_chat_fragment(task_key, chat_task_key, codgeo, commune)'''
text = re.sub(old_chat, new_chat, text, flags=re.DOTALL)

# 3. Cleanup `show_details_dialog` internals
old_asso = r'''                @st.fragment\(run_every=3\.0\).*?# Call the fragment\s+associations_polling_fragment\(\)'''
new_asso = r'''                # Call the fragment\n                associations_polling_fragment(commune, h)'''
text = re.sub(old_asso, new_asso, text, flags=re.DOTALL)

# 4. Fix ai_pitch_container run_every
text = text.replace('@st.fragment(run_every=1.0)\ndef ai_pitch_container', '@st.fragment(run_every=2.0)\ndef ai_pitch_container')

with open("app/ui/results.py", "w") as f:
    f.write(text)

print("Replacement complete.")
