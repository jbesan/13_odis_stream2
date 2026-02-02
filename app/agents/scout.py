import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
import config as cfg
from .state import ODISGraphState, ODISDeps, FocusCity, compute_criteria_hash
from .agent_config import get_model
from .tools import (
    search_places, 
    compute_routes, 
    search_refugee_associations, 
    search_odis_associations,
    search_ccas,
)

logger = logging.getLogger("scout_agent_v2")

class SearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(..., description="Domaine: ['rome_codes', 'communes', 'regions', 'departements'].")

SCOUT_ANALYSIS_SYSTEM_PROMPT = """
**Rôle** : Tu es le Scout ODIS. Tu épaules le travailleur social pour trouver les infrastructures locales pertinentes pour le projet de vie de la personne accompagnée.
**Objectif** : Rapporter le résultat d'un analyse poussée sur la commune demandée.
**Ton** : Hyper synthétique, direct, factuel.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY_NAME} (Code INSEE: {FOCUS_CITY_CODE})

**Instructions** :
1. **Gestion du Focus** : La localité d'intérêt est `{FOCUS_CITY_NAME}`.
2. **Recherche de Terrain** : Dans cet ordre de priorité
    - Utilise TOUJOURS `search_refugee_associations_tool(codgeo='{FOCUS_CITY_CODE}')` pour trouver des associations d'aide aux réugiés.
    - Utilise `search_odis_associations_tool(codgeo='{FOCUS_CITY_CODE}')` pour trouver des associations d'aide à l'insertion sociale our de loisir (ex: sport, culture, etc)
    - Utilise `search_places` pour trouver des POIs (écoles, parcs, commerces, lieux de culte) **dans un rayon de 50km de `{FOCUS_CITY_NAME}`**.
    - Utilise `compute_routes` pour calculer les temps de trajet. Utilise `{FOCUS_CITY_NAME}` comme origine si non spécifié.
    - Utilise TOUJOURS `search_ccas` pour trouver le Centre Communal d'Action Social local.

3. **Réponse** :
    - Commence toujours ta réponse par rappeler en une phrase ce que tu as recherché.
    - Tu DOIS préparer une synthèse factuelle, argumentative et concise de tes découvertes sur le terrain.
    - Retourne TOUJOURS le `CCAS` trouvé.
    - Ne garde que ce qui est pertinent au regard du `CONTEXTE RÉSUMÉ`.
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
    - Utilise `search_refugee_associations_tool(codgeo='{FOCUS_CITY_CODE}')` pour trouver des associations de support aux réfugiés.
    - Utilise `search_odis_associations_tool(codgeo='{FOCUS_CITY_CODE}')` pour trouver des associations d'aide à l'insertion sociale.
    - Utilise `search_places` pour trouver des POIs (écoles, parcs, commerces, lieux de culte) **dans un rayon de 50km de `{FOCUS_CITY_NAME}`**.
    - Utilise `compute_routes` pour calculer les temps de trajet. Utilise `{FOCUS_CITY_NAME}` comme origine si non spécifié.
"""

scout_agent = Agent(
    get_model("scout"),
    deps_type=ODISDeps
)

@scout_agent.system_prompt
async def scout_instructions(ctx: RunContext[ODISDeps]) -> str:
    focus = ctx.deps.state.focus_city
    city_name = focus.name if focus else "Non définie"
    city_code = focus.codgeo if focus else "Inconnu"
    last_message = ctx.deps.state.messages[-1].get("content", "Non disponible") if ctx.deps.state.messages else "Non disponible"
    h = compute_criteria_hash(ctx.deps.state.search_criteria)
    artifacts = ctx.deps.state.commune_artifacts.get(city_name.lower().strip(), {}).get(h, {})
    

    # We select prompt according to mode: generic commune analysis or a specific question
    mode = ctx.deps.state.execution_mode
    if mode == 'specific_ask':
        prompt = SCOUT_SPECIFIC_SYSTEM_PROMPT
    else:
        prompt = SCOUT_ANALYSIS_SYSTEM_PROMPT

    
    return prompt.format(
        BRIEFING=ctx.deps.state.briefing or "",
        FOCUS_CITY_NAME=city_name,
        FOCUS_CITY_CODE=city_code,
        LAST_MESSAGE = last_message,
        COMMUNE_ARTIFACT=artifacts.get("scout", "Non disponible")
    )

# --- Tools ---


@scout_agent.tool
def search_places_tool(ctx: RunContext[ODISDeps], queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des lieux (POIs).
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        queries (List[str]): Liste des requêtes de recherche.
        location (str): Localisation de la recherche.
    
    Returns:
        Dict[str, Any]: Dictionnaire des lieux correspondants.
    """
    return search_places(queries, location)

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
def search_odis_associations_tool(ctx: RunContext[ODISDeps], codgeo: str) -> List[Dict[str, Any]]:
    """Recherche associations ODIS.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        codgeo (str): Code INSEE de la commune.
    
    Returns:
        List[Dict[str, Any]]: Liste des associations ODIS correspondantes.
    """
    return search_odis_associations(codgeo)

@scout_agent.tool
def search_ccas_tool(ctx: RunContext[ODISDeps], codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les informations du CCAS (Centre Communal d'Action Sociale) pour une commune.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        codgeo (str): Code INSEE de la commune (ex: '33063').
    """
    return search_ccas(codgeo)
