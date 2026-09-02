import logging
from typing import List, Dict, Any
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import (
    search_rna_rag_batch,
    search_places_batch,
)

logger = logging.getLogger("social_integration_expert")


class SocialIntegrationResult(BaseModel):
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
        ..., description="Analyse détaillée des découvertes sur l'intégration sociale."
    )


SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}

# Contexte commun du dossier (préfixe stable entre experts) :
```json
{COMMON_CONTEXT}
```

# Contexte spécifique à l'intégration sociale :
```json
{SPECIFIC_CONTEXT}
```

**Rôle** : Agent thématique Accompagnement Social & Intégration (Social Integration Expert).
**Règle** : Reste STRICTEMENT sur l'Intégration Sociale (associations d'aide, cours de français/FLE, loisirs/sports, inclusion locale). Ne traite aucun autre sujet (logement, transport, santé, écoles, emploi), d'autres experts s'en chargent.
**Note importante sur le CCAS** : Ne recherche PAS les coordonnées ou missions du CCAS. Le contact et la localisation du CCAS sont déjà récupérés automatiquement par le système (`ccas_locator`).

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Recherches Web & Exploration terrain** : Si les outils fiables ne suffisent pas sur un point essentiel, utilise une seule fois le tool `search_web_batch_tool` avec toutes les recherches indépendantes regroupées dans une même liste. Ne fais JAMAIS de requêtes similaires, de reformulations ou de variations pour un même sujet. Si l'information est introuvable après cet essai, n'insiste pas et signale-le.
2. **Associations d'aide aux réfugiés (RNA)** : Les associations d'accueil et d'aide aux réfugiés issues du Répertoire National des Associations (RNA) officiel sont déjà injectées dans ton contexte (`Données inclusion`). Si aucune association n'est recensée au RNA officiel, tu peux vérifier (via Google Search ou Google Maps / Places) s'il existe des collectifs locaux, antennes citoyennes ou initiatives informelles non répertoriées au RNA si cela apporte une valeur directe au bénéficiaire.
3. **Priorisation des outils** : Utilise en priorité `search_rna_rag_batch_tool` (recherche sémantique RNA pour loisirs, sports, culture, entraide). N'utilise `search_places_batch_tool` qu'avec parcimonie pour des structures institutionnelles indispensables (FLE, centres sociaux, mairies, max 3 à 5 requêtes ciblées dans un seul batch). Utilise `search_web_batch_tool` seulement pour les lacunes essentielles restantes. Ne cherche PAS le CCAS.
"""


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


async def search_places_batch_tool(queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des centres sociaux, mairies ou structures FLE en mode batch.
    À utiliser avec parcimonie : un seul appel batch par mission regroupant au maximum 3 à 5 requêtes ciblées indispensables.
    Args:
        queries: Liste de requêtes ciblées (ex: ['centre social', 'cours de français FLE', 'mairie'], max 5).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)


social_integration_expert_agent: Agent[ODISDeps, SocialIntegrationResult] = (
    create_agent(
        "social_integration_expert",
        deps_type=ODISDeps,
        tools=[
            search_rna_rag_batch_tool,
            search_places_batch_tool,
        ],
        output_type=SocialIntegrationResult,
    )
)


@social_integration_expert_agent.system_prompt
async def social_integration_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    common_context, specific_context = ODISContextBuilder.expert_prompt_contexts(
        state, "social_integration_expert"
    )
    skill_inst = state.expert_skill_instructions.get(
        "social_integration_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        COMMON_CONTEXT=common_context,
        SPECIFIC_CONTEXT=specific_context,
        SKILL_INSTRUCTIONS=skill_inst,
    )
