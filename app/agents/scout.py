import logging
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
import config as cfg
from .state import ODISGraphState, ODISDeps, FocusCity, compute_criteria_hash
from .agent_config import get_model

logger = logging.getLogger(__name__)
from .tools import (
    search_places_batch, 
    compute_routes, 
    search_refugee_associations, 
    search_rna_rag,
    search_ccas,
)

class ScoutResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(..., description="Analyse détaillée des découvertes sur le terrain.")

SCOUT_ANALYSIS_SYSTEM_PROMPT = """
**Rôle** : Tu es le Scout ODIS. Tu épaules le travailleur social pour trouver les infrastructures locales pertinentes pour le projet de vie de la personne accompagnée.
**Objectif** : Rapporter le résultat d'un analyse poussée sur la commune demandée.
**Ton** : Hyper synthétique, direct, factuel.

**CONTEXTE RÉSUMÉ** : {BRIEFING}

**Instructions** :
1. **Gestion du Focus** : La localité d'intérêt est `{FOCUS_CITY_NAME}`.
2. Sois efficace et ne cherche JAMAIS deux fois la même chose
3. **Recherche de Terrain** : Effectue TOUTES les recherches suivantes en choisissant le bon outil :
    - Utilise SYSTEMATQIEUEMENT `search_refugee_associations_tool` pour trouver des associations spécialisées dans l'aide aux réugiés.
    - Utilise SYSTEMATIQUEMENT `search_ccas_tool` pour trouver le Centre Communal d'Action Social local.
    - Utilise SYSTEMATIQUEMENT `search_places_batch_tool` UNIQUEMENT pour trouver des POIs pertinents au regard du contexte:
        - Des infastrctures de transports (ex: gares, gares routières)
        - Des commerces spécialisés (ex: boucherie halal, épicerie asiatique)
        - Des lieux de culte **pertinents** hors églises (ex: pagode, mosquée, temple) 
        - Lieux d'hébergement et d'insertion (ex: CPH, CHRS, CADA)
    - Utilise `search_rna_rag_tool` UNIQUEMENT pour trouver des associations pertinentes pour leur insertion (loisirs, affinités culturelles, solidarité)
    - Utilise `compute_routes_tool` pour calculer les temps de trajet (ex: vers prefecture).
    

3. **Réponse (STRUCTURED)** :
    - Tu DOIS retourner un objet `ScoutResult`.
    - `searched` : Une phrase courte listant les outils/recherches effectués.
    - `result` : Ton analyse factuelle, argumentative et concise (incluant systématiquement le CCAS trouvé). Vise 250 mots minimum et ne garde que ce qui est pertinent au regard du `CONTEXTE RÉSUMÉ`.
"""

SCOUT_SPECIFIC_SYSTEM_PROMPT = """
**Rôle** : Tu es le Scout ODIS. Ta mission est de compléter une analyse existante en effectuant des recherches additionnelles avec les outils disponibles.
**Objectif** : Fournir des informations d'actualité, de contexte social et de veille sur la ville de réinstallation.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY_NAME} (Code INSEE: {FOCUS_CITY_CODE})
**QUESTION POSÉE** : {LAST_MESSAGE}
**CONNAISSANCES ACTUELLES** : {COMMUNE_ARTIFACT}

**Instructions** :
1. Si la `QUESTION POSÉE` peut-être répondue avec les `CONNAISSANCES ACTUELLES` ne fais rien.
2. Si des données manquent pour répondre à la `QUESTION POSÉE` : 
    - Utilise `search_refugee_associations_tool` pour trouver des associations de support aux réfugiés.
    - Utilise `search_rna_rag_tool` pour trouver des associations pertinentes pour leur insertion (loisirs, affinités culturelles, solidarité).
    - Utilise `search_places_batch_tool` pour trouver des POIs (écoles, parcs, commerces, lieux de culte).
    - Utilise `compute_routes_tool` pour calculer les temps de trajet.

3. **Réponse (STRUCTURED)** :
    - Tu DOIS retourner un objet `ScoutResult`.
    - `searched` : Résumé des recherches additionnelles effectuées.
    - `result` : Réponse à la `QUESTION POSÉE` basée sur les nouvelles recherches ou les `CONNAISSANCES ACTUELLES`.
"""

