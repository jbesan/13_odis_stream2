import logging
from typing import Optional
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps, compute_criteria_hash
from .agent_config import get_model

logger = logging.getLogger("web_agent_v2")

WEB_ANALYSIS_SYSTEM_PROMPT = """
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
    - Commence toujours ta réponse par rapperler en une phrase ce que tu as recherché.
    - Cite tes sources si possible ou mentionne que l'info vient d'une recherche web récente.
"""

WEB_SPECIFIC_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Expert Web ODIS (Agent WEB). Ta mission est de compléter une analyse existante en effectuant des recherches sur le web.
**Objectif** : Fournir des informations d'actualité, de contexte social et de veille sur la ville de réinstallation.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}
**QUESTION POSÉE** : {LAST_MESSAGE}
**CONNAISSANCES ACTUELLES** : {COMMUNE_ARTIFACT}

**Directives** :
1. Si la `QUESTION POSÉE` peut-être répondue avec les `CONNAISSANCES ACTUELLES` ne fais rien.
2. Si des données manquent, utilise ton accès natif à Google Search et le `CONTEXTE RÉSUMÉ` pour répondre `QUESTION POSÉE` sur `VILLE ACTIVE`
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
    last_message = ctx.deps.state.messages[-1].get("content", "Non disponible") if ctx.deps.state.messages else "Non disponible"
    h = compute_criteria_hash(ctx.deps.state.search_criteria)
    artifacts = ctx.deps.state.commune_artifacts.get(city_name.lower().strip(), {}).get(h, {})
    

    # We select prompt according to mode: generic commune analysis or a specific question
    mode = ctx.deps.state.execution_mode
    if mode == 'specific_ask':
        prompt = WEB_SPECIFIC_SYSTEM_PROMPT
    else:
        prompt = WEB_ANALYSIS_SYSTEM_PROMPT

    return prompt.format(
        BRIEFING=ctx.deps.state.briefing or "",
        FOCUS_CITY=f"{city_name} ({city_code})",
        LAST_MESSAGE = last_message,
        COMMUNE_ARTIFACT=artifacts.get("web", "Non disponible")
    )
