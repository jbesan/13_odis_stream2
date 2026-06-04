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
