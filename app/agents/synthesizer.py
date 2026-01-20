
import logging
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model

logger = logging.getLogger("synthesizer_agent")

SYNTH_SYSTEM_PROMPT = """
**Rôle** : Tu es le Synthétiseur ODIS. Ta mission est de fusionner les retours des experts pour donner une réponse unique, fluide et ultra-convaincante au travailleur social.

# CONTEXTE RÉSUMÉ : 
{BRIEFING}

Ville Analysée : {FOCUS_CITY}

# DONNÉES CHIFFRÉES (SCORER ODIS) :
{CITY_DETAILS}

# Expert Terrain (Scout) : 
{SCOUT_RES}

# Expert News (Web) : 
{WEB_RES}

# Expert Emploi (Job Hunter) : 
{JOB_RES}

# Instructions :
1. Fais une synthèse argumentée des éléments ci-dessus qui soit factuelle et convaincante en FRANÇAIS.
2. Utilise les **DONNÉES CHIFFRÉES** (scores, points forts ODIS) pour asseoir ta démonstration.
3. S'il y a des points noirs dis-le clairement.
4. Structurer la réponse par thématiques (Vie Quotidienne, Opportunités Emploi, etc).
5. Fais le lien avec le projet de vie.
6. Termine par une question ouverte pour analyser une autre ville du top 5 ou approfondir l'analyse.
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
        for c in ctx.deps.state.top_cities:
            if c.get("name") == ctx.deps.state.focus_city:
                city_details = str(c.get("details", {}))
                break
    
    # Extract experts results from state
    # Logic in graph.py injects them into message, but here we can also read from stats if needed.
    # However, the user prompt in graph.py overrides this structure partially or appends to it?
    # Actually, graph.py calls `synthesizer_agent.run(input_msg, ...)`
    # The `input_msg` already contains the [SCOUT], [WEB] blocks constructed in `graph.py`.
    # BUT, the Agent System Prompt is prepended/mixed.
    # If the Input Msg has the content, do we need it in System Prompt?
    # Redundancy is fine. Or we can cleaner:
    # Let the Graph construct the prompt payload in User Message, and System Prompt only contains Role/Instructions.
    # 
    # Current SYNTH_SYSTEM_PROMPT has placeholders.
    # If graph.py passes the text in `input_msg`, the agent treats it as User Message.
    # We should populate the System Prompt with available info from State too.
    
    scout_res = ctx.deps.state.experts_results.get("scout", "Non disponible")
    web_res = ctx.deps.state.experts_results.get("web", "Non disponible")
    job_res = ctx.deps.state.experts_results.get("job_hunter", "Non disponible")
    
    prompt = SYNTH_SYSTEM_PROMPT.replace("{BRIEFING}", ctx.deps.state.briefing or "")
    prompt = prompt.replace("{FOCUS_CITY}", str(ctx.deps.state.focus_city or "Non définie"))
    prompt = prompt.replace("{CITY_DETAILS}", city_details)
    
    prompt = prompt.replace("{SCOUT_RES}", scout_res)
    prompt = prompt.replace("{WEB_RES}", web_res)
    prompt = prompt.replace("{JOB_RES}", job_res)
    
    return prompt
