from typing import Any, Dict
from pydantic_ai import Agent

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
    for label in config.inc_asso_add_selection:
        codes = cfg.WALDEC_INC_ASSO_ADD_MAPPING.get(label, [])
        code_str = ",".join(codes) if codes else "000"
        inc_assos.append(CriteriaItem(code=code_str, label=str(label)))
        
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

import threading
import logging

def launch_background_scorer(search_criterias: SearchCriterias, results_dict: dict, hash_val: str, top_cities: list = None):
    """
    Launches a background thread to generate the SCORER AI pitch.
    Stores the result in `results_dict[hash_val]`.
    """
    def bg_task():
        import asyncio
        import os
        from google import genai
        from google.genai import types
        from agents.state import ODISGraphState, ODISDeps
        from agents.scorer import scorer_agent
        from agents.agent_config import get_p_model
        from pydantic_ai import ModelSettings
        
        try:
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
                res = await scorer_agent.run(
                    "Génère le résumé explicatif des résultats pour ce profil.", 
                    deps=deps, 
                    model=model, 
                    model_settings=ModelSettings(max_output_tokens=4096)
                )
                return res.output
            
            try:
                response_obj = loop.run_until_complete(run_agent())
                # response_obj is a ScorerResult
                pitches_dict = {
                    "global": response_obj.response,
                    "pitches": {p.codgeo: p.pitch for p in response_obj.pitches_per_city}
                }
                results_dict[hash_val] = pitches_dict
                logging.info(f"✅ Background Scorer finished for hash {hash_val}")
            except Exception as e:
                logging.error(f"❌ Background Scorer Error: {e}")
                results_dict[hash_val] = f"⚠️ L'analyse IA a échoué: {e}"
        except Exception as global_e:
            logging.error(f"❌ Background Scorer Setup Error: {global_e}")
            results_dict[hash_val] = f"⚠️ L'analyse IA a échoué: {global_e}"
            
    thread = threading.Thread(target=bg_task)
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
