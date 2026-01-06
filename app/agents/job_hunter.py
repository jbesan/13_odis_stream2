from .base import BaseAgent
from .state import AgentContext
from .tools import search_job_offers, get_job_details
import logging

logger = logging.getLogger("job_hunter_agent")

JOB_HUNTER_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert du marché de l'emploi.
**Critères** : {CRITERIA}
**VILLE ACTIVE (FOCUS)** : {FOCUS_CITY}

**Instructions** :
1. **APPEL SYSTÉMATIQUE** : Tu DOIS toujours appeler `search_job_offers` pour cette ville ({FOCUS_CITY}) en utilisant le code métier principal ({METIERS}).
2. **Contexte** : Utilise les codes métiers ({METIERS}) et la ville active ({FOCUS_CITY}) pour tes recherches.
3. **Recherche** : Si tu as un code INSEE pour la ville active, utilise-le.
4. **Détails** : Si l'utilisateur s'intéresse à une offre précise, utilise `get_job_details`.
5. **Réponse** : 
    - Présente les offres de manière synthétique et attrayante.
    - Met en avant l'adéquation avec le projet de vie.
    - Tu DOIS toujours répondre en FRANÇAIS.
    - Termine TOUJOURS en demandant si l'utilisateur veut postuler ou voir d'autres offres.
"""

class JobHunterAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        # Extract first metier as fap_code if available
        metiers_lists = context.search_criteria.get('codes_metiers', [])
        fap_code = None
        if metiers_lists and isinstance(metiers_lists, list) and metiers_lists[0]:
            fap_code = metiers_lists[0][0] if isinstance(metiers_lists[0], list) else metiers_lists[0]

        focus_city = context.focus_city or "Non définie"
        
        # Look up INSEE code in top_cities
        insee_code = None
        for city in context.top_cities:
            if city.get('name') == focus_city or city.get('codgeo') == focus_city:
                insee_code = city.get('codgeo')
                break
        
        logger.info(f"🔍 [JOB_HUNTER] Context Debug: city='{focus_city}' | INSEE='{insee_code}' | FAP: {fap_code}")
        
        prompt = JOB_HUNTER_PROMPT.format(
            CRITERIA=str(context.search_criteria),
            METIERS=str(metiers_lists),
            FOCUS_CITY=f"{focus_city} (Code INSEE: {insee_code})" if insee_code else focus_city
        )

        logger.info(f"🔍 [JOB_HUNTER] Running for city: {focus_city} | INSEE: {insee_code} | FAP: {fap_code}")
        try:
            res = self._execute_tool_loop(prompt, message, [search_job_offers, get_job_details])
            logger.info(f"💼 [JOB_HUNTER] Tool loop finished. Result: {res[:100]}...")
            return res
        except Exception as e:
            logger.error(f"❌ [JOB_HUNTER] Error: {e}", exc_info=True)
            return "Désolé, je n'ai pas pu accéder aux offres d'emploi pour le moment."
