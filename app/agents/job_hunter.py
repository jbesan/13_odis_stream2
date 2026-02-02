import logging
import re
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISGraphState, ODISDeps, compute_criteria_hash
from .agent_config import get_model
from .tools import (
    search_job_offers_batch,
    get_job_details, 
    search_referentiels_batch
)

logger = logging.getLogger("job_hunter_agent_v2")

class SearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(..., description="Domaine de recherche possibles:['rome_codes', 'communes'].")

class JobSearchQuery(BaseModel):
    location: Optional[str] = Field(None, description="Code INSEE de la commune (ex: 33063)")
    rome: Optional[str] = Field(None, description="Code métier ROME de 5 caractères (ex: D1102)")
    # query: Optional[str] = Field(None, description="Mots clés supplémentaires")
    # distance: int = Field(10, description="Rayon de recherche en km")

JOB_HUNTER_ANALYSIS_SYSTEM_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
** CODES METIERS IDENTIFIES** : {ROME_CODES}
**VILLE ACTIVE** : {FOCUS_CITY_NAME} (Code INSEE: {FOCUS_CITY_CODE})

**Objectif** : Trouver des offres d'emploi RÉELLES et PERTINENTES selon le `CONTEXTE RÉSUMÉ` dans `{FOCUS_CITY_NAME}` pour TOUS les adultes du ménage.

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **UTILISATION DU CODE INSEE** : Ne cherche pas le code, utilise celui fourni : `{FOCUS_CITY_CODE}`.
2. **RECHERCHE D'OFFRES (BATCH ONLY)** : Lance `search_job_offers_batch_tool` pour TOUS les codes ROME identifiés dans le `CONTEXTE RÉSUMÉ` en utilisant `location='{FOCUS_CITY_CODE}'`.
6. **NE DEMANDE PAS DE PRÉCISIONS** : Tu as les informations sur les métiers dans les critères. AGIS IMMÉDIATEMENT sans attendre de confirmation.
7. **SÉLECTION ET RÉPONSE (CRITIQUE)** : 
    - Commence toujours ta réponse par rappeler en une phrase les métiers que tu as recherché.
    - Pour chaque catégorie `rome`, tu DOIS sélectionner et présenter les **3 offres les plus pertinentes** selon le `CONTEXTE RÉSUMÉ`.
    - Pour chaque offre, indique : Intitulé, ID (ex: 7874186), lieu, type de contrat, durée, salaire et une phrase expliquant pourquoi elle correspond bien au `CONTEXTE RÉSUMÉ`.
    - Ne te contente JAMAIS d'une seule offre si l'outil en retourne plusieurs.
"""

JOB_HUNTER_SPECIFIC_SYSTEM_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.
**Objectif** : Faire d'éventuelles recherches supplémentaires pour répondre à une question spécifique de l'utilisateur.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY_NAME} (Code INSEE: {FOCUS_CITY_CODE})
**QUESTION POSÉE** : {LAST_MESSAGE}
**CONNAISSANCES ACTUELLES** : {COMMUNE_ARTIFACT}

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. Si la `QUESTION POSÉE` peut-être répondue avec les `CONNAISSANCES ACTUELLES` ne fais rien.
2. Si des données manquent:
    - Pour récupérer le détail d'une offre appele IMMEDIATEMENT `get_job_details` pour l'ID du dans `QUESTION POSÉE` structure ta réponse avec les points suivants :
        - Lien vers l'offre
        - Type de contrat et durée.
        - Compétences attendues (traduis si trop technique).
        - Analyse d'adéquation avec le `CONTEXTE RÉSUMÉ`.
        - Employeur. Localisation précise et salaire (si disponible).
    - Pour récupérer de nouvelles offres:
        - Utilise `search_referentiels_batch_tool` pour récupérer le/les code(s) ROME ou un code commune manquant (ne les invente JAMAIS)
        - Utilise ensuite `search_job_offers_batch_tool` pour récupérer les offres correspondantes.
"""

job_hunter_agent = Agent(
    get_model("job_hunter"),
    deps_type=ODISDeps
)

@job_hunter_agent.system_prompt
async def job_hunter_instructions(ctx: RunContext[ODISDeps]) -> str:
    briefing = ctx.deps.state.briefing or ""
    focus = ctx.deps.state.focus_city
    city_name = focus.name if focus else "Non définie"
    city_code = focus.codgeo if focus else "Inconnu"
    codes_metiers = ctx.deps.state.search_criteria.codes_metiers or []
    last_message = ctx.deps.state.messages[-1].get("content", "Non disponible") if ctx.deps.state.messages else "Non disponible"
    h = compute_criteria_hash(ctx.deps.state.search_criteria)
    artifacts = ctx.deps.state.commune_artifacts.get(city_name.lower().strip(), {}).get(h, {})

    # We select prompt according to mode: generic commune analysis or a specific question
    mode = ctx.deps.state.execution_mode
    if mode == 'specific_ask':
        prompt = JOB_HUNTER_SPECIFIC_SYSTEM_PROMPT
    else:
        prompt = JOB_HUNTER_ANALYSIS_SYSTEM_PROMPT
    
    prompt = prompt.format(
        BRIEFING=briefing, 
        FOCUS_CITY_NAME=city_name, 
        FOCUS_CITY_CODE=city_code,
        ROME_CODES= codes_metiers,
        LAST_MESSAGE = last_message,
        COMMUNE_ARTIFACT=artifacts.get("job_hunter", "Non disponible")
    )    

    logger.debug(f"Job Hunter Prompt: {prompt}")

    return prompt

# Tools wrapped for PydanticAI
@job_hunter_agent.tool
def search_job_offers_batch_tool(
    ctx: RunContext[ODISDeps], 
    searches: List[JobSearchQuery]
) -> Dict[str, Any]:
    """
    Version optimisée pour effectuer plusieurs recherches d'offres d'emploi en UN SEUL tour.
    Utilise cet outil pour trouver des opportunités concrètes pour tous les métiers identifiés.
    
    Args:
        searches: Liste d'objets JobSearchQuery {location, rome}
    """
    return search_job_offers_batch([s.model_dump() for s in searches])

@job_hunter_agent.tool
def get_job_details_tool(ctx: RunContext[ODISDeps], job_id: str) -> Dict[str, Any]:
    """Recherche des détails d'une offre d'emploi.
    
    Args:
        job_id (str): ID de l'offre d'emploi.
    
    Returns:
        Dict[str, Any]: Dictionnaire des détails de l'offre d'emploi.
    """
    return get_job_details(job_id)


@job_hunter_agent.tool
def search_referentiels_batch_tool(ctx: RunContext[ODISDeps], searches: List[SearchQuery]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    Utilise cet outil si tu as plusieurs informations à normaliser (ex: ville + métier).
    
    Args:
        searches (List[SearchQuery]): Liste d'objets {query, domain}
    """
    return search_referentiels_batch([s.model_dump() for s in searches])

