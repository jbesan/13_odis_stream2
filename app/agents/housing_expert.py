import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic import BaseModel, Field
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings, get_swarm_boilerplate
from .tools import (
    search_places_batch, 
    compute_routes, 
    search_ccas,
)

logger = logging.getLogger("housing_expert")

class HousingResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(..., description="Analyse détaillée des découvertes sur le logement.")

HOUSING_EXPERT_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}
**Rôle** : Agent thématique Logement (Housing Expert).
**Règle** : Reste STRICTEMENT sur le Logement (loyer m², logement social, hébergements). Ne traite aucun autre sujet (transport, santé, école, association/intégration, emploi), d'autres experts s'en chargent.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Frugalité & Précision (Recherche Web)** : Limite au MAXIMUM tes appels à Google Search. Fais au maximum 1 seule requête par objet de recherche/sujet distinct. Ne fais JAMAIS de requêtes similaires, de reformulations ou de variations pour un même sujet. Si l'information est introuvable après un essai, n'insiste pas et signale-le.
2. **Analyse de terrain** : Interroge en priorité les données chiffrées de logement du dossier. S'il manque des éléments (ex: structures d'hébergement comme CADA, CHRS, CPH), appelle `search_places_batch_tool` ou fais une recherche web avec Google Search.
3. **CCAS** : Utilise `search_ccas_tool` pour obtenir les coordonnées du CCAS de la commune.
4. **Réponse (Structured)** : Tu DOIS retourner un objet `HousingResult`.
5. **Formatage** : Sois clair et concis dans tes réponses.
"""

housing_expert_agent = Agent(
    get_model("housing_expert"),
    model_settings=get_model_settings("housing_expert"),
    deps_type=ODISDeps,
    builtin_tools=[WebSearchTool()],
    output_type=HousingResult
)

@housing_expert_agent.system_prompt
async def housing_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "housing_expert")
    mission = state.expert_tasks.get("housing_expert", "Analyse générale des conditions de logement.")
    skill_inst = state.expert_skill_instructions.get("housing_expert", "Aucune consigne spécifique de Skill Card active.")
    boilerplate = get_swarm_boilerplate("expert")

    return HOUSING_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context,
        MISSION=mission,
        SKILL_INSTRUCTIONS=skill_inst
    )


@housing_expert_agent.tool
async def search_places_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des lieux (POIs), structures ou services de logement en mode batch.
    Args:
        queries: Liste de requêtes (ex: ['CHRS', 'CADA', 'CPH']).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)

@housing_expert_agent.tool
def compute_routes_tool(ctx: RunContext[ODISDeps], origin: str, destination: str, mode: str = "transit") -> Dict[str, Any]:
    """Calcule des itinéraires et temps de trajet."""
    return compute_routes(origin, destination, mode)

@housing_expert_agent.tool
def search_ccas_tool(ctx: RunContext[ODISDeps], codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les coordonnées du CCAS pour une commune."""
    return search_ccas(codgeo)


