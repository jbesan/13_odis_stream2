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

logger = logging.getLogger("healthcare_expert")

class HealthcareResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(..., description="Analyse détaillée des découvertes sur la santé.")

HEALTHCARE_EXPERT_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Expert Santé ODIS (Agent HEALTHCARE_EXPERT). 
Ta mission est d'évaluer l'accès aux soins de la ville (APL index, hôpitaux, centres médicaux) et d'identifier des structures ou des associations d'accompagnement médical spécialisées.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Analyse de terrain** : Interroge les données de santé pré-chargées (APL, liste des établissements de santé).
2. **Recherches de proximité** : Si des structures clés manquent (ex. hôpital, cabinet médical, Protection Maternelle et Infantile - PMI), appelle `search_places_batch_tool`.
3. **Associations médicales** : Si le candidat a un besoin d'accompagnement social/médical spécifique (ex: handicap, addiction), appelle `search_rna_rag_batch_tool` ou fais une recherche web avec Google Search pour trouver des relais locaux.
4. **Réponse (Structured)** : Tu DOIS retourner un objet `HealthcareResult`.
   - `searched` : Liste concise des requêtes ou outils utilisés.
   - `result` : Ton analyse factuelle et argumentée sur l'accès aux soins locaux, avec les structures de santé de référence et contacts d'associations d'entraide si pertinents.
"""

healthcare_expert_agent = Agent(
    get_model("healthcare_expert"),
    model_settings=get_model_settings("healthcare_expert"),
    deps_type=ODISDeps,
    builtin_tools=[WebSearchTool()],
    output_type=HealthcareResult
)

@healthcare_expert_agent.system_prompt
async def healthcare_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "healthcare_expert")
    mission = state.expert_tasks.get("healthcare_expert", "Analyse générale de l'accès aux soins et infrastructures médicales.")
    skill_inst = state.expert_skill_instructions.get("healthcare_expert", "Aucune consigne spécifique de Skill Card active.")

    return HEALTHCARE_EXPERT_SYSTEM_PROMPT.format(
        DATA_CONTEXT=data_context,
        MISSION=mission,
        SKILL_INSTRUCTIONS=skill_inst
    )

@healthcare_expert_agent.tool
async def search_places_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], location: str) -> Dict[str, Any]:
    """Recherche des hôpitaux, centres médicaux ou PMI en mode batch.
    Args:
        queries: Liste de requêtes (ex: ['PMI', 'hôpital', 'médecin généraliste']).
        location: Ville cible (ex: 'Bordeaux, Nouvelle-Aquitaine').
    """
    return await search_places_batch(queries, location)

@healthcare_expert_agent.tool
async def search_rna_rag_batch_tool(ctx: RunContext[ODISDeps], queries: List[str], codgeo: str, top_k: int = 10) -> List[Dict[str, Any]]:
    """Recherche sémantique d'associations de santé locales (RNA)."""
    return await search_rna_rag_batch(queries, codgeo, top_k=top_k)


