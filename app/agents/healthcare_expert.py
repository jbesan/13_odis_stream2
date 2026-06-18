import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext
from pydantic_ai.capabilities import WebSearch
from pydantic import BaseModel, Field
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings, get_swarm_boilerplate
from .tools import (
    search_places_batch, 
    search_rna_rag_batch,
)

logger = logging.getLogger("healthcare_expert")

class HealthcareResult(BaseModel):
    searched: str = Field(..., description="Résumé des outils et termes recherchés.")
    result: str = Field(..., description="Analyse détaillée des découvertes sur la santé.")

HEALTHCARE_EXPERT_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}
**Rôle** : Agent thématique Santé (Healthcare Expert).
**Règle** : Reste STRICTEMENT sur la Santé (accès aux soins, hôpitaux, PMI, spécialistes). Ne traite aucun autre sujet (logement, transport, école, association/intégration générale, emploi), d'autres experts s'en chargent.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Ta Mission Spécifique pour ce tour :
{MISSION}

# Consignes additionnelles issues des Skill Cards actives :
{SKILL_INSTRUCTIONS}

**DIRECTIVES DE TRAVAIL** :
1. **Recherches Web** : Utilise Google Search mais limite-toi au maximum 1 seule requête par objet de recherche/sujet distinct. Ne fais JAMAIS de requêtes similaires, de reformulations ou de variations pour un même sujet. Si l'information est introuvable après un essai, n'insiste pas et signale-le.
2. **Priorisation des outils** : Utilise en priorité `search_places_batch_tool` (pour PMI, hôpitaux, cabinets) et `search_rna_rag_batch_tool` (pour les associations). N'utilise Google Search qu'en dernier recours pour des structures introuvables.
3. **Analyse de terrain** : Interroge les données de santé pré-chargées (APL, liste des établissements de santé).
4. **Formatage** : Sois hyper concis dans tes réponses.
"""

healthcare_expert_agent = Agent(
    get_model("healthcare_expert"),
    model_settings=get_model_settings("healthcare_expert"),
    deps_type=ODISDeps,
    capabilities=[WebSearch()],
    output_type=HealthcareResult
)

@healthcare_expert_agent.system_prompt
async def healthcare_expert_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "healthcare_expert")
    mission = state.expert_tasks.get("healthcare_expert", "Analyse générale de l'accès aux soins et infrastructures médicales.")
    skill_inst = state.expert_skill_instructions.get("healthcare_expert", "Aucune consigne spécifique de Skill Card active.")
    boilerplate = get_swarm_boilerplate("expert")

    return HEALTHCARE_EXPERT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
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
    """
    Recherche sémantique d'associations de santé locales (RNA).
    
    Args:
        queries: Liste de termes de recherche.
                 ATTENTION : Ne mets JAMAIS le nom de la ville dans ces requêtes car le filtrage géographique est déjà géré par l'outil via `codgeo`.
                 Exemple correct : ['cours de langue FLE', 'accompagnement administratif'].
                 Exemple incorrect : ['FLE Aix-en-Provence'].
        codgeo: Code INSEE de la commune.
        top_k: Nombre maximum de résultats.
    """
    return await search_rna_rag_batch(queries, codgeo, top_k=top_k)



