import logging
from typing import List, Dict, Any
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import WebSearch
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import (
    search_refugee_associations,
    search_rna_rag_batch,
    search_ccas,
    search_places_batch,
)

logger = logging.getLogger("social_integration_expert")


class SocialIntegrationResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(
        ..., description="Analyse détaillée des découvertes sur l'intégration sociale."
    )


SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}
**Rôle** : Agent thématique Accompagnement Social & Intégration (Social Integration Expert).
**Règle** : Reste STRICTEMENT sur l'Intégration Sociale (CCAS local, associations d'aide, cours de français/FLE, loisirs/sports). Ne traite aucun autre sujet (logement, transport, santé, écoles, emploi), d'autres experts s'en chargent.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Recherches Web** : Utilise Google Search avec parcimonie:  limite-toi à maximum 1 recherche par objet de recherche/sujet distinct. Ne fais JAMAIS de requêtes similaires, de reformulations ou de variations pour un même sujet. Si l'information est introuvable après un essai, n'insiste pas et signale-le.
2. **Priorisation des outils** : Utilise en priorité `search_ccas_tool`, `search_refugee_associations_tool`, `search_rna_rag_batch_tool` et `search_places_batch_tool` (FLE, sports, centres sociaux, mairies).
3. **Formatage** : Sois hyper concis dans tes réponses.
"""


def search_refugee_associations_tool(codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les associations dédiées à l'aide aux réfugiés pour une commune."""
    return search_refugee_associations(codgeo)


async def search_rna_rag_batch_tool(
    queries: List[str], codgeo: str, top_k: int = 10
) -> List[Dict[str, Any]]:
    """
    Recherche sémantique d'associations d'inclusion, sport, loisirs ou solidarité locale (RNA).

    Args:
        queries: Liste de termes de recherche.
                 ATTENTION : Ne mets JAMAIS le nom de la ville dans ces requêtes car le filtrage géographique est déjà géré par l'outil via `codgeo`.
                 Exemple correct : ['cours de langue FLE', 'accompagnement administratif'].
                 Exemple incorrect : ['FLE Aix-en-Provence'].
        codgeo: Code INSEE de la commune.
        top_k: Nombre maximum de résultats.
    """
    return await search_rna_rag_batch(queries, codgeo, top_k=top_k)


def search_ccas_tool(codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les coordonnées du CCAS pour une commune."""
    return search_ccas(codgeo)


async def search_places_batch_tool(queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des centres sociaux, mairies, bibliothèques ou autres équipements en mode batch.
    Args:
        queries: Liste de requêtes (ex: ['centre social', 'mairie', 'MJC']).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)


social_integration_expert_agent: Agent[ODISDeps, SocialIntegrationResult] = create_agent(
    "social_integration_expert",
    deps_type=ODISDeps,
    tools=[
        search_refugee_associations_tool,
        search_rna_rag_batch_tool,
        search_ccas_tool,
        search_places_batch_tool,
    ],
    capabilities=[WebSearch()],
    output_type=SocialIntegrationResult,
)


@social_integration_expert_agent.system_prompt
async def social_integration_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "social_integration_expert")
    mission = state.expert_tasks.get(
        "social_integration_expert",
        "Analyse générale de l'intégration sociale et du tissu associatif.",
    )
    skill_inst = state.expert_skill_instructions.get(
        "social_integration_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context,
        MISSION=mission,
        SKILL_INSTRUCTIONS=skill_inst,
    )
