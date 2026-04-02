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
    Builds a minimal, token-efficient context for the Refiner LLM,
    focusing exclusively on the User Profile and Search Intent.

    Args:
        ctx: LangGraph run context containing ODISDeps with the current state.

    Returns:
        Formatted prompt string focused strictly on the "Person Profile".
    """
    state = ctx.deps.state
    new_messages = state.messages[state.last_summarized_idx:]

    new_history = ""
    for msg in new_messages:
        role = "User" if msg.get("role") == "user" else "Assistant"
        text = msg.get("content", "")
        if not text and "parts" in msg:
            text = " ".join([p.get("text", "") for p in msg["parts"] if isinstance(p, dict)])
        new_history += f"{role}: {text}\n"

    # --- Selective Criteria Context (Dossier Intent Only) ---
    sc = state.search_criteria
    def _get_desc(field_name: str) -> str:
        return SearchCriterias.model_fields.get(field_name).description or field_name

    def _labels(items):
        if not items: return []
        flat = []
        for item in items:
            if isinstance(item, list):
                flat.extend([i.label if hasattr(i, 'label') else str(i) for i in item if i])
            elif hasattr(item, 'label'):
                flat.append(item.label)
            else:
                flat.append(str(item))
        return flat

    # Strict focus on the PERSON and their NEEDS
    dossier_summary = {
        _get_desc("commune_actuelle"): sc.commune_actuelle.label if sc.commune_actuelle else None,
        _get_desc("nb_adultes"): sc.nb_adultes,
        _get_desc("nb_enfants"): sc.nb_enfants,
        _get_desc("classe_enfants"): sc.classe_enfants,
        _get_desc("codes_metiers"): _labels(sc.codes_metiers),
        _get_desc("codes_formations"): _labels(sc.codes_formations),
        _get_desc("inc_services_add_selection"): _labels(sc.inc_services_add_selection),
        _get_desc("inc_asso_add_selection"): _labels(sc.inc_asso_add_selection),
        _get_desc("hebergement_cible"): sc.hebergement_cible,
        _get_desc("logement"): sc.logement,
        _get_desc("besoin_sante"): sc.besoin_sante,
        _get_desc("weight_profile"): sc.weight_profile,
        _get_desc("notes_qualitatives"): sc.notes_qualitatives,
    }

    criteria_json = json.dumps(dossier_summary, ensure_ascii=False, indent=2)

    # --- Selective Results Context (Top 5 Podium) ---
    results_summary = []
    if state.search_results and state.search_results.results:
        # We take top 5 to keep the prompt focused and small
        for city in state.search_results.results[:5]:
            results_summary.append({
                "nom": city.name,
                "insee": city.codgeo,
                "score_global": f"{round(city.global_score * 100, 1)}%",
                "scores_categories": {
                    "emploi": round(city.employment.cat_score, 2),
                    "logement": round(city.housing.cat_score, 2),
                    "education": round(city.education.cat_score, 2),
                    "sante": round(city.health.cat_score, 2),
                    "inclusion": round(city.inclusion.cat_score, 2),
                    "mobilite": round(city.mobility.cat_score, 2),
                },
                "pitch_expert": city.scorer_pitch or "Non encore généré"
            })

    results_json = json.dumps(results_summary, ensure_ascii=False, indent=2)

    # DEBUG: Log full context sent to Refiner for review during testing
    logger.info(f"📋 [REFINER-CONTEXT] Sending dossier + results context (~{len(criteria_json) + len(results_json)} chars)")
    logger.debug(f"📋 [REFINER-CONTEXT] dossier={criteria_json}")
    logger.debug(f"📋 [REFINER-CONTEXT] results={results_json}")

    return REFINER_PROMPT.format(
        PREVIOUS_BRIEFING=state.odis_brief or "Début du dossier.",
        NEW_HISTORY=new_history or "Aucun nouvel échange.",
        SCORING_RESULTS=results_json,
        STRUCTURED_CRITERIA=criteria_json,
        TOP_CITIES=f"Top 5: {', '.join([c.name for c in state.search_results.results[:5]])}" if state.search_results and state.search_results.results else "Aucun résultat."
    )
