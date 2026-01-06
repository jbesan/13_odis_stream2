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
**Critères** : {CRITERIA}
**VILLE ACTIVE (FOCUS)** : {FOCUS_CITY}

**Instructions** :
1. **Gestion du Focus** :
    - Si l'utilisateur mentionne une NOUVELLE ville spécifique, appelle TOUT DE SUITE `set_focus_city`.
    - Si l'utilisateur pose une question de contexte (ex: "temps de trajet", "écoles ici") SANS préciser la ville, utilise **VILLE ACTIVE**.
    - Si VILLE ACTIVE est vide et que la ville n'est pas claire, demande de préciser.

2. **Recherche de Terrain** :
    - Utilise `search_places` pour trouver des POIs (écoles, parcs, commerces).
    - Utilise `compute_routes` pour les temps de trajet. Utilise VILLE ACTIVE comme origine si non spécifié.

3. **Réponse** :
    - Tu DOIS toujours répondre avec du texte explicatif.
    - Termine TOUJOURS en suggérant à l'utilisateur de lancer une recherche supplémentaire ou lancer une recherche approfondie sur une autre commune.
"""

class ScoutAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        # Prepare Prompt-based Memory
        history_summary = ""
        if context.history:
            for turn in context.history[-10:]:
                role = "Utilisateur" if turn.get("role") == "user" else "Assistant"
                parts = turn.get("parts", [])
                text_parts = [p.get("text") for p in parts if isinstance(p, dict) and p.get("text")]
                text = " ".join(text_parts) if text_parts else ""
                history_summary += f"- {role}: {text}\n"

        prompt = SCOUT_PROMPT.replace("{CRITERIA}", str(context.search_criteria))
        
        # Inject Focus City
        focus_city = context.focus_city or "Non définie"
        prompt = prompt.replace("{FOCUS_CITY}", focus_city)

        if "{HISTORY_SUMMARY}" in prompt:
             prompt = prompt.replace("{HISTORY_SUMMARY}", history_summary)
        else:
             prompt += f"\n\n**Historique Recent** :\n{history_summary}"

        try:
            # Added set_focus_city to the tools list
            return self._execute_tool_loop(prompt, message, [search_places, compute_routes, set_focus_city])
        except Exception as e:
            logger.error(f"Scout error: {e}")
            return "Une erreur est survenue lors de la vérification terrain."
