
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
import config as cfg
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model
from .tools import (
    search_places, 
    compute_routes, 
    set_focus_city, 
    search_referentiels, 
    search_referentiels_batch,
    search_refugee_associations, 
    search_odis_associations
)

logger = logging.getLogger("scout_agent_v2")

class SearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(..., description="Domaine: ['rome_codes', 'communes', 'regions', 'departements'].")

SCOUT_SYSTEM_PROMPT = """
**Rôle** : Tu es le Scout ODIS. Expert en terrain. Tu épaules l'orchestrator pour trouver des informations et infrastructures locales pertinentes pour le projet de vie de la personne accompagnée.
**Objectif** : Rapporter le résultat d'un analyse poussée sur la commune demandée.
**Ton** : Hyper synthétique, direct, factuel.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}

**Instructions** :
1. **Gestion du Focus** : Utilise la `VILLE ACTIVE` fournie. Si elle est vide, demande à l'utilisateur de préciser sur quelle ville il souhaite des informations.

2. **Recherche de Terrain** : Dans cet ordre de priorité
    - **Gestion du Code INSEE** : Récupère le Code INSEE de la ville de `VILLE ACTIVE` avec l'outil `search_referentiels_batch_tool` (domain='communes').
    - **Utilise systématiquement** `search_refugee_associations(codgeo=code)` pour identifier les structures spécialisées. C'est CRUCIAL pour l'argumentaire inclusion.
    - **Utilise systématiquement** `search_odis_associations(codgeo=code)` pour enrichir la vision de la vie locale pertinente (Clubs, Culture, Sport, Social).
    - Utilise `search_places` pour trouver des POIs (écoles, parcs, commerces, lieux de culte) **dans un rayon de 50km de `VILLE ACTIVE`**. Exemples de recherches:
        - des lieux publics en lien avec l'origine culturelle (ex: restaurant libanais, épicerie indienne, etc)
        - les commerces solidaires (ex: Emmaus, Recycleries)
        - les lieux de cultes (hors églises) si culturelement pertinent.
    - Utilise `compute_routes` pour calculer les temps de trajet. Utilise `VILLE ACTIVE` comme origine si non spécifié.

3. **Réponse** :
    - Tu DOIS préparer une synthèse factuelle, argumentative et concise de tes découvertes sur le terrain.
    - Ne garde que ce qui est pertinent au regard du `CONTEXTE RÉSUMÉ`.
"""

scout_agent = Agent(
    get_model("scout"),
    deps_type=ODISDeps
)

@scout_agent.system_prompt
async def scout_instructions(ctx: RunContext[ODISDeps]) -> str:
    return SCOUT_SYSTEM_PROMPT.format(
        BRIEFING=ctx.deps.state.briefing or "",
        FOCUS_CITY=str(ctx.deps.state.focus_city or "Non définie")
    )

# --- Tools ---

@scout_agent.tool
def set_focus_city_tool(ctx: RunContext[ODISDeps], city_name: str) -> str:
    """Définit la ville 'active' ou 'focus' pour la conversation.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        city_name (str): Nom de la ville à définir.
    
    Returns:
        str: Message de confirmation.
    """
    ctx.deps.state.focus_city = city_name
    return set_focus_city(city_name)


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
def search_referentiels_batch_tool(
    ctx: RunContext[ODISDeps], 
    searches: List[SearchQuery]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    
    Args:
        searches: Liste d'objets {query, domain}
    """
    return search_referentiels_batch([s.model_dump() for s in searches])

# Deprecated in favor of search_referentiels_batch_tool
# @scout_agent.tool
# def search_referentiels_tool(ctx: RunContext[ODISDeps], query: str, domain: str) -> List[Dict[str, Any]]:
#     ...
