import time
from typing import Any, Dict, List, Optional
import streamlit as st
from pydantic_ai import Agent
import threading
import logging
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
        poids_territoire=getattr(config, 'poids_territoire', 0.5)
    )

def launch_background_scorer(search_criterias: SearchCriterias, results_dict_ignored: dict, hash_val: str, top_cities: Optional[list] = None, interaction_id: Optional[str] = None, username: Optional[str] = None):
    """
    Launches a background thread to generate the SCORER AI pitch.
    Stores the result in the cached global store.
    """
    # Get the store here (main thread) to ensure it's initialized in the cache
    store = get_odis_bg_store()
    
    def bg_scorer_task(results_store: dict):
        import asyncio
        import os
        from google import genai
        from google.genai import types
        from agents.state import GraphState, ODISDeps
        from agents.scorer import scorer_agent
        from agents.agent_config import get_p_model
        from pydantic_ai import ModelSettings
        
        try:
            logging.debug(f"🚀 [BG] Starting background scorer for hash {hash_val}")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            logging.debug(f"🚀 [BG] API Key present: {bool(api_key)}")
            
            client = genai.Client(
                api_key=api_key, 
                http_options=types.HttpOptions(
                    api_version="v1beta",
                    retry_options=types.HttpRetryOptions(attempts=3)
                )
            )
            
            # Prepare search_results for the background state
            search_results_data = None
            if top_cities:
                results_objs = []
                for c in top_cities:
                    details = c.get("details", {})
                    results_objs.append({
                        "codgeo": str(c.get("codgeo")),
                        "name": str(c.get("libgeo", c.get("name", ""))),
                        "population": int(c.get("population", 0)),
                        "global_score": float(c.get("weighted_score", 0.0)),
                        "scores": c.get("scores", {}),
                    })
                search_results_data = {
                    "search_hash": hash_val,
                    "results": results_objs,
                    "current_geo": results_objs[0] if results_objs else None
                }

            from core.models import SearchResultsData
            sr_obj = SearchResultsData.model_validate(search_results_data) if search_results_data else None
            
            state = GraphState(
                search_criteria=search_criterias,
                execution_mode="full_analysis",
                search_results=sr_obj,
                interaction_id=interaction_id or "",
                username=username or "unknown"
            )
            deps = ODISDeps(state=state, client=client)
            model = get_p_model("scorer", client=client)
            
            async def run_agent():
                logging.debug(f"🚀 [BG] Calling scorer_agent.run for hash {hash_val}")
                return await scorer_agent.run(
                    "Génère le résumé explicatif des résultats pour ce profil.", 
                    deps=deps, 
                    model=model, 
                    model_settings=ModelSettings(max_tokens=4096)
                )
            
            try:
                result_run = loop.run_until_complete(run_agent())
                response_obj = result_run.output
                logging.debug(f"🚀 [BG] Agent call successful for hash {hash_val}")
                for p in response_obj.pitches_per_city:
                    logging.debug(f"💎 [DEBUG-SCORER-PITCH] codgeo={p.codgeo} pitch={repr(p.pitch)}")
                
                pitches_dict = {
                    "global": sanitize_llm_markdown(response_obj.response),
                    "pitches": {p.codgeo: sanitize_llm_markdown(p.pitch) for p in response_obj.pitches_per_city}
                }
                
                # Harmonized storage: merge with existing results if any
                current_val = results_store.get(hash_val, {})
                if not isinstance(current_val, dict): current_val = {}
                current_val["pitches"] = pitches_dict
                results_store[hash_val] = current_val
                
                logging.debug(f"✅ [BG] Background Scorer fully finished for hash {hash_val}")
            except Exception as e:
                logging.error(f"❌ [BG] Background Scorer Error for hash {hash_val}: {e}")
                current_val = results_store.get(hash_val, {})
                if not isinstance(current_val, dict): current_val = {}
                current_val["pitches_error"] = f"⚠️ L'analyse IA a échoué: {e}"
                results_store[hash_val] = current_val
        except Exception as global_e:
            logging.error(f"❌ [BG] Background Scorer Setup Error for hash {hash_val}: {global_e}")
            current_val = results_store.get(hash_val, {})
            if not isinstance(current_val, dict): current_val = {}
            current_val["pitches_error"] = f"⚠️ L'analyse IA a échoué (Setup): {global_e}"
            results_store[hash_val] = current_val
        finally:
            if 'loop' in locals():
                loop.close()
                logging.info(f"🚀 [SCORER] Loop closed for hash {hash_val}")
            
    thread = threading.Thread(target=bg_scorer_task, args=(store,))
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

    # 1. Capture session metadata FROM THE MAIN THREAD
    try:
        from services.telemetry import get_interaction_id
        interaction_id = get_interaction_id()
        username = st.session_state.get('username', 'unknown')
    except:
        interaction_id = "unknown"
        username = "unknown"

    # 2. Extract city data for Scorer Agent
    top_cities_full = [
        {
            "codgeo": str(c.codgeo), 
            "libgeo": c.name, 
            "weighted_score": c.global_score, 
            "scores": c.scores,
            "population": c.population
        } 
        for c in search_results.results
    ]
    
    # 3. Launch Scorer (AI Pitch)
    launch_background_scorer(config, {}, h, top_cities=top_cities_full, interaction_id=interaction_id, username=username)
    
    # 4. Launch Enrichment (Detailed Associations - BQ/RAG)
    target_codgeos = [c['codgeo'] for c in top_cities_full]
    launch_background_enrichment(engine, target_codgeos, h)
    
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
            
            state_dict = {
                "search_criteria": search_criterias.model_dump() if hasattr(search_criterias, "model_dump") else search_criterias,
                "is_interview_complete": True,
                "execution_mode": execution_mode,
                "focus_city": {"name": nom, "codgeo": codgeo},
                "search_results": search_results.model_dump() if hasattr(search_results, "model_dump") else search_results,
                "criteria_hash": h,
                "messages": messages or [{"role": "user", "content": f"Fais une analyse complète pour {nom}."}],
                "interaction_id": interaction_id or "unknown",
                "username": username or "unknown"
            }
            
            try:
                final_state = loop.run_until_complete(run_logic(state_dict))
                
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

def run_autodetect_safe(text: str):
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
    return result.data

async def run_logic(input_data: dict):
    """Logique asynchrone pure."""
    import os
    from google import genai
    from google.genai import types
    from agents.state import GraphState, ODISDeps, FocusCity
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
    sc_data = input_data.get("search_criteria", {})
    sc = SearchCriterias.model_validate(sc_data) if isinstance(sc_data, dict) else sc_data

    sr_data = input_data.get("search_results")
    sr = None
    if sr_data:
        sr = SearchResultsData.model_validate(sr_data) if isinstance(sr_data, dict) else sr_data
             
    fc_data = input_data.get("focus_city")
    fc = None
    if fc_data:
         fc = FocusCity.model_validate(fc_data) if isinstance(fc_data, dict) else fc_data
             
    input_state = GraphState(
        search_criteria=sc,
        search_results=sr,
        focus_city=fc,
        execution_mode=input_data.get("execution_mode", "full_analysis"),
        messages=input_data.get("messages", []),
        interaction_id=input_data.get("interaction_id", "unknown"),
        username=input_data.get("username", "unknown")
    )
    
    deps = ODISDeps(state=input_state, client=client)
    
    # 3. Graphe
    app = create_odis_graph() 
    
    # 5. Appel
    result = await app.run(state=input_state, deps=deps)
    
    return {
        "search_results": input_state.search_results,
        "messages": input_state.messages
    }
