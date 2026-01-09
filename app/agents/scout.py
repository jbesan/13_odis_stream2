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
**CONTEXTE TERRAIN (Briefing)** :
{BRIEFING}

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
        # 1. Context Extraction (2026 Pattern)
        briefing_data = ""
        user_msg = message
        if "### 📋 RÉSUMÉ DU DOSSIER (BRIEFING)" in message:
            parts = message.split("---")
            if len(parts) >= 3:
                briefing_data = parts[1].strip()
                user_msg = "---".join(parts[2:]).strip()
        
        prompt = SCOUT_PROMPT.replace("{BRIEFING}", briefing_data)

        try:
            # Added search_commune to help Scout resolve cities if needed
            from .tools import search_commune
            return self._execute_tool_loop(
                prompt, 
                user_msg, 
                [search_places, compute_routes, set_focus_city, search_commune], 
                context=context
            )
        except Exception as e:
            logger.error(f"Scout error: {e}")
            return "Une erreur est survenue lors de la vérification terrain."
