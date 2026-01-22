
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
import json
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model
# Import the pure tools
from .tools import compute_top_cities
from core.models import SearchCriterias

logger = logging.getLogger("scorer_agent_v2")

# --- Structured Output ---
class ScorerResult(BaseModel):
    response: str
    model_config = ConfigDict(populate_by_name=True)

# --- System Prompt ---
SCORER_SYSTEM_PROMPT = """
**Rôle** : Tu es le Scorer ODIS. Ton job est de calculer et expliquer le Top Villes.
**CONTEXTE RÉSUMÉ** : {BRIEFING}
**PROFILE** : {PROFILE}

**DIRECTIVE CRITIQUE** :
Tu DOIS utiliser l'outil `compute_top_cities`.
Les critères de recherche sont injectés automatiquement dans le contexte, utilise-les tels quels.

**Instructions** :
1. Lance `compute_top_cities` (pas besoin de passer d'arguments).
2. Une fois les résultats reçus, analyse le **Top 5** des meilleures communes dans ton message de réponse.
    a. Pour chaque ville du Top 5:
        - Donne son nom, sa population et son score global comme un pourcentage.
        - Cite 1 ou 2 points forts pertinents par rapport au profil (Famille, Emploi, etc.).
    b. Termine TOUJOURS en suggérant à l'utilisateur de lancer une recherche approfondie sur l'une des communes.
3. **IMPORTANT** : Ne retourne JAMAIS les données brutes des villes dans ta réponse. Contente-toi d'analyser le résultat de l'outil.
4. **SORTIE STRUCTUREE** : Retourne l'objet `response` avec ton analyse textuelle uniquement.

"""

# --- Agent Definition ---
scorer_agent = Agent(
    get_model("scorer"),
    deps_type=ODISDeps,
    output_type=ScorerResult
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
        from datetime import datetime
        start_time = datetime.now()
        logger.debug(f"🛠️  [TOOL] Starting compute_top_cities_tool at {start_time.strftime('%H:%M:%S.%f')[:-3]}")
        
        # We use the criteria model directly from the state (deps)
        criteria = ctx.deps.state.search_criteria
        res = compute_top_cities(criteria)
        
        end_time = datetime.now()
        logger.debug(f"✅ [TOOL] compute_top_cities_tool finished at {end_time.strftime('%H:%M:%S.%f')[:-3]} - Duration: {(end_time - start_time).total_seconds():.3f}s")
        return res
    except Exception as e:
        logger.error(f"❌ [TOOL] compute_top_cities failed: {e}")
        return {"error": str(e)}