scout_agent = Agent(
    get_model("scout"),
    deps_type=ODISDeps,
    output_type=ScoutResult
)

@scout_agent.system_prompt
async def scout_instructions(ctx: RunContext[ODISDeps]) -> str:
    focus = ctx.deps.state.focus_city
    city_name = focus.name if focus else "Non définie"
    city_code = focus.codgeo if focus else "Inconnu"
    last_message = ctx.deps.state.messages[-1].get("content", "Non disponible") if ctx.deps.state.messages else "Non disponible"
    h = compute_criteria_hash(ctx.deps.state.search_criteria)
    
    # Get artifacts from the new search_results structure
    artifacts = {}
    if ctx.deps.state.search_results:
        city_res = ctx.deps.state.search_results.get_by_code(city_code)
        if city_res:
             artifacts = city_res.expert_analysis
    

    # We select prompt according to mode: generic commune analysis or a specific question
    mode = ctx.deps.state.execution_mode
    if mode == 'specific_ask':
        prompt = SCOUT_SPECIFIC_SYSTEM_PROMPT
    else:
        prompt = SCOUT_ANALYSIS_SYSTEM_PROMPT

    
    return prompt.format(
        BRIEFING=ctx.deps.state.odis_brief or "",
        FOCUS_CITY_NAME=city_name,
        FOCUS_CITY_CODE=city_code,
        LAST_MESSAGE = last_message,
        COMMUNE_ARTIFACT=artifacts.get("scout", "Non disponible")
    )

# --- Tools ---


@scout_agent.tool
def search_places_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des lieux (POIs) en mode batch.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        queries (List[str]): Liste des requêtes.
        location (str): Nom de la ville suivi du nom de la région (ex: 'Bordeaux, Nouvelle-Aquitaine')
    
    Returns:
        Dict[str, Any]: Dictionnaire des lieux correspondants.
    """
    logger.info(f"🔍 [SCOUT] search_places_batch_tool: {queries} in {location}")
    return search_places_batch(queries, location)

@scout_agent.tool
def compute_routes_tool(ctx: RunContext[ODISDeps], origin: str, destination: str, mode: str = "transit") -> Dict[str, Any]:
    """Calcul itinéraires.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        origin (str): Origine de la recherche (default=focus_city).
        destination (str): Destination de la recherche (default=focus_city).
        mode (str): Mode de transport (default="transit").
    
    Returns:
        Dict[str, Any]: Dictionnaire des itinéraires correspondants.
    """
    return compute_routes(origin, destination, mode)

@scout_agent.tool
def search_refugee_associations_tool(ctx: RunContext[ODISDeps], codgeo: str) -> List[Dict[str, Any]]:
    """Recherche associations réfugiés.
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        codgeo (str): Code INSEE de la commune.
    
    Returns:
        List[Dict[str, Any]]: Liste des associations réfugiés correspondantes.
    """
    return search_refugee_associations(codgeo)

@scout_agent.tool
def search_rna_rag_tool(ctx: RunContext[ODISDeps], query: str, codgeo: str, top_k: int = 10) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """Recherche sémantique d'associations. Un appel par terme de recherche.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        query (str): Terme de recherche (ex: 'football', 'hébergement d'urgence').
        codgeo (str): Code INSEE de la commune (5 chiffres).
        top_k (int): Nombre de résultats.
    
    Returns:
        Union[List[Dict[str, Any]], Dict[str, Any]]: Liste des associations correspondantes.
    """
    return search_rna_rag(query, codgeo, top_k=top_k)

@scout_agent.tool
def search_ccas_tool(ctx: RunContext[ODISDeps], codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les informations du CCAS (Centre Communal d'Action Sociale) pour une commune.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        codgeo (str): Code INSEE de la commune (ex: '33063').
    """
    return search_ccas(codgeo)
