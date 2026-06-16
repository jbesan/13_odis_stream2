import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic import BaseModel, Field
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings
from .tools import (
    search_refugee_associations,
    search_rna_rag_batch,
    search_ccas,
)

logger = logging.getLogger("social_integration_expert")

class SocialIntegrationResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(..., description="Analyse détaillée des découvertes sur l'intégration sociale.")

SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Expert Intégration Sociale ODIS (Agent SOCIAL_INTEGRATION_EXPERT). 
Ta mission est d'identifier les ressources d'inclusion et d'accompagnement social local (CCAS de la commune, associations d'aide aux réfugiés, cours de français, clubs de loisirs/sport).

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **CCAS** : Appelle obligatoirement `search_ccas_tool` pour obtenir les détails du CCAS local.
2. **Associations Réfugiés** : Appelle `search_refugee_associations_tool` pour identifier les associations spécifiques d'accueil des réfugiés.
3. **Accompagnement et Loisirs (RAG)** : Appelle `search_rna_rag_batch_tool` pour chercher s'il y a des clubs sportifs, cours de français (FLE) ou associations de solidarité locale correspondant au dossier.
4. **Réponse (Structured)** : Tu DOIS retourner un objet `SocialIntegrationResult`.
   - `searched` : Liste concise des requêtes ou outils utilisés.
   - `result` : Ton analyse détaillée et factuelle des opportunités d'intégration locale, incluant le CCAS et les associations trouvées avec leurs missions respectives.
"""

social_integration_expert_agent = Agent(
    get_model("social_integration_expert"),
    model_settings=get_model_settings("social_integration_expert"),
    deps_type=ODISDeps,
    builtin_tools=[WebSearchTool()],
    output_type=SocialIntegrationResult
)

@social_integration_expert_agent.system_prompt
async def social_integration_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "social_integration_expert")
    mission = state.expert_tasks.get("social_integration_expert", "Analyse générale de l'intégration sociale et du tissu associatif.")
    skill_inst = state.expert_skill_instructions.get("social_integration_expert", "Aucune consigne spécifique de Skill Card active.")

    return SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT.format(
        DATA_CONTEXT=data_context,
        MISSION=mission,
        SKILL_INSTRUCTIONS=skill_inst
    )

@social_integration_expert_agent.tool
def search_refugee_associations_tool(ctx: RunContext[ODISDeps], codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les associations dédiées à l'aide aux réfugiés pour une commune."""
    return search_refugee_associations(codgeo)

@social_integration_expert_agent.tool
async def search_rna_rag_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], codgeo: str, top_k: int = 10) -> List[Dict[str, Any]]:
    """Recherche sémantique d'associations d'inclusion, sport, loisirs ou solidarité locale (RNA)."""
    return await search_rna_rag_batch(queries, codgeo, top_k=top_k)

@social_integration_expert_agent.tool
def search_ccas_tool(ctx: RunContext[ODISDeps], codgeo: str) -> List[Dict[str, Any]]:
    """Recherche les coordonnées du CCAS pour une commune."""
    return search_ccas(codgeo)
