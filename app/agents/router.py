
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model

logger = logging.getLogger("router_agent")

ROUTING_SYSTEM_PROMPT = """
**Rôle** : Tu es le Cerveau de l'Assistant ODIS. Ton job est de router le message de l'utilisateur vers le bon agent spécialisé.

**Contexte Actuel** :
- Villes identifiées : {CITIES_IDENTIFIED}
- Ville cible : {FOCUS_CITY}
- Interview terminée : {INTERVIEW_COMPLETED}

**Dossier (Briefing)** :
{BRIEFING}

**Agents disponibles** :
    1. **SCORER** : Lancer un calcul de score pour retourner un premier Top 5 communes selon les critères collectés.
    2. **ANALYSIS** : Analyse approfondie (Scout + Web + Job Hunter) pour explorer une commune de `Communes identifiées`.
    3. **SCOUT** : Pour une question spécifique de vie locale (ex: "Temps trajet prefecture", "associations présentes").
    4. **WEB** : Pour des recherches d'actualités/contextuelles.
    5. **JOB_HUNTER** : Pour une question spécifique emploi (ex: "Offres boulangerie").
    6. **SYNTHESIZER** : Pour formuler le pitch final argumenté avec toutes les données évaluées et collectées
    7. **INTERVIEWER** : Pour modifier ou ajouter des critères de recherche.
"""
# ** Extraction de la Commune cible (CRITIQUE) ** :
# - Si l'utilisateur mentionne une commune cible (ex: "Bordeaux" ou y fait référence (ex: "La première"), identifie-la et retourne la valeur dans `focus_city`.
# """

class RoutingResult(BaseModel):
    target_agent: Literal['interviewer', 'scorer', 'analysis', 'scout', 'web', 'job_hunter', 'synthesizer']
    # focus_city: Optional[str] = Field(None, description="The name of the city the user is currently interested in (if mentioned).")
    # reasoning: str = Field(..., description="Why this agent was selected in a few words.")
    model_config = ConfigDict(populate_by_name=True)

router_agent = Agent(
    get_model("router"),
    deps_type=ODISDeps,
    output_type=RoutingResult
)

@router_agent.system_prompt
async def router_instructions(ctx: RunContext[ODISDeps]) -> str:
    top_cities_names = [getattr(c, 'name', str(c)) for c in ctx.deps.state.top_cities]
    return ROUTING_SYSTEM_PROMPT.format(
        CITIES_IDENTIFIED=str(top_cities_names),
        BRIEFING=ctx.deps.state.briefing or "(Pas encore de briefing)",
        FOCUS_CITY=ctx.deps.state.focus_city or "Non définie",
        INTERVIEW_COMPLETED=ctx.deps.state.is_interview_complete
    ).replace("Communes identifiées", "Villes identifiées")
