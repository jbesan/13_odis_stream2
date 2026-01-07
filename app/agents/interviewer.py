import logging
from typing import List, Dict, Any, Optional
from .base import BaseAgent
from .state import AgentContext
from google.genai import types
from .tools import search_referentiels, search_commune

logger = logging.getLogger("interviewer_agent")

INTERVIEWER_PROMPT = """
**Rôle** : Tu es l'Interviewer ODIS. Ta mission est de collecter auprès d'un travailleur social (l'utilisateur) les besoins d'un réfugié (et éventuellement sa famille) pour sa mobilité en France.
**Ton** : Empathique, professionnel, direct. Utilise le tutoiement.

**État Actuel des Critères** :
{CURRENT_CRITERIA}

**Historique Recent** :
{HISTORY_SUMMARY}

**DIRECTIVE CRITIQUE** :
- **RÉPONSE FRANÇAISE** : Tu DOIS toujours répondre en Français, même après des appels d'outils.
- **PROTOCOLE SEARCH-THEN-SAVE** : Chaque fois que tu trouves un code (INSEE ou FAP) via un outil de recherche, tu DOIS IMMÉDIATEMENT appeler `update_search_criteria` pour l'enregistrer. 
  - Si tu ne l'appelles pas, la donnée est PERDUE pour le tour suivant.
- **ANTI-REDONDANCE** : Consulte proactivement `CURRENT_CRITERIA`. 
  - Si `commune_actuelle` a déjà un code, NE RELANCE PAS `search_commune`.
  - Si `codes_metiers` contient déjà des codes, NE RELANCE PAS `search_referentiels` pour les mêmes métiers.
- **CIBLAGE DES VIDES** : Ton but unique est de remplir les cases vides de `CURRENT_CRITERIA`.

**Instructions de Collecte (Ordre Prioritaire)** :
1. **Commune Actuelle** : Cherche le code INSEE avec `search_commune`.
2. **Composition Familiale** : Demande le nombre d'adultes et d'enfants.
3. **Profil de Pondération (Priorité Haute)** : Si `weight_profile` est vide (""), fais choisir entre : 'Famille', 'Santé', 'Économique', 'Équilibré'.
4. **Périmètre de Recherche** : France entière, Région, ou Département.
5. **Logement & Hébergement** : 
   - **Hébergement souhaité** : Choisi dans ["Chez l'habitant", "Location", "Foyer"].
   - **Type de Logement** : Choisi dans ["Location", "Logement Social"].
6. **Éducation des Enfants** : Si `nb_enfants` > 0, choisis EXCLUSIVEMENT dans : ['Crèche / Assistante Maternelle', 'Maternelle', 'Elémentaire', 'Collège', 'Lycée']. Enregistre une LISTE (une valeur par enfant, ex: `['Maternelle', 'Collège']`) dans `classe_enfants`.
7. **Santé Spécifique** : Choisi dans ["Aucun", "Hopital", "Maternité", "Soutien Psychologique & Addictologie"].
8. **Services d'Inclusion** : Cherche via `search_referentiels(domain='inclusion_services')` (ex: FLE, aide juridique) et enregistre le `code`.
9. **Associations** : Cherche via `search_referentiels(domain='waldec_codes')` (ex: Football, Yoga) et enregistre le `code`.
10. **Projet Pro & Formations** : Cherche via `search_referentiels(domain='fap_codes' ou 'formation_codes')` et enregistre les codes.

**DIRECTIVE DE TRANSITION** :
Tant que tu n'as pas au moins : **Commune Actuelle**, **Nb Adultes**, **Profil de Pondération** et **Périmètre**, reste en phase de collecte.
Une fois acquis, dis : "J'ai bien noté vos critères (Profil: {weight_profile}, Zone: {loc_search_area}). Voulez-vous que je lance le calcul pour trouver vos meilleures villes ?"
NE LANCE PAS `compute_top_cities` TOI-MÊME.
"""

class InterviewerAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        
        # 1. Prepare Prompt-based Memory
        history_summary = ""
        if context.history:
            # Get last 10 turns for better continuity
            # Limit history to 5 turns to save tokens
            for turn in context.history[-5:]:
                role = "Utilisateur" if turn.get("role") == "user" else "Assistant"
                parts = turn.get("parts", [])
                text_parts = [p.get("text") for p in parts if isinstance(p, dict) and p.get("text")]
                text = " ".join(text_parts) if text_parts else ""
                history_summary += f"- {role}: {text}\n"
        
        # Use .replace instead of .format to avoid KeyError with braces in criteria dict
        prompt = INTERVIEWER_PROMPT.replace("{CURRENT_CRITERIA}", str(context.search_criteria))
        prompt = prompt.replace("{HISTORY_SUMMARY}", history_summary)
        
        # 2. Local Criteria Interceptor
        def local_update_criteria(
            commune_actuelle: Optional[str] = None,
            nb_adultes: Optional[int] = None,
            nb_enfants: Optional[int] = None,
            weight_profile: Optional[str] = None,
            loc_search_area: Optional[str] = None,
            loc_custom_code: Optional[str] = None,
            loc_custom_type: Optional[str] = None,
            codes_metiers: Optional[List[List[str]]] = None,
            codes_formations: Optional[List[List[str]]] = None,
            classe_enfants: Optional[List[str]] = None,
            inc_services_add_selection: Optional[List[str]] = None,
            inc_asso_add_selection: Optional[List[str]] = None,
            hebergement: Optional[str] = None,
            logement: Optional[str] = None,
            sante: Optional[str] = None
        ) -> str:
            """
            Enregistre les données validées dans le dossier du bénéficiaire. 
            
            Args:
                commune_actuelle: Code INSEE de la ville (ex: '75056')
                nb_adultes: Nombre d'adultes
                nb_enfants: Nombre d'enfants
                weight_profile: 'Famille', 'Santé', 'Économique' ou 'Équilibré'
                loc_search_area: 'departement', 'region' ou 'france'
                loc_custom_code: Code de la région/département cible (ex: '75')
                loc_custom_type: 'region' ou 'departement'
                codes_metiers: Liste de liste de codes FAP (ex: [['T2A60']])
                codes_formations: Liste de liste de codes formation
                classe_enfants: Liste des niveaux scolaires (ex: ['Maternelle', 'CP'])
                inc_services_add_selection: Codes des services d'inclusion (ex: ['FLE'])
                inc_asso_add_selection: Codes des associations (ex: ['FOOT'])
                hebergement: Type d'hébergement souhaité ("Chez l'habitant", "Location", "Foyer")
                logement: Type de logement ("Location", "Logement Social")
                sante: Besoin santé spécifique ("Aucun", "Hopital", "Maternité", "Soutien Psychologique & Addictologie")
            """
            updates = {}
            if commune_actuelle: updates['commune_actuelle'] = commune_actuelle
            if nb_adultes is not None: updates['nb_adultes'] = nb_adultes
            if nb_enfants is not None: updates['nb_enfants'] = nb_enfants
            if weight_profile: updates['weight_profile'] = weight_profile
            if loc_search_area: updates['loc_search_area'] = loc_search_area
            if loc_custom_code: updates['loc_custom_code'] = loc_custom_code
            if loc_custom_type: updates['loc_custom_type'] = loc_custom_type
            if codes_metiers: updates['codes_metiers'] = codes_metiers
            if codes_formations: updates['codes_formations'] = codes_formations
            if classe_enfants: updates['classe_enfants'] = classe_enfants
            if inc_services_add_selection: updates['inc_services_add_selection'] = inc_services_add_selection
            if inc_asso_add_selection: updates['inc_asso_add_selection'] = inc_asso_add_selection
            if hebergement: updates['hebergement'] = hebergement
            if logement: updates['logement'] = logement
            if sante: updates['sante'] = sante
            
            if updates:
                context.search_criteria.update(updates)
                logger.info(f"💾 [CRITERIA_UPDATED] {updates}")
                return f"SUCCESS: Données enregistrées: {list(updates.keys())}"
            return "Rien à mettre à jour."

        # We inject the local wrapper instead of the stub
        tools = [search_referentiels, search_commune, local_update_criteria]
        # BUT we must set the name of the function to match the tool definition the LLM sees
        local_update_criteria.__name__ = "update_search_criteria"

        try:
            return self._execute_tool_loop(prompt, message, tools, context=context)
        except Exception as e:
            logger.error(f"❌ [INTERVIEWER] Loop Error: {e}")
            return "Je traite vos informations... Pouvez-vous me donner plus de précisions ?"

