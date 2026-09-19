import logging
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import (
    search_job_offers_batch_tool,
    get_job_details_tool,
    search_inclusion_jobs_batch_tool,
    get_inclusion_job_details_tool,
    search_referentiels_batch_tool,
)

logger = logging.getLogger("job_hunter_agent_v2")


class JobHunterResult(BaseModel):
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
        ..., description="Synthèse des offres d'emploi pertinentes trouvées."
    )


JOB_HUNTER_SYSTEM_PROMPT = """
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

# Contexte spécifique à l'emploi :
```json
{SPECIFIC_CONTEXT}
```

**Rôle** : Agent thématique Emploi (Job Hunter) / Expert du marché de l'emploi.
**Règle** : Reste STRICTEMENT sur l'Emploi (offres France Travail/SIAE, adéquation métier, détails d'offres). Ne traite aucun autre sujet (logement, transport, santé, école, association/intégration générale), d'autres experts s'en chargent.

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Priorisation et Outils** :
   - Pour la recherche d'offres France Travail : vérifie TOUJOURS si des offres correspondantes pré-chargées sont disponibles sous `Données emploi et formation`. Si oui, **n'appelle pas** `search_job_offers_batch_tool`, utilise-les directement.
   - Pour obtenir le détail d'une offre : appelle immédiatement `get_job_details_tool` pour cet ID.
   - Pour les métiers en insertion : utilise `search_inclusion_jobs_batch_tool` si demandé.
"""


job_hunter_agent: Agent[ODISDeps, JobHunterResult] = create_agent(
    "job_hunter",
    deps_type=ODISDeps,
    tools=[
        search_job_offers_batch_tool,
        get_job_details_tool,
        search_inclusion_jobs_batch_tool,
        get_inclusion_job_details_tool,
        search_referentiels_batch_tool,
    ],
    output_type=JobHunterResult,
)


@job_hunter_agent.system_prompt
async def job_hunter_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds Job Hunter agent prompt using ODISContextBuilder."""
    state = ctx.deps.state
    contexts = ODISContextBuilder.expert_prompt_contexts(
        state, "job_hunter"
    )
    skill_inst = state.expert_skill_instructions.get(
        "job_hunter", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return JOB_HUNTER_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DOSSIER_BRIEFING=contexts.briefing,
        CRITERIA_CONTEXT=contexts.criteria,
        COMMUNE_CONTEXT=contexts.commune,
        SPECIFIC_CONTEXT=contexts.specific,
        SKILL_INSTRUCTIONS=skill_inst,
    )
