
import logging
import json
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model

logger = logging.getLogger("synthesizer_agent")

SYNTH_SYSTEM_PROMPT = """
**Rôle** : Tu es le Synthétiseur ODIS. Ta mission est de fusionner les retours des experts pour donner une réponse factuelle, actionnable et ultra-convaincante au travailleur social qui accompagne la ou les pesonnes réfugiées.

# Contexte résumé : 
{BRIEFING}

# Ville Analysée : 
{FOCUS_CITY}

# Données chiffrées TOP 5 (Scorer ODIS) :
```json
{CITY_DETAILS}
```

# Expert Terrain (Scout) : 
{SCOUT_RES}

# Expert News (Web) : 
{WEB_RES}

# Expert Emploi (Job Hunter) : 
{JOB_RES}

# Instructions :
1. Fais une synthèse argumentée des éléments ci-dessus et du projet de vie qui soit factuelle et convaincante en FRANÇAIS.
2. Utilise les **DONNÉES CHIFFRÉES** (scores, points forts ODIS) pour asseoir ta démonstration, lorsque pertinent présente les données sous forme de pourcentages.
3. N'utilise JAMAIS les codes seuls mais mentionne les en plus du libellé normalisé.
4. Commence par une description en trois phrases de la commune et structure la réponse par thématiques (Vie Quotidienne, Inclusion, Opportunités Emploi, etc).
5. Avant de terminer, construit un tableau des forces et faiblesses.
6. Termine par une question ouverte pour analyser une autre ville listée dans `DONNÉES CHIFFRÉES TOP 5` ou approfondir l'analyse de la ville en cours.
"""

synthesizer_agent = Agent(
    get_model("synthesizer"),
    deps_type=ODISDeps
)

@synthesizer_agent.system_prompt
async def synth_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Get city details
    city_details = "N/A"
    if ctx.deps.state.focus_city and ctx.deps.state.top_cities:
        target_city = str(ctx.deps.state.focus_city).lower().strip()
        for c in ctx.deps.state.top_cities:
            # Robust matching: normalize both names
            cand_name = str(c.get("name", "")).lower().strip()
            if cand_name == target_city:
                # Format as nice JSON for the prompt
                city_details = json.dumps(c.get("details", {}), indent=2, ensure_ascii=False)
                break
    
    prompt = SYNTH_SYSTEM_PROMPT.format(
        BRIEFING=ctx.deps.state.briefing or "",
        FOCUS_CITY=str(ctx.deps.state.focus_city or "Non définie"),
        CITY_DETAILS=city_details,
        SCOUT_RES=ctx.deps.state.experts_results.get("scout", "Non disponible"),
        WEB_RES=ctx.deps.state.experts_results.get("web", "Non disponible"),
        JOB_RES=ctx.deps.state.experts_results.get("job_hunter", "Non disponible")
    )

    logger.debug(f"SYNTH PROMPT: {prompt}")

    return prompt
