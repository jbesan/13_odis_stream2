import logging
import json
import re
from typing import Dict, Any, List
from .state import ODISGraphState
from core.models import SearchCriterias
from google import genai
from google.genai import types
from services.mcp_server import _get_labels_for_codes_logic

logger = logging.getLogger(__name__)

REFINER_PROMPT = """
**Rôle** : Tu es l'Expert en Synthèse de Contexte ODIS. Ta mission est de condenser l'historique d'une conversation entre un travailleur social et un assistant IA en quelques points clés percutants.

**Historique Brut** :
{HISTORY}

**Instructions** :
1. Extrais UNIQUEMENT les faits validés, les décisions prises et les préférences exprimées par l'utilisateur.
2. **CONSERVATION DES IDENTIFIANTS** : Tu DOIS conserver les identifiants techniques mentionnés (ex: IDs d'offres '202GPKJ', codes métiers) car ils sont cruciaux pour les outils.
3. Ignore les salutations, les erreurs techniques ou les hésitations qui ont été résolues.
4. Produis une note structurée et ultra-concise en FRANÇAIS.
5. Si l'historique est vide, réponds par "Début de la conversation."
"""

from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps, SearchCriterias
from .agent_config import get_model
from google.genai import types

# --- Agent Definition ---

refiner_agent = Agent(
    get_model("refiner"),
    deps_type=ODISDeps
)

@refiner_agent.system_prompt
async def refiner_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Prepare history for the agent
    messages = ctx.deps.state.messages
    raw_history = ""
    for msg in messages[-3:]:
        role = "User" if msg.get("role") == "user" else "Assistant"
        text = msg.get("content", "")
        if not text and "parts" in msg:
            text = " ".join([p.get("text", "") for p in msg["parts"] if isinstance(p, dict)])
        raw_history += f"{role}: {text}\n"
    
    return REFINER_PROMPT.replace("{HISTORY}", raw_history)

# --- Legacy Helper for node integration ---

async def generate_briefing(state: ODISGraphState, deps: ODISDeps) -> str:
    """Génère une synthèse complète (Critères + Histoire) du contexte en utilisant l'agent Refiner."""
    
    # 1. Resolve Focus City Info (Heuristic)
    focus_info = state.focus_city or "Non définie"
    if state.focus_city and state.top_cities:
        for city in state.top_cities:
            if city.get('name') == state.focus_city:
                focus_info += f" (Code INSEE: {city.get('codgeo')})"
                break
    
    # 2. Extract recent Job IDs (Heuristic)
    recent_job_refs = []
    if state.found_jobs:
        for job in state.found_jobs[:5]: 
            jid = job.get('id')
            title = job.get('intitule')
            if jid:
                recent_job_refs.append(f"{title} ({jid})" if title else jid)
    
    # Regex fallback from history
    history_ids = []
    for msg in reversed(state.messages[-3:]): 
        text = msg.get("content", "")
        if not text and "parts" in msg:
             text = " ".join([p.get("text", "") for p in msg["parts"] if isinstance(p, dict)])

        if text:
            matches = re.findall(r'\b([A-Z0-9]*[0-9][A-Z0-9]*)\b', text.upper())
            for match in matches:
                if 7 <= len(match) <= 8:
                    if not any(match in ref for ref in recent_job_refs):
                        history_ids.append(match)
    
    if history_ids:
        recent_job_refs.extend(list(set(history_ids)))
    
    jobs_part = f"\n**RÉFÉRENCES RÉCENTES** : {', '.join(recent_job_refs)}" if recent_job_refs else ""

    # 3. Heuristic Criteria Summary
    criteria_summary = _summarize_criteria_logic(state.search_criteria)
    
    # 4. LLM History Summary (using Agent)
    # Correct mapping for PydanticAI
    model_name = get_model("refiner")

    from pydantic_ai.models.google import GoogleModel
    from pydantic_ai.providers.google import GoogleProvider

    # Explicitly inject the fresh client from deps
    provider = GoogleProvider(client=deps.client)
    model = GoogleModel(model_name, provider=provider)
    
    result = await refiner_agent.run("Synthèse de l'historique", deps=deps, model=model)
    history_summary = result.output
    
    briefing = f"""
### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)

**PROJET DE VIE** :
{criteria_summary}

**MÉMOIRE DE L'ÉCHANGE** :
{history_summary}{jobs_part}


**CIBLE ACTUELLE** : {focus_info}
"""
    briefing = briefing.strip()
    state.briefing = briefing
    logger.debug(f"🧠 [REFINER] Synthesized Briefing len={len(briefing)}")
    return briefing

def _summarize_criteria_logic(criteria: Any) -> str:
    if not criteria:
        return "- Aucun critère enregistré pour le moment."
    
    if isinstance(criteria, SearchCriterias):
        criteria_dict = criteria.model_dump()
    else:
        criteria_dict = criteria
        
    if not criteria_dict or all(not v for v in criteria_dict.values() if v is not None):
         return "- Aucun critère enregistré pour le moment."

    criteria = criteria_dict
    all_codes = []
    if 'commune_actuelle' in criteria: all_codes.append(str(criteria['commune_actuelle']))
    
    def extract_flat_codes(source_key):
        raw = criteria.get(source_key, [])
        return [str(code) for sublist in raw for code in (sublist if isinstance(sublist, list) else [sublist])]
    
    codes_rome = extract_flat_codes('codes_metiers')
    formations = extract_flat_codes('codes_formations')
    assos = criteria.get('inc_asso_add_selection', [])
    incl = criteria.get('inc_services_add_selection', [])
    
    all_codes.extend(codes_rome)
    all_codes.extend(formations)
    all_codes.extend(assos)
    all_codes.extend(incl)
    
    labels_map = _get_labels_for_codes_logic(all_codes)
    
    def fmt(code):
        label = labels_map.get(str(code))
        return f"{label} ({code})" if label else str(code)

    lines = []
    if 'commune_actuelle' in criteria:
        lines.append(f"- Localisation : {fmt(criteria['commune_actuelle'])}")
    
    nb_a = criteria.get('nb_adultes', 0)
    nb_e = criteria.get('nb_enfants', 0)
    if nb_a or nb_e:
        lines.append(f"- Composition : {nb_a} adulte(s), {nb_e} enfant(s)")
        
    if 'weight_profile' in criteria and criteria['weight_profile']:
        lines.append(f"- Priorité : {criteria['weight_profile']}")
        
    if 'loc_search_area' in criteria:
        lines.append(f"- Zone de recherche : {criteria['loc_search_area']}")
        
    if codes_rome:
        lines.append(f"- Métiers (ROME) : {', '.join([fmt(c) for c in codes_rome])}")
        
    if formations:
        lines.append(f"- Formations : {', '.join([fmt(c) for c in formations])}")

    if assos:
        lines.append(f"- Associations : {', '.join([fmt(c) for c in assos])}")

    if incl:
        lines.append(f"- Besoins inclusion : {', '.join([fmt(c) for c in incl])}")
        
    if not lines:
        return "- Dossier en cours de création."
        
    return "\n".join(lines)


# Legacy ContextRefiner class kept for compatibility if needed elsewhere, but marked as DEPRECATED
class ContextRefiner:
    def __init__(self, model_id: str, client: genai.Client):
        self.model_id = model_id
        self.client = client
    
    async def get_briefing(self, state: ODISGraphState) -> str:
        # Re-route to new async functional version
        return await generate_briefing(state, ODISDeps(state=state, client=self.client))
