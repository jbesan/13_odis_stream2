import time
from typing import Any, Dict, List, Optional
import streamlit as st
from pydantic_ai import Agent
import threading
import logging
import logfire
from utils.logger import log_search_results
from services.telemetry import log_search_complete
from core.models import SearchCriterias, SearchCriterias, CriteriaItem
import pandas as pd

# Global storage for background tasks (Now restricted to session state for privacy)
def get_odis_bg_store() -> dict:
    """Returns a session-specific dictionary for background task results."""
    if 'odis_bg_store' not in st.session_state:
        st.session_state['odis_bg_store'] = {}
    return st.session_state['odis_bg_store']

def odis_get_bg_result(hash_val: str) -> Any:
    """Safely retrieves a background result from the global store."""
    return get_odis_bg_store().get(hash_val)

def sanitize_llm_markdown(text: str) -> str:
    """
    Cleans up common LLM artifacts in markdown strings, 
    specifically literal '\\n' strings and other escaping artifacts.
    """
    if not text:
        return ""
    
    # Handle literal double-escaped newlines
    # Some LLMs return "\\n" which becomes "\n" literal in Python
    # Some might return "\\\\n"
    res = text
    for _ in range(3):
        res = res.replace('\\r\\n', '\n').replace('\\n', '\n').replace('\\r', '\n')
    
    # Also handle literal markdown escaping if the LLM is too aggressive
    # (e.g. \" replaced by ")
    res = res.replace('\\"', '"').replace("\\'", "'")
    
    return res


def map_ui_config_to_search_criterias(config: SearchCriterias, app_data: Dict[str, Any]) -> SearchCriterias:
    """
    Converts a UI SearchCriterias into a Pydantic SearchCriterias object
    expected by the IA agents.
    """
    # 1. Commune Actuelle
    codgeo = config.commune_actuelle
    libgeo = app_data['odis'].loc[codgeo, 'libgeo'] if codgeo in app_data['odis'].index else str(codgeo)
    commune_actuelle = CriteriaItem(code=str(codgeo), label=str(libgeo))
    
    # 2. Métiers
    rome_index = app_data.get('rome_index', pd.DataFrame())
    codes_metiers = []
    for metier_list in config.codes_metiers:
        enriched_list = []
        for code in metier_list:
            label = rome_index.loc[code, 'label'] if not rome_index.empty and code in rome_index.index else str(code)
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_metiers.append(enriched_list)
        
    # 3. Formations
    form_index = app_data.get('codformations_index', pd.DataFrame())
    codes_formations = []
    for form_list in config.codes_formations:
        enriched_list = []
        for code in form_list:
            label = form_index.loc[code, 'label'] if not form_index.empty and code in form_index.index else str(code)
            enriched_list.append(CriteriaItem(code=str(code), label=str(label)))
        codes_formations.append(enriched_list)
        
    # 4. Inclusion Services
    inc_index = app_data.get('inclusion_services_index', pd.DataFrame())
    inc_services = []
    for code in config.inc_services_add_selection:
        label = inc_index.loc[code, 'label'] if not inc_index.empty and code in inc_index.index else str(code)
        inc_services.append(CriteriaItem(code=str(code), label=str(label)))
        
    # 5. Inclusion Associations
    import config as cfg
    inc_assos = []
    waldec_index = app_data.get('waldec_index', pd.DataFrame())
    for item in config.inc_asso_add_selection:
        if isinstance(item, CriteriaItem):
            inc_assos.append(item)
        else:
            # item is likely a label (string)
            code_str = "000"
            if not waldec_index.empty:
                matches = waldec_index[waldec_index['label'] == item]
                if not matches.empty:
                    code_str = str(matches.index[0])
            inc_assos.append(CriteriaItem(code=code_str, label=str(item)))
        
    # 6. Type Logement
    type_log = None
    if config.type_logement and config.type_logement in cfg.HOUSING_TYPE_OPTIONS:
        type_log = CriteriaItem(code=config.type_logement, label=cfg.HOUSING_TYPE_OPTIONS[config.type_logement])
        
    return SearchCriterias(
        commune_actuelle=commune_actuelle,
        loc_search_area=config.loc_search_area,
        loc_search_code=config.loc_search_code,
        nb_adultes=config.nb_adultes,
        nb_enfants=config.nb_enfants,
        classe_enfants=config.classe_enfants,
        codes_metiers=codes_metiers,
        codes_formations=codes_formations,
        inc_services_add_selection=inc_services,
        inc_asso_add_selection=inc_assos,
        hebergement_cible=config.hebergement_cible,
        logement=config.logement,
        type_logement=type_log,
        sante=config.besoin_sante,
        weight_profile=config.weight_profile,
        criteria_weights=config.criteria_weights,
        notes_qualitatives=[],
        
        # Org Specifics
        org_context=getattr(config, 'org_context', None),
        org_strategic_locations=getattr(config, 'org_strategic_locations', []),
        org_strategic_locations_type=getattr(config, 'org_strategic_locations_type', 'departement'),
        poids_territoire=getattr(config, 'poids_territoire', 1.0)
    )

