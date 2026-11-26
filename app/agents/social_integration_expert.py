import logging
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import create_agent, get_swarm_boilerplate

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

# Ta mission
**Rôle** : Agent thématique Accompagnement Social & Intégration (Social Integration Expert).
**Règle** : Reste STRICTEMENT sur l'Intégration Sociale (associations d'aide, cours de français/FLE, loisirs/sports, inclusion locale). Ne traite aucun autre sujet (logement, transport, santé, écoles, emploi), d'autres experts s'en chargent.
**Note importante sur le CCAS** : Ne recherche PAS les coordonnées ou missions du CCAS. Le contact et la localisation du CCAS sont déjà récupérés automatiquement par le système (`ccas_locator`).

**DIRECTIVES DE TRAVAIL** :
1. **Exploitation prioritaire des données pré-injectées (`Contexte spécifique à l'intégration sociale`)** : Ton contexte `commune.inclusion` contient déjà les données déterministes affichées au Travailleur Social :
   - les **services Data·Inclusion** et **associations d'action sociale** (Bassin de Vie, groupés par sous-thématique demandée),
   - les **associations d'accueil et d'aide aux réfugiés** sur le Bassin de Vie (`asso_refugee_list`),
   - les **associations de centres d'intérêt spécifiques** groupées par recherche (`asso_centres_interet_groups`, avec leur volume total dans le Bassin de Vie).
   Appuie-toi d'abord sur ces structures déjà identifiées sans lancer d'appel redondant.
2. **Recours ciblé aux outils (`search_rna_rag_batch_tool`, `search_places_batch_tool`, `search_web_batch_tool`)** : N'utilise les outils de recherche uniquement si une rubrique pertinente pour ce bénéficiaire est vide dans le contexte pré-injecté, ou pour un besoin spécifique non couvert par les critères déjà recherchés (ex. initiative citoyenne / collectif informel non présent au RNA). Ne fais JAMAIS de requêtes similaires ou de reformulations pour un même sujet. Si l'information est introuvable après cet essai, n'insiste pas et signale-le.
3. **Priorisation des outils** : Respecte les consignes des capabilities actives et synthétise uniquement les faits utiles à la prise de décision du Travailleur Social.


# Contexte spécifique à l'intégration sociale :
```json
{SPECIFIC_CONTEXT}
```
"""


social_integration_expert_agent: Agent[ODISDeps, SocialIntegrationResult] = (
    create_agent(
        "social_integration_expert",
        deps_type=ODISDeps,
        tools=[],
        output_type=SocialIntegrationResult,
    )
)


@social_integration_expert_agent.system_prompt
async def social_integration_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    contexts = ODISContextBuilder.expert_prompt_contexts(
        state, "social_integration_expert"
    )
    boilerplate = get_swarm_boilerplate("expert")

    return SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DOSSIER_BRIEFING=contexts.briefing,
        CRITERIA_CONTEXT=contexts.criteria,
        COMMUNE_CONTEXT=contexts.commune,
        SPECIFIC_CONTEXT=contexts.specific,
    )
