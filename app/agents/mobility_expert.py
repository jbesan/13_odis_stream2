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
    # Champ réservé à un futur mode « juge/audit » (désactivé volontairement).
    # Il devra être produit uniquement à partir des appels effectivement
    # observés, et rester distinct de l'analyse finale pour éviter les doublons.
    #
    # searched: str = Field(
    #     ...,
    #     max_length=300,
    #     description=(
    #         "Résumé factuel et très court des recherches exécutées : "
    #         "outils/thèmes généraux et compteurs uniquement. "
    #         "Aucun résultat, URL, adresse, citation, note ou Markdown."
    #     ),
    # )
    result: str = Field(
        ..., description="Analyse détaillée des découvertes sur la mobilité."
    )


MOBILITY_EXPERT_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}

# Contexte commun du dossier (préfixe stable entre experts) :
```json
{COMMON_CONTEXT}
```

# Contexte spécifique à la mobilité :
```json
{SPECIFIC_CONTEXT}
```

**Rôle** : Agent thématique Mobilité (Mobility Expert).
**Règle** : Reste STRICTEMENT sur la Mobilité (transports, temps de trajet, aides au permis, tarifs transports). Ne traite aucun autre sujet (logement, santé, école, association/intégration, emploi), d'autres experts s'en chargent.

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

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
    capabilities=[WebSearch(max_uses=1)],
    output_type=MobilityResult,
)


@mobility_expert_agent.system_prompt
async def mobility_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    common_context, specific_context = ODISContextBuilder.expert_prompt_contexts(
        state, "mobility_expert"
    )
    skill_inst = state.expert_skill_instructions.get(
        "mobility_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return MOBILITY_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        COMMON_CONTEXT=common_context,
        SPECIFIC_CONTEXT=specific_context,
        SKILL_INSTRUCTIONS=skill_inst,
    )
