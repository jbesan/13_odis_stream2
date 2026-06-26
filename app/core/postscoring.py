import time
from typing import Any, Dict, List, Optional
import streamlit as st
import threading
import logging
import logfire
import pandas as pd

# Core utilities imported from agents.utils
from agents.utils import get_odis_bg_store, sanitize_llm_markdown, rehydrate_graph_state

def launch_background_refining(search_criterias: Any, results_dict_ignored: dict, hash_val: str, top_cities: Optional[list] = None, current_geo: Optional[dict] = None, commune_pressentie: Optional[dict] = None, interaction_id: Optional[str] = None, username: Optional[str] = None):
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
            
            from agents.agent_config import get_gemini_client
            client = get_gemini_client(attempts=2)
            
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
                    model=model
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

def launch_background_association_enrichment(engine: Any, codgeos: List[str], hash_val: str):
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

def _curate_jobs_with_agent(
    jobs: List[Dict[str, Any]], 
    profile_brief: str, 
    notes_qualitatives: List[str],
    target_city: Optional[Any] = None
) -> List[Dict[str, Any]]:
    """Curates a list of job offers using job_curator_agent based on candidate context.

    Args:
        jobs: List of job offer details dictionaries.
        profile_brief: Narrative summary of candidate's situation.
        notes_qualitatives: List of qualitative project notes.
        target_city: Optional CommuneResult representing the city ciblée.

    Returns:
        List of curated top 5 job offer details dictionaries.
    """
    try:
        from agents.job_curator import job_curator_agent
        from agents.state import GraphState, ODISDeps
        from core.models import SearchCriterias
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
                f"  Salaire: {job.get('salary') or 'Non spécifié'}\n"
                f"  Expérience requise: {job.get('experience') or 'Non spécifiée'}\n"
                f"  Durée de travail: {job.get('work_duration') or 'Non spécifiée'}\n"
                f"  Date de création: {job.get('date_creation') or 'Non spécifiée'}\n\n"
            )

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        import os
        from google import genai
        from google.genai import types
        from agents.agent_config import get_p_model, get_model_settings, get_gemini_client

        client = get_gemini_client(attempts=1)
        model = get_p_model("job_curator", client=client)

        # Construct a lightweight GraphState to carry context
        state = GraphState()
        state.odis_brief = profile_brief
        state.focus_city = target_city
        state.search_criteria = SearchCriterias(notes_qualitatives=notes_qualitatives)
        
        deps = ODISDeps(state=state, client=client)
        prompt = f"Voici la liste des offres d'emploi récupérées à trier et curer :\n\n{jobs_list_str}"

        # Run the curator agent synchronously within the background thread
        result = loop.run_until_complete(
            job_curator_agent.run(
                prompt, 
                deps=deps, 
                model=model,
                model_settings=get_model_settings("job_curator")
            )
        )
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

def launch_background_job_curation(codgeos: List[str], config: Any, hash_val: str, search_results: Optional[Any] = None):
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
                
        profile_brief = "\n".join(profile_parts) if profile_parts else "Brief non disponible"
        notes_qualitatives = config.notes_qualitatives or []
    else:
        # Legacy compatibility fallback
        codes_metiers = config
        profile_brief = ""
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
            api_total_count = 0
            
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
                        api_total_count += res.get("total", 0)
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
                                "rome_label": rome_label,
                                "date_creation": o.get("dateCreation"),
                                "work_duration": o.get("dureeTravailLibelle"),
                                "experience": o.get("experienceLibelle")
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
                    curated_jobs = _curate_jobs_with_agent(
                        adult_pooled_jobs, 
                        profile_brief, 
                        notes_qualitatives,
                        target_city=target_city
                    )
                    
                city_results.append(curated_jobs)
            
            # Atomic update to results_store nested dictionary
            current_val = results_store.get(hash_val, {})
            if isinstance(current_val, dict) and "jobs_enrichment" in current_val:
                current_val["jobs_enrichment"][str(cg)] = {
                    "status": "done",
                    "jobs": city_results,
                    "total": api_total_count
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
    launch_background_refining(config, {}, h, top_cities=top_cities_full, current_geo=current_geo_full, commune_pressentie=commune_pressentie_full, interaction_id=interaction_id, username=username)
    
    # 4. Launch Enrichment (Detailed Associations - BQ/RAG)
    target_codgeos = [c['codgeo'] for c in top_cities_full]
    if commune_pressentie_full:
        target_codgeos.append(commune_pressentie_full['codgeo'])
    launch_background_association_enrichment(engine, target_codgeos, h)
    
    # 4b. Launch Employment Enrichment (Detailed Jobs - France Travail)
    launch_background_job_curation(target_codgeos, config, h, search_results)
    
    # 5. Launch Logging & Telemetry
    launch_background_audit_log(config, search_results, h, interaction_id=interaction_id, username=username)
