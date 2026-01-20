
import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext
import json
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model
# Import the pure tools
from .tools import compute_top_cities
from core.models import SearchCriterias

logger = logging.getLogger("scorer_agent_v2")

# --- System Prompt ---
SCORER_SYSTEM_PROMPT = """
**Rôle** : Tu es le Scorer ODIS. Ton job est de calculer et expliquer le Top Villes.
**CONTEXTE RÉSUMÉ** : {BRIEFING}
**PROFILE** : {PROFILE}

**DIRECTIVE CRITIQUE** :
Tu DOIS utiliser l'outil `compute_top_cities`.
Les critères de recherche sont injectés automatiquement dans le contexte, utilise-les tels quels.

**Instructions** :
1. Lance `compute_top_cities` (pas besoin de passer d'arguments, je les injecterai via le contexte).
2. Une fois les résultats reçus, présente le **Top 5** des meilleures communes.
3. Pour chaque ville du Top 5:
    - Donne son nom, sa population et son score global comme un pourcentage.
    - Cite 1 ou 2 points forts pertinents par rapport au profil (Famille, Emploi, etc.).
4. Termine TOUJOURS en suggérant à l'utilisateur de lancer une recherche approfondie sur l'une des communes.
"""

# --- Agent Definition ---
scorer_agent = Agent(
    get_model("scorer"),
    deps_type=ODISDeps
)

@scorer_agent.system_prompt
async def scorer_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Validation / Pre-processing
    try:
        # criteria is already a SearchCriterias model in ODISGraphState
        criteria_model = ctx.deps.state.search_criteria
        profile = criteria_model.weight_profile or "Équilibré"
    except Exception as e:
        return f"ATTENTION: Les critères sont invalides ({e}). Demande à l'utilisateur de compléter."
        
    briefing = ctx.deps.state.briefing
    
    prompt = SCORER_SYSTEM_PROMPT.replace("{PROFILE}", profile)
    prompt = prompt.replace("{BRIEFING}", briefing)
    
    return prompt

# --- Tool Wrapper ---

@scorer_agent.tool
def compute_top_cities_tool(ctx: RunContext[ODISDeps]) -> Dict[str, Any]:
    """
    Calcule le top des villes de réinstallation selon les critères du contexte.

    Args:
        ctx (RunContext[ODISDeps]): Contexte de l'agent.
    
    Returns:
        Dict[str, Any]: Dictionnaire des villes correspondantes.
    """
    try:
        # We use the criteria model directly from the state (deps)
        criteria = ctx.deps.state.search_criteria
        return compute_top_cities(criteria)
    except Exception as e:
        logger.error(f"❌ [TOOL] compute_top_cities failed: {e}")
        return {"error": str(e)}

