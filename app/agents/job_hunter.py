from .base import BaseAgent
from .state import AgentContext
from .tools import (
    search_job_offers, 
    get_job_details, 
    search_rome_appellations,
    get_labels_for_codes,
    get_rome_for_fap
)
import logging

logger = logging.getLogger("job_hunter_agent")

JOB_HUNTER_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.
**VILLE ACTIVE (FOCUS)** : {FOCUS_CITY}

**Objectif** : Trouver des offres d'emploi RÉELLES et PERTINENTES dans la ville de {FOCUS_CITY} pour TOUS les adultes du ménage.

**MÉTIERS PAR PERSONNE** : 
{METIERS_DETAILS}

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **TRANSFORMATION EN ROME** : Tu DOIS transformer TOUS les métiers listés ci-dessus en codes ROME précis.
   - Pour chaque personne, utilise `get_rome_for_fap` avec leurs codes FAP respectifs.
   - Si un code semble être déjà un code ROME (5 caractères, ex: D1104), utilise-le directement.
2. **RECHERCHE D'OFFRES** : Lance `search_job_offers` pour CHAQUE personne ou groupe de métiers pertinents. Tu peux faire plusieurs appels si nécessaire pour couvrir les différents profils d'adultes.
3. **NE DEMANDE PAS DE PRÉCISIONS** : Tu as les informations sur les métiers dans les critères. Ne demande pas "qui cherche quoi". AGIS.
4. **RÉPONSE** : 
    - Présente les offres trouvées de manière synthétique pour CHAQUE adulte (Adulte 1, Adulte 2).
    - Explique le lien avec le projet de vie.
    - Termine en demandant si l'utilisateur veut postuler ou voir d'autres détails.
"""

class JobHunterAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        # Extract metier codes grouped by person
        metiers_lists = context.search_criteria.get('codes_metiers', [])
        
        details_lines = []
        all_codes = []
        
        if isinstance(metiers_lists, list):
            nb_adultes_total = context.search_criteria.get('nb_adultes', 1)
            details_lines.append(f"**Composition du ménage** : {nb_adultes_total} adulte(s)")
            for i, person_codes in enumerate(metiers_lists):
                person_name = f"Adulte {i+1}"
                if not isinstance(person_codes, list):
                    person_codes = [person_codes]
                
                codes = [str(c) for c in person_codes if c]
                all_codes.extend(codes)
                
                labels_map = get_labels_for_codes(codes)
                display_list = []
                for c in codes:
                    label = labels_map.get(c, "Code ROME ou LIBELLÉ inconnu")
                    display_list.append(f"  - {c}: {label}")
                
                if display_list:
                    details_lines.append(f"### {person_name} :\n" + "\n".join(display_list))
                else:
                    details_lines.append(f"### {person_name} : Aucun métier spécifié.")
        
        metiers_details = "\n\n".join(details_lines)
        if not metiers_details:
            metiers_details = "Aucun métier identifié pour le moment."

        focus_city = context.focus_city or "Non définie"
        # Look up INSEE code in top_cities
        insee_code = None
        for city in context.top_cities:
            if city.get('name') == focus_city or city.get('codgeo') == focus_city:
                insee_code = city.get('codgeo')
                break
        
        prompt = JOB_HUNTER_PROMPT.format(
            METIERS_DETAILS=metiers_details,
            FOCUS_CITY=f"{focus_city} (Code INSEE: {insee_code})" if insee_code else focus_city
        )

        logger.info(f"🔍 [JOB_HUNTER] Proactive Run for city: {focus_city} | Métiers: {list(labels_map.values())}")
        try:
            res = self._execute_tool_loop(
                prompt, 
                message, 
                [search_job_offers, get_job_details, search_rome_appellations, get_rome_for_fap], 
                context=context
            )
            return res
        except Exception as e:
            logger.error(f"❌ [JOB_HUNTER] Error: {e}", exc_info=True)
            return "Désolé, je n'ai pas pu accéder aux offres d'emploi pour le moment."
