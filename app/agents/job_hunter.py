from .base import BaseAgent
from .state import AgentContext
from .tools import (
    search_job_offers, 
    get_job_details, 
    search_referentiels,
    search_commune
)
import logging
import re

logger = logging.getLogger("job_hunter_agent")

JOB_HUNTER_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}

**Objectif** : Trouver des offres d'emploi RÉELLES et PERTINENTES selon le `CONTEXTE RÉSUMÉ` dans `VILLE ACTIVE` pour TOUS les adultes du ménage.

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **Utilisation du Code INSEE (codgeo)** : Récupère le Code INSEE (codgeo) de la ville de `VILLE ACTIVE` avec l'outil `search_commune`.
2. **RECHERCHE D'OFFRES (ROME ONLY)** : Lance `search_job_offers` pour CHAQUE code ROME identifié dans le `CONTEXTE RÉSUMÉ`.
   - Utilise le paramètre `rome`.
   - Si tu as un doute sur le code ROME, utilise `search_referentiels` avec le domaine `rome_codes` pour trouver la catégorie correspondante.
   - Ne spécifie pas de `query` (mots-clés) sauf si l'utilisateur a donné une précision particulière (ex: "en alternance").
3. **CONTEXTE LIVE** : Le briefing contient un nombre d'offres global (Live) pour la ville. Utilise ce chiffre UNIQUEMENT pour donner une tendance générale.
4. **COMPTAGE PRÉCIS** : Pour CHAQUE métier recherché, utilise la valeur `total` retournée par l'outil `search_job_offers`. C'est le SEUL chiffre précis pour le métier en question.
5. **LOCALISATION** : Utilise toujours le code INSEE de la ville cible du `CONTEXTE RÉSUMÉ` pour la recherche.
6. **NE DEMANDE PAS DE PRÉCISIONS** : Tu as les informations sur les métiers dans les critères. AGIS IMMÉDIATEMENT sans attendre de confirmation.
7. **SÉLECTION ET RÉPONSE (CRITIQUE)** : 
    - Pour chaque recherche réussie, tu DOIS sélectionner et présenter les **3 offres les plus pertinentes** (ou toutes si moins de 3 sont disponibles).
    - Pour chaque offre, indique : Intitulé, ID (ex: 7874186) et une phrase expliquant pourquoi elle correspond bien au profil (distance, contrat, expérience).
    - Ne te contente JAMAIS d'une seule offre si l'outil en retourne plusieurs.
    - Termine en demandant si l'utilisateur veut voir plus de détails (`get_job_details`) sur une offre spécifique.
"""

JOB_DETAILS_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.
**Objectif** : Donner le DETAIL d'une offre d'emploi précise que l'utilisateur a repéré.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}
**OFFRE CIBLÉE** : {JOB_ID}

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **RECUPERATION DE L'OFFRE** : Tu DOIS IMMEDIATEMENT appeler `get_job_details` pour l'ID `{JOB_ID}`.
2. **SYNTHÈSE DE L'OFFRE** : Synthétise les points clés : 
   - Lien vers l'offre
   - Type de contrat et durée.
   - Compétences attendues (traduis si trop technique).
   - Analyse d'adéquation avec le `CONTEXTE RÉSUMÉ`.
   - Employeur. Localisation précise et salaire (si disponible).
3. **NE RECHERCHE PAS d'autres offres** sauf si explicitement demandé. Reste focus on cette offre.
"""

class JobHunterAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        briefing_data, user_msg = self._get_briefing_and_user_msg(message)
        
        # 2. Detect Intent: Detail or Search?
        # Matches 6-12 alphanumeric chars WITH at least 3 digits (to avoid words like "QUELLES")
        job_id_match = re.search(r'\b((?=(?:\D*\d){3,})[A-Z0-9]{6,12})\b', user_msg.upper())
        
        if job_id_match:
            job_id = job_id_match.group(1)
            prompt = JOB_DETAILS_PROMPT.replace("{JOB_ID}", job_id)
            prompt = prompt.replace("{BRIEFING}", briefing_data)
            prompt = prompt.replace("{FOCUS_CITY}", str(context.focus_city or "Non définie"))
        else:
            # Standard Search Logic - Simplified by Briefing
            prompt = JOB_HUNTER_PROMPT.replace("{BRIEFING}", briefing_data)
            prompt = prompt.replace("{FOCUS_CITY}", str(context.focus_city or "Non définie"))

        # print(f"JOB_HUNTER_PROMPT: {prompt}")
        
        try:
            res = self._execute_tool_loop(
                prompt, 
                user_msg, 
                [search_job_offers, get_job_details, search_referentiels, search_commune], 
                    context=context
                )
            return res
        except Exception as e:
            logger.error(f"❌ [JOB_HUNTER] Error: {e}", exc_info=True)
            return "Désolé, je n'ai pas pu accéder aux offres d'emploi pour le moment."
