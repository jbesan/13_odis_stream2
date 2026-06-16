import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic import BaseModel, Field
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings
from .tools import (
    search_places_batch, 
    search_rna_rag_batch,
)

logger = logging.getLogger("education_expert")

class EducationResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(..., description="Analyse détaillée des découvertes sur l'éducation.")

EDUCATION_EXPERT_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Expert Éducation ODIS (Agent EDUCATION_EXPERT). 
Ta mission est de lister les établissements scolaires et d'accueil locaux (crèches, maternelles, écoles primaires, collèges, lycées) correspondants aux besoins de la famille, et d'expliquer les modalités administratives d'inscription.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Analyse de terrain** : Identifie les niveaux scolaires des enfants dans le dossier.
2. **Recherches scolaires** : Utilise `search_places_batch_tool` pour trouver les adresses des établissements de la commune (ex: écoles, crèches).
3. **Modalités d'inscription** : Fais une recherche Google Search pour extraire les démarches spécifiques de la mairie locale (guichet famille, pré-inscriptions scolaires).
4. **Réponse (Structured)** : Tu DOIS retourner un objet `EducationResult`.
   - `searched` : Liste concise des requêtes ou outils utilisés.
   - `result` : Ton analyse détaillée et factuelle des écoles locales, avec les coordonnées principales des structures et les étapes d'inscription parentale.
"""

education_expert_agent = Agent(
    get_model("education_expert"),
    model_settings=get_model_settings("education_expert"),
    deps_type=ODISDeps,
    builtin_tools=[WebSearchTool()],
    output_type=EducationResult
)

@education_expert_agent.system_prompt
async def education_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "education_expert")
    mission = state.expert_tasks.get("education_expert", "Analyse générale de l'accès à l'éducation et infrastructures scolaires.")
    skill_inst = state.expert_skill_instructions.get("education_expert", "Aucune consigne spécifique de Skill Card active.")

    return EDUCATION_EXPERT_SYSTEM_PROMPT.format(
        DATA_CONTEXT=data_context,
        MISSION=mission,
        SKILL_INSTRUCTIONS=skill_inst
    )

@education_expert_agent.tool
async def search_places_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des crèches, écoles maternelles, primaires, collèges ou lycées en mode batch.
    Args:
        queries: Liste de requêtes (ex: ['école primaire', 'collège', 'crèche']).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)

@education_expert_agent.tool
async def search_rna_rag_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], codgeo: str, top_k: int = 10) -> List[Dict[str, Any]]:
    """Recherche sémantique d'associations d'accompagnement scolaire ou de parents d'élèves (RNA)."""
    return await search_rna_rag_batch(queries, codgeo, top_k=top_k)


