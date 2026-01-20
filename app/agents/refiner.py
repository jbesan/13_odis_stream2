import logging
import json
import re
from typing import Dict, Any, List
from .state import ODISGraphState
from core.models import SearchCriterias
from google.genai import types

logger = logging.getLogger(__name__)

REFINER_PROMPT = """
**Dossier Actuel (Critères Structurés)** :
{STRUCTURED_CRITERIA}

**Briefing Précédent** :
{PREVIOUS_BRIEFING}

**Nouveaux Échanges** :
{NEW_HISTORY}

**Nouveaux Retours Experts (Scout, Web, Job Hunter)** :
{NEW_EXPERTS}

**Instructions** :
1. Analyse le Dossier (Critères) et le Briefing précédent pour produire une synthèse globale.
2. Complète-la avec les faits validés, décisions et préférences issus des nouveaux échanges et retours experts.
3. **CONSERVATION DES IDENTIFIANTS** : Conserve les identifiants techniques (Codes ROME, INSEE, IDs d'offres) car ils sont cruciaux, n'invente et ne devine rien.
4. Produis un Briefing structuré, semantic et ultra-concise (5 bullet points max) en FRANÇAIS.
5. Si c'est le début, crée le premier Briefing.
"""

from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps, SearchCriterias
from .agent_config import get_model
from google.genai import types
from pydantic import BaseModel, Field

# --- Structured Output ---

class RefinerResult(BaseModel):
    """Synthesis of the conversation context."""
    briefing: str = Field(..., description="The complete synthesized briefing of the project (summary of criteria, history and project goals).")

# --- Agent Definition ---

refiner_agent = Agent(
    get_model("refiner"),
    deps_type=ODISDeps,
    output_type=RefinerResult
)

@refiner_agent.system_prompt
async def refiner_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Prepare history for the agent
    state = ctx.deps.state
    new_messages = state.messages[state.last_summarized_idx:]
    
    new_history = ""
    for msg in new_messages:
        role = "User" if msg.get("role") == "user" else "Assistant"
        text = msg.get("content", "")
        if not text and "parts" in msg:
            text = " ".join([p.get("text", "") for p in msg["parts"] if isinstance(p, dict)])
        new_history += f"{role}: {text}\n"

    # Experts Results
    experts_json = json.dumps(state.experts_results, indent=2, ensure_ascii=False) if state.experts_results else "Aucun nouveau résultat expert."
    
    # Enriched Criteria
    criteria_json = state.search_criteria.model_dump_json(indent=2)
    
    return REFINER_PROMPT.format(
        PREVIOUS_BRIEFING=state.briefing or "Début du dossier.",
        NEW_HISTORY=new_history or "Aucun nouvel échange.",
        NEW_EXPERTS=experts_json,
        STRUCTURED_CRITERIA=criteria_json
    )


