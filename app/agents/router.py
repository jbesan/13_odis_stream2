
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model

logger = logging.getLogger("router_agent")

ROUTING_SYSTEM_PROMPT = """
**Rôle** : Tu es le Cerveau de l'Assistant ODIS. Ton job est de router le message de l'utilisateur vers le bon agent spécialisé.

**Agents disponibles** :
1. **INTERVIEWER** : Pour la collecte de besoins (phase initiale puis si besoin pour l'ajout des information supplémentaires).
2. **SCORER** : Pour calculer le Top 5 villes (quand l'utilisateur confirme ou demande le résultat).
3. **DECORATION** : Cascade Scout + Web + Job Hunter. Utilise-la UNIQUEMENT quand l'utilisateur demande "plus d'infos" ou "des détails" sur une ville DÉJÀ identifiée ou affichée dans le Top 5.
4. **SCOUT** : Pour une question spécifique de vie locale (ex: "Temps trajet prefecture").
5. **WEB** : Pour des recherches d'actualités/contextuelles.
6. **JOB_HUNTER** : Pour une question spécifique emploi (ex: "Offres boulangerie").

** Stratégie de routage (CRITIQUE) ** :
- Si l'utilisateur décrit sa situation -> **INTERVIEWER**.
- Si validation finale -> **SCORER**.
- Si "plus d'infos" sur une ville -> **DECORATION**.
- Si question précise sur un résultat -> **SCOUT** ou **JOB_HUNTER**.
- Si modif ou ajout de critère -> **INTERVIEWER**.

**Contexte Actuel** :
- Phase Actuelle : {PHASE}
- Villes identifiées : {CITIES_COUNT}
- Critères présents : {CRITERIA_KEYS}

**Dossier (Briefing)** :
{BRIEFING}

** Extraction du Contexte (CRITIQUE) ** :
- Si l'utilisateur mentionne une ville cible (ex: "Bordeaux", "Carcassonne") ou y fait référence ("Celle-ci", "La première"), identifie-la et remplis `focus_city`.
- **Note** : Ne confonds pas avec `commune_actuelle` (où l'utilisateur vit actuellement). `focus_city` est la ville sur laquelle il veut des détails.

**DIRECTIVE DE SORTIE** : Réponds toujours de manière hyper concise et structurée selon le schéma RoutingResult fourni.
"""

class RoutingResult(BaseModel):
    target_agent: Literal['interviewer', 'scorer', 'decoration', 'scout', 'web', 'job_hunter']
    focus_city: Optional[str] = Field(None, description="The name of the city the user is currently interested in (if mentioned).")
    reasoning: str = Field(..., description="Why this agent was selected in a few words.")
    model_config = ConfigDict(populate_by_name=True)

router_agent = Agent(
    get_model("router"),
    deps_type=ODISDeps,
    output_type=RoutingResult
)

@router_agent.system_prompt
async def router_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Prepare context variables
    phase = "DISCOVERY" # Default, or we track it in state
    
    cities_count = len(ctx.deps.state.top_cities)
    # Summary for the router
    criteria = ctx.deps.state.search_criteria
    city = criteria.commune_actuelle
    if hasattr(city, 'label'): city = city.label
    
    metiers_count = sum(len(m) for m in criteria.codes_metiers)
    
    criteria_summary = (
        f"- Commune: {city or 'Non renseignée'}\n"
        f"- Métiers: {metiers_count}\n"
        f"- Profil: {criteria.weight_profile or 'Non défini'}\n"
        f"- Zone: {criteria.loc_search_area or 'Non définie'}"
    )

    return ROUTING_SYSTEM_PROMPT.format(
        PHASE="N/A",
        CITIES_COUNT=str(cities_count),
        CRITERIA_KEYS=criteria_summary,
        BRIEFING=ctx.deps.state.briefing or "(Pas encore de briefing)"
    )
