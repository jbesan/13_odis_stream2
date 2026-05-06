
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
from .state import GraphState, ODISDeps, FocusCity, ODISContextBuilder
from .agent_config import get_model, get_model_settings

logger = logging.getLogger("router_agent")

ROUTING_SYSTEM_PROMPT = """
**Contexte** : Un Travailleur Social cherche la ville la plus adéquate pour une personne réfugiée (et sa famille). A partir de critères de recherches, l'outil identifie un Top 5 que l'on analyse et compare à la commune actuelle de la personne accompagnée.
**Rôle** : Tu es le Cerveau de l'Assistant ODIS. Ton job est de router le message de l'utilisateur vers le bon agent spécialisé.

**Contexte Actuel (Dossier)** :
```json
{DATA_CONTEXT}
```

**Agents disponibles** :
    1. **ANALYSIS** : Analyse conjuguée SEULEMENT pour une première exploration à 360° d'une commune de `Villes identifiées`.
    2. **SYNTHESIZER** : Pour formuler une réponse argumentée au Travailleur Social SANS avoir besoin de faire de recherches supplémentaires.

Pour répondre à une question SPECIFIQUE sur la ville identifée :
    1. **SCOUT** : Pour trouver les resources d'une ville (infrastructures, associations, temps de trajets).
    2. **JOB_HUNTER** : Pour trouver des données sur l'emploi (offres détaillées, recherches métiers précises).
    3. **WEB** : Pour toute autre question PRÉCISE  sur la ville (logement, actualités, contexte).
    
    

**Extraction de la ville cible** :
- Si l'utilisateur exprime l'intention d'analyser une ville spécifique ou pose une question sur une ville, identifie-la.
- Recoupe-la avec les `Villes identifiées` fournies dans le contexte pour obtenir le NOM et le CODE INSEE exacts.
- Retourne l'objet `FocusCity` avec `name` et `codgeo` remplis.
- IMPORTANT : Ne propose d'analyse QUE pour les villes présentes dans `Villes identifiées`.
- Si le contexte contient déjà une `Ville cible` et que l'utilisateur n'en change pas explicitement, conserve la ville actuelle.
"""

class RoutingResult(BaseModel):
    target_agent: Literal['analysis', 'scout', 'web', 'job_hunter', 'synthesizer']
    focus_city: Optional[FocusCity] = Field(None, description="La ville cible identifiée pour l'analyse")
    model_config = ConfigDict(populate_by_name=True)

router_agent = Agent(
    get_model("router"),
    model_settings=get_model_settings("router"),
    deps_type=ODISDeps,
    output_type=RoutingResult
)

@router_agent.system_prompt
async def router_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds Router prompt using ODISContextBuilder."""
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "router")
    return ROUTING_SYSTEM_PROMPT.format(DATA_CONTEXT=data_context)



