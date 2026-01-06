import logging
from typing import Dict, Any, List, Optional
from google import genai
from google.genai import types

from .state import AgentContext, UserProfile
from .interviewer import InterviewerAgent
from .scorer import ScorerAgent
from .scout import ScoutAgent
from .job_hunter import JobHunterAgent

logger = logging.getLogger("orchestrator")

ROUTING_PROMPT = """
Tu es le Cerveau de l'Assistant ODIS. Ton job est de router le message de l'utilisateur vers le bon agent spécialisé pour éffectuer une recherche multi-étapes et de retourner la synthèse finale.

**Agents disponibles** :
1. **INTERVIEWER** : Pour la collecte de besoins, questions sur la famille, les métiers, la ville de départ. (Par défaut au début).
2. **SCORER** : Pour lancer le calcul du Top villes, expliquer les scores, ou quand l'utilisateur dit "Calcule" / "Quelles sont les meilleures villes ?".
3. **SCOUT** : Pour les questions sur les itinéraires (Gmaps), la recherche de lieux précis (écoles, commerces) dans une ville donnée.
4. **JOB_HUNTER** : Pour les questions sur la recherche d'emploi concrète, les offres d'emploi, le marché du travail.

** Déroulé de la recherche ** : Un déroulé typique serait ces 4 phases dans cet ordre:
1. INTERVIEWER: récupérer les critères de base et obligatoires ainsi qu'un maximum de préférences du projet de vie
2. SCORER: calculer le top communes et retourner le Top 3 selon les critères de base et les préférences et demander à l'utilisateur s'il veut en savoir plus sur l'une d'entre elle
3. DECORATION: Si l'utilisateur demande des informations sur une commune, on lance:
    - SCOUT: rechercher des informations complémentaires sur la commune demandée
    - JOB_HUNTER: rechercher des emplois dans la commune demandée
4. SYNTHÈSE: Formule un pitch qui présente les points forts et faibles de la commune demandée selon le contexte obtenu lors des étapes SCORER (scores numériques) et DECORATION (informations complémentaires)

**Règles de décision** :
1. Si l'utilisateur pose une question de détail (écoles, trajet, vie locale) sur une des villes identifiées -> SCOUT.
2. Si l'utilisateur parle de trouver un job précis, de CV ou d'offres concrètes -> JOB_HUNTER.
3. Si l'utilisateur veut changer ses critères de base -> INTERVIEWER.

**Contexte Actuel** :
- Agent Actif : {ACTIVE_AGENT}
- Critères récoltés : {HAS_CRITERIA} (True/False)
- Villes identifiées** : {CITIES_LIST}
- Phase Actuelle** : {PHASE}

** Contraintes ** : 
- Commence toujours la réponse avec le NOM de l'agent en MAJUSCULES (Exemple: SCORER).
- Ne retourne jamais le score numérique 'scaled' mais uniquement l'interprétation de ce score.
"""

class MultiAgentOrchestrator:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        
        # Configuration des modèles (2026)
        self.models = {
            "orchestrator": "gemini-3-flash-preview", 
            "interviewer": "gemini-3-flash-preview",
            "scorer": "gemini-2.5-flash-lite", 
            "scout": "gemini-2.5-flash-lite",
            "job_hunter": "gemini-2.5-flash-lite"
        }
        
        # Initialisation des Agents
        self.agents = {
            "interviewer": InterviewerAgent(self.models["interviewer"], self.client),
            "scorer": ScorerAgent(self.models["scorer"], self.client),
            "scout": ScoutAgent(self.models["scout"], self.client),
            "job_hunter": JobHunterAgent(self.models["job_hunter"], self.client)
        }

    def _route(self, message: str, context: AgentContext) -> str:
        """Détermine quel agent doit répondre en fonction de la phase et du message."""
        low_msg = message.lower()
        
        # --- PHASE 1: DISCOVERY (INTERVIEWER) ---
        if context.workflow_phase == "DISCOVERY":
            # Priority: Check if we have enough data to switch to SCORING
            has_min_data = (
                context.search_criteria.get("commune_actuelle") and 
                context.search_criteria.get("nb_adultes") and
                context.search_criteria.get("weight_profile") and
                context.search_criteria.get("loc_search_area")
            )
            
            calc_keywords = ["calcule", "score", "résultat", "top", "meilleur", "lancer", "vas-y", "go", "oui", "ok"]
            
            # Transition -> SCORING
            if has_min_data and any(kw in low_msg for kw in calc_keywords):
                context.workflow_phase = "SCORING"
                return "scorer"
            
            return "interviewer"

        # --- PHASE 2: SCORING (SCORER) ---
        if context.workflow_phase == "SCORING":
            # If user wants to restart/modify
            if any(kw in low_msg for kw in ["changer", "modifier", "nouveau", "recommence", "critère"]):
                 context.workflow_phase = "DISCOVERY"
                 return "interviewer"

            # If the previous turn was Scorer and it wasn't an error, we assume Scoring is done.
            # Transition -> DECORATION (Default next step)

            context.workflow_phase = "DECORATION" 
            
        # --- PHASE 3: DECORATION (SCOUT / JOB_HUNTER) ---
        if context.workflow_phase == "DECORATION":
             # Back to Discovery
            if any(kw in low_msg for kw in ["changer", "modifier", "nouveau", "recommence", "critère"]):
                 context.workflow_phase = "DISCOVERY"
                 return "interviewer"
            
            # Job specific
            if any(kw in low_msg for kw in ["job", "emploi", "travail", "poste", "recrutement", "salaire", "offres"]):
                return "job_hunter"
            
            # Default to SCOUT for city details, amenities, POIs, life
            return "scout"

        # Fallback
        return "interviewer"

    def process_message(self, message: str, context: AgentContext) -> str:
        """Point d'entrée principal pour traiter un message."""
        
        # 1. Routing
        target_agent_name = self._route(message, context)
        logger.info(f"🎯 [ORCHESTRATOR] Routing to: {target_agent_name} | Message: '{message[:50]}...'")
        
        # 2. Update context active agent
        context.active_agent = target_agent_name
        
        # 3. Delegate to Agent
        agent = self.agents.get(target_agent_name)
        if not agent:
            return "Désolé, j'ai rencontré une erreur de routing."
            
        try:
            response_text = agent.run(message, context)
            
            # Simple History storage (for logic, not for SDK)
            context.history.append({"role": "user", "parts": [{"text": message}]})
            valid_res = response_text if response_text and response_text.strip() else "..."
            context.history.append({"role": "model", "parts": [{"text": valid_res}]})
            
            return valid_res
        except Exception as e:
            logger.error(f"❌ [ORCHESTRATOR] Process Message Error: {e}")
            return "Une erreur technique est survenue. Peux-tu reformuler ta demande ?"
