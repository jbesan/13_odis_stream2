import logging
from typing import List, Dict, Any
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import search_places_batch

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

# Contexte commun du dossier (préfixe stable entre experts) :
```json
{COMMON_CONTEXT}
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


async def search_places_batch_tool(queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des crèches, écoles maternelles, primaires, collèges ou lycées en mode batch.
    Args:
        queries: Liste de requêtes (ex: ['école primaire', 'collège', 'crèche']).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)


# async def search_rna_rag_batch_tool(
#     queries: List[str], codgeo: str, top_k: int = 10
# ) -> List[Dict[str, Any]]:
#     """
#     Recherche sémantique d'associations d'accompagnement scolaire ou de parents d'élèves (RNA).

#     Args:
#         queries: Liste de termes de recherche.
#                  ATTENTION : Ne mets JAMAIS le nom de la ville dans ces requêtes car le filtrage géographique est déjà géré par l'outil via `codgeo`.
#                  Exemple correct : ['cours de langue FLE', 'accompagnement administratif'].
#                  Exemple incorrect : ['FLE Aix-en-Provence'].
#         codgeo: Code INSEE de la commune.
#         top_k: Nombre maximum de résultats.
#     """
#     return await search_rna_rag_batch(queries, codgeo, top_k=top_k)


education_expert_agent: Agent[ODISDeps, EducationResult] = create_agent(
    "education_expert",
    deps_type=ODISDeps,
    tools=[search_places_batch_tool],
    output_type=EducationResult,
)


@education_expert_agent.system_prompt
async def education_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    common_context, specific_context = ODISContextBuilder.expert_prompt_contexts(
        state, "education_expert"
    )
    skill_inst = state.expert_skill_instructions.get(
        "education_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return EDUCATION_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        COMMON_CONTEXT=common_context,
        SPECIFIC_CONTEXT=specific_context,
        SKILL_INSTRUCTIONS=skill_inst,
    )
