import logging
from typing import List, Dict, Any
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import WebSearch
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import search_places_batch

logger = logging.getLogger("education_expert")


class EducationResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
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

**DIRECTIVES DE TRAVAIL** :
1. **Frugalité & Précision (Recherche Web)** : Limite au MAXIMUM tes appels à Google Search. Fais au maximum 1 seule requête par objet de recherche/sujet distinct. Ne fais JAMAIS de requêtes similaires, de reformulations ou de variations pour un même sujet. Si l'information est introuvable après un essai, n'insiste pas et signale-le.
2. **Priorisation des outils** : Utilise en priorité `search_places_batch_tool` pour localiser les crèches et établissements scolaires. N'utilise Google Search qu'en dernier recours (ex. pour les modalités d'inscription spécifiques du site internet de la mairie).
3. **Analyse de terrain** : Identifie les niveaux scolaires des enfants dans le dossier.
4. **Réponse (Structured)** : Tu DOIS retourner un objet `EducationResult`.
   - `searched` : Liste concise des requêtes ou outils utilisés.
   - `result` : Ton analyse détaillée et factuelle des écoles locales, avec les coordonnées principales des structures et les étapes d'inscription parentale.
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
    capabilities=[WebSearch()],
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
