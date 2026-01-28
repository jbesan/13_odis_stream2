import logging
import re
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model
from .tools import (
    search_job_offers_batch,
    get_job_details, 
    search_referentiels_batch
)

logger = logging.getLogger("job_hunter_agent_v2")

class RefSearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(..., description="Domaine: ['rome_codes', 'communes', 'regions', 'departements'].")

class JobSearchQuery(BaseModel):
    location: Optional[str] = Field(None, description="Code INSEE de la commune (ex: 33063)")
    rome: Optional[str] = Field(None, description="Code métier ROME de 5 caractères (ex: D1102)")
    # query: Optional[str] = Field(None, description="Mots clés supplémentaires")
    # distance: int = Field(10, description="Rayon de recherche en km")

JOB_HUNTER_SYSTEM_PROMPT = """
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
    - Pour chaque catégorie `rome`, tu DOIS sélectionner et présenter les **3 offres les plus pertinentes** selon le `CONTEXTE RÉSUMÉ`.
    - Pour chaque offre, indique : Intitulé, ID (ex: 7874186), lieu, type de contrat, durée, salaire et une phrase expliquant pourquoi elle correspond bien au `CONTEXTE RÉSUMÉ`.
    - Ne te contente JAMAIS d'une seule offre si l'outil en retourne plusieurs.
    - Termine en demandant si l'utilisateur veut voir plus de détails (`get_job_details`) sur une offre spécifique.
"""

JOB_DETAILS_SYSTEM_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.
**Objectif** : Donner le DETAIL d'une offre d'emploi précise que l'utilisateur a repéré.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}
**OFFRE CIBLÉE** : {JOB_ID}

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **RECUPERATION DE L'OFFRE** : Tu DOIS IMMEDIATEMENT appeler `get_job_details` pour l'ID `{JOB_ID}`.
2. **SYNTHÈSE DE L'OFFRE** : Synthétise les points clés : 
   - Lien vers l'offre
   - Type de contrat et durée.
   - Compétences attendues (traduis si trop technique).
   - Analyse d'adéquation avec le `CONTEXTE RÉSUMÉ`.
   - Employeur. Localisation précise et salaire (si disponible).
3. **NE RECHERCHE PAS d'autres offres** sauf si explicitement demandé. Reste focus on cette offre.
"""

job_hunter_agent = Agent(
    get_model("job_hunter"),
    deps_type=ODISDeps
)

@job_hunter_agent.system_prompt
async def job_hunter_instructions(ctx: RunContext[ODISDeps]) -> str:
    briefing = ctx.deps.state.briefing or ""
    city = str(ctx.deps.state.focus_city or "Non définie")
    codes_metiers = ctx.deps.state.search_criteria.codes_metiers or []
    
    # We combine the prompts and format them using the base variables
    # Note: JOB_DETAILS_SYSTEM_PROMPT also has {JOB_ID} placeholder which is NOT in context yet,
    # but that's fine as it's an instruction for the agent to look for it or we can leave it as {JOB_ID}.
    # However, to avoid KeyError with .format(), we need to be careful.
    # Let's use a double brace {{JOB_ID}} or just handle it.
    
    # Refined approach: use f-string for the wrapper and format the internal prompts properly.
    focus = ctx.deps.state.focus_city
    city_name = focus.name if focus else "Non définie"
    city_code = focus.codgeo if focus else "Inconnu"
    
    return f"""
    **Rôle** : Tu es le Job Hunter ODIS.
    **CONTEXTE** : {briefing}
    **VILLE ACTIVE** : {city_name} ({city_code})
    
    {JOB_HUNTER_SYSTEM_PROMPT.format(
        BRIEFING=briefing, 
        FOCUS_CITY_NAME=city_name, 
        FOCUS_CITY_CODE=city_code,
        ROME_CODES= codes_metiers
    )}

    **IMPORTANT** : Si tu dois répondre sur une offre précise, utilise ces directives :
    {JOB_DETAILS_SYSTEM_PROMPT.format(BRIEFING=briefing, FOCUS_CITY=city_name, JOB_ID='{JOB_ID}')}
    """

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
def search_referentiels_batch_tool(
    ctx: RunContext[ODISDeps], 
    searches: List[RefSearchQuery]
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    
    Args:
        searches: Liste d'objets RefSearchQuery{query, domain}
    """
    return search_referentiels_batch([s.model_dump() for s in searches])

