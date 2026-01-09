import logging
import json
import re
from .state import AgentContext
from google import genai
from google.genai import types
from services.mcp_server import _get_labels_for_codes_logic

logger = logging.getLogger(__name__)

REFINER_PROMPT = """
Tu es l'Expert en Synthèse de Contexte ODIS. Ta mission est de condenser l'historique d'une conversation entre un travailleur social et un assistant IA en quelques points clés percutants.

**Historique Brut** :
{HISTORY}

**Instructions** :
1. Extrais UNIQUEMENT les faits validés, les décisions prises et les préférences exprimées par l'utilisateur.
2. **CONSERVATION DES IDENTIFIANTS** : Tu DOIS conserver les identifiants techniques mentionnés (ex: IDs d'offres '202GPKJ', codes métiers) car ils sont cruciaux pour les outils.
3. Ignore les salutations, les erreurs techniques ou les hésitations qui ont été résolues.
4. Produis une liste à puces (max 4-5 points) ultra-concise en FRANÇAIS.
5. Si l'historique est vide, réponds par "Début de la conversation."
"""

class ContextRefiner:
    def __init__(self, model_id: str, client: genai.Client):
        self.model_id = model_id
        self.client = client

    def get_briefing(self, context: AgentContext) -> str:
        """Génère une synthèse complète (Critères + Histoire) du contexte."""
        
        # 1. Resolve Focus City Codegeo
        focus_info = context.focus_city or "Non définie"
        if context.focus_city and context.top_cities:
            for city in context.top_cities:
                if city.get('name') == context.focus_city:
                    focus_info += f" (Code INSEE: {city.get('codgeo')})"
                    break
        
        # 2. Extract recent Job IDs (Structured from context + Regex fallback)
        recent_job_refs = []
        
        # A. Structured from context
        if context.found_jobs:
            for job in context.found_jobs[:5]: # Take last 5
                jid = job.get('id')
                title = job.get('intitule')
                if jid:
                    recent_job_refs.append(f"{title} ({jid})" if title else jid)
        
        # B. Regex fallback from history (to catch IDs mentioned by user or model that might not be in the current search buffer)
        history_ids = []
        for turn in reversed(context.history[-3:]): # Check last 3 turns
            parts = turn.get("parts", [])
            for p in parts:
                if isinstance(p, dict) and p.get("text"):
                    text = p.get("text") or ""
                    matches = re.findall(r'\b([A-Z0-9]*[0-9][A-Z0-9]*)\b', text.upper())
                    for match in matches:
                        if 7 <= len(match) <= 8:
                            # Avoid duplicates from context
                            if not any(match in ref for ref in recent_job_refs):
                                history_ids.append(match)
        
        if history_ids:
            recent_job_refs.extend(list(set(history_ids)))
        
        jobs_part = f"\n**RÉFÉRENCES RÉCENTES** : {', '.join(recent_job_refs)}" if recent_job_refs else ""

        # 3. Heuristic Criteria Summary (with Labels)
        criteria_summary = self._summarize_criteria(context.search_criteria)
        
        # 4. LLM History Summary (if history exists)
        history_summary = self._summarize_history(context.history)
        
        briefing = f"""
### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)

**PROJET DE VIE** :
{criteria_summary}

**MÉMOIRE DE L'ÉCHANGE** :
{history_summary}{jobs_part}

**CIBLE ACTUELLE** : {focus_info}
"""
        briefing = briefing.strip()
        logger.debug(f"\n{'='*50}\n🧠 [REFINER] Synthesized Briefing:\n{briefing}\n{'='*50}")
        return briefing

    def _summarize_criteria(self, criteria: dict) -> str:
        if not criteria:
            return "- Aucun critère enregistré pour le moment."
        
        # 1. Collect all codes for label resolution
        all_codes = []
        if 'commune_actuelle' in criteria: all_codes.append(str(criteria['commune_actuelle']))
        
        def extract_flat_codes(source_key):
            raw = criteria.get(source_key, [])
            return [str(code) for sublist in raw for code in (sublist if isinstance(sublist, list) else [sublist])]
        
        faps = extract_flat_codes('codes_metiers')
        formations = extract_flat_codes('codes_formations')
        assos = criteria.get('inc_asso_add_selection', [])
        incl = criteria.get('inc_services_add_selection', [])
        
        all_codes.extend(faps)
        all_codes.extend(formations)
        all_codes.extend(assos)
        all_codes.extend(incl)
        
        # 2. Resolve Labels
        labels_map = _get_labels_for_codes_logic(all_codes)
        
        def fmt(code):
            label = labels_map.get(str(code))
            return f"{label} ({code})" if label else str(code)

        # 3. Build lines
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
            
        if faps:
            lines.append(f"- Métiers (FAP) : {', '.join([fmt(c) for c in faps])}")
            
        if formations:
            lines.append(f"- Formations : {', '.join([fmt(c) for c in formations])}")

        if assos:
            lines.append(f"- Associations : {', '.join([fmt(c) for c in assos])}")

        if incl:
            lines.append(f"- Besoins inclusion : {', '.join([fmt(c) for c in incl])}")
            
        if not lines:
            return "- Dossier en cours de création."
            
        return "\n".join(lines)

    def _summarize_history(self, history: list) -> str:
        if not history:
            return "- Aucun échange préalable."
        
        # Limit the history we send to the refiner to save tokens
        raw_history = ""
        for turn in history[-10:]:
            role = "User" if turn.get("role") == "user" else "Assistant"
            parts = turn.get("parts", [])
            text = " ".join([p.get("text", "") for p in parts if isinstance(p, dict)])
            raw_history += f"{role}: {text}\n"

        try:
            response = self.client.models.generate_content(
                model=self.model_id,
                contents=raw_history,
                config=types.GenerateContentConfig(
                    system_instruction=REFINER_PROMPT.replace("{HISTORY}", raw_history),
                    temperature=0.1
                )
            )
            return response.text.strip() if response.text else "- Échanges en cours."
        except Exception as e:
            logger.error(f"❌ [REFINER] History condensation failed: {e}")
            return "- Erreur de synthèse de l'historique."
