from typing import Any, Dict, List, Optional
import streamlit as st
from pydantic_ai import Agent
import threading
import logging

# Global storage for background tasks (Thread-safe, survives reloads via cache_resource)
@st.cache_resource
def get_odis_bg_store() -> dict:
    """Returns a singleton dictionary for background task results."""
    return {}

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

# Humoristic messages for the ODIS agents
AGENT_TOASTS = {
    "interviewer": {
        "emoji": "💬",
        "messages": [
            "Interrogatoire poli en cours.",
            "Je prépare mes meilleures questions pièges.",
            "Discussion mondaine avec l'IA.",
            "À l'écoute de chaque pixel de votre demande.",
            "Le détective ODIS mène l'enquête."
        ]
    },
    "scorer": {
        "emoji": "📈",
        "messages": [
            "Sortez les calculatrices, ça va chauffer !",
            "Tri sélectif des meilleures opportunités.",
            "Le jury a délibéré... Calcul des scores.",
            "Je cherche la perle rare sur la carte.",
            "Alchimie urbaine : transformer les données en pépites."
        ]
    },
    "scout": {
        "emoji": "🏘️",
        "messages": [
            "Exploration du quartier en baskets virtuelles.",
            "Je vérifie si la boulangerie est ouverte.",
            "Repérage terrain. GPS activé.",
            "Je fouille les recoins de chaque commune.",
            "Mission de reconnaissance lancée !"
        ]
    },
    "web": {
        "emoji": "🌐",
        "messages": [
            "Plongeon dans les abysses d'Internet.",
            "Google est mon meilleur ami (pour le moment).",
            "Surf sur la vague de l'information.",
            "Je rapporte des nouvelles fraîches du Web.",
            "Connexion au grand cerveau mondial."
        ]
    },
    "job_hunter": {
        "emoji": "💼",
        "messages": [
            "Chasseur de jobs : Mode furtif activé.",
            "Je déniche des offres avant qu'elles ne refroidissent.",
            "Pêche au gros dans le bassin de l'emploi.",
            "Tri des CV et des annonces... C'est du sérieux.",
            "Le recruteur de choc est sur le coup !"
        ]
    },
    "synthesizer": {
        "emoji": "🧩",
        "messages": [
            "Assemblage des pièces du puzzle.",
            "La cerise sur le gâteau ODIS.",
            "Grand mélange final... Agitez bien.",
            "Dernière vérification avant le décollage.",
            "Je mets de l'ordre dans tout ce bazar."
        ]
    }
}

from core.models import SearchCriterias, SearchCriterias, CriteriaItem
import pandas as pd

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
        notes_qualitatives=[]
    )

def launch_background_scorer(search_criterias: SearchCriterias, results_dict_ignored: dict, hash_val: str, top_cities: list = None):
    """
    Launches a background thread to generate the SCORER AI pitch.
    Stores the result in the cached global store.
    """
    # Get the store here (main thread) to ensure it's initialized in the cache
    store = get_odis_bg_store()
    
    def bg_task(results_store: dict):
        import asyncio
        import os
        from google import genai
        from google.genai import types
        from agents.state import ODISGraphState, ODISDeps
        from agents.scorer import scorer_agent
        from agents.agent_config import get_p_model
        from pydantic_ai import ModelSettings
        
        try:
            logging.info(f"🚀 [BG] Starting background scorer for hash {hash_val}")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
            logging.info(f"🚀 [BG] API Key present: {bool(api_key)}")
            
            client = genai.Client(
                api_key=api_key, 
                http_options=types.HttpOptions(
                    api_version="v1beta",
                    retry_options=types.HttpRetryOptions(attempts=3)
                )
            )
            
            state_dict = {
                "search_criteria": search_criterias.model_dump(),
                "is_interview_complete": True,
                "execution_mode": "full_analysis",
                "top_cities": top_cities or []
            }
            state = ODISGraphState.model_validate(state_dict)
            deps = ODISDeps(state=state, client=client)
            model = get_p_model("scorer", client=client)
            
            async def run_agent():
                logging.info(f"🚀 [BG] Calling scorer_agent.run for hash {hash_val}")
                return await scorer_agent.run(
                    "Génère le résumé explicatif des résultats pour ce profil.", 
                    deps=deps, 
                    model=model, 
                    model_settings=ModelSettings(max_output_tokens=4096)
                )
            
            try:
                result_run = loop.run_until_complete(run_agent())
                response_obj = result_run.output
                logging.info(f"🚀 [BG] Agent call successful for hash {hash_val}")
                logging.info(f"💎 [DEBUG-BG-RAW] response={repr(response_obj.response)}")
                for p in response_obj.pitches_per_city:
                    logging.info(f"💎 [DEBUG-BG-PITCH] codgeo={p.codgeo} pitch={repr(p.pitch)}")
                
                pitches_dict = {
                    "global": sanitize_llm_markdown(response_obj.response),
                    "pitches": {p.codgeo: sanitize_llm_markdown(p.pitch) for p in response_obj.pitches_per_city}
                }
                for code, p in pitches_dict["pitches"].items():
                    logging.info(f"✨ [DEBUG-SANITY-CHECK] code={code} pitch={repr(p)}")
                results_store[hash_val] = pitches_dict
                logging.info(f"✅ [BG] Background Scorer fully finished for hash {hash_val}")
            except Exception as e:
                logging.error(f"❌ [BG] Background Scorer Error for hash {hash_val}: {e}")
                results_store[hash_val] = f"⚠️ L'analyse IA a échoué: {e}"
        except Exception as global_e:
            logging.error(f"❌ [BG] Background Scorer Setup Error for hash {hash_val}: {global_e}")
            results_store[hash_val] = f"⚠️ L'analyse IA a échoué (Setup): {global_e}"
        finally:
            if 'loop' in locals():
                loop.close()
                logging.info(f"🚀 [BG] Loop closed for hash {hash_val}")
            
    thread = threading.Thread(target=bg_task, args=(store,))
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
    try:
        # 1. Check current loop
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

async def run_logic(input_data: dict):
    """Logique asynchrone pure."""
    import os
    from google import genai
    from google.genai import types
    from agents.state import ODISGraphState, ODISDeps
    from agents.graph import create_odis_graph
    
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
    input_state = ODISGraphState.model_validate(input_data)
    deps = ODISDeps(state=input_state, client=client)
    
    # 3. Graphe
    app = create_odis_graph() 
    
    # 4. Config & Injection Deps
    config = {
        "configurable": {
            "thread_id": "session_user",
            "deps": deps 
        }
    }
    
    # 5. Appel
    final_state = await app.ainvoke(input_state, config=config)
    return final_state
