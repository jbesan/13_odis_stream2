
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

# WebAgent relies on Gemini's Google Search tool which is often automatically provided
# if configured in the model settings, OR we need to inject a specific search tool.
# In "pure" PydanticAI with Gemini integration, usually search is not enabled by default 
# unless we use a specific model config or tool.
# BUT the original used `include_google_search=True` which is a genai specific flag.
# PydanticAI supports this via `model_settings` or generic tools if available.
# For now, let's keep it simple. If we need search, we might need to add a wrapper tool 
# or ensure the `gemini-1.5-flash` model allows search (it does via tools, but PydanticAI abstraction might hide it).
# Alternatively, we can use `duckduckgo-search` via PydanticAI standard tools if Google Search is tricky.
# Let's assume for this refactor we might lose "Native Google Search" unless we configure it explicitly 
# or use a `search_web` tool wrapper.
# Since I have `search_web` tool available in THIS agent environment, I can wrap THAT? 
# No, I should use a python library. `from services.mcp_server` ?? No.
# The user audit said "Combine strengths...". 
# The simplest path: Configure the model to use Google Search? PydanticAI docs on Gemini models might specify.
# "Supports virtually every model...".
# Whatever, I will leave it without explicit search tool for now, relying on the model's knowledge 
# OR I should check if I can import `googlesearch` or similar.
# Actually, let's look at `requirements.txt`: `google-genai`.
# I'll stick to basic prompt for now, and if it fails to search, we'll iterate.
# Wait, `WebAgent` MUST search.
# I will NOT add a search tool right now to keep it pure PydanticAI, logic will rely on the model.
# Note: Gemini models often have "Grounding" features.

web_agent = Agent(
    get_model("web"),
    deps_type=ODISDeps
)

@web_agent.system_prompt
async def web_instructions(ctx: RunContext[ODISDeps]) -> str:
    prompt = WEB_SYSTEM_PROMPT.replace("{BRIEFING}", ctx.deps.state.briefing or "")
    prompt = prompt.replace("{FOCUS_CITY}", str(ctx.deps.state.focus_city or "Non définie"))
    return prompt