def launch_background_refiner(search_criterias: SearchCriterias, results_dict_ignored: dict, hash_val: str, top_cities: Optional[list] = None, current_geo: Optional[dict] = None, commune_pressentie: Optional[dict] = None, interaction_id: Optional[str] = None, username: Optional[str] = None):
    """
    Launches a background thread to generate the REFINER AI briefing and pitches.
    Stores the result in the cached global store.
    """
    # Get the store here (main thread) to ensure it's initialized in the cache
    store = get_odis_bg_store()
    
    # Capture Logfire context to propagate it to the background thread
    context = logfire.get_context()
    
    @logfire.instrument("Background Refiner: {hash_val}")
    def bg_refiner_task(results_store: dict, hash_val: str):
        # Attach the context from the main thread
        logfire.attach_context(context)
        
        logfire.info("Refiner background task started for hash: {search_hash}", search_hash=hash_val)
        import asyncio
        import os
        from google import genai
        from google.genai import types
        from agents.state import GraphState, ODISDeps
        from agents.refiner import refiner_agent
        from agents.agent_config import get_p_model
        from pydantic_ai import ModelSettings
        
        try:
            logging.debug(f"🚀 [BG] Starting background refiner for hash {hash_val}")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            
            client = genai.Client(
                api_key=api_key, 
                http_options=types.HttpOptions(
                    api_version="v1beta",
                    retry_options=types.HttpRetryOptions(attempts=3)
                )
            )
            
            # 3. Unified State Rehydration
            input_data = {
                "search_criteria": search_criterias,
                "search_results": {
                    "search_hash": hash_val,
                    "results": top_cities,
                    "current_geo": current_geo or (top_cities[0] if top_cities else None),
                    "commune_pressentie": commune_pressentie
                } if top_cities else None,
                "execution_mode": "full_analysis",
                "interaction_id": interaction_id or "unknown",
                "username": username or "unknown"
            }
            
            from agents.utils import rehydrate_graph_state
            state = rehydrate_graph_state(input_data)
            logging.info(f"🔍 [REFINER-DEBUG] commune_pressentie in input_data: {commune_pressentie is not None}")
            logging.info(f"🔍 [REFINER-DEBUG] commune_pressentie in rehydrated state: {state.search_results.commune_pressentie is not None if state.search_results else False}")
            deps = ODISDeps(state=state, client=client)
            model = get_p_model("refiner", client=client)
            
            async def run_agent():
                logging.debug(f"🚀 [BG] Calling refiner_agent.run for hash {hash_val}")
                return await refiner_agent.run(
                    "Génère le briefing du dossier et les explications des résultats.", 
                    deps=deps, 
                    model=model, 
                    model_settings=ModelSettings(max_tokens=4096)
                )
            
            try:
                result_run = loop.run_until_complete(run_agent())
                response_obj = result_run.output
                logging.debug(f"🚀 [BG] Refiner Agent call successful for hash {hash_val}")
                
                pitches_dict = {
                    "global": sanitize_llm_markdown(response_obj.global_pitch),
                    "pitches": {p.codgeo: sanitize_llm_markdown(p.pitch) for p in response_obj.pitches_per_city}
                }
                
                # Harmonized storage: merge with existing results if any
                current_val = results_store.get(hash_val, {})
                if not isinstance(current_val, dict): current_val = {}
                current_val["pitches"] = pitches_dict
                current_val["odis_brief"] = sanitize_llm_markdown(response_obj.odis_brief)
                current_val["status_refiner"] = "done"
                results_store[hash_val] = current_val
                
                logging.debug(f"✅ [BG] Background Refiner fully finished for hash {hash_val}")
            except Exception as e:
                logging.error(f"❌ [BG] Background Refiner Error for hash {hash_val}: {e}")
                current_val = results_store.get(hash_val, {})
                if not isinstance(current_val, dict): current_val = {}
                current_val["pitches_error"] = f"⚠️ L'analyse IA a échoué: {e}"
                current_val["status_refiner"] = "error"
                results_store[hash_val] = current_val
        except Exception as global_e:
            logging.error(f"❌ [BG] Background Refiner Setup Error for hash {hash_val}: {global_e}")
            current_val = results_store.get(hash_val, {})
            if not isinstance(current_val, dict): current_val = {}
            current_val["pitches_error"] = f"⚠️ L'analyse IA a échoué (Setup): {global_e}"
            current_val["status_refiner"] = "error"
            results_store[hash_val] = current_val
        finally:
            if 'loop' in locals():
                loop.close()
                
    # 4. Threading (Non-blocking)
    import threading
    thread = threading.Thread(target=bg_refiner_task, args=(store, hash_val))
    thread.daemon = True # Ensure it doesn't block exit
    thread.start()

