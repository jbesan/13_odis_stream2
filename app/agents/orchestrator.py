import logging
import copy
import json
from typing import Dict, Any, List, Optional
import random
import streamlit as st
from google import genai
from google.genai import types

from .state import AgentContext, UserProfile
from .interviewer import InterviewerAgent
from .scorer import ScorerAgent
from .scout import ScoutAgent
from .job_hunter import JobHunterAgent
from .web import WebAgent

logger = logging.getLogger("orchestrator")

ROUTING_PROMPT = """
Tu es le Cerveau de l'Assistant ODIS. Ton job est de router le message de l'utilisateur vers le bon agent spécialisé pour éffectuer une recherche multi-étapes et de retourner la synthèse finale.

**Agents disponibles** :
1. **INTERVIEWER** : Pour la collecte de besoins (phase initiale).
2. **SCORER** : Pour calculer le Top 5 villes. Utilise-le dès que le dossier est prêt et que l'utilisateur confirme que l'on peut lancer la recherche.
3. **DECORATION** : Cascade Scout + Web + Job Hunter + Synthèse. Utilise-la UNIQUEMENT quand l'utilisateur demande "plus d'infos" ou "des détails" sur une ville déjà identifiée dans le Top 5.
4. **SCOUT** : Pour une question **spécifique** de vie locale ou trajet sur une ville (ex: "Combien de temps pour la préfecture ?", "Y a-t-il un parc ?").
5. **WEB** : Pour des recherches d'actualités, news ou contexte social sur le web (ex: "Quelles sont les news à Bordeaux ?", "Comment est l'accueil des réfugiés ?").
6. **JOB_HUNTER** : Pour une question **spécifique** sur l'emploi (ex: "Y a-t-il des offres en boulangerie ?").

** Stratégie de routage (CRITIQUE) ** :
- Si l'utilisateur décrit la situation de la personne accompagnée -> **INTERVIEWER**.
- Si l'utilisateur a fini de donner ses critères et veut voir les résultats -> **SCORER**.
- Si l'utilisateur veut explorer une ville de manière générale -> **DECORATION**.
- Si l'utilisateur pose une question dtrès pécifique sur un des résultats -> **SCOUT** ou **JOB_HUNTER** directement (PAS de décoration).
- Si l'utilisateur veut modifier un critère de recherche -> **INTERVIEWER**.
- Si l'utilisateur veut relancer un calcul -> **SCORER**.

**Contexte Actuel** :
- Agent Actif : {ACTIVE_AGENT}
- Villes identifiées : {CITIES_LIST}
- Phase Actuelle : {PHASE}
- Critères récoltés : {CRITERIA_JSON}

** Contraintes ** : 
- Réponds UNIQUEMENT par le NOM de l'agent en MAJUSCULES (ex: SCORER). Aucun texte avant ou après.
"""


