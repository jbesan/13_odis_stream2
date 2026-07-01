import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings, get_swarm_boilerplate
from .tools import (
    search_job_offers_batch,
    get_job_details,
    search_referentiels_batch,
    search_inclusion_jobs_batch,
    get_inclusion_job_details,
)

logger = logging.getLogger("job_hunter_agent_v2")


class JobHunterResult(BaseModel):
    searched: str = Field(..., description="Liste des codes ROME et lieux recherchés.")
    result: str = Field(
        ..., description="Synthèse des offres d'emploi pertinentes trouvées."
    )


class SearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(
        ..., description="Domaine de recherche possibles:['rome_codes', 'communes']."
    )


class JobSearchQuery(BaseModel):
    location: Optional[str] = Field(
        None, description="Code INSEE de la commune (ex: 33063)"
    )
    rome: Optional[str] = Field(
        None, description="Code métier ROME de 5 caractères (ex: D1102)"
    )


JOB_HUNTER_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}
**Rôle** : Agent thématique Emploi (Job Hunter) / Expert du marché de l'emploi.
**Règle** : Reste STRICTEMENT sur l'Emploi (offres France Travail/SIAE, adéquation métier, détails d'offres). Ne traite aucun autre sujet (logement, transport, santé, école, association/intégration générale), d'autres experts s'en chargent.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES CRITIQUES DE TRAVAIL** :
1. **Frugalité & Précision** : Sois chirurgical (maximum 1 ou 2 requêtes d'offres/recherche en batch). Ne fais pas de recherches répétitives.
2. **Priorisation et Outils** :
   - Pour la recherche d'offres France Travail : vérifie TOUJOURS si des offres correspondantes pré-chargées sont disponibles sous `Données emploi et formation`. Si oui, **n'appelle pas** `search_job_offers_batch_tool`, utilise-les directement.
   - Pour obtenir le détail d'une offre (lorsqu'un ID d'offre est demandé ou spécifié dans ta mission) : appelle immédiatement `get_job_details_tool` pour cet ID.
   - Pour les métiers en insertion : utilise `search_inclusion_jobs_batch_tool` si demandé.
3. **Réponse (STRUCTURED)** : Tu DOIS retourner un objet `JobHunterResult`.
   - `searched` : Liste concise des codes ROME, localisations ou IDs consultés.
   - `result` : Ton analyse détaillée des opportunités d'emploi correspondantes ou le détail structuré de l'offre consultée.
"""


async def search_job_offers_batch_tool(
    searches: List[JobSearchQuery],
) -> Dict[str, Any]:
    """
    Version optimisée pour effectuer plusieurs recherches d'offres d'emploi en UN SEUL tour.
    Utilise cet outil pour trouver des opportunités concrètes pour tous les métiers identifiés.

    Args:
        searches: Liste d'objets JobSearchQuery {location, rome}
    """
    return await search_job_offers_batch([s.model_dump() for s in searches])


def get_job_details_tool(job_id: str) -> Dict[str, Any]:
    """Recherche des détails d'une offre d'emploi (utilise soit FT soit SIAE selon l'ID)."""
    # Simple heuristic: if ID has letters it might be FT, if it's numeric/longer it might be SIAE
    # Better: try both if unsure, or Job Hunter can decide based on previous results.
    if len(job_id) < 10:  # France Travail IDs are usually 7-8 chars
        return get_job_details(job_id)
    return get_inclusion_job_details(job_id)


async def search_inclusion_jobs_batch_tool(
    searches: List[JobSearchQuery],
) -> Dict[str, Any]:
    """
    Recherche d'offres d'insertion (SIAE) en mode Batch.
    À utiliser spécifiquement pour les publics en insertion.

    Args:
        searches: Liste d'objets JobSearchQuery {location, rome}
    """
    return await search_inclusion_jobs_batch([s.model_dump() for s in searches])


def get_inclusion_job_details_tool(siae_id: str) -> Dict[str, Any]:
    """Détails d'une structure SIAE et ses offres."""
    return get_inclusion_job_details(siae_id)


async def search_referentiels_batch_tool(
    searches: List[SearchQuery],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    Utilise cet outil si tu as plusieurs informations à normaliser (ex: ville + métier).

    Args:
        searches (List[SearchQuery]): Liste d'objets {query, domain}
    """
    return await search_referentiels_batch([s.model_dump() for s in searches])


job_hunter_agent = Agent(
    get_model("job_hunter"),
    model_settings=get_model_settings("job_hunter"),
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
    data_context = ODISContextBuilder.agent_context(state, "job_hunter")
    mission = state.expert_tasks.get(
        "job_hunter", "Analyse générale des opportunités d'emploi."
    )
    skill_inst = state.expert_skill_instructions.get(
        "job_hunter", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return JOB_HUNTER_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context,
        MISSION=mission,
        SKILL_INSTRUCTIONS=skill_inst,
    )
