import logging
import json
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISGraphState, ODISDeps, compute_criteria_hash
from .agent_config import get_model

logger = logging.getLogger("synthesizer_agent")

class SynthesizerResult(BaseModel):
    response: str = Field(..., description="La synthèse finale argumentée pour le travailleur social.")

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

# Données chiffrées COMMUNE ACTUELLE :
{CURRENT_CITY_DETAILS}

# Expert Terrain (Scout) : 
{SCOUT_RES}

# Expert News (Web) : 
{WEB_RES}

# Expert Emploi (Job Hunter) : 
{JOB_RES}

# Instructions :
1. Fais une synthèse structurée, argumentée et détaillée pour le Travailleur Social qui soit factuelle, actionnable et ultra-convaincante en FRANÇAIS.
    - Utilise les **DONNÉES CHIFFRÉES** (scores, points forts ODIS) pour asseoir ta démonstration. Utilise une notation en pourcentage dès que c'est pertient (80% plutot que 0.8)
    - N'utilise JAMAIS les codes normatifs sans les intitulés.
    - N'invente rien, utilise uniqument les éléments des Experts. Mets en gras les élements importants.
    - Sois le plus factuel possible et cite toujours le nom des entités identifiées (associations, entreprises, lieux etc.)
    - Longueur synthèse: minimum 750 mots, idéal 1000 mots. Utilise des listes à puces ou tableaux dès que pertinents.
2. Structure ta réponse au format Markdown avec les sections suivantes :
    - ## 🏙️ Aperçu de {FOCUS_CITY} : 3 à 5 phrases de description.
    - ## ⚖️ Analyse comparative entre {FOCUS_CITY} et {CURRENT_CITY_NAME} :
        - Identifie les **3 à 5 points chiffrés les plus déterminants** en faveur de {FOCUS_CITY} (ex: loyer plus bas, meilleures écoles, plus d'opportunités d'emploi spécifiques).
    - ## 🧭 Synthèse thématique :
        - **Vie Quotidienne** : Synthèse (Logement, mobilité sur place, éducation, santé, affinités culturelles, sports, loisirs etc.)
        - **Inclusion** : Synthèse (associations, solidarité, insertion)
        - **Opportunités Emploi** : Synthèse (marché du travail, secteurs porteurs, emploi)
        - ** Actualités **: Synthèse des actualités pertinentes des dernières années.
    - ## ✅ Forces & ⚠️ Vigilances : Tableau Markdown.
    - ## 💡 Contact du CCAS de {FOCUS_CITY}
    - ## ❓ Et ensuite ? : Propose d'analyser une autre ville du Top 5 ou d'approfondir un point sur la ville en court.
"""

SYNTH_SYSTEM_PROMPT_SPECIFIC = """
**Rôle** : Ta mission est de répondre UNIQUEMENT à une question spécifique du Travailleur Social en utilisant les données des experts.

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
- Sois factuel et précis et mentionne les codes & identifiants des éléments trouvés. Si pertinent, utilise un tableau Markdown.
- N'invente rien, utilise uniqument les éléments des Experts. Mets en gras les élements importants. Si les données des experts sont insuffisantes, mentionne-le clairement.
"""

synthesizer_agent = Agent(
    get_model("synthesizer"),
    deps_type=ODISDeps,
    output_type=SynthesizerResult
)

@synthesizer_agent.system_prompt
async def synth_instructions(ctx: RunContext[ODISDeps]) -> str:
    # Get city details for focused city
    print("Hello")
    city_details = "N/A"
    scorer_pitch = "N/A"
    if ctx.deps.state.focus_city and ctx.deps.state.search_results:
        sr = ctx.deps.state.search_results
        target_res = sr.get_by_code(ctx.deps.state.focus_city.codgeo)
        # Fallback by name if code is missing (e.g. from router)
        if not target_res and ctx.deps.state.focus_city.name:
            norm_name = ctx.deps.state.focus_city.name.lower().strip()
            target_res = next((r for r in sr.results if r.name.lower().strip() == norm_name), None)
            
        if target_res:
            # We must use model_dump() to ensure we have a dict for the prompt
            if isinstance(target_res, dict):
                logger.debug(f"target_res is a dict, keys: {list(target_res.keys())}")
                target_dict = target_res
            else:
                target_dict = target_res.model_dump(exclude={'geometry', 'centroid', 'expert_analysis'})
            
            city_details = json.dumps(target_dict, indent=2, ensure_ascii=False)
            scorer_pitch = target_res.scorer_pitch or "Non disponible"
    
    # Get details for current city (comparison)
    current_city_details = "N/A"
    current_city_name = "la commune actuelle"
    if ctx.deps.state.search_results:
        sr = ctx.deps.state.search_results
        if sr.current_geo:
            if isinstance(sr.current_geo, dict):
                cur_dict = sr.current_geo
            else:
                cur_dict = sr.current_geo.model_dump(exclude={'geometry', 'centroid', 'expert_analysis'})
                
            current_city_details = json.dumps(cur_dict, indent=2, ensure_ascii=False)
            current_city_name = sr.current_geo.name
    
    # Get expert results from search_results
    focus_codgeo = ctx.deps.state.focus_city.codgeo if ctx.deps.state.focus_city else ""
    focus_res = ctx.deps.state.search_results.get_by_code(focus_codgeo) if ctx.deps.state.search_results else None
    
    # Fallback by name
    if not focus_res and ctx.deps.state.focus_city and ctx.deps.state.search_results:
        norm_name = ctx.deps.state.focus_city.name.lower().strip()
        focus_res = next((r for r in ctx.deps.state.search_results.results if r.name.lower().strip() == norm_name), None)

    artifacts = focus_res.expert_analysis if focus_res else {}
    
    # Dynamic mode logic
    mode = ctx.deps.state.execution_mode
    if mode == 'specific_ask':
        prompt = SYNTH_SYSTEM_PROMPT_SPECIFIC
    else:
        prompt = SYNTH_SYSTEM_PROMPT_ANALYSIS

    prompt = prompt.format(
        BRIEFING=ctx.deps.state.odis_brief or "",
        FOCUS_CITY=str(ctx.deps.state.focus_city.name or "Non définie"),
        CITY_DETAILS=city_details,
        CURRENT_CITY_NAME=current_city_name,
        CURRENT_CITY_DETAILS=current_city_details,
        SCOUT_RES=artifacts.get("scout", "Non disponible"),
        WEB_RES=artifacts.get("web", "Non disponible"),
        LAST_MESSAGE=ctx.deps.state.messages[-1].get("content", "Non disponible"),
        JOB_RES=artifacts.get("job_hunter", "Non disponible"),
    )

    logger.debug(f"Prompt prepared for {ctx.deps.state.focus_city.name if ctx.deps.state.focus_city else 'Unknown'}")
    logger.debug(f"SYNTH PROMPT: {prompt}")

    return prompt
