
import logging
import re
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext
from .state import ODISGraphState, ODISDeps
from .agent_config import get_model
# Pure tools
from .tools import (
    search_job_offers, 
    get_job_details, 
    search_referentiels,
    search_commune
)

logger = logging.getLogger("job_hunter_agent_v2")

JOB_HUNTER_SYSTEM_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.

**CONTEXTE RÉSUMÉ** : {BRIEFING}
**VILLE ACTIVE** : {FOCUS_CITY}

**Objectif** : Trouver des offres d'emploi RÉELLES et PERTINENTES selon le `CONTEXTE RÉSUMÉ` dans `VILLE ACTIVE` pour TOUS les adultes du ménage.

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **Utilisation du Code INSEE (codgeo)** : Récupère le Code INSEE (codgeo) de la ville de `VILLE ACTIVE` avec l'outil `search_commune`.
2. **RECHERCHE D'OFFRES (ROME ONLY)** : Lance `search_job_offers` pour CHAQUE code ROME identifié dans le `CONTEXTE RÉSUMÉ`.
   - Utilise le paramètre `rome`.
   - Si tu as un doute sur le code ROME, utilise `search_referentiels` avec le domaine `rome_codes` pour trouver la catégorie correspondante.
   - Ne spécifie pas de `query` (mots-clés) sauf si l'utilisateur a donné une précision particulière (ex: "en alternance").
3. **CONTEXTE LIVE** : Le briefing contient un nombre d'offres global (Live) pour la ville. Utilise ce chiffre UNIQUEMENT pour donner une tendance générale.
4. **COMPTAGE PRÉCIS** : Pour CHAQUE métier recherché, utilise la valeur `total` retournée par l'outil `search_job_offers`. C'est le SEUL chiffre précis pour le métier en question.
5. **LOCALISATION** : Utilise toujours le code INSEE de la ville cible du `CONTEXTE RÉSUMÉ` pour la recherche.
6. **NE DEMANDE PAS DE PRÉCISIONS** : Tu as les informations sur les métiers dans les critères. AGIS IMMÉDIATEMENT sans attendre de confirmation.
7. **SÉLECTION ET RÉPONSE (CRITIQUE)** : 
    - Pour chaque recherche réussie, tu DOIS sélectionner et présenter les **3 offres les plus pertinentes** (ou toutes si moins de 3 sont disponibles).
    - Pour chaque offre, indique : Intitulé, ID (ex: 7874186) et une phrase expliquant pourquoi elle correspond bien au profil (distance, contrat, expérience).
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
    # Logic to switch prompt based on intent (Search vs Detail)
    # We can inspect the last message from user in ctx.deps.state.messages or similar if we had it.
    # But here we only have the 'message' argument passed to .run(). 
    # Wait, system_prompt doesn't receive the user message directly?
    # PydanticAI system prompt is static or dynamic based on deps.
    # The user message is processed AFTER system prompt.
    # To switch context based on user message content (id regex), we might need to do it 
    # BEFORE calling run(), or use a Router Node in the Graph to decide intent "Job Search" vs "Job Detail".
    # BUT, to keep it self-contained like before:
    # Implementation detail: The 'run' method of the old agent checked regex.
    # Here, we can't easily change the PROMPT based on the current user message content inside `system_prompt` 
    # unless we pass the message into `deps` properly in the Graph before calling the agent.
    #
    # STRATEGY: The Orchestrator/Graph node for 'job_hunter' should detect the ID and update `briefing` or a `job_focus_id` in state.
    # However, let's try to assume the Graph passes the message to the agent, and the agent decides what tools to use.
    # The prompts are instructions. We can merge them!
    
    # Combined Prompt
    briefing = ctx.deps.state.briefing or ""
    city = str(ctx.deps.state.focus_city or "Non définie")
    
    combined = f"""
    **Rôle** : Tu es le Job Hunter ODIS.
    **CONTEXTE** : {briefing}
    **VILLE ACTIVE** : {city}
    
    {JOB_HUNTER_SYSTEM_PROMPT}

    **IMPORTANT** : Si tu dois répondre sur une offre précise, utilise ces directives :
    {JOB_DETAILS_SYSTEM_PROMPT}
    """
    return combined

# We wrap tools
@job_hunter_agent.tool
def search_job_offers_tool(
    ctx: RunContext[ODISDeps], 
    query: Optional[str] = None, 
    location: Optional[str] = None, 
    rome: Optional[str] = None, 
    appellation_codes: Optional[List[str]] = None,
    distance: int = 10,
    rome_code: Optional[str] = None,
    rome_codes: Optional[str] = None
) -> Dict[str, Any]:
    """Recherche des offres d'emploi dans le référentiel ROME.
    
    Args:
        query (str): Recherche à effectuer.
        location (str): Localisation de la recherche.
        rome (str): Code ROME de la recherche.
        distance (int): Distance de la recherche.
    
    Returns:
        Dict[str, Any]: Dictionnaire des offres d'emploi correspondantes.
    """
    return search_job_offers(query, location, rome, appellation_codes, distance, rome_code, rome_codes)

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
def search_referentiels_tool(ctx: RunContext[ODISDeps], query: str, domain: str) -> List[Dict[str, Any]]:
    """Recherche des codes officiels (Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels.
    
    Args:
        query (str): Recherche à effectuer.
        domain (str): Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements'].
    
    Returns:
        List[Dict[str, Any]]: Liste des codes officiels correspondants.
    """
    return search_referentiels(query, domain)

@job_hunter_agent.tool
def search_commune_tool(ctx: RunContext[ODISDeps], query: str) -> List[Dict[str, Any]]:
    """Recherche une ville française pour obtenir son code INSEE.
    
    Args:
        query (str): Nom de la commune à rechercher.
    
    Returns:
        List[Dict[str, Any]]: Liste des codes INSEE correspondants.
    """
    return search_commune(query)
