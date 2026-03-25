import logging
from typing import Optional
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic import BaseModel, Field
from .state import ODISGraphState, ODISDeps, compute_criteria_hash
from .agent_config import get_model

logger = logging.getLogger("web_agent_v2")

class WebResult(BaseModel):
    searched: str = Field(..., description="Résumé des mots-clés et requêtes recherchés sur le web.")
    result: str = Field(..., description="Synthèse des actualités et du contexte local.")

WEB_ANALYSIS_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Expert Web ODIS (Agent WEB). Ta mission est de compléter l'analyse du Scout en effectuant des recherches sur le web.
**Objectif** : Fournir des informations d'actualité, de contexte social et de veille sur la ville de réinstallation.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}

**Directives** :
1. **Recherche Google Search** : Utilise ton accès natif à Google Search et le `CONTEXTE RÉSUMÉ` pour {FOCUS_CITY}:
    - Recherche TOUJOURS des informations locales liées à l'origine ethno-culturelle des personnes accompagnées (ex. 'Syrien' ou 'Moyen Orient').
    - Recherche TOUJOURS l'actualité pour identifier le climat social et l'accueil des réfugiés (politique locale, initiatives citoyennes).
    - Les événements culturels ou festivals en lien avec les intérêts de l'utilisateur (si exprimés).
    - les services de transports en commun et leur possible gratuité.

2. ** Hors Scope** : Ne recherche PAS les emplois, assocations et formations d'autres agents s'en occupent

4. **Réponse (STRUCTURED)** :
    - Tu DOIS retourner un objet `WebResult`.
    - `searched` : Une liste concise des mots-clés recherchés (même sans résultat).
    - `result` : Ton analyse factuelle et hyper-concise (10 bullet-points max). N'invente RIEN. Vise 200 mots et ne garde que ce qui est pertinent au regard du `CONTEXTE RÉSUMÉ`.
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

4. **Réponse (STRUCTURED)** :
    - Tu DOIS retourner un objet `WebResult`.
    - `searched` : Les termes de recherche utilisés pour répondre à la question.
    - `result` : La réponse détaillée à la `QUESTION POSÉE`.
"""

web_agent = Agent(
    get_model("web"),
    deps_type=ODISDeps,
    builtin_tools=[WebSearchTool()],
    output_type=WebResult
)

@web_agent.system_prompt
async def web_instructions(ctx: RunContext[ODISDeps]) -> str:
    focus = ctx.deps.state.focus_city
    city_name = focus.name if focus else "Non définie"
    city_code = focus.codgeo if focus else "Inconnu"
    last_message = ctx.deps.state.messages[-1].get("content", "Non disponible") if ctx.deps.state.messages else "Non disponible"
    h = compute_criteria_hash(ctx.deps.state.search_criteria)
    
    # Get artifacts from the new search_results structure
    artifacts = {}
    if ctx.deps.state.search_results:
        city_res = ctx.deps.state.search_results.get_by_code(city_code)
        if city_res:
             artifacts = city_res.expert_analysis
    

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
