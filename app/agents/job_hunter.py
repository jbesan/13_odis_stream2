import logging
import re
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import GraphState, ODISDeps, compute_criteria_hash, ODISContextBuilder
from .agent_config import get_model, get_model_settings
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
    location: Optional[str] = Field(None, description="Code INSEE de la commune (ex: 33063)")
    rome: Optional[str] = Field(None, description="Code métier ROME de 5 caractères (ex: D1102)")

JOB_HUNTER_ANALYSIS_SYSTEM_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

**Objectif** : Trouver des offres d'emploi RÉELLES et PERTINENTES selon le dossier JSON dans la `Ville analysée` pour TOUS les adultes du ménage. 
**Note** : Les offres de Structures d'insertion par l'activité Economique (SIAE) sont particulièrement pertinentes même si les codes ROME ne correspondent pas exactement.

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
1. **UTILISATION DU CODE INSEE** : Ne cherche pas le code, utilise celui fourni dans `Ville analysée` (`Code INSEE`).
2. **RECHERCHE D'OFFRES (FT & SIAE)** :
   - **France Travail** : Vérifie TOUJOURS si des offres d'emploi correspondantes pré-chargées sont déjà disponibles sous `Ville analysée` -> `Données emploi et formation` -> `Liste des offres d'emploi correspondantes séparées par adulte du ménage`.
     * Si elles sont présentes, **n'appelle JAMAIS** `search_job_offers_batch_tool`. Utilise-les DIRECTEMENT comme source de vérité !
     * Si elles sont absentes ou vides, lance l'outil `search_job_offers_batch_tool` pour les métiers identifiés.
   - **SIAE (Inclusion)** : Lance `search_inclusion_jobs_batch_tool` pour récupérer des offres d'insertion s'il n'y a pas d'offres SIAE déjà listées dans `Données emploi et formation`.
3. **NE DEMANDE PAS DE PRÉCISIONS** : Tu as les informations sur les métiers dans les critères. AGIS IMMÉDIATEMENT sans attendre de confirmation.
4. **Réponse (STRUCTURED)** : 
    - Tu DOIS retourner un objet `JobHunterResult`.
    - `searched` : Liste TOUS les codes ROME + libellés recherchés. Mentionne s'il s'agit d'offres pré-chargées du cache (ex: "[Cache] ROME D1102").
    - `result` : Pour chaque catégorie `rome`, présente les **3 offres les plus pertinentes** (mélange FT et SIAE). Pour les offres SIAE, précise EXPLICITEMENT qu'il s'agit d'offres d'insertion (SIAE). Indique : Intitulé, ID, lieu, type de contrat, et une phrase d'explication.
"""

JOB_HUNTER_SPECIFIC_SYSTEM_PROMPT = """
**Rôle** : Tu es le Job Hunter ODIS. Expert ultra-proactif du marché de l'emploi.
**Objectif** : Faire d'éventuelles recherches supplémentaires pour répondre à une question spécifique de l'utilisateur.

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

**DIRECTIVES CRITIQUES (NE PAS DEMANDER, AGIR)** :
- Pour récupérer le détail d'une offre appelle IMMEDIATEMENT `get_job_details` pour l'ID mentionné dans `Dernière question`. Structure ta réponse avec les points suivants :
    - Lien vers l'offre.
    - Type de contrat et durée.
    - Compétences attendues (traduis si trop technique).
    - Analyse d'adéquation avec le dossier.
    - Employeur. Localisation précise et salaire (si disponible).
- Pour récupérer de nouvelles offres :
    - Utilise `search_referentiels_batch_tool` pour récupérer le/les code(s) ROME ou un code commune manquant (ne les invente JAMAIS).
    - Utilise `search_job_offers_batch_tool` (France Travail) ou `search_inclusion_jobs_batch_tool` (SIAE) selon la demande.

**Réponse (STRUCTURED)** :
- Tu DOIS retourner un objet `JobHunterResult`.
- `searched` : Détails des nouvelles recherches ou IDs consultés.
- `result` : Détails de l'offre (Lien, Contrat, Compétences, Adéquation, Employeur) ou nouvelles offres trouvées.
"""

job_hunter_agent = Agent(
    get_model("job_hunter"),
    model_settings=get_model_settings("job_hunter"),
    deps_type=ODISDeps,
    output_type=JobHunterResult
)

@job_hunter_agent.system_prompt
async def job_hunter_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds Job Hunter agent prompt using ODISContextBuilder."""
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "job_hunter")
    mode = ctx.deps.state.execution_mode
    prompt_template = JOB_HUNTER_ANALYSIS_SYSTEM_PROMPT if mode in ["analysis", "full_analysis"] else JOB_HUNTER_SPECIFIC_SYSTEM_PROMPT

    prompt = prompt_template.format(DATA_CONTEXT=data_context)
    return prompt

# Tools wrapped for PydanticAI
@job_hunter_agent.tool
async def search_job_offers_batch_tool(
    ctx: RunContext[ODISDeps], 
    searches: List[JobSearchQuery]
) -> Dict[str, Any]:
    """
    Version optimisée pour effectuer plusieurs recherches d'offres d'emploi en UN SEUL tour.
    Utilise cet outil pour trouver des opportunités concrètes pour tous les métiers identifiés.
    
    Args:
        searches: Liste d'objets JobSearchQuery {location, rome}
    """
    return await search_job_offers_batch([s.model_dump() for s in searches])

def get_job_details_tool(ctx: RunContext[ODISDeps], job_id: str) -> Dict[str, Any]:
    """Recherche des détails d'une offre d'emploi (utilise soit FT soit SIAE selon l'ID)."""
    # Simple heuristic: if ID has letters it might be FT, if it's numeric/longer it might be SIAE
    # Better: try both if unsure, or Job Hunter can decide based on previous results.
    if len(job_id) < 10: # France Travail IDs are usually 7-8 chars
        return get_job_details(job_id)
    return get_inclusion_job_details(job_id)

@job_hunter_agent.tool
async def search_inclusion_jobs_batch_tool(
    ctx: RunContext[ODISDeps], 
    searches: List[JobSearchQuery]
) -> Dict[str, Any]:
    """
    Recherche d'offres d'insertion (SIAE) en mode Batch.
    À utiliser spécifiquement pour les publics en insertion.
    
    Args:
        searches: Liste d'objets JobSearchQuery {location, rome}
    """
    return await search_inclusion_jobs_batch([s.model_dump() for s in searches])

@job_hunter_agent.tool
def get_inclusion_job_details_tool(ctx: RunContext[ODISDeps], siae_id: str) -> Dict[str, Any]:
    """Détails d'une structure SIAE et ses offres."""
    return get_inclusion_job_details(siae_id)


@job_hunter_agent.tool
async def search_referentiels_batch_tool(ctx: RunContext[ODISDeps], searches: List[SearchQuery]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    Utilise cet outil si tu as plusieurs informations à normaliser (ex: ville + métier).
    
    Args:
        searches (List[SearchQuery]): Liste d'objets {query, domain}
    """
    return await search_referentiels_batch([s.model_dump() for s in searches])


# --- Job Curation Skill ---

class CuratedJob(BaseModel):
    job_id: str = Field(
        ...,
        description="L'identifiant de l'offre d'emploi"
    )
    job_brief: str = Field(
        ...,
        description="Une phrase concise et claire (sans saut de ligne) décrivant l'offre et justifiant pourquoi elle correspond particulièrement bien au profil et aux contraintes du candidat (langue, mobilité, expérience)."
    )

class JobCurationResult(BaseModel):
    selected_jobs: List[CuratedJob] = Field(
        ...,
        description="Liste des offres d'emploi sélectionnées par ordre de pertinence décroissante (maximum 5)"
    )

job_curator_agent = Agent(
    get_model("job_hunter"),
    model_settings=get_model_settings("job_hunter"),
    output_type=JobCurationResult
)

JOB_CURATOR_SYSTEM_PROMPT = """
Tu es un expert en accompagnement social et en insertion professionnelle. Ton rôle est de sélectionner et résumer les 5 offres d'emploi les plus pertinentes pour un candidat de type réfugié BPI à partir d'une liste d'offres déjà récupérées.

Voici la situation et le profil du candidat :
- Résumé de la situation (Briefing) : {briefing}

Voici les offres d'emploi disponibles (triées par distance croissante) :
{jobs_list}

Consignes de sélection, d'ordonnancement et de justification :
1. Évalue attentivement chaque offre par rapport aux contraintes et critères du candidat en prenant en compte les dimensions suivantes :
   - Maîtrise de la langue : Si le candidat a des difficultés avec le français (ex: débutant, maîtrise partielle, réfugié récemment arrivé), privilégie les offres manuelles, techniques ou nécessitant peu de communication verbale/écrite.
   - Mobilité : Si le candidat n'a pas de permis de conduire ou de voiture, évite les offres exigeant explicitement le permis ou un véhicule personnel, et privilégie les offres situées en centre ville.
   - Niveau d'expérience : Aligne l'expérience demandée dans l'offre avec le profil du candidat (débutant souvent préférable).
   - Adéquation avec le projet de vie : Priorise les offres qui s'alignent le mieux avec les aspirations et les contraintes mentionnées dans son dossier (ex: travail en journée si enfants).
2. Pour chaque offre d'emploi sélectionnée, tu dois rédiger un court résumé de deux phrases (`job_brief`) qui décrit:
    Phrase 1: l'offre et notamment l'employeur, le type de poste, la localisation
    Phrase 2: explique concrètement pourquoi cette offre est pertinente pour le profil accompagné.
3. Sélectionne et retourne au maximum 5 offres (ou toutes s'il y en a moins de 5) ordonnées par pertinence décroissante dans le champ `selected_jobs`.
"""


