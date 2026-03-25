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
    search_referentiels_batch,
    search_inclusion_jobs_batch,
    get_inclusion_job_details
)

logger = logging.getLogger("job_hunter_agent_v2")

class JobHunterResult(BaseModel):
    searched: str = Field(..., description="Liste des codes ROME et lieux recherchés.")
    result: str = Field(..., description="Synthèse des offres d'emploi pertinentes trouvées.")

class SearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(..., description="Domaine de recherche possibles:['rome_codes', 'communes'].")

class JobSearchQuery(BaseModel):
    location: str = Field(None, description="Code INSEE de la commune (ex: 33063)")
    rome: Optional[str] = Field(None, description="Code métier ROME de 5 caractères (ex: D1102)")

JOB_HUNTER_ANALYSIS_SYSTEM_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
** CODES METIERS IDENTIFIES** : {ROME_CODES}
**VILLE ACTIVE** : {FOCUS_CITY_NAME} (Code INSEE: {FOCUS_CITY_CODE})

**Objectif** : Trouver des offres d'emploi RÉELLES et PERTINENTES selon le `CONTEXTE RÉSUMÉ` dans `{FOCUS_CITY_NAME}` pour TOUS les adultes du ménage. 
**Note** : Les offres de Structures d'insertion par l'activité Economique (SIAE) sont particulièrement pertinentes même si les codes ROME ne correspondent pas exactement.

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **UTILISATION DU CODE INSEE** : Ne cherche pas le code, utilise celui fourni : `{FOCUS_CITY_CODE}`.
2. **RECHERCHE D'OFFRES (FT & SIAE)** :  Lance `search_job_offers_batch_tool` (France Travail) ET `search_inclusion_jobs_batch_tool` pour TOUS les codes ROME identifiés.
3. **NE DEMANDE PAS DE PRÉCISIONS** : Tu as les informations sur les métiers dans les critères. AGIS IMMÉDIATEMENT sans attendre de confirmation.
4. **Réponse (STRUCTURED)** : 
    - Tu DOIS retourner un objet `JobHunterResult`.
    - `searched` : Liste TOUS les codes ROME + libellés recherchés.
    - `result` : Pour chaque catégorie `rome`, présente les **3 offres les plus pertinentes** (mélange FT et SIAE). Pour les offres SIAE, précise EXPLICITEMENT qu'il s'agit d'offres d'insertion (SIAE). Indique : Intitulé, ID, lieu, type de contrat, et une phrase d'explication.
"""

JOB_HUNTER_SPECIFIC_SYSTEM_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.
**Objectif** : Faire d'éventuelles recherches supplémentaires pour répondre à une question spécifique de l'utilisateur.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY_NAME} (Code INSEE: {FOCUS_CITY_CODE})
**QUESTION POSÉE** : {LAST_MESSAGE}
**CONNAISSANCES ACTUELLES** : {COMMUNE_ARTIFACT}

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
- Pour récupérer le détail d'une offre appele IMMEDIATEMENT `get_job_details` pour l'ID du dans `QUESTION POSÉE` structure ta réponse avec les points suivants :
    - Lien vers l'offre
    - Type de contrat et durée.
    - Compétences attendues (traduis si trop technique).
    - Analyse d'adéquation avec le `CONTEXTE RÉSUMÉ`.
    - Employeur. Localisation précise et salaire (si disponible).
- Pour récupérer de nouvelles offres:
    - Utilise `search_referentiels_batch_tool` pour récupérer le/les code(s) ROME ou un code commune manquant (ne les invente JAMAIS)
    - Utilise `search_job_offers_batch_tool` (France Travail) ou `search_inclusion_jobs_batch_tool` (SIAE) selon la demande.

**Réponse (STRUCTURED)** :
- Tu DOIS retourner un objet `JobHunterResult`.
- `searched` : Détails des nouvelles recherches ou IDs consultés.
- `result` : Détails de l'offre (Lien, Contrat, Compétences, Adéquation, Employeur) ou nouvelles offres trouvées.
"""

job_hunter_agent = Agent(
    get_model("job_hunter"),
    deps_type=ODISDeps,
    output_type=JobHunterResult
)

@job_hunter_agent.system_prompt
async def job_hunter_instructions(ctx: RunContext[ODISDeps]) -> str:
    odis_brief = ctx.deps.state.odis_brief or ""
    focus = ctx.deps.state.focus_city
    city_name = focus.name if focus else "Non définie"
    city_code = focus.codgeo if focus else "Inconnu"
    codes_metiers = ctx.deps.state.search_criteria.codes_metiers or []
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
        prompt = JOB_HUNTER_SPECIFIC_SYSTEM_PROMPT
    else:
        prompt = JOB_HUNTER_ANALYSIS_SYSTEM_PROMPT
    
    prompt = prompt.format(
        BRIEFING=odis_brief, 
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

def get_job_details_tool(ctx: RunContext[ODISDeps], job_id: str) -> Dict[str, Any]:
    """Recherche des détails d'une offre d'emploi (utilise soit FT soit SIAE selon l'ID)."""
    # Simple heuristic: if ID has letters it might be FT, if it's numeric/longer it might be SIAE
    # Better: try both if unsure, or Job Hunter can decide based on previous results.
    if len(job_id) < 10: # France Travail IDs are usually 7-8 chars
        return get_job_details(job_id)
    return get_inclusion_job_details(job_id)

@job_hunter_agent.tool
def search_inclusion_jobs_batch_tool(
    ctx: RunContext[ODISDeps], 
    searches: List[JobSearchQuery]
) -> Dict[str, Any]:
    """
    Recherche d'offres d'insertion (SIAE) en mode Batch.
    À utiliser spécifiquement pour les publics en insertion.
    
    Args:
        searches: Liste d'objets JobSearchQuery {location, rome}
    """
    return search_inclusion_jobs_batch([s.model_dump() for s in searches])

@job_hunter_agent.tool
def get_inclusion_job_details_tool(ctx: RunContext[ODISDeps], siae_id: str) -> Dict[str, Any]:
    """Détails d'une structure SIAE et ses offres."""
    return get_inclusion_job_details(siae_id)


@job_hunter_agent.tool
def search_referentiels_batch_tool(ctx: RunContext[ODISDeps], searches: List[SearchQuery]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    Utilise cet outil si tu as plusieurs informations à normaliser (ex: ville + métier).
    
    Args:
        searches (List[SearchQuery]): Liste d'objets {query, domain}
    """
    return search_referentiels_batch([s.model_dump() for s in searches])

