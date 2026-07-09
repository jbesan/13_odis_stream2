import logging
from typing import List, Dict, Any
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import WebSearch
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import (
    search_places_batch,
    compute_routes,
)

logger = logging.getLogger("mobility_expert")


class MobilityResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(
        ..., description="Analyse détaillée des découvertes sur la mobilité."
    )


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
1. **Recherches Web** : Utilise Google Search mais limite-toi au maximum 1 seule requête par objet de recherche/sujet distinct. Ne fais JAMAIS de requêtes similaires, de reformulations ou de variations pour un même sujet. Si l'information est introuvable après un essai, n'insiste pas et signale-le.
2. **Priorisation des outils** : Utilise en priorité `compute_routes_tool` et `search_places_batch_tool` pour les itinéraires et infrastructures de transport locaux. N'utilise Google Search qu'en dernier recours pour des tarifs ou aides spécifiques.
3. **Analyse factuelle** : Appuies-toi au maximum sur les données chiffrées du dossier. Ne fais pas de suppositions. 
4. **Formatage** : Sois hyper concis dans tes réponses.
"""


async def search_places_batch_tool(queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des infrastructures de transport ou des POIs en mode batch.
    Args:
        queries: Liste de requêtes (ex: ['gare routière', 'gare SNCF']).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)


def compute_routes_tool(
    origin: str, destination: str, mode: str = "transit"
) -> Dict[str, Any]:
    """Calcule des itinéraires et temps de trajet entre 2 localisations."""
    return compute_routes(origin, destination, mode)


mobility_expert_agent: Agent[ODISDeps, MobilityResult] = create_agent(
    "mobility_expert",
    deps_type=ODISDeps,
    tools=[search_places_batch_tool, compute_routes_tool],
    capabilities=[WebSearch()],
    output_type=MobilityResult,
)


@mobility_expert_agent.system_prompt
async def mobility_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "mobility_expert")
    mission = state.expert_tasks.get(
        "mobility_expert",
        "Analyse générale de la mobilité et des réseaux de transport.",
    )
    skill_inst = state.expert_skill_instructions.get(
        "mobility_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return MOBILITY_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context,
        MISSION=mission,
        SKILL_INSTRUCTIONS=skill_inst,
    )
