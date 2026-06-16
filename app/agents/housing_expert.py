import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic import BaseModel, Field
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings
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
**Rôle** : Tu es l'Expert Logement ODIS (Agent HOUSING_EXPERT). 
Ta mission est d'évaluer les conditions de logement de la ville analysée (loyer moyen m², délais de logement social) et d'identifier les structures locales d'hébergement ou d'accueil pertinentes pour le profil.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Analyse de terrain** : Interroge en priorité les données chiffrées de logement du dossier. S'il manque des éléments (ex: structures d'hébergement comme CADA, CHRS, CPH), appelle `search_places_batch_tool` ou fais une recherche web avec Google Search.
2. **CCAS** : Utilise `search_ccas_tool` pour obtenir les coordonnées du CCAS de la commune.
3. **Réponse (Structured)** : Tu DOIS retourner un objet `HousingResult`.
   - `searched` : Liste concise des mots-clés ou outils utilisés.
   - `result` : Ton analyse détaillée, factuelle et argumentée sur le logement dans la commune (loyer moyen, logement social, hébergements d'urgence pertinents). Cite les adresses ou noms des structures trouvées.
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

    return HOUSING_EXPERT_SYSTEM_PROMPT.format(
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


