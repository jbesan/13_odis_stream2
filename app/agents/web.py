import logging
from typing import Optional
from .base import BaseAgent
from .state import AgentContext

logger = logging.getLogger("web_agent")

WEB_PROMPT = """
**Rôle** : Tu es l'Expert Web ODIS (Agent WEB). Ta mission est de compléter l'analyse du Scout en effectuant des recherches sur le web.
**Objectif** : Fournir des informations d'actualité, de contexte social et de veille sur la ville de réinstallation.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}

**Directives** :
1. **Recherche Google Search** : Utilise ton accès natif à Google Search et le `CONTEXTE RÉSUMÉ` pour répondre aux questions sur `VILLE ACTIVE`:
    - L'actualité récente
    - Le climat social et l'accueil des réfugiés (politique locale, initiatives citoyennes).
    - Les événements culturels ou festivals en lien avec les intérêts de l'utilisateur.
    - Des services très spécifiques non trouvés par Maps.

2. **Réponse** :
    - Sois factuel, synthétique et surtout **contextuel**.
    - Cite tes sources si possible ou mentionne que l'info vient d'une recherche web récente.
    - Ton retour sera fusionné avec celui de l'expert terrain (Google Maps).
"""

class WebAgent(BaseAgent):
    def run(self, message: str, context: AgentContext) -> str:
        briefing_data, user_msg = self._get_briefing_and_user_msg(message)
        prompt = WEB_PROMPT.replace("{FOCUS_CITY}", context.focus_city)
        prompt = prompt.replace("{BRIEFING}", briefing_data)
        # print(f"WEB_PROMPT: {prompt}")
        
        try:
            # L'agent WEB n'a AUCUNE fonction personnalisée, seulement Google Search
            # Cela évite le bug 'Tool use with function calling is unsupported'
            return self._execute_tool_loop(prompt, message, tools=[], context=context, include_google_search=True)
        except Exception as e:
            logger.error(f"WebAgent error: {e}")
            return "Je n'ai pas pu effectuer de recherche web pour le moment."
