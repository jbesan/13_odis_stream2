import logging
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import search_places_batch_tool

logger = logging.getLogger("education_expert")


class EducationResult(BaseModel):
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
        ..., description="Analyse détaillée des découvertes sur l'éducation."
    )


EDUCATION_EXPERT_SYSTEM_PROMPT = """
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

# Contexte spécifique à l'éducation :
```json
{SPECIFIC_CONTEXT}
```

**Rôle** : Agent thématique Éducation (Education Expert).
**Règle** : Reste STRICTEMENT sur l'Éducation (crèches, écoles, collèges, lycées, modalités scolaires). Ne traite aucun autre sujet (logement, transport, santé, association/intégration, emploi), d'autres experts s'en chargent.

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

"""


education_expert_agent: Agent[ODISDeps, EducationResult] = create_agent(
    "education_expert",
    deps_type=ODISDeps,
    tools=[search_places_batch_tool],
    output_type=EducationResult,
)


@education_expert_agent.system_prompt
async def education_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    contexts = ODISContextBuilder.expert_prompt_contexts(
        state, "education_expert"
    )
    skill_inst = state.expert_skill_instructions.get(
        "education_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return EDUCATION_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DOSSIER_BRIEFING=contexts.briefing,
        CRITERIA_CONTEXT=contexts.criteria,
        COMMUNE_CONTEXT=contexts.commune,
        SPECIFIC_CONTEXT=contexts.specific,
        SKILL_INSTRUCTIONS=skill_inst,
    )

