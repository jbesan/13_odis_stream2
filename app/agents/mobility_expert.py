import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic import BaseModel, Field
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings, get_swarm_boilerplate
from .tools import (
    search_places_batch, 
    compute_routes, 
)

logger = logging.getLogger("mobility_expert")

class MobilityResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(..., description="Analyse détaillée des découvertes sur la mobilité.")

MOBILITY_EXPERT_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}
**Rôle** : Agent thématique Mobilité (Mobility Expert).
**Règle** : Reste STRICTEMENT sur la Mobilité (transports, temps de trajet, aides au permis, tarifs transports). Ne traite aucun autre sujet (logement, santé, école, association/intégration, emploi), d'autres experts s'en chargent.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Frugalité & Précision** : Sois chirurgical (maximum 1 ou 2 recherches web ciblées). Ne fais pas de recherches répétitives ou redondantes. Si l'information n'est pas disponible, indique-le simplement dans ton rapport final plutôt que d'insister.
2. **Priorisation des outils** : Utilise en priorité `compute_routes_tool` et `search_places_batch_tool` pour les itinéraires et infrastructures de transport locaux. N'utilise Google Search qu'en dernier recours pour des tarifs ou aides spécifiques.
3. **Analyse de terrain** : Interroge les données de transport en commun du dossier (nombre d'arrêts de bus, tram, métro, gares).
4. **Réponse (Structured)** : Tu DOIS retourner un objet `MobilityResult`.
   - `searched` : Liste concise des requêtes ou outils utilisés.
   - `result` : Ton analyse factuelle et argumentée sur la mobilité locale, incluant les temps de parcours calculés et les aides tarifaires identifiées.
"""

mobility_expert_agent = Agent(
    get_model("mobility_expert"),
    model_settings=get_model_settings("mobility_expert"),
    deps_type=ODISDeps,
    builtin_tools=[WebSearchTool()],
    output_type=MobilityResult
)

@mobility_expert_agent.system_prompt
async def mobility_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "mobility_expert")
    mission = state.expert_tasks.get("mobility_expert", "Analyse générale de la mobilité et des réseaux de transport.")
    skill_inst = state.expert_skill_instructions.get("mobility_expert", "Aucune consigne spécifique de Skill Card active.")
    boilerplate = get_swarm_boilerplate("expert")

    return MOBILITY_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context,
        MISSION=mission,
        SKILL_INSTRUCTIONS=skill_inst
    )


@mobility_expert_agent.tool
async def search_places_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des infrastructures de transport ou des POIs en mode batch.
    Args:
        queries: Liste de requêtes (ex: ['gare routière', 'gare SNCF']).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)

@mobility_expert_agent.tool
def compute_routes_tool(ctx: RunContext[ODISDeps], origin: str, destination: str, mode: str = "transit") -> Dict[str, Any]:
    """Calcule des itinéraires et temps de trajet."""
    return compute_routes(origin, destination, mode)


