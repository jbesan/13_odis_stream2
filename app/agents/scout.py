import logging
from typing import List, Dict, Any, Optional, Union
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
import config as cfg
from .state import ODISGraphState, ODISDeps, FocusCity, compute_criteria_hash, ODISContextBuilder
from .agent_config import get_model

logger = logging.getLogger(__name__)
from .tools import (
    search_places_batch, 
    compute_routes, 
    search_refugee_associations, 
    search_ccas,
    search_rna_rag_batch,
)

class ScoutResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(..., description="Analyse détaillée des découvertes sur le terrain.")

SCOUT_ANALYSIS_SYSTEM_PROMPT = """
**Contexte** : Un Travailleur Social cherche la ville la plus adéquate pour une personne réfugiée (et sa famille). A partir de critères de recherches, l'outil identifie un Top 5 que l'on analyse et compare à la commune actuelle de la personne accompagnée.
**Rôle** : Tu es le Scout ODIS. Tu épaules le travailleur social pour trouver les infrastructures locales pertinentes pour le projet de vie de la personne accompagnée.
**Objectif** : Rapporter le résultat d'une analyse poussée sur la commune demandée.
**Ton** : Hyper synthétique, direct, factuel.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

**Instructions** :
1. **Gestion du Focus** : La localité d'intérêt est la `Ville analysée` dans le contexte.
2. Sois efficace et ne cherche JAMAIS deux fois la même chose. Fais particulièrement attention aux `Notes qualitatives` pour tes recherches.
3. **Recherche de Terrain** : Effectue TOUTES les recherches suivantes en choisissant le bon outil :
    - Utilise SYSTEMATIQUEMENT `search_refugee_associations_tool` pour trouver des associations spécialisées dans l'aide aux réfugiés.
    - Utilise SYSTEMATIQUEMENT `search_ccas_tool` pour trouver le Centre Communal d'Action Social local.
    - Utilise SYSTEMATIQUEMENT `search_places_batch_tool` UNIQUEMENT pour trouver des POIs pertinents au regard du contexte:
        - Des infrastructures de transports (ex: gares, gares routières)
        - Des commerces spécialisés (ex: boucherie halal, épicerie asiatique) **si mentionnés dans les notes qualitatives**
        - Des lieux de culte **pertinents** hors églises (ex: pagode, mosquée, temple)
        - Lieux d'hébergement et d'insertion (ex: CPH, CHRS, CADA)
    - Utilise `search_rna_rag_batch_tool` pour trouver des associations pertinentes pour leur insertion (loisirs, affinités culturelles, solidarité).
    - Utilise `compute_routes_tool` pour calculer les temps de trajet (ex: vers préfecture).

5. **Réponse (STRUCTURED)** :
    - Tu DOIS retourner un objet `ScoutResult`.
    - `searched` : Une phrase courte listant les outils/recherches effectués.
    - `result` : Ton analyse factuelle, argumentative et concise (incluant systématiquement le CCAS trouvé). Vise 250 mots minimum et ne garde que ce qui est pertinent au regard du dossier.
"""

SCOUT_SPECIFIC_SYSTEM_PROMPT = """
**Contexte** : Un Travailleur Social cherche la ville la plus adéquate pour une personne réfugiée (et sa famille). A partir de critères de recherches, l'outil identifie un Top 5 que l'on analyse et compare à la commune actuelle de la personne accompagnée.
**Rôle** : Tu es le Scout ODIS. Ta mission est de compléter une analyse existante en effectuant des recherches locales additionnelles.
**Objectif** : Fournir des informations d'actualité, de contexte social et de veille sur la ville de réinstallation.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

**Instructions** :
1. Si la `Dernière question` peut être répondue avec les `Connaissances actuelles (Scout)` ne fais rien.
2. Si des données manquent pour répondre à la `Dernière question` :
    - Utilise `search_refugee_associations_tool` pour trouver des associations de support aux réfugiés.
    - Utilise `search_rna_rag_batch_tool` pour trouver des associations pertinentes (loisirs, affinités culturelles, solidarité).
    - Utilise `search_places_batch_tool` pour trouver des POIs (écoles, parcs, commerces, lieux de culte).
    - Utilise `compute_routes_tool` pour calculer les temps de trajet.

3. **Réponse (STRUCTURED)** :
    - Tu DOIS retourner un objet `ScoutResult`.
    - `searched` : Résumé des recherches additionnelles effectuées.
    - `result` : Réponse à la question basée sur les nouvelles recherches ou les connaissances actuelles.
"""

scout_agent = Agent(
    get_model("scout"),
    deps_type=ODISDeps,
    output_type=ScoutResult
)

@scout_agent.system_prompt
async def scout_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds Scout prompt using ODISContextBuilder."""
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "scout")
    mode = ctx.deps.state.execution_mode
    prompt_template = SCOUT_ANALYSIS_SYSTEM_PROMPT if mode in ["analysis", "full_analysis"] else SCOUT_SPECIFIC_SYSTEM_PROMPT
    
    prompt = prompt_template.format(DATA_CONTEXT=data_context)
    return prompt

# --- Tools ---


@scout_agent.tool
async def search_places_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des lieux (POIs) en mode batch.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        queries (List[str]): Liste des requêtes.
        location (str): Nom de la ville suivi du nom de la région (ex: 'Bordeaux, Nouvelle-Aquitaine')
    
    Returns:
        Dict[str, Any]: Dictionnaire des lieux correspondants.
    """
    logger.info(f"🔍 [SCOUT] search_places_batch_tool async: {queries} in {location}")
    return await search_places_batch(queries, location)

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
async def search_rna_rag_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], codgeo: str, top_k: int = 10) -> Dict[str, Any]:
    """Recherche sémantique d'associations en mode batch. 
    
    Permet d'effectuer plusieurs recherches distinctes en un seul appel.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        queries (List[str]): Liste de termes de recherche courts (ex: ['football', 'aide alimentaire']).
        codgeo: Code INSEE de la commune (5 chiffres).
        top_k (int): Nombre de résultats par terme.
    
    Returns:
        Dict[str, Any]: Dictionnaire mappant chaque requête à ses résultats.
    """
    return await search_rna_rag_batch(queries, codgeo, top_k=top_k)

@scout_agent.tool
def search_ccas_tool(ctx: RunContext[ODISDeps], codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les informations du CCAS (Centre Communal d'Action Sociale) pour une commune.
    
    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
        codgeo (str): Code INSEE de la commune (ex: '33063').
    """
    return search_ccas(codgeo)