def launch_background_enrichment(engine: Any, codgeos: List[str], hash_val: str):
    """
    Launches a background thread to fetch detailed associations for the search results.
    """
    store = get_odis_bg_store()
    
    def bg_enrichment_task(results_store: dict):
        try:
            logging.info(f"🚀 [ENRICH] Starting background enrichment for {len(codgeos)} communes (hash: {hash_val})")
            
            # Use the provided engine to prefetch
            # Note: engine is likely a ScoringEngine instance
            enrichment_data = engine.prefetch_associations(codgeos)
            
            # Merge into harmonized storage
            current_val = results_store.get(hash_val, {})
            if not isinstance(current_val, dict): current_val = {}
            current_val["enrichment"] = enrichment_data
            results_store[hash_val] = current_val
            
            logging.info(f"✅ [ENRICH] Background enrichment finished for hash {hash_val}")
        except Exception as e:
            logging.error(f"❌ [ENRICH] Background enrichment error for {hash_val}: {e}")
            
    thread = threading.Thread(target=bg_enrichment_task, args=(store,))
    thread.daemon = True
    thread.start()

def _curate_jobs_with_llm(
    jobs: List[Dict[str, Any]], 
    odis_brief: str, 
    notes_qualitatives: List[str],
    target_city: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """Curates a list of job offers using job_curator_agent based on candidate context.

    Args:
        jobs: List of job offer details dictionaries.
        odis_brief: Narrative summary of candidate's situation.
        notes_qualitatives: List of qualitative project notes.
        target_city: Optional CommuneResult representing the city ciblée.

    Returns:
        List of curated top 5 job offer details dictionaries.
    """
    if not odis_brief and not notes_qualitatives:
        logging.info("ℹ️ [JOBS-CURATE] No candidate context available. Bypassing LLM curation.")
        return jobs[:5]

    try:
        from agents.job_hunter import job_curator_agent, JOB_CURATOR_SYSTEM_PROMPT
        import asyncio
        import json

        # Format jobs for the LLM prompt
        jobs_list_str = ""
        for job in jobs:
            jobs_list_str += (
                f"- ID: {job['id']}\n"
                f"  Intitulé: {job['title']}\n"
                f"  Entreprise: {job.get('company') or 'Non spécifiée'}\n"
                f"  Type de contrat: {job.get('contract_label') or job.get('contract_type') or 'N/A'}\n"
                f"  Lieu: {job.get('location') or 'Non spécifié'}\n"
                f"  Description: {job.get('description') or 'Aucune'}\n"
                f"  Salaire: {job.get('salary') or 'Non spécifié'}\n\n"
            )

        # Build target city context using metadata-driven builder
        target_city_context = "Non spécifiée"
        if target_city:
            from agents.state import ODISContextBuilder
            city_ctx = ODISContextBuilder._auto_build_context(target_city, "agent_job_hunter")
            if isinstance(city_ctx, dict):
                target_city_context = json.dumps(city_ctx, ensure_ascii=False, indent=2)
            else:
                target_city_context = str(city_ctx)

        prompt = JOB_CURATOR_SYSTEM_PROMPT.format(
            briefing=odis_brief,
            target_city_context=target_city_context,
            notes_qualitatives=", ".join(notes_qualitatives) if notes_qualitatives else "Aucune",
            jobs_list=jobs_list_str
        )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        # Run the curator agent synchronously within the background thread
        result = loop.run_until_complete(job_curator_agent.run(prompt))
        selected_jobs_list = getattr(result.output, "selected_jobs", [])

        # Map selected IDs back to job dicts, keeping LLM relevance order
        curated_jobs = []
        seen_ids = set()
        for curated_item in selected_jobs_list:
            j_id = str(curated_item.job_id)
            if j_id in seen_ids:
                continue
            match = next((j for j in jobs if str(j["id"]) == j_id), None)
            if match:
                curated_jobs.append({**match, "job_brief": curated_item.job_brief})
                seen_ids.add(j_id)

        # Fill in with remaining jobs if LLM selected fewer than 5 but total is larger
        if len(curated_jobs) < 5 and len(jobs) > len(curated_jobs):
            for j in jobs:
                if len(curated_jobs) >= 5:
                    break
                j_id = str(j["id"])
                if j_id not in seen_ids:
                    curated_jobs.append({**j, "job_brief": None})
                    seen_ids.add(j_id)

        logging.info(f"✅ [JOBS-CURATE] LLM successfully curated {len(curated_jobs)} jobs.")
        return curated_jobs[:5]
    except Exception as e:
        logging.error(f"❌ [JOBS-CURATE] LLM job curation failed, falling back to distance sort: {e}", exc_info=True)
        return jobs[:5]


def launch_background_jobs_enrichment(codgeos: List[str], config: Any, hash_val: str, search_results: Optional[Any] = None):
    """Launches background threads in parallel (one per target commune) to fetch and curate job offers.

    Args:
        codgeos: List of geographic INSEE codes of target communes.
        config: The SearchCriterias configuration object or a list of ROME codes for legacy compatibility.
        hash_val: Unique MD5 search criteria hash.
        search_results: Optional SearchResultsData container for target city lookup.
    """
    store = get_odis_bg_store()
    
    # 1. Pre-initialize the jobs_enrichment dictionary for all communes to pending state
    current_val = store.get(hash_val, {})
    if not isinstance(current_val, dict): current_val = {}
    
    from core.models import SearchCriterias
    if isinstance(config, SearchCriterias):
        codes_metiers = config.codes_metiers
        
        # Build candidate profile summary directly using the metadata-driven odis_visibility system
        from agents.state import ODISContextBuilder
        ctx_dict = ODISContextBuilder._auto_build_context(config, "agent_job_hunter")
        
        profile_parts = []
        for label, val in ctx_dict.items():
            # Skip code lists that are handled separately in query fetching
            if label in ["Métiers ciblés par adulte", "Formations ciblées"]:
                continue
            if isinstance(val, list):
                if val:
                    profile_parts.append(f"{label} : {', '.join(map(str, val))}")
            else:
                profile_parts.append(f"{label} : {val}")
                
        odis_brief = "\n".join(profile_parts) if profile_parts else "Aucun critère spécifique fourni."
        notes_qualitatives = config.notes_qualitatives or []
    else:
        # Legacy compatibility fallback
        codes_metiers = config
        odis_brief = ""
        notes_qualitatives = []

    # Initialize nested dict
    if "jobs_enrichment" not in current_val:
        current_val["jobs_enrichment"] = {str(cg): {"status": "pending", "jobs": []} for cg in codgeos}
    else:
        # Reset specific keys to pending
        for cg in codgeos:
            current_val["jobs_enrichment"][str(cg)] = {"status": "pending", "jobs": []}
    store[hash_val] = current_val

    # 3. Extract unique valid ROME codes per adult
    adult_romes_list = []
    for adult_list in codes_metiers:
        adult_romes = []
        for item in adult_list:
            code = None
            label = None
            if hasattr(item, "code"):
                code = item.code
                label = item.label
            elif isinstance(item, dict) and "code" in item:
                code = item["code"]
                label = item.get("label")
            elif isinstance(item, str):
                code = item
                label = item
            
            if code and len(code) == 5 and code[0].isalpha() and code[1:].isdigit():
                if not any(r["code"] == code for r in adult_romes):
                    adult_romes.append({"code": code, "label": label or code})
        adult_romes_list.append(adult_romes)
    
    # If no ROME codes are present at all, return empty results for all communes
    if not any(adult_romes_list):
        logging.info(f"ℹ️ [JOBS-ENRICH] No valid ROME codes to search.")
        current_val = store.get(hash_val, {})
        if not isinstance(current_val, dict): current_val = {}
        current_val["jobs_enrichment"] = {cg: {"status": "done", "jobs": [[] for _ in adult_romes_list]} for cg in codgeos}
        store[hash_val] = current_val
        return

    from services.mcp_france_travail import _search_job_offers_logic

    # Define the worker task for a single city
    def bg_jobs_enrichment_for_city_task(cg: str, results_store: dict):
        try:
            logging.info(f"🚀 [JOBS-ENRICH-CITY] Starting background job enrichment for commune {cg} (hash: {hash_val})")
            city_results = []
            
            # Fetch and pool up to 10 offers per ROME code per adult
            for i, adult_romes in enumerate(adult_romes_list):
                adult_pooled_jobs = []
                for rome_entry in adult_romes:
                    rome = rome_entry["code"]
                    rome_label = rome_entry["label"]
                    
                    try:
                        # sort=2 (distance ascending), distance=20 (radius in km)
                        res = _search_job_offers_logic(
                            rome=rome,
                            location=cg,
                            distance=20,
                            sort=2,
                            range_start=0,
                            range_end=9
                        )
                        offres = res.get("offres", [])[:10]
                        for o in offres:
                            job_detail = {
                                "id": str(o.get("id", "")),
                                "title": str(o.get("intitule", "Poste sans titre")),
                                "company": o.get("entreprise", {}).get("nom") if o.get("entreprise") else None,
                                "contract_type": str(o.get("typeContrat", "")),
                                "contract_label": o.get("typeContratLibelle"),
                                "description": o.get("description_sh"),
                                "location": o.get("lieuTravail", {}).get("libelle") if o.get("lieuTravail") else None,
                                "location_insee": o.get("lieuTravail", {}).get("codeINSEE") if o.get("lieuTravail") else None,
                                "salary": o.get("salaire", {}).get("libelle") if o.get("salaire") else None,
                                "url": o.get("origineOffre", {}).get("urlOrigine") if o.get("origineOffre") else None,
                                "rome_code": rome,
                                "rome_label": rome_label
                            }
                            adult_pooled_jobs.append(job_detail)
                    except Exception as e:
                        logging.warning(f"⚠️ [JOBS-ENRICH-CITY] API error for {cg} ROME {rome}: {e}")
                
                # Apply post-curation to the pooled jobs list for this adult
                if len(adult_pooled_jobs) <= 5:
                    curated_jobs = adult_pooled_jobs
                else:
                    # Resolve target city CommuneResult for this specific city
                    target_city = None
                    if search_results:
                        if hasattr(search_results, "get_by_code"):
                            target_city = search_results.get_by_code(cg)
                        elif isinstance(search_results, dict):
                            results = search_results.get("results", [])
                            for r in results:
                                r_code = r.get("codgeo") if isinstance(r, dict) else getattr(r, "codgeo", None)
                                if r_code == cg:
                                    target_city = r
                                    break
                    curated_jobs = _curate_jobs_with_llm(
                        adult_pooled_jobs, 
                        odis_brief, 
                        notes_qualitatives,
                        target_city=target_city
                    )
                    
                city_results.append(curated_jobs)
            
            # Atomic update to results_store nested dictionary
            current_val = results_store.get(hash_val, {})
            if isinstance(current_val, dict) and "jobs_enrichment" in current_val:
                current_val["jobs_enrichment"][str(cg)] = {
                    "status": "done",
                    "jobs": city_results
                }
                results_store[hash_val] = current_val
            
            logging.info(f"✅ [JOBS-ENRICH-CITY] Background job enrichment finished for commune {cg} (hash: {hash_val})")
        except Exception as e:
            logging.error(f"❌ [JOBS-ENRICH-CITY] Error for commune {cg}: {e}", exc_info=True)
            current_val = results_store.get(hash_val, {})
            if isinstance(current_val, dict) and "jobs_enrichment" in current_val:
                current_val["jobs_enrichment"][str(cg)] = {
                    "status": "error",
                    "error": str(e),
                    "jobs": []
                }
                results_store[hash_val] = current_val
                
    # 4. Spawn a concurrent thread for each target commune code
    for cg in codgeos:
        thread = threading.Thread(target=bg_jobs_enrichment_for_city_task, args=(str(cg), store))
        thread.daemon = True
        thread.start()


def launch_background_audit_log(config: Any, search_results: Any, h: str, interaction_id: Optional[str] = None, username: Optional[str] = None):
    """
    Launches a background thread to log search results to Markdown and Telemetry.
    """
    def bg_logging_task():
        try:
            logging.info(f"💾 [LOGGING] Starting background audit log for hash {h}")
            
            # 1. Markdown Local Logging (Dev Audit)
            try:
                from utils.logger import log_search_results
                log_search_results(config, search_results, prefix="classic", interaction_id=interaction_id, username=username)
            except Exception as e:
                logging.warning(f"⚠️ [LOGGING] Markdown logging failed: {e}")
            
            # 2. Telemetry Logging (BigQuery)
            try:
                from services.telemetry import log_search_complete
                log_search_complete(config, search_results, source_flow='classic', interaction_id=interaction_id, username=username)
            except Exception as e:
                logging.error(f"❌ [LOGGING] Telemetry logging failed for hash {h}: {e}", exc_info=True)
                
            logging.info(f"✅ [LOGGING] Background logging finished for hash {h}")
        except Exception as e:
            logging.error(f"❌ [LOGGING] Background logging FATAL error for {h}: {e}", exc_info=True)
            
    thread = threading.Thread(target=bg_logging_task)
    thread.daemon = True
    thread.start()

def launch_post_scoring_tasks(engine: Any, config: Any, search_results: Any, h: str):
    """
    Orchestrator for all background tasks triggered after scoring.
    """
    # 0. Initialize the store entry for this hash to prevent race conditions between threads
    store = get_odis_bg_store()
    if h not in store:
        store[h] = {}
    store[h]["status_refiner"] = "running"

    # 1. Capture session metadata FROM THE MAIN THREAD
    try:
        from services.telemetry import get_interaction_id
        interaction_id = get_interaction_id()
        username = st.session_state.get('username', 'unknown')
    except:
        interaction_id = "unknown"
        username = "unknown"

    # 2. Extract city data for Scorer Agent (using mode='json' for safe cross-thread serialization)
    top_cities_full = [c.model_dump(mode='json') for c in search_results.results]
    current_geo_full = search_results.current_geo.model_dump(mode='json') if search_results.current_geo else None
    commune_pressentie_full = search_results.commune_pressentie.model_dump(mode='json') if search_results.commune_pressentie else None
    
    # 3. Launch Refiner (AI Briefing & Pitch)
    launch_background_refiner(config, {}, h, top_cities=top_cities_full, current_geo=current_geo_full, commune_pressentie=commune_pressentie_full, interaction_id=interaction_id, username=username)
    
    # 4. Launch Enrichment (Detailed Associations - BQ/RAG)
    target_codgeos = [c['codgeo'] for c in top_cities_full]
    if commune_pressentie_full:
        target_codgeos.append(commune_pressentie_full['codgeo'])
    launch_background_enrichment(engine, target_codgeos, h)
    
    # 4b. Launch Employment Enrichment (Detailed Jobs - France Travail)
    launch_background_jobs_enrichment(target_codgeos, config, h, search_results)
    
    # 5. Launch Logging & Telemetry
    launch_background_audit_log(config, search_results, h, interaction_id=interaction_id, username=username)

def launch_background_city_analysis(nom: str, codgeo: str, search_criterias: Any, search_results: Any, h: str, messages: Optional[list] = None, interaction_id: Optional[str] = None, username: Optional[str] = None):
    """
    Launches a background thread to generate the full ODIS synthesis (or answer a specific question) for a specific city.
    Stores the result in the cached global store under a unique key analysis_{h}_{codgeo}.
    """
    store = get_odis_bg_store()
    task_key = f"analysis_{h}_{codgeo}"
    
    # Ensure it's not already running
    if store.get(task_key, {}).get("status") == "running":
        return
        
    store[task_key] = {"status": "running", "start_time": time.time()}

    def bg_analysis_task(results_store: dict):
        import asyncio
        from agents.utils import run_logic
        
        try:
            logging.info(f"🚀 [BG-ANALYSIS] Starting background analysis for {nom} ({codgeo})")
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            execution_mode = "specific_ask" if messages and len(messages) > 1 else "full_analysis"
            
            input_data = {
                "search_criteria": search_criterias,
                "execution_mode": execution_mode,
                "focus_city": {"name": nom, "codgeo": codgeo},
                "search_results": search_results,
                "criteria_hash": h,
                "messages": messages or [{"role": "user", "content": f"Fais une analyse complète pour {nom}."}],
                "interaction_id": interaction_id or "unknown",
                "username": username or "unknown"
            }
            
            try:
                final_state = loop.run_until_complete(run_logic(input_data))
                
                final_search_results = final_state.get("search_results")
                current_val = results_store.get(task_key, {})
                current_val["status"] = "done"
                current_val["result"] = final_search_results
                results_store[task_key] = current_val
                
                logging.info(f"✅ [BG-ANALYSIS] Background thread finished for {nom} ({codgeo})")
            except Exception as e:
                logging.error(f"❌ [BG-ANALYSIS] Agent Error for {nom} ({codgeo}): {e}", exc_info=True)
                current_val = results_store.get(task_key, {})
                current_val["status"] = "error"
                current_val["error"] = str(e)
                results_store[task_key] = current_val
                
        except Exception as global_e:
            logging.error(f"❌ [BG-ANALYSIS] Setup Error for {nom} ({codgeo}): {global_e}")
            current_val = results_store.get(task_key, {})
            current_val["status"] = "error"
            current_val["error"] = str(global_e)
            results_store[task_key] = current_val
        finally:
            if 'loop' in locals() and not loop.is_closed():
                loop.close()
                logging.info(f"🚀 [BG-ANALYSIS] Loop closed for {nom}")
            
    thread = threading.Thread(target=bg_analysis_task, args=(store,))
    thread.daemon = True
    thread.start()

import asyncio

def run_async_safe(input_data: dict):
    """
    Exécute la logique asynchrone de manière sécurisée.
    Stratégie: "Non-Destructive Loop Management".
    On réutilise la loop du thread si elle existe, on en crée une si besoin,
    MAIS on ne la ferme JAMAIS explicitement ici. C'est le thread/process
    qui gérera son cycle de vie.
    """
    # 1. Harvest Telemetry Metadata (Main Thread)
    try:
        from services.telemetry import get_interaction_id
        interaction_id = get_interaction_id()
        username = st.session_state.get('username', 'unknown')
    except:
        interaction_id = "unknown"
        username = "unknown"

    # 2. Inject into input_data
    input_data["interaction_id"] = interaction_id
    input_data["username"] = username

    try:
        # 3. Check current loop
        loop = asyncio.get_event_loop()
    except RuntimeError:
        # 2. If no loop exists, create new
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_closed():
        # 3. If found loop is closed, replace it
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # 4. Run without closing
    return loop.run_until_complete(run_logic(input_data))

@logfire.instrument("Agent: Interviewer (Auto-Detect)")
def run_autodetect_safe(text: str):
    logfire.info("Interviewer Auto-Detect started")
    from agents.interviewer import interviewer_agent
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    if loop.is_closed():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    result = loop.run_until_complete(interviewer_agent.run(text))
    return result.output

def rehydrate_graph_state(input_data: dict) -> "GraphState":
    """
    Rehydrates raw dictionary data into a rich GraphState object.
    Ensures that all nested models (SearchResultsData, CommuneResult, etc.) 
    are properly validated and instantiated.
    """
    from agents.state import GraphState
    from core.models import SearchCriterias, SearchResultsData, CommuneResult

    # 1. Search Criteria
    sc_data = input_data.get("search_criteria", {})
    sc = SearchCriterias.model_validate(sc_data) if isinstance(sc_data, dict) else sc_data

    # 2. Search Results (The most critical part for Scorer)
    sr_data = input_data.get("search_results")
    sr = None
    if sr_data:
        # Pydantic v2 recursive validation handles nested thematic data automatically
        sr = SearchResultsData.model_validate(sr_data) if isinstance(sr_data, dict) else sr_data
             
    # 3. Focus City
    fc_data = input_data.get("focus_city")
    fc = None
    if fc_data:
         fc = CommuneResult.model_validate(fc_data) if isinstance(fc_data, dict) else fc_data
             
    # 4. Construct Final State
    return GraphState(
        search_criteria=sc,
        search_results=sr,
        focus_city=fc,
        execution_mode=input_data.get("execution_mode", "full_analysis"),
        odis_brief=getattr(sc, 'odis_brief', ""),
        messages=input_data.get("messages", []),
        interaction_id=input_data.get("interaction_id", "unknown"),
        username=input_data.get("username", "unknown")
    )

@logfire.instrument("ODIS Graph Logic")
async def run_logic(input_data: dict):
    """Logique asynchrone pure."""
    # Label the trace with metadata if available
    h = input_data.get("criteria_hash")
    iid = input_data.get("interaction_id")
    
    logfire.info(
        "Processing ODIS Graph Logic for {search_hash}", 
        search_hash=h or "unknown",
        interaction_id=iid or "unknown"
    )
    
    import os
    from google import genai
    from google.genai import types
    from agents.state import GraphState, ODISDeps
    from agents.graph import create_odis_graph
    from core.models import SearchCriterias, SearchResultsData
    
    # 1. Client Local (Critique: Fresh instance per request)
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    client = genai.Client(
        api_key=api_key, 
        http_options=types.HttpOptions(
            api_version="v1beta",
            retry_options=types.HttpRetryOptions(
                attempts=3,
                initial_delay=1.0,
                max_delay=10.0,
                http_status_codes=[429, 503]
            )
        )
    )
    
    # 2. State & Deps
    input_state = rehydrate_graph_state(input_data)
    deps = ODISDeps(state=input_state, client=client)
    
    # 3. Graphe
    app = create_odis_graph() 
    
    # 5. Appel
    result = await app.run(state=input_state, deps=deps)
    
    return {
        "search_results": input_state.search_results,
        "messages": input_state.messages
    }
