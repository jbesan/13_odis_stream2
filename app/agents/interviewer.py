import logging
from typing import List, Dict, Any, Optional
from .base import BaseAgent
from .state import AgentContext
from google.genai import types
from .tools import search_referentiels, search_commune
import config as cfg

logger = logging.getLogger("interviewer_agent")

INTERVIEWER_PROMPT = """
**Rôle** : Tu es l'Interviewer ODIS. Ta mission est de collecter auprès d'un travailleur social (l'utilisateur) les besoins d'un réfugié (et éventuellement sa famille) pour sa mobilité en France.
**Ton** : Empathique, professionnel, direct. Utilise le tutoiement.

**Contexte de l'entretien (Briefing)** :
{BRIEFING}


**Outils disponibles** :
- `search_commune` : Cherche le code INSEE d'une commune.
- `search_referentiels` : Cherche les codes ROME (métier), Formation, Services d'inclusion ou catégorie associative (WALDEC) correspondants.
- `update_search_criteria` : Enregistre les données validées dans le dossier du bénéficiaire.

**DIRECTIVE DE COLLECTE (CRITIQUE)** :
- **GROUPEMENT DES APPELS** : Ne fais JAMAIS plusieurs appels à `update_search_criteria` à la suite. Collecte TOUTES les informations d'un message utilisateur (ex: nom, métier, ville) et fais UN SEUL appel à l'outil à la fin.
- **PARCIMONIE DES OUTILS** : N'utilise `search_referentiels` ou `search_commune` que si l'information n'est pas déjà claire dans le **Briefing**.
- **PAS DE DISCOURS INUTILE** : Ne commente pas tes appels d'outils ("Je vais chercher le code..."). Fais l'appel, et donne la réponse finale.

**Instructions de Collecte (Ordre Prioritaire)** :
1. **Commune Actuelle** : Cherche le code INSEE avec `search_commune`.
2. **Composition Familiale** : Demande le nombre d'adultes et d'enfants.
3. **Périmètre de Recherche** : {LOC_SEARCH_AREAS}.
4. **Projet Pro & Formations** : Cherche IMMEDIATEMENT via `search_referentiels` les codes ROME (`rome_codes`) ou Formation (`formation_codes`) correspondant. Si tu ne trouve aucune correspondance qui correspond demande 
5. **Logement & Hébergement** : Choisi dans {HEBERGEMENT_OPTIONS} et {LOGEMENT_OPTIONS}.
6. **Éducation des Enfants** : Choisi dans {CLASSES_SCOLAIRES}.
7. **Santé Spécifique** : Choisi dans {SANTE_OPTIONS}.
8. **Notes Qualitatives** : Passions, origine, besoins spécifiques (Religion, Velo, Halal, etc).
9. **Inclusions & Assos** : Cherche IMMEDIATEMENT via `search_referentiels` (inclusion_services ou waldec_codes).

**Profil de Pondération** : Suggère un profil parmi : {WEIGHT_PROFILES}.

**DIRECTIVE DE TRANSITION** :
Tant que tu n'as pas : **Commune Actuelle**, **Nb Adultes**, **Profil** et **Périmètre**, reste en collecte.
Une fois acquis, dis : "J'ai bien noté vos critères (Profil: {weight_profile}, Zone: {loc_search_area}). Voulez-vous que je lance le calcul pour trouver vos meilleures villes ?"
Demande la confirmation avant de terminer.
"""

class InterviewerAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        
        briefing_data, user_msg = self._get_briefing_and_user_msg(message)
        
        # Use .replace instead of .format to avoid KeyError with braces in criteria dict
        prompt = INTERVIEWER_PROMPT.replace("{BRIEFING}", briefing_data)
        # print(f"INTERVIEWER_PROMPT: {prompt}")

        # Inject config values
        # Filter 'custom' from the options presented to the LLM (internal logic handled by Orchestrator/Engine)
        filtered_areas_keys = [k for k in cfg.LOC_SEARCH_AREA_OPTIONS.keys() if k != 'custom']
        filtered_areas_values = [v for k, v in cfg.LOC_SEARCH_AREA_OPTIONS.items() if k != 'custom']

        prompt = prompt.replace("{CLASSES_SCOLAIRES}", str(cfg.CLASSES_SCOLAIRES))
        prompt = prompt.replace("{HEBERGEMENT_OPTIONS}", str(cfg.HEBERGEMENT_OPTIONS))
        prompt = prompt.replace("{LOGEMENT_OPTIONS}", str(cfg.LOGEMENT_OPTIONS))
        prompt = prompt.replace("{SANTE_OPTIONS}", str(cfg.SANTE_OPTIONS))
        prompt = prompt.replace("{WEIGHT_PROFILES}", str(list(cfg.WEIGHT_PROFILES.keys())))
        prompt = prompt.replace("{LOC_SEARCH_AREAS}", ", ".join(filtered_areas_values))
        
        # Fill current values for the transition directive
        wp = context.search_criteria.get('weight_profile', 'Non défini')
        lsa = context.search_criteria.get('loc_search_area', 'Non définie')
        prompt = prompt.replace("{weight_profile}", wp)
        prompt = prompt.replace("{loc_search_area}", lsa)
        
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
            sante: Optional[str] = None,
            notes_qualitatives: Optional[List[str]] = None
        ) -> str:
            """
            Enregistre les données validées dans le dossier du bénéficiaire. 
            
            Args:
                commune_actuelle: Code INSEE de la ville (ex: '75056')
                nb_adultes: Nombre d'adultes
                nb_enfants: Nombre d'enfants
                weight_profile: {WEIGHT_PROFILES_LIST}
                loc_search_area: {LOC_SEARCH_AREAS_LIST}
                loc_custom_code: Code de la région/département cible (ex: '75')
                loc_custom_type: 'region' ou 'departement'
                codes_metiers: Liste de liste de codes ROME (ex: [['M1805']])
                codes_formations: Liste de liste de codes formation
                classe_enfants: Liste des niveaux scolaires (ex: {CLASSES_SCOLAIRES_LIST})
                inc_services_add_selection: Codes des services d'inclusion (ex: ['FLE'])
                inc_asso_add_selection: Codes des associations (ex: ['FOOT'])
                hebergement: Type d'hébergement souhaité ({HEBERGEMENT_OPTIONS_LIST})
                logement: Type de logement ({LOGEMENT_OPTIONS_LIST})
                sante: Besoin santé spécifique ({SANTE_OPTIONS_LIST})
                notes_qualitatives: Indices de vie (ex: ["Passionné d'échecs", "Libanais"])
            """
            # Injecting dynamic values into docstring (LLM will see these in the tool definition)
            # This is done manually here since docstrings are static in Python but the tool extraction reads them.
            # We'll use a hack below to update the docstring dynamically if needed, 
            # but for now let's just use the literals for the most critical ones if they don't change often
            # OR better: use placeholders and replace them in the agent loop if the framework allows.
            # Actually, the simplest is to update this docstring during runtime.

            updates: Dict[str, Any] = {}
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
            if notes_qualitatives: updates['notes_qualitatives'] = notes_qualitatives
            
            if updates:
                context.search_criteria.update(updates)
                # logger.info(f"💾 [CRITERIA_UPDATED] {updates}")
                return f"SUCCESS: Données enregistrées: {list(updates.keys())}"
            return "Rien à mettre à jour."

        # We inject the local wrapper instead of the stub
        tools = [search_referentiels, search_commune, local_update_criteria]
        # BUT we must set the name of the function to match the tool definition the LLM sees
        local_update_criteria.__name__ = "update_search_criteria"
        
        # Update docstring dynamically to reflect config values for the LLM
        doc = local_update_criteria.__doc__ or ""
        local_update_criteria.__doc__ = doc.replace("{WEIGHT_PROFILES_LIST}", str(list(cfg.WEIGHT_PROFILES.keys())))
        local_update_criteria.__doc__ = local_update_criteria.__doc__.replace("{LOC_SEARCH_AREAS_LIST}", str(filtered_areas_keys))
        local_update_criteria.__doc__ = local_update_criteria.__doc__.replace("{CLASSES_SCOLAIRES_LIST}", str(cfg.CLASSES_SCOLAIRES))
        local_update_criteria.__doc__ = local_update_criteria.__doc__.replace("{HEBERGEMENT_OPTIONS_LIST}", str(cfg.HEBERGEMENT_OPTIONS))
        local_update_criteria.__doc__ = local_update_criteria.__doc__.replace("{LOGEMENT_OPTIONS_LIST}", str(cfg.LOGEMENT_OPTIONS))
        local_update_criteria.__doc__ = local_update_criteria.__doc__.replace("{SANTE_OPTIONS_LIST}", str(cfg.SANTE_OPTIONS))


        try:
            return self._execute_tool_loop(prompt, user_msg, tools, context=context)
        except Exception as e:
            logger.error(f"❌ [INTERVIEWER] Loop Error: {e}")
            return "J'éprouve des dificultés pour traiter vos informations..."

