import logging
import json
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps, compute_criteria_hash
from .agent_config import get_model

logger = logging.getLogger("synthesizer_agent")

SYNTH_SYSTEM_PROMPT_ANALYSIS = """
**Rôle** : Tu es le Synthétiseur ODIS. Ta mission est de fusionner les retours du scorer et des experts pour donner une réponse au travailleur social qui accompagne la ou les pesonnes réfugiées.

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
1. Fais une synthèse argumentée pour le Travailleur Social des éléments ci-dessus qui soit factuelle, actionnable et ultra-convaincante en FRANÇAIS.
    - Utilise les **DONNÉES CHIFFRÉES** (scores, points forts ODIS) pour asseoir ta démonstration.
    - N'utilise JAMAIS les codes sans les intitulés.
2. Structure ta synthèse comme suit :
    - Description en trois phrases de la commune.
    - Synthèses par thématiques (Vie Quotidienne, Inclusion, Opportunités Emploi, etc).
    - Tableau des forces et faiblesses.
    - Contact CCAS local.
    - Question ouverte pour analyser une autre ville.
"""

SYNTH_SYSTEM_PROMPT_SPECIFIC = """
**Rôle** : Ta mission est de répondre à une question spécifique du Travailleur Social en utilisant les données des experts.

# Contexte résumé : 
{BRIEFING}

# Question posée : 
**QUESTION POSÉE** : {LAST_MESSAGE}

# Ville Analysée : 
{FOCUS_CITY}

# Expert Terrain (Scout) : 
{SCOUT_RES}

# Expert News (Web) : 
{WEB_RES}

# Expert Emploi (Job Hunter) : 
{JOB_RES}

# Instructions :
- Réponds UNIQUEMENT et de manière détaillée à la question de l'utilisateur : {LAST_MESSAGE}
- Si pertinent utilise un tableau pour structurer ta réponse.
"""

synthesizer_agent = Agent(
    get_model("synthesizer"),
    deps_type=ODISDeps,
)

@synthesizer_agent.system_prompt
async def synth_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Get city details
    city_details = "N/A"
    if ctx.deps.state.focus_city and ctx.deps.state.top_cities:
        target_city = ctx.deps.state.focus_city.name.lower().strip()
        for c in ctx.deps.state.top_cities:
            # Robust matching: normalize both names
            cand_name = str(c.get("name", "")).lower().strip()
            if cand_name == target_city:
                # Format as nice JSON for the prompt
                city_details = json.dumps(c.get("details", {}), indent=2, ensure_ascii=False)
                break
    
    # Get expert results from commune_artifacts
    h = compute_criteria_hash(ctx.deps.state.search_criteria)
    focus = ctx.deps.state.focus_city.name if ctx.deps.state.focus_city else "Unknown"
    
    # Structure: { focus: { hash: { scout: result, web: result, job_hunter: result } } }
    artifacts = ctx.deps.state.commune_artifacts.get(focus.lower().strip(), {}).get(h, {})
    
    # Dynamic mode logic
    mode = ctx.deps.state.execution_mode
    if mode == 'specific_ask':
        prompt = SYNTH_SYSTEM_PROMPT_SPECIFIC
    else:
        prompt = SYNTH_SYSTEM_PROMPT_ANALYSIS

    prompt = prompt.format(
        BRIEFING=ctx.deps.state.briefing or "",
        FOCUS_CITY=str(ctx.deps.state.focus_city.name or "Non définie"),
        CITY_DETAILS=city_details,
        SCOUT_RES=artifacts.get("scout", "Non disponible"),
        WEB_RES=artifacts.get("web", "Non disponible"),
        LAST_MESSAGE=ctx.deps.state.messages[-1].get("content", "Non disponible"),
        JOB_RES=artifacts.get("job_hunter", "Non disponible"),
    )

    logger.info(f"🎤 [SYNTHESIZER] Prompt prepared for {ctx.deps.state.focus_city.name if ctx.deps.state.focus_city else 'Unknown'}")
    logger.debug(f"SYNTH PROMPT: {prompt}")

    return prompt
