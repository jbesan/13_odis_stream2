import logging
from typing import List, Dict, Any, Optional
from .base import BaseAgent
from .state import AgentContext
from google.genai import types
from models import SearchCriterias
from .tools import search_places, compute_routes, set_focus_city

logger = logging.getLogger("scout_agent")

SCOUT_PROMPT = """
**Rôle** : Tu es le Scout ODIS. Expert en terrain.
**CRITÈRES RELEVANTS** : {CRITERIA_SUMMARY}
**VILLE ACTIVE (FOCUS)** : {FOCUS_CITY}

**Instructions** :
1. **Gestion du Focus (PRIORITÉ ABSOLUE)** :
    - La ville active est **{FOCUS_CITY}**. Utilise-la pour tes recherches.
    - Si l'utilisateur mentionne une **NOUVELLE** ville, appelle `set_focus_city`.

2. **Recherche de Terrain (CIBLÉE & PROACTIVE)** :
    - Utilise `search_places` avec **maximum 3 mots-clés par appel** pour rester rapide. 
    - Priorités d'exploration : 
        - **Incontournables** : Services publics, gares, commerces de proximité.
        - **Profil Utilisateur** : Si l'utilisateur a des besoins spécifiques (ex: enfants, santé, culture), cherche des lieux en lien (écoles, hôpitaux, restaurants culturels). 
        - **Solidarité** : Commerces solidaires, recycleries.
    - Utilise `compute_routes` pour valider l'accès aux points de repère (ex: Beaucaire vers la Préfecture).

3. **Réponse** :
    - Fais une synthèse "humaine" de tes découvertes.
    - Ne liste pas juste des adresses, explique pourquoi ces lieux sont intéressants pour le projet de vie.
    - Suggère toujours une action suivante (ex: "Voulez-vous que je vérifie les temps de trajet vers Nîmes ?").
"""

class ScoutAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        # Prepare Prompt-based Memory
        history_summary = ""
        if context.history:
            # Limit history to 3 turns to save tokens while keeping context
            for turn in context.history[-3:]:
                role = "Utilisateur" if turn.get("role") == "user" else "Assistant"
                parts = turn.get("parts", [])
                text_parts = [p.get("text") for p in parts if isinstance(p, dict) and p.get("text")]
                text = " ".join(text_parts) if text_parts else ""
                history_summary += f"- {role}: {text}\n"

        # Prepare Specialized Criteria Summary
        crit = context.search_criteria
        summary = []
        if crit.get('nb_enfants'): summary.append(f"- Famille: {crit.get('nb_adultes')} adultes, {crit.get('nb_enfants', 0)} enfants ({crit.get('classe_enfants', [])})")
        if crit.get('inc_services_add_selection'): summary.append(f"- Besoins inclusion: {crit.get('inc_services_add_selection')}")
        if crit.get('inc_asso_add_selection'): summary.append(f"- Intérêts/Assos: {crit.get('inc_asso_add_selection')}")
        if crit.get('sante') and crit.get('sante') != "Aucun": summary.append(f"- Santé: {crit.get('sante')}")
        
        criteria_summary = "\n".join(summary) if summary else "Aucun critère spécifique."

        prompt = SCOUT_PROMPT.replace("{CRITERIA_SUMMARY}", criteria_summary)
        
        # Inject Focus City
        focus_city = context.focus_city or "Non définie"
        prompt = prompt.replace("{FOCUS_CITY}", focus_city)

        if "{HISTORY_SUMMARY}" in prompt:
             prompt = prompt.replace("{HISTORY_SUMMARY}", history_summary)
        else:
             prompt += f"\n\n**Historique Recent** :\n{history_summary}"

        try:
            # Added search_commune to help Scout resolve cities if needed
            from .tools import search_commune
            return self._execute_tool_loop(prompt, message, [search_places, compute_routes, set_focus_city, search_commune], context=context)
        except Exception as e:
            logger.error(f"Scout error: {e}")
            return "Une erreur est survenue lors de la vérification terrain."
