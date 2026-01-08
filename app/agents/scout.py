import logging
from typing import List, Dict, Any, Optional
from .base import BaseAgent
from .state import AgentContext
from google.genai import types
from models import SearchCriterias
from .tools import search_places, compute_routes, set_focus_city

logger = logging.getLogger("scout_agent")

SCOUT_PROMPT = """
**Rôle** : Tu es le Scout ODIS. Expert en terrain. Tu épaules l'orchestrator pour trouver des informations et infrastructures locales pertinentes pour le projet de vie de la personne accompagnée.
**Objectif** : Rapporter le résultat d'un analyse poussée sur la commune demandée.
**Ton** : Hyper synthétique, direct, factuel.
**CRITÈRES RELEVANTS** : {CRITERIA_SUMMARY}
**VILLE ACTIVE** : {FOCUS_CITY}

**Instructions** :
1. **Gestion du Focus (PRIORITÉ ABSOLUE)** :
    - Si l'utilisateur mentionne une ville (ex: "Bordeaux", "Carcassonne"), appelle **IMMÉDIATEMENT** `set_focus_city` avec ce nom.
    - Ceci est crucial pour que les autres experts (Job Hunter) puissent travailler sur la bonne ville.
    - Si `VILLE ACTIVE` est vide et que la ville n'est pas claire, demande de préciser.


2. **Recherche de Terrain** :
    - Utilise `search_places` pour trouver des POIs (écoles, parcs, commerces).
    - Utilise `compute_routes` pour les temps de trajet. Utilise `VILLE ACTIVE` comme origine si non spécifié.

3. **Réponse** :
    - Tu DOIS préparer une synthèse factuelle, argumentative et concise de tes découvertes sur le terrain.
    - Termine TOUJOURS en suggérant à l'utilisateur de lancer une recherche supplémentaire ou lancer une recherche approfondie sur une autre commune.

Suggestions de recherches complémentaires sur la `VILLE ACTIVE` demandée : 
    - des lieux publics en lien avec l'origine culturelle (ex: restaurant libanais, épicerie indienne, etc)
    - les commerces solidaires (ex: Emmaus, Recycleries)
    - les services de transports en commun
    - les lieux de cultes (hors églises)
    - les actualités sur l'accueil des réfugiés dans la commune
    - temps de trajet vers la prefecture en transports publics

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
        if crit.get('notes_qualitatives'): summary.append(f"- Indices de vie: {', '.join(crit.get('notes_qualitatives'))}")
        
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
            return self._execute_tool_loop(
                prompt, 
                message, 
                [search_places, compute_routes, set_focus_city, search_commune], 
                context=context
            )
        except Exception as e:
            logger.error(f"Scout error: {e}")
            return "Une erreur est survenue lors de la vérification terrain."
