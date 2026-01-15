import logging
from typing import List, Dict, Any, Optional
from .base import BaseAgent
from .state import AgentContext
from google.genai import types
from core.models import SearchCriterias
from .tools import search_places, compute_routes, set_focus_city, search_refugee_associations, search_odis_associations

logger = logging.getLogger("scout_agent")

SCOUT_PROMPT = """
**Rôle** : Tu es le Scout ODIS. Expert en terrain. Tu épaules l'orchestrator pour trouver des informations et infrastructures locales pertinentes pour le projet de vie de la personne accompagnée.
**Objectif** : Rapporter le résultat d'un analyse poussée sur la commune demandée.
**Ton** : Hyper synthétique, direct, factuel.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}

**Instructions** :
1. **Gestion du Focus (PRIORITÉ ABSOLUE)** :
    - Si l'utilisateur mentionne une ville (ex: "Bordeaux", "Carcassonne"), appelle **IMMÉDIATEMENT** `set_focus_city` avec ce nom.
    - Ceci est crucial pour que les autres experts (Job Hunter) puissent travailler sur la bonne ville.
    - Si `VILLE ACTIVE` est vide et que la ville n'est pas claire, demande de préciser.

2. **Recherche de Terrain** : Dans cet ordre de priorité
    - **Utilisation du Code INSEE (codgeo)** : Récupère le Code INSEE de la ville de `VILLE ACTIVE` avec l'outil `search_commune`.
    - **Utilise systématiquement** `search_refugee_associations(codgeo=...)` pour identifier les structures spécialisées. C'est CRUCIAL pour l'argumentaire inclusion.
    - **Utilise systématiquement** `search_odis_associations(codgeo=...)` pour enrichir la vision de la vie locale pertinente (Clubs, Culture, Sport, Social).
    - Utilise `search_places` pour trouver des POIs (écoles, parcs, commerces, lieux de culte) **dans un rayon de 50km de `VILLE ACTIVE`**. Exemples de recherches:
        - des lieux publics en lien avec l'origine culturelle (ex: restaurant libanais, épicerie indienne, etc)
        - les commerces solidaires (ex: Emmaus, Recycleries)
        - les lieux de cultes (hors églises) si culturelement pertinent
    - Utilise `compute_routes` pour calculer les temps de trajet. Utilise `VILLE ACTIVE` comme origine si non spécifié. Exemples de trajets à rechercher:
        - temps de trajet depuis `VILLE ACTIVE` vers la prefecture en transports publics
        - temps de trajet depuis `VILLE ACTIVE` vers la commune actuelle en train si pertinent

3. **Réponse** :
    - Tu DOIS préparer une synthèse factuelle, argumentative et concise de tes découvertes sur le terrain et ne garder que ce qui est pertinent au regard du `CONTEXTE RÉSUMÉ`.
    - Termine TOUJOURS en suggérant à l'utilisateur de lancer une recherche supplémentaire ou lancer une recherche approfondie sur une autre commune.

"""

class ScoutAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        briefing_data, user_msg = self._get_briefing_and_user_msg(message)

        prompt = SCOUT_PROMPT.replace("{BRIEFING}", briefing_data)
        prompt = prompt.replace("{FOCUS_CITY}", str(context.focus_city or "Non définie"))
        # print(f"SCOUT_PROMPT: {prompt}")
        
        try:
            # Added search_commune to help Scout resolve cities if needed
            from .tools import search_commune
            return self._execute_tool_loop(
                prompt, 
                user_msg, 
                [search_places, compute_routes, set_focus_city, search_commune, search_refugee_associations, search_odis_associations], 
                context=context
            )
        except Exception as e:
            logger.error(f"Scout error: {e}")
            return "Une erreur est survenue lors de la vérification terrain."
