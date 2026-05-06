import logging
import json
import re
from typing import Dict, Any, List, Optional
from core.models import SearchCriterias
from google.genai import types
from pydantic_ai import Agent, RunContext
from .state import GraphState, ODISDeps, SearchCriterias, FocusCity, ODISContextBuilder
from .agent_config import get_model, get_model_settings
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

REFINER_PROMPT = """
**Contexte** : Un Travailleur Social cherche la ville la plus adéquate pour une personne réfugiée (et sa famille). A partir de critères de recherches, l'outil identifie un Top 5 que l'on analyse et compare à la commune actuelle de la personne accompagnée.
**Rôle** : Tu es le Refiner ODIS. Ta mission est de maintenir et mettre à jour le "Briefing" du dossier en fonction des nouveaux échanges et des résultats d'analyse.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Instructions :
1. Produis ou met à jour une synthèse hyper concise (5 à 10 bullet points maximum) qui résume à date la connaissance:
    - des critères de recherches,
    - des villes analsées validés,
    - des nouveaux échanges,
    - des retours experts et du briefing précédent.
2. Rapporte **SYSTÉMATIQUEMENT** les codes techniques (INSEE, ROME, Formation) à côté de chaque intitulé. N'invente et ne devine rien et utilise le format : `Intitulé (CODE)` (ex: "Bordeaux (33063)")
"""


class RefinerResult(BaseModel):
    """Synthesis of the conversation context."""
    odis_brief: str = Field(..., description="The complete synthesized briefing")

RefinerResult.model_rebuild()

refiner_agent = Agent(
    get_model("refiner"),
    model_settings=get_model_settings("refiner"),
    deps_type=ODISDeps,
    output_type=RefinerResult
)

@refiner_agent.system_prompt
async def refiner_instructions(ctx: RunContext[ODISDeps]) -> str:
    """
    Builds a minimal, token-efficient context for the Refiner LLM using ODISContextBuilder.
    """
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "refiner")
    
    prompt = REFINER_PROMPT.format(
        DATA_CONTEXT=data_context
    )
    return prompt
