
import logging
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model

logger = logging.getLogger("router_agent")

ROUTING_SYSTEM_PROMPT = """
**Rôle** : Tu es le Cerveau de l'Assistant ODIS. Ton job est de router le message de l'utilisateur vers le bon agent spécialisé.

**Agents disponibles** :
1. **INTERVIEWER** : Pour la collecte de besoins (phase initiale).
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
- Si modif de critère -> **INTERVIEWER**.

**Contexte Actuel** :
- Phase Actuelle : {PHASE}
- Villes identifiées : {CITIES_COUNT}
- Critères présents : {CRITERIA_KEYS}

**Dossier (Briefing)** :
{BRIEFING}

**DIRECTIVE DE SORTIE** : Réponds toujours de manière structurée selon le schéma RoutingResult fourni.
"""

class RoutingResult(BaseModel):
    target_agent: Literal['interviewer', 'scorer', 'decoration', 'scout', 'web', 'job_hunter']
    reasoning: str

    model_config = ConfigDict(populate_by_name=True)

router_agent = Agent(
    get_model("router"),
    deps_type=ODISDeps,
    output_type=RoutingResult,
    system_prompt=ROUTING_SYSTEM_PROMPT
)

@router_agent.system_prompt
async def router_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Prepare context variables
    phase = "DISCOVERY" # Default, or we track it in state
    
    cities_count = len(ctx.deps.state.top_cities)
    # We dump ONLY set fields to show what we have
    criteria_keys = list(ctx.deps.state.search_criteria.model_dump(exclude_unset=True).keys())
    
    prompt = ROUTING_SYSTEM_PROMPT.replace("{PHASE}", "N/A") 
    prompt = prompt.replace("{CITIES_COUNT}", str(cities_count))
    prompt = prompt.replace("{CRITERIA_KEYS}", ", ".join(criteria_keys))
    prompt = prompt.replace("{BRIEFING}", ctx.deps.state.briefing or "(Pas encore de briefing)")
    
    return prompt
