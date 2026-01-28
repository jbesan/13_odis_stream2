import logging
from typing import Optional
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model

logger = logging.getLogger("web_agent_v2")

WEB_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Expert Web ODIS (Agent WEB). Ta mission est de compléter l'analyse du Scout en effectuant des recherches sur le web.
**Objectif** : Fournir des informations d'actualité, de contexte social et de veille sur la ville de réinstallation.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}

**Directives** :
1. **Recherche Google Search** : Utilise ton accès natif à Google Search et le `CONTEXTE RÉSUMÉ` pour répondre aux questions sur `VILLE ACTIVE`:
    - L'actualité récente
    - Le climat social et l'accueil des réfugiés (politique locale, initiatives citoyennes).
    - Les événements culturels ou festivals en lien avec les intérêts de l'utilisateur.
    - les services de transports en commun.

2. **Réponse** :
    - Sois factuel, synthétique et surtout **contextuel**.
    - Cite tes sources si possible ou mentionne que l'info vient d'une recherche web récente.
"""

web_agent = Agent(
    get_model("web"),
    deps_type=ODISDeps
)

@web_agent.system_prompt
async def web_instructions(ctx: RunContext[ODISDeps]) -> str:
    focus = ctx.deps.state.focus_city
    city_name = focus.name if focus else "Non définie"
    city_code = focus.codgeo if focus else "Inconnu"
    
    return WEB_SYSTEM_PROMPT.format(
        BRIEFING=ctx.deps.state.briefing or "",
        FOCUS_CITY=f"{city_name} ({city_code})"
    )
