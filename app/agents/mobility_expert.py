import logging
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import (
    search_places_batch_tool,
    compute_routes_tool,
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

# Projet de vie du bénéficiaire (Briefing du Travailleur Social) :
{DOSSIER_BRIEFING}

# Critères de recherche du foyer :
```json
{CRITERIA_CONTEXT}
```

# Commune à analyser :
```json
{COMMUNE_CONTEXT}
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


mobility_expert_agent: Agent[ODISDeps, MobilityResult] = create_agent(
    "mobility_expert",
    deps_type=ODISDeps,
    tools=[search_places_batch_tool, compute_routes_tool],
    output_type=MobilityResult,
)


@mobility_expert_agent.system_prompt
async def mobility_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    contexts = ODISContextBuilder.expert_prompt_contexts(
        state, "mobility_expert"
    )
    skill_inst = state.expert_skill_instructions.get(
        "mobility_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return MOBILITY_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DOSSIER_BRIEFING=contexts.briefing,
        CRITERIA_CONTEXT=contexts.criteria,
        COMMUNE_CONTEXT=contexts.commune,
        SPECIFIC_CONTEXT=contexts.specific,
        SKILL_INSTRUCTIONS=skill_inst,
    )
