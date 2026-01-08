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
**Context**: La  ou les personnes accompagnées sont des adultes nouvellement arrivés en France (réfugiés, migrants, etc.) qui cherchent à s'intégrer par l'emploi. Ils ont probablement des difficultés de langue mais sont motivés et disposés à apprendre.

**VILLE ACTIVE (FOCUS)** : {FOCUS_CITY}

**Objectif** : Trouver des offres d'emploi RÉELLES et PERTINENTES dans la ville de `VILLE ACTIVE` pour TOUS les adultes du ménage.

**MÉTIERS PAR PERSONNE** : 
{METIERS_DETAILS}

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **RECHERCHE D'OFFRES (PASSAGE FAP)** : Lance `search_job_offers` pour CHAQUE métier des `MÉTIERS PAR PERSONNE`. 
   - Utilise le paramètre `fap_code` directement avec le code FAP de l'adulte (ex: G0B41).
   - Le moteur de recherche s'occupe désormais de la traduction automatique en domaines ROME pertinents.
   - Ne spécifie pas de `query` (mots-clés) sauf si l'utilisateur a donné une précision particulière (ex: "en alternance").
2. **LOCALISATION** : Utilise toujours le code INSEE de `VILLE ACTIVE` pour la recherche.
3. **NE DEMANDE PAS DE PRÉCISIONS** : Tu as les informations sur les métiers dans les critères. AGIS IMMÉDIATEMENT sans attendre de confirmation.
4. **RÉPONSE** : 
    - Pour chaque recherche, sélectionne les 3 offres les plus pertinentes (compatibilité, distance, date de publication).
    - Présente chaque offre trouvée avec son code de référence (ex: 048KLTP) de manière synthétique pour CHAQUE adulte.
    - Termine en demandant si l'utilisateur veut voir plus de détails (get_job_details) sur une offre spécifique.
"""

class JobHunterAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        # Extract metier codes grouped by person
        metiers_lists = context.search_criteria.get('codes_metiers', [])
        
        details_lines = []
        all_codes = []
        labels_map = {}
        
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
