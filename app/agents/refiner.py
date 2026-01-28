import logging
import json
import re
from typing import Dict, Any, List, Optional
from core.models import SearchCriterias
from google.genai import types
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps, SearchCriterias, FocusCity
from .agent_config import get_model
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

REFINER_PROMPT = """
**Critères recherches** :
```json
{STRUCTURED_CRITERIA}
```

**Briefing Précédent** :
{PREVIOUS_BRIEFING}

**Nouveaux Échanges** :
{NEW_HISTORY}

**Top villes identifiées** :
{TOP_CITIES}

**Nouveau Scoring** :
{SCORING_RESULTS}

**Instructions** :

2. **IDENTIFICATION DE LA VILLE CIBLE** : 
   - Essaye TOUJOURS d'identifier le nom de la commune/ville à analyser à partir des `Nouveaux Échanges` et récupère le code INSEE correspondant dans `top villes identifiées`. Retourne le résultat dans l'objet `focus_city` structuré
   - N'invente JAMAIS de ville. Si pas de ville identifée retourne `focus_city` vide.
3. **NOUVEAU BRIEFING** : 
   - Produis une synthèse hyper concise (5 à 10 bullet points maximum) à partir des éléments suivants : les critères de recherches, les faits validés, les nouveaux échanges, les retours experts et le briefing précédent.
   - Rapporte **SYSTÉMATIQUEMENT** les codes techniques (INSEE, ROME, Formation) à côté de chaque intitulé. N'invente et ne devine rien et utilise le format : `Intitulé (CODE)` (ex: "Bordeaux (33063)")
"""



class RefinerResult(BaseModel):
    """Synthesis of the conversation context."""
    focus_city: FocusCity = Field(..., description="The city and INSEE code")
    briefing: str = Field(..., description="The complete synthesized briefing")

RefinerResult.model_rebuild()

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

    # Scoring Results
    scoring_results_json = json.dumps(state.scoring_results, indent=2, ensure_ascii=False) if state.scoring_results else "Aucun nouveau résultat expert."
    
    # Enriched Criteria
    criteria_json = state.search_criteria.model_dump_json(indent=2)
    
    # Top 5 Cities podium
    top_cities = json.dumps([{"name": c.get("name"), "codgeo": c.get("codgeo")} for c in state.top_cities], indent=2, ensure_ascii=False) if state.top_cities else "Aucune ville identifiée."

    prompt = REFINER_PROMPT.format(
        PREVIOUS_BRIEFING=state.briefing or "Début du dossier.",
        NEW_HISTORY=new_history or "Aucun nouvel échange.",
        SCORING_RESULTS=scoring_results_json,
        STRUCTURED_CRITERIA=criteria_json,
        TOP_CITIES=top_cities
    )

    logger.debug(f"Refiner Prompt: {prompt}")

    return prompt


