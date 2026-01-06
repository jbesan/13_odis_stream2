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
            has_commune = bool(context.search_criteria.get("commune_actuelle"))
            has_adults = bool(context.search_criteria.get("nb_adultes"))
            has_profile = bool(context.search_criteria.get("weight_profile"))
            has_area = bool(context.search_criteria.get("loc_search_area"))
            
            has_min_data = has_commune and has_adults and has_profile and has_area
            
            logger.info(f"🔎 [ORCHESTRATOR] DISCOVERY Check: commune={has_commune}, adults={has_adults}, profile={has_profile}, area={has_area}")
            
            calc_keywords = ["calcule", "score", "résultat", "top", "meilleur", "lancer", "vas-y", "go", "oui", "ok"]
            
            if has_min_data and any(kw in low_msg for kw in calc_keywords):
                context.workflow_phase = "SCORING"
                logger.info("🚀 [ORCHESTRATOR] Transitioning DISCOVERY -> SCORING")
                return "scorer"
            
            return "interviewer"

        # --- PHASE 2: SCORING (SCORER) ---
        if context.workflow_phase == "SCORING":
            if any(kw in low_msg for kw in ["changer", "modifier", "nouveau", "recommence", "critère"]):
                 context.workflow_phase = "DISCOVERY"
                 return "interviewer"

            # Transition automatique vers DECORATION après le premier scoring réussi (ou sur demande de détail)
            context.workflow_phase = "DECORATION" 
            return "scout" # Scout par défaut pour la transition, mais process_message gérera le duo

        # --- PHASE 3: DECORATION / SYNTHESE ---
        if context.workflow_phase == "DECORATION":
            if any(kw in low_msg for kw in ["changer", "modifier", "nouveau", "recommence", "critère"]):
                 context.workflow_phase = "DISCOVERY"
                 return "interviewer"
            
            # Si l'utilisateur pose une question très spécifique à l'emploi
            if any(kw in low_msg for kw in ["job", "emploi", "travail", "poste", "recrutement", "salaire", "offres"]):
                return "job_hunter"
            
            return "scout"

        return "interviewer"

    def _synthesize(self, message: str, context: AgentContext, scout_res: str, job_res: str) -> str:
        """Fusionne les résultats de Scout et Job Hunter dans un pitch final."""
        logger.info("🧠 [ORCHESTRATOR] Synthesizing Scout + Job Hunter results...")
        
        synth_prompt = f"""
        Tu es le Synthétiseur ODIS. Ta mission est de fusionner les retours de deux experts pour donner une réponse unique, fluide et ultra-convaincante au travailleur social.
        
        **Expert Terrain (Scout)** : {scout_res}
        
        **Expert Emploi (Job Hunter)** : {job_res}
        
        **Instructions** :
        1. Fais une synthèse élégante en FRANÇAIS.
        2. Ne répète pas les titres. Structure la réponse par thématiques (Vie Quotidienne, Opportunités Emploi).
        3. Fais le lien avec le projet de vie de l'utilisateur ({context.search_criteria.get('weight_profile', 'Standard')}).
        4. Termine par une question ouverte pour valider l'intérêt pour cette ville.
        """
        
        response = self.client.models.generate_content(
            model=self.models["orchestrator"],
            contents=message,
            config=types.GenerateContentConfig(system_instruction=synth_prompt, temperature=0.3)
        )
        return response.text.strip() if response.text else "J'ai collecté les informations, comment puis-je vous aider davantage ?"

    def process_message(self, message: str, context: AgentContext) -> str:
        """Point d'entrée principal avec gestion des cascades multi-agents."""
        
        # 1. Routing
        target_agent_name = self._route(message, context)
        logger.info(f"🎯 [ORCHESTRATOR] Routing to: {target_agent_name} | Phase: {context.workflow_phase}")
        
        # 2. Update context active agent
        context.active_agent = target_agent_name
        
        # 3. Special Case: DECORATION (Scout + Job Hunter Cascade)
        if context.workflow_phase == "DECORATION" and target_agent_name in ["scout", "job_hunter"]:
            logger.info(f"⛓️ [ORCHESTRATOR] Starting Decoration Cascade (Initial City: {context.focus_city})")
            
            # --- AUTO-DETECTION SAFETY ---
            # If focus_city is empty, check if message mentions one of the top cities
            if not context.focus_city:
                for city in context.top_cities:
                    if city['name'].lower() in message.lower():
                        context.focus_city = city['name']
                        logger.info(f"✨ [ORCHESTRATOR] Auto-detected city in message: {context.focus_city}")
                        break
            
            logger.info("📡 [ORCHESTRATOR] Calling SCOUT...")
            scout_res = self.agents["scout"].run(message, context)
            logger.info(f"✅ [ORCHESTRATOR] SCOUT finished. Current City: {context.focus_city}")
            
            # RE-CHECK after Scout as Scout might have set it
            if not context.focus_city:
                 logger.warning("⚠️ [ORCHESTRATOR] Focus city still empty after Scout. Job Hunter might fail/be broad.")
            
            logger.info("📡 [ORCHESTRATOR] Calling JOB_HUNTER...")
            job_res = self.agents["job_hunter"].run(message, context)
            logger.info(f"✅ [ORCHESTRATOR] JOB_HUNTER finished. Current City: {context.focus_city}")
            
            final_response = self._synthesize(message, context, scout_res, job_res)
            logger.info("🏁 [ORCHESTRATOR] Synthesis complete.")
            
            # Record in history
            context.history.append({"role": "user", "parts": [{"text": message}]})
            context.history.append({"role": "model", "parts": [{"text": final_response}]})
            return final_response

        # 4. Standard Case (Single Agent)
        agent = self.agents.get(target_agent_name)
        if not agent:
            return "Désolé, j'ai rencontré une erreur de routing."
            
        try:
            response_text = agent.run(message, context)
            
            context.history.append({"role": "user", "parts": [{"text": message}]})
            valid_res = response_text if response_text and response_text.strip() else "..."
            context.history.append({"role": "model", "parts": [{"text": valid_res}]})
            
            return valid_res
        except Exception as e:
            logger.error(f"❌ [ORCHESTRATOR] Process Message Error: {e}")
            return "Une erreur technique est survenue. Peux-tu reformuler ta demande ?"
