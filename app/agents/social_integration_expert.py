import logging
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate
from .tools import (
    search_rna_rag_batch_tool,
    search_places_batch_tool,
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
1. **Recherches Web & Exploration terrain** : Si les outils fiables ne suffisent pas sur un point essentiel, utilise le tool `search_web_batch_tool` en regroupant les recherches nécessaires. Ne fais JAMAIS de requêtes similaires ou de reformulations pour un même sujet. Si l'information est introuvable après cet essai, n'insiste pas et signale-le.
2. **Associations d'aide aux réfugiés (RNA)** : Les associations d'accueil et d'aide aux réfugiés issues du Répertoire National des Associations (RNA) officiel sont déjà injectées dans ton contexte (`Données inclusion`). Si aucune association n'est recensée au RNA officiel, tu peux vérifier (via Google Search ou Google Maps / Places) s'il existe des collectifs locaux, antennes citoyennes ou initiatives informelles non répertoriées au RNA si cela apporte une valeur directe au bénéficiaire.
3. **Priorisation des outils** : Utilise en priorité `search_rna_rag_batch_tool` pour les besoins associatifs (loisirs, sports, culture, entraide). Utilise `search_places_batch_tool` pour les structures institutionnelles indispensables (FLE, centres sociaux, mairies). Utilise `search_web_batch_tool` seulement pour les lacunes essentielles restantes. Ne cherche PAS le CCAS.
"""


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
    contexts = ODISContextBuilder.expert_prompt_contexts(
        state, "social_integration_expert"
    )
    skill_inst = state.expert_skill_instructions.get(
        "social_integration_expert", "Aucune consigne spécifique de Skill Card active."
    )
    boilerplate = get_swarm_boilerplate("expert")

    return SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DOSSIER_BRIEFING=contexts.briefing,
        CRITERIA_CONTEXT=contexts.criteria,
        COMMUNE_CONTEXT=contexts.commune,
        SPECIFIC_CONTEXT=contexts.specific,
        SKILL_INSTRUCTIONS=skill_inst,
    )
