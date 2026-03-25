
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps, FocusCity
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

    1. **INTERVIEWER** : Pour modifier ou ajouter des critères de recherche.
    2. **SCORER** : Lancer un calcul de score pour retourner un premier Top 5 communes selon les critères collectés.
    3. **ANALYSIS** : Analyse conjuguée SEULEMENT pour une première exploration à 360° d'une commune de `Villes identifiées`.
    4. **SYNTHESIZER** : Pour formuler une réponse argumentée au Travailleur Social

Pour répondre à une question SPECIFIQUE sur la ville identifée 
    1. **SCOUT** : Pour trouver les resources d'une ville (infrastructures, associations, temps de trajets).
    2. **JOB_HUNTER** : Pour trouver des données sur l'emploi (offres détaillées, recherches métiers précises).
    3. **WEB** : Pour toute autre question PRÉCISE  sur la ville (logement, actualités, contexte).
    
    

**Extraction de la ville cible** :
- Si l'utilisateur exprime l'intention d'analyser une ville spécifique ou pose une question sur une ville, identifie-la.
- Recoupe-la avec les `Villes identifiées` pour obtenir le code INSEE et retourne l'objet `FocusCity` correspondant.
- Si le contexte contient déjà une `Ville cible` et que l'utilisateur n'en change pas explicitement, conserve la ville actuelle.
"""

class RoutingResult(BaseModel):
    target_agent: Literal['interviewer', 'scorer', 'analysis', 'scout', 'web', 'job_hunter', 'synthesizer']
    focus_city: Optional[FocusCity] = Field(None, description="La ville cible identifiée pour l'analyse")
    model_config = ConfigDict(populate_by_name=True)

router_agent = Agent(
    get_model("router"),
    deps_type=ODISDeps,
    output_type=RoutingResult
)

@router_agent.system_prompt
async def router_instructions(ctx: RunContext[ODISDeps]) -> str:
    results = ctx.deps.state.search_results.results if ctx.deps.state.search_results else []
    top_cities_names = [getattr(c, 'name', str(c)) for c in results]
    return ROUTING_SYSTEM_PROMPT.format(
        CITIES_IDENTIFIED=str(top_cities_names),
        BRIEFING=ctx.deps.state.odis_brief or "(Pas encore de briefing)",
        FOCUS_CITY=ctx.deps.state.focus_city or "Non définie",
        INTERVIEW_COMPLETED=ctx.deps.state.is_interview_complete
    ).replace("Communes identifiées", "Villes identifiées")



