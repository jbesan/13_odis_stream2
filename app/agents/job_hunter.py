from .base import BaseAgent
from .state import AgentContext
from .tools import (
    search_job_offers, 
    get_job_details, 
    search_rome_appellations
)
import logging
import re

logger = logging.getLogger("job_hunter_agent")

JOB_HUNTER_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.
**CONTEXTE ACTUEL (Briefing)** :
{BRIEFING}

**Objectif** : Trouver des offres d'emploi RÉELLES et PERTINENTES dans la ville cible (voir Briefing) pour TOUS les adultes du ménage.

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **RECHERCHE D'OFFRES (ROME ONLY)** : Lance `search_job_offers` pour CHAQUE code ROME identifié dans le **Briefing**. 
   - Utilise le paramètre `rome_code`.
   - Ne spécifie pas de `query` (mots-clés) sauf si l'utilisateur a donné une précision particulière (ex: "en alternance").
2. **CONTEXTE LIVE** : Le système a déjà calculé le nombre d'offres en direct (Live) pour la ville cible dans le briefing. Utilise ces chiffres pour valoriser les opportunités réelles.
3. **LOCALISATION** : Utilise toujours le code INSEE de la ville cible du **Briefing** pour la recherche.
4. **NE DEMANDE PAS DE PRÉCISIONS** : Tu as les informations sur les métiers dans les critères. AGIS IMMÉDIATEMENT sans attendre de confirmation.
5. **RÉPONSE** : Pour chaque recherche
    - Dénombre et retourne le nombre d'offres trouvées par domaine métier.
    - Sélectionne les 3 offres les plus pertinentes selon le {BRIEFING} (compatibilité, distance, date de publication). Présente chaque offre trouvée avec son code de référence (ex: 048KLTP) de manière synthétique et précise en une phrase pourquoi elle te semble pertinente.
    - Termine en demandant si l'utilisateur veut voir plus de détails (get_job_details) sur une offre spécifique.
"""

JOB_DETAILS_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.
**Objectif** : Donner le DETAIL d'une offre d'emploi précise que l'utilisateur a repéré.

**OFFRE CIBLÉE** : {JOB_ID}

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **RECUPERATION DE L'OFFRE** : Tu DOIS IMMEDIATEMENT appeler `get_job_details` pour l'ID `{JOB_ID}`.
2. **SYNTHÈSE DE L'OFFRE** : Synthétise les points clés : 
   - Lien vers l'offre
   - Type de contrat et durée.
   - Compétences attendues (traduis si trop technique).
   - Employeur. Localisation précise et salaire (si dispo).
3. **NE RECHERCHE PAS d'autres offres** sauf si explicitement demandé. Reste focus sur cette offre.
"""

class JobHunterAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        briefing_data, user_msg = self._get_briefing_and_user_msg(message)
        
        # 2. Detect Intent: Detail or Search?
        # Matches 6-12 alphanumeric chars WITH at least 3 digits (to avoid words like "QUELLES")
        job_id_match = re.search(r'\b((?=(?:\D*\d){3,})[A-Z0-9]{6,12})\b', user_msg.upper())
        
        if job_id_match:
            job_id = job_id_match.group(1)
            logger.info(f"🎯 [JOB_HUNTER] Detail Intent detected for ID: {job_id}")
            prompt = JOB_DETAILS_PROMPT.format(JOB_ID=job_id)
        else:
            # Standard Search Logic - Simplified by Briefing
            prompt = JOB_HUNTER_PROMPT.replace("{BRIEFING}", briefing_data)
            logger.info(f"🔍 [JOB_HUNTER] Proactive Search with Briefing")

        try:
            res = self._execute_tool_loop(
                prompt, 
                user_msg, 
                [search_job_offers, get_job_details, search_rome_appellations], 
                    context=context
                )
            return res
        except Exception as e:
            logger.error(f"❌ [JOB_HUNTER] Error: {e}", exc_info=True)
            return "Désolé, je n'ai pas pu accéder aux offres d'emploi pour le moment."
