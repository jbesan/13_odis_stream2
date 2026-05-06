import logging
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext

from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model
from .tools import compute_top_cities
from core.models import SearchCriterias

logger = logging.getLogger("scorer_agent_v2")

# --- Structured Output ---
class CityPitch(BaseModel):
    codgeo: str = Field(description="Code INSEE de la commune (5 chiffres)")
    name: str = Field(description="Nom de la commune")
    pitch: str = Field(description="Résumé explicatif très concis des points forts de la commune pour le projet.")

class ScorerResult(BaseModel):
    response: str = Field(description="Réponse textuelle globale à afficher dans le chat (introduction).")
    pitches_per_city: List[CityPitch] = Field(default_factory=list, description="Liste des points forts pertinents pour chaque cette ville")

# --- System Prompt ---
SCORER_SYSTEM_PROMPT = """
**Contexte** : Un Travailleur Social cherche la ville la plus adéquate pour une personne réfugiée (et sa famille). A partir de critères de recherches, l'outil identifie un Top 5 que l'on analyse et compare à la commune actuelle de la personne accompagnée.
**Rôle** : Tu es le Scorer ODIS. Ton job est de calculer et argumenter le Top 5 des Villes identifiées à partir des scores obtenus.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

**DIRECTIVE CRITIQUE** :
Tu DOIS utiliser l'outil `compute_top_cities` si tu n'as pas déjà un top 5 et les scores dans le contexte.
Les critères de recherche sont injectés automatiquement dans le contexte JSON, utilise-les tels quels.

**Instructions** :
1. Si nécessaire lance `compute_top_cities` (pas besoin de passer d'arguments).
2. Une fois les résultats reçus, analyse le **Top 5** des meilleures communes.
3. Remplis `response` avec une synthèse du top 5 concise et engageante pour l'utilisateur.
4. Pour chaque `pitches_per_city`, des 5 villes du Top 5 :
    a. Fournis le code INSEE exact (`codgeo`) et le nom (`name`).
    b. Rédige une liste à puces markdown de 3 à 5 points forts pertinents au regard de la recherche et chiffrés (en pourcentage si pertinent). 
5. **IMPORTANT** : Ne retourne jamais les références des données techniques internes (ex: %{{log_soc_inoc_scaled}}).
"""

# --- Agent Definition ---
scorer_agent = Agent(
    get_model("scorer"),
    deps_type=ODISDeps,
    output_type=ScorerResult
)

@scorer_agent.system_prompt
async def scorer_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds Scorer agent prompt using ODISContextBuilder."""
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "scorer")
    
    prompt = SCORER_SYSTEM_PROMPT.format(
        DATA_CONTEXT=data_context
    )
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

    # OPTIMIZATION: If results were already computed (e.g. by the UI or a previous turn)
    # we return them directly to avoid a double backend heavy computation.
    if ctx.deps.state.search_results and ctx.deps.state.search_results.results:
        sr = ctx.deps.state.search_results
        results = sr.results if hasattr(sr, 'results') else sr.get('results', [])
        
        logger.info(f"⚡ [TOOL:SCORER] Using cached search results ({len(results)} cities found in state).")
        
        # We only return the top 20 to keep the LLM context lean, 
        # since the engine usually returns 50+.
        cities_list = [c.model_dump(exclude={'geometry', 'centroid'}) for c in results[:20]]
        return {
            "cities": cities_list, 
            "search_hash": sr.search_hash,
            "current_geo": sr.current_geo,
            "source": "cache", 
            "message": "Results retrieved from internal state (already computed)."
        }
        
    try:
        logger.info("🔍 [SCORER] Triggering FRESH compute_top_cities calculation...")
        start_time = datetime.now()
        res = compute_top_cities(ctx.deps.state.search_criteria)
        end_time = datetime.now()
        logger.info(f"✅ [TOOL] compute_top_cities finished in {(end_time - start_time).total_seconds():.3f}s")
        return {**res, "source": "engine"}
    except Exception as e:
        logger.error(f"❌ [TOOL] compute_top_cities failed: {e}")
        return {"error": str(e)}

