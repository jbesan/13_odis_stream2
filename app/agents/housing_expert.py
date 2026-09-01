import logging
from typing import List, Dict, Any
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import (
    search_places_batch,
    compute_routes,
)

logger = logging.getLogger("housing_expert")


class HousingResult(BaseModel):
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
        ..., description="Analyse détaillée des découvertes sur le logement."
    )


HOUSING_EXPERT_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}

# Contexte commun du dossier (préfixe stable entre experts) :
```json
{COMMON_CONTEXT}
```

# Contexte spécifique au logement :
```json
{SPECIFIC_CONTEXT}
```

**Rôle** : Agent thématique Logement (Housing Expert).
**Règle** : Reste STRICTEMENT sur le Logement (loyer m², logement social, hébergements). Ne traite aucun autre sujet (transport, santé, école, association/intégration, emploi), d'autres experts s'en chargent.
**Note importante sur le CCAS** : Ne recherche PAS les coordonnées ou missions du CCAS. Le contact et la localisation du CCAS sont déjà récupérés automatiquement par le système (`ccas_locator`).

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

"""


async def search_places_batch_tool(queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des structures ou services de logement d'urgence en mode batch.
    À utiliser avec parcimonie : un seul appel batch par mission regroupant au maximum 3 à 5 requêtes ciblées indispensables.
    Args:
        queries: Liste de requêtes ciblées (ex: ['CHRS', 'CADA', 'CPH'], max 5).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)


def compute_routes_tool(
    origin: str, destination: str, mode: str = "transit"
) -> Dict[str, Any]:
    """Calcule des itinéraires et temps de trajet."""
    return compute_routes(origin, destination, mode)


housing_expert_agent: Agent[ODISDeps, HousingResult] = create_agent(
    "housing_expert",
    deps_type=ODISDeps,
    tools=[search_places_batch_tool, compute_routes_tool],
    output_type=HousingResult,
)


@housing_expert_agent.system_prompt
async def housing_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    common_context, specific_context = ODISContextBuilder.expert_prompt_contexts(
        state, "housing_expert"
    )
    skill_inst = state.expert_skill_instructions.get(
        "housing_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return HOUSING_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        COMMON_CONTEXT=common_context,
        SPECIFIC_CONTEXT=specific_context,
        SKILL_INSTRUCTIONS=skill_inst,
    )
