import logging
import json
import re
from typing import Dict, Any, List, Optional
from core.models import SearchCriterias
from google.genai import types
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps, SearchCriterias, FocusCity, ODISContextBuilder
from .agent_config import get_model
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

REFINER_PROMPT = """
**Rôle** : Tu es le Refiner ODIS. Ta mission est de maintenir et mettre à jour le "Briefing" du dossier en fonction des nouveaux échanges et des résultats d'analyse.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Instructions :
1. **RÉSUMÉ DU DOSSIER** : 
   - Produis une synthèse hyper concise (5 à 10 bullet points maximum) à partir des critères de recherches, des faits validés, des nouveaux échanges, des retours experts et du briefing précédent.
   - Rapporte **SYSTÉMATIQUEMENT** les codes techniques (INSEE, ROME, Formation) à côté de chaque intitulé. N'invente et ne devine rien et utilise le format : `Intitulé (CODE)` (ex: "Bordeaux (33063)")
"""


class RefinerResult(BaseModel):
    """Synthesis of the conversation context."""
    odis_brief: str = Field(..., description="The complete synthesized briefing")

RefinerResult.model_rebuild()

refiner_agent = Agent(
    get_model("refiner"),
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