class MultiAgentOrchestrator:
    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        
        # Configuration des modèles (2026)
        self.models = {
            "orchestrator": "gemini-3-flash-preview", 
            "interviewer": "gemini-3-flash-preview",
            "scorer": "gemini-2.5-flash-lite", 
            "scout": "gemini-3-flash-preview",
            "web": "gemini-3-flash-preview",
            "job_hunter": "gemini-2.5-flash-lite"
        }
        
        # Initialisation des Agents
        self.agents = {
            "interviewer": InterviewerAgent(self.models["interviewer"], self.client),
            "scorer": ScorerAgent(self.models["scorer"], self.client),
            "scout": ScoutAgent(self.models["scout"], self.client),
            "web": WebAgent(self.models["web"], self.client),
            "job_hunter": JobHunterAgent(self.models["job_hunter"], self.client)
        }

    def _route(self, message: str, context: AgentContext) -> str:
        """Détermine quel agent doit répondre en utilisant le LLM."""
        cities_list = ", ".join([c['name'] for c in context.top_cities]) if context.top_cities else "Aucune"
        criteria_json = json.dumps(context.search_criteria, ensure_ascii=False, indent=2)
        
        prompt = ROUTING_PROMPT
        prompt = prompt.replace("{ACTIVE_AGENT}", context.active_agent or "Aucun")
        prompt = prompt.replace("{CITIES_LIST}", cities_list)
        prompt = prompt.replace("{PHASE}", context.workflow_phase)
        prompt = prompt.replace("{CRITERIA_JSON}", criteria_json)
        
        # Nudge transition heuristics (to help LLM if conservative)
        low_msg = message.lower()
        go_keywords = ["calcule", "résultat", "top", "vas-y", "c'est parti", "go", "ok", "d'accord", "oui", "lancer", "liste"]
        
        logger.info(f"🧠 [ORCHESTRATOR] Routing message: '{message[:50]}...'")
        
        response = self.client.models.generate_content(
            model=self.models["orchestrator"],
            contents=message,
            config=types.GenerateContentConfig(
                system_instruction=prompt,
                temperature=0.1
            )
        )
        
        # Track Tokens for Routing
        if response.usage_metadata:
            in_t = response.usage_metadata.prompt_token_count or 0
            out_t = response.usage_metadata.candidates_token_count or 0
            context.total_tokens_sent += in_t
            context.total_tokens_received += out_t
            context.tokens_g3_input += in_t
            context.tokens_g3_output += out_t

        res = response.text.strip().upper() if response.text else "INTERVIEWER"
        
        # Extract last word or the clean name
        target = res.replace(":", "").replace("**", "").split()[-1]
        
        # # Transition nudge: If we have basic data and user says "go/ok", override to SCORER if router is too cautious
        # basic_fields = ["commune_actuelle", "nb_adultes", "loc_search_area", "weight_profile"]
        # has_basic = all(context.search_criteria.get(f) for f in basic_fields)
        
        # if target == "INTERVIEWER" and has_basic and any(kw in low_msg for kw in go_keywords):
        #      logger.info("⚡ [ORCHESTRATOR] Nudging INTERVIEWER -> SCORER based on message and criteria completeness.")
        #      target = "SCORER"

        logger.info(f"🎯 [ORCHESTRATOR] Router choice: {target}")
        
        # Mapping choice back to internal names and updating phase
        target = target.upper()
        
        # Security: Force Interviewer if basic data is missing and user didn't ask for calculation
        basic_fields = ["commune_actuelle", "nb_adultes", "loc_search_area", "weight_profile"]
        missing_basic = [f for f in basic_fields if not context.search_criteria.get(f)]
        
        # Until we get at least the basic fields, we force INTERVIEWER
        if missing_basic:
            logger.warning(f"⚠️ [ORCHESTRATOR] Fields {missing_basic} are missing. Forcing INTERVIEWER.")
            context.workflow_phase = "DISCOVERY"
            return "interviewer"

        if "INTERVIEWER" in target:
            context.workflow_phase = "DISCOVERY"
            return "interviewer"
        elif "SCORER" in target:
            context.workflow_phase = "SCORING"
            return "scorer"
        elif "DECORATION" in target:
            context.workflow_phase = "DECORATION"
            return "DECORATION"
        elif "SCOUT" in target:
            context.workflow_phase = "DECORATION"
            return "scout"
        elif "JOB_HUNTER" in target:
            context.workflow_phase = "DECORATION"
            return "job_hunter"
        
        return "interviewer"

    def _get_specialized_context(self, agent_name: str, context: AgentContext) -> AgentContext:
        """Crée une vue limitée du contexte pour un agent spécifique afin d'économiser des tokens."""
        # Deep copy the context to avoid mutating the master one
        pruned_context = copy.deepcopy(context)
        
        full_criteria = context.search_criteria
        slim_criteria = {}
        
        if agent_name == "scout":
            # Scout a maintenant accès à l'intégralité du contexte pour une personnalisation maximale
            slim_criteria = full_criteria
            
        elif agent_name == "job_hunter":
            # Job Hunter a besoin des métiers et formations
            keys = ["nb_adultes", "codes_metiers", "codes_formations"]
            for k in keys:
                if k in full_criteria: slim_criteria[k] = full_criteria[k]
        
        elif agent_name == "interviewer":
            # Interviewer a besoin de tout pour savoir ce qu'il reste à remplir
            slim_criteria = full_criteria
            
        elif agent_name == "scorer":
            # Scorer a besoin de tout pour le calcul
            slim_criteria = full_criteria
            
        pruned_context.search_criteria = slim_criteria
        
        # On limite aussi l'historique dans le contexte passé (déjà fait dans les agents, mais ici c'est plus propre)
        pruned_context.history = context.history[-5:]
        
        return pruned_context

    def _synthesize(self, message: str, context: AgentContext, scout_res: str, job_res: str, web_res: str = "") -> str:
        """Fusionne les résultats de Scout, Web et Job Hunter dans un pitch final."""
        logger.info("🧠 [ORCHESTRATOR] Synthesizing Scout + Web + Job Hunter results...")
        
        # Extraction des données chiffrées (Scorer/ODIS) pour la ville focus
        city_details = {}
        for city in context.top_cities:
            if city['name'] == context.focus_city:
                city_details = city.get('details', {})
                break
        
        synth_prompt = f"""
        Tu es le Synthétiseur ODIS. Ta mission est de fusionner les retours des experts pour donner une réponse unique, fluide et ultra-convaincante au travailleur social.
        
        **DONNÉES CHIFFRÉES (SCORER ODIS)** :
        - Ville : {context.focus_city}
        - Détails ODIS : {json.dumps(city_details, indent=2, ensure_ascii=False)}
        
        **Expert Terrain (Maps)** : {scout_res}
        
        **Expert News (Web)** : {web_res}
        
        **Expert Emploi (Job Hunter)** : {job_res}
        
        **Notes Qualitatives (indices de vie)** : {context.search_criteria.get('notes_qualitatives', [])}
        
        **Instructions** :
        1. Fais une synthèse argumentée, factuelle et convaincante en FRANÇAIS.
        2. Utilise les **DONNÉES CHIFFRÉES** (scores, points forts ODIS) pour asseoir ta démonstration.
        3. S'il y a des points noirs dis-le clairement.
        4. Ne répète pas les titres. Structure la réponse par thématiques (Vie Quotidienne, Opportunités Emploi, etc).
        5. Fais le lien avec le projet de vie (Profil: {context.search_criteria.get('weight_profile', 'Standard')}) et les indices de vie.
        6. Termine par une question ouverte pour analyser une autre ville du top 5 ou approfondir l'analyse.
        """
        
        response = self.client.models.generate_content(
            model=self.models["orchestrator"],
            contents=message,
            config=types.GenerateContentConfig(system_instruction=synth_prompt, temperature=0.3)
        )
        
        # Track Tokens
        if response.usage_metadata:
            in_tokens = response.usage_metadata.prompt_token_count or 0
            out_tokens = response.usage_metadata.candidates_token_count or 0
            
            # Global totals
            context.total_tokens_sent += in_tokens
            context.total_tokens_received += out_tokens
            
            # Model-specific tracking (Orchestrator uses G3)
            context.tokens_g3_input += in_tokens
            context.tokens_g3_output += out_tokens
            
            logger.info(f"📊 [ORCHESTRATOR_SYNTH] Usage: +{in_tokens} in / +{out_tokens} out")

        return response.text.strip() if response.text else "J'ai collecté les informations, comment puis-je vous aider davantage ?"

    def process_message(self, message: str, context: AgentContext) -> str:
        """Point d'entrée principal avec gestion des cascades multi-agents."""
        
        # 1. Routing
        target_agent_name = self._route(message, context)
        logger.info(f"🎯 [ORCHESTRATOR] Routing to: {target_agent_name} | Phase: {context.workflow_phase}")
        
        # 2. Update context active agent
        context.active_agent = target_agent_name
        
        # 3. Special Case: DECORATION (Scout + Job Hunter Cascade)
        # On ne déclenche la cascade QUE si le router demande spécifiquement "DECORATION"
        if target_agent_name == "DECORATION":
            logger.info(f"⛓️ [ORCHESTRATOR] Starting Decoration Cascade (Initial City: {context.focus_city})")
            
            # --- AUTO-DETECTION SAFETY ---
            # If focus_city is empty, check if message mentions one of the top cities
            if not context.focus_city:
                for city in context.top_cities:
                    if city['name'].lower() in message.lower():
                        context.focus_city = city['name']
                        logger.info(f"✨ [ORCHESTRATOR] Auto-detected city in message: {context.focus_city}")
                        break
            
            city_name = context.focus_city or "votre ville"
            
            # Preparation des contextes spécialisés (On le fait séquentiellement car focus_city peut changer)
            scout_ctx = self._get_specialized_context("scout", context)
            
            logger.info("📡 [ORCHESTRATOR] Calling SCOUT...")
            st.toast(random.choice([
                "Interrogatoire des pigeons locaux...",
                "Déploiement de nos drones diplomatiques...",
                "Analyse des passages secrets..."
            ]), icon="🕵️")
            scout_res = self.agents["scout"].run(message, scout_ctx)
            # Sync tokens only. focus_city is updated directly by the tool in st.session_state
            self._sync_tokens(context, scout_ctx)
            
            # Refresh city name if scout updated it
            city_name = context.focus_city or city_name

            logger.info(f"✅ [ORCHESTRATOR] SCOUT finished. Current City: {context.focus_city}")

            logger.info("📡 [ORCHESTRATOR] Calling WEB...")
            st.toast(random.choice([
                "Lecture rapide de la gazette locale...",
                "On écoute les derniers potins du web...",
                "Scan des gros titres pour prendre la température..."
            ]), icon="🌐")
            web_res = self.agents["web"].run(message, context) # Web doesn't need pruning
            self._sync_tokens(context, context) # Simple sync
            
            # NOW prepare Job Hunter context, AFTER Scout may have updated focus_city
            job_ctx = self._get_specialized_context("job_hunter", context)
            
            # RE-CHECK after Scout/Web as they might have set it
            if not context.focus_city:
                 logger.warning("⚠️ [ORCHESTRATOR] Focus city still empty. Job Hunter might fail/be broad.")
            
            logger.info("📡 [ORCHESTRATOR] Calling JOB_HUNTER...")
            st.toast(random.choice([
                "Chasse aux offres d'emploi (sans fusil, promis)...",
                "Pêche miraculeuse dans les filets de France Travail...",
                "Infiltration discrète du marché du travail..."
            ]), icon="💼")
            job_res = self.agents["job_hunter"].run(message, job_ctx)
            self._sync_tokens(context, job_ctx)
            
            logger.info(f"✅ [ORCHESTRATOR] JOB_HUNTER finished. Current City: {context.focus_city}")
            
            st.toast(random.choice([
                "Mixage de la potion magique ODIS...",
                "Assemblage du puzzle de votre future vie...",
                "Rédaction de la synthèse finale..."
            ]), icon="🧪")
            final_response = self._synthesize(message, context, scout_res, job_res, web_res)
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
            # Add Humor Based on Agent
            if target_agent_name == "interviewer":
                st.toast(random.choice([
                    "Ouverture du dossier d'accompagnement...",
                    "Écoute active du récit de vie...",
                    "Analyse des besoins et des rêves...",
                    "Enregistrement de votre histoire..."
                ]), icon="📝")
            elif target_agent_name == "scorer":
                st.toast(random.choice([
                    "Grand brassage des statistiques ODIS...",
                    "Calcul des coordonnées du bonheur...",
                    "Extraction du quinté gagnant des villes...",
                    "Propulsion des algorithmes de scoring..."
                ]), icon="📊")
            elif target_agent_name == "scout":
                city_name = context.focus_city or "la ville"
                st.toast(f"🕵️ Inspection du terrain à {city_name}...", icon="🕵️")
            elif target_agent_name == "job_hunter":
                st.toast("💼 Consultation du catalogue des opportunités...", icon="💼")

            pruned_ctx = self._get_specialized_context(target_agent_name, context)
            response_text = agent.run(message, pruned_ctx)
            
            # Sync back tokens and critical updates
            self._sync_tokens(context, pruned_ctx)
            
            # CRITICAL: Only sync back criteria if the agent is the Interviewer
            # (to avoid overwriting full criteria with pruned ones from other agents)
            if target_agent_name == "interviewer":
                context.search_criteria = pruned_ctx.search_criteria
            
            # Note: focus_city sync removed. Trust tools updating st.session_state and sync tokens.
            
            context.history.append({"role": "user", "parts": [{"text": message}]})
            valid_res = response_text if response_text and response_text.strip() else "..."
            context.history.append({"role": "model", "parts": [{"text": valid_res}]})
            
            return valid_res
        except Exception as e:
            logger.error(f"❌ [ORCHESTRATOR] Process Message Error: {e}")
            return "Une erreur technique est survenue. Peux-tu reformuler ta demande ?"

    def _sync_tokens(self, master: AgentContext, sub: AgentContext):
        """Met à jour les compteurs de tokens du contexte maître à partir d'un sous-contexte."""
        # This is a bit manual but robust
        master.total_tokens_sent = sub.total_tokens_sent
        master.total_tokens_received = sub.total_tokens_received
        master.tokens_g3_input = sub.tokens_g3_input
        master.tokens_g3_output = sub.tokens_g3_output
        master.tokens_g25_input = sub.tokens_g25_input
        master.tokens_g25_output = sub.tokens_g25_output
