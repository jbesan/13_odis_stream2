import logging
from typing import Optional
from pydantic_ai import Agent, RunContext, WebSearchTool
from pydantic import BaseModel, Field
from .state import ODISGraphState, ODISDeps, compute_criteria_hash, ODISContextBuilder
from .agent_config import get_model

logger = logging.getLogger("web_agent_v2")

class WebResult(BaseModel):
    searched: str = Field(..., description="Résumé des mots-clés et requêtes recherchés sur le web.")
    result: str = Field(..., description="Synthèse des actualités et du contexte local.")

WEB_ANALYSIS_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Expert Web ODIS (Agent WEB). Ta mission est de compléter l'analyse du Scout en effectuant des recherches sur le web.
**Objectif** : Fournir des informations d'actualité, de contexte social et de veille sur la ville de réinstallation.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

**Directives** :
1. **Recherche Google Search** : Utilise ton accès natif à Google Search et le dossier JSON pour la `Ville analysée`. 
    - Fais particulièrement attention aux `Notes qualitatives` pour identifier des pistes de recherches.
    - Recherche TOUJOURS des informations locales liées à l'origine ethno-culturelle des personnes accompagnées (ex. 'Syrien' ou 'Moyen Orient').
    - Recherche TOUJOURS l'actualité pour identifier le climat social et l'accueil des réfugiés (politique locale, initiatives citoyennes).
    - Les événements culturels ou festivals en lien avec les intérêts de l'utilisateur (si exprimés).
    - les services de transports en commun et leur possible gratuité.

2. **Hors Scope** : Ne recherche PAS les emplois, associations et formations d'autres agents s'en occupent

3. **Réponse (STRUCTURED)** :
    - Tu DOIS retourner un objet `WebResult`.
    - `searched` : Une liste concise des mots-clés recherchés (même sans résultat).
    - `result` : Ton analyse factuelle et hyper-concise (10 bullet-points max). N'invente RIEN. Vise 200 mots et ne garde que ce qui est pertinent au regard du dossier.
"""

WEB_SPECIFIC_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Expert Web ODIS (Agent WEB). Ta mission est de compléter une analyse existante en effectuant des recherches sur le web.
**Objectif** : Fournir des informations d'actualité, de contexte social et de veille sur la ville de réinstallation.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

**Directives** :
1. Si la `Dernière question` peut être répondue avec les `Connaissances actuelles (Web)` ne fais rien.
2. Si des données manquent, utilise ton accès natif à Google Search et le contexte JSON pour répondre à `Dernière question`.
3. **Réponse (STRUCTURED)** :
    - Tu DOIS retourner un objet `WebResult`.
    - `searched` : Les termes de recherche utilisés pour répondre à la question.
    - `result` : La réponse détaillée à la `Dernière question`.
"""

web_agent = Agent(
    get_model("web"),
    deps_type=ODISDeps,
    builtin_tools=[WebSearchTool()],
    output_type=WebResult
)

@web_agent.system_prompt
async def web_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds Web agent prompt using ODISContextBuilder."""
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "web")
    mode = ctx.deps.state.execution_mode
    prompt_template = WEB_SPECIFIC_SYSTEM_PROMPT if mode == "specific_ask" else WEB_ANALYSIS_SYSTEM_PROMPT
    
    prompt = prompt_template.format(DATA_CONTEXT=data_context)
    return prompt
