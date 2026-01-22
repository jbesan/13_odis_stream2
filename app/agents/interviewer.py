
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
import config as cfg
from .state import ODISGraphState, ODISDeps
from core.models import SearchCriterias
from .agent_config import get_model
# Import the pure tools
from .tools import search_referentiels_batch

logger = logging.getLogger("interviewer_agent_v2")

# --- Structured Output ---
# Though Interviewer mainly talks, defining a structure helps if we want to extract final data.
class SearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(..., description="Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements', 'communes', 'housing_types'].")

class InterviewerResult(BaseModel):
    response: str
    search_criteria: Optional[SearchCriterias] = None
    is_complete: bool = Field(False, description="Closes the interview process.")
    model_config = ConfigDict(populate_by_name=True)

# --- Prompt ---
# We keep the core prompt but adapt it for PydanticAI (system_prompt)
INTERVIEWER_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Interviewer ODIS. Ta mission est de collecter les besoins d'un réfugié (et éventuellement sa famille) pour sa mobilité en France. Tu n'interragis pas directement avec le bénéficiaire mais avec un Travailleur Social qui te transmet les informations.
**Ton** : Empathique, professionnel, direct (tutoiement). 

** Directives d'entretien ** :
- Ne pose JAMAIS toutes les questions en un seul message, mais un message avec les questions par thème (composition du foyer, projet pro, logement, etc.).
- Vérifie TOUJOURS les données déjà collectées ci-dessous avant de poser une question et ne redemande JAMAIS la même information.
- Utilise TOUJOURS `search_referentiels_batch` pour normaliser un ou plusieurs inputs en un seul appel (ex: commune + métier). 

** Données déjà collectées (À NE PAS REDEMANDER) ** :
{SEARCH_CRITERIAS}

**Directives de Collecte (Ordre Prioritaire)** :
1. **Commune Actuelle** [OBLIGATOIRE] : Utilise `search_referentiels` (domain='communes').
2. **Zone de Recherche** [OBLIGATOIRE] : Identifie la zone cible (Dépt par défaut). Si spécifique, cherche le code via `search_referentiels` et règle `loc_search_area` ('departement', 'region', 'france') et `loc_search_code`.
3. **Foyer** [OBLIGATOIRE] : Nb adultes/enfants, si une grossesse est en cours, note le nombre d'enfants attendus.
4. **Projet Pro/Formations** : Cherche codes ROME (`rome_codes`) ou Formation (`formation_codes`) via `search_referentiels`.
5. **Logement** : {HEBERGEMENT_OPTIONS} et {LOGEMENT_OPTIONS}. Si 'location' choisi un type de logement dans {HOUSING_TYPE_OPTIONS} et demande confirmation.
6. **Éducation** : {CLASSES_SCOLAIRES}.
7. **Besoins Spécifiques** : {SANTE_OPTIONS}, Passions, Religion, etc.
8. **Support à l'inclusion** : Utilise `search_referentiels` (domain='inclusion_services' ou 'waldec_codes'). Si pas déjà mentionné demande toujours s'ils on besoin de renforcer leur Français (FLE).
9. **Profil de pondération** [OBLIGATOIRE] : Fais une proposition parmis {WEIGHT_PROFILES} selon les informations collectées et demande confirmation en expliquant ton choix.

**Directives Techniques** :
- **ENRICHISSEMENT** : Remplis TOUJOURS les champs de `search_criteria` avec des objets JSON `{{"code": "...", "label": "..."}}` complets (pas de texte comme "CriteriaItem(...)").
- Ne t'arrête pas tant que tu n'as pas toutes les informations [OBLIGATOIRES] et pose des questions pour obtenir les autres éléments facultatifs.
- **TRANSITION** : Une fois l'entretien fini et les données [OBLIGATOIRES] acquises, synthétise tes trouvailles et demande confirmation : "J'ai suffisamment de critères, on lance la recherche ou voulez-vous ajouter d'autres besoins ?".
- **SORTIE** : Une fois la demande de recherche confirmée retourne IMMEDIATEMENT `InterviewerResult` avec `is_complete=True`
"""

# Version Compactée & Orientée Action
INTERVIEWER_SYSTEM_PROMPT_2 = """
**Rôle**: Interviewer qui collecte un projet de vie d'une personne (ou famille de) réfugié pour leur relocalisation dans la ville/commune idéale. Tu interragis avec leur Travailleur Social assigné.
**Objectif**: Compléter les critères de réinstallation ({SEARCH_CRITERIAS}).
**Style**: Direct, professionnel, itératif (1 thème/message).

**RÈGLES D'OR**:
1. Vérifie TOUJOURS les données existantes avant de questionner.
2. Ne demande JAMAIS une info déjà présente dans le contexte.
3. Utilise TOUJOURS le tool `search_referentiels_batch` pour normaliser UN ou PLUSIEURS inputs en un seul appel (ex: commune + métier)

**COLLECTE PRIORITAIRE** :
1. [OBLIGATOIRE] **Départ**: `Commune Actuelle` (Code INSEE via tool).
2. [OBLIGATOIRE] **Cible**: `Zone de Recherche` (Dép/Région/France et code via tool).
3. [OBLIGATOIRE] **Foyer**: Adultes, Enfants. Si grossesse ajoute enfant(s).
4. **Projet Pro**: Métiers (Codes ROME), Formations.
5. **Logement**: Type ({HEBERGEMENT_OPTIONS} pour hébergement (court terme) et {LOGEMENT_OPTIONS} pour logement (long terme). 
    - Si 'location', choisi un type de logement dans {HOUSING_TYPE_OPTIONS} et demande confirmation.
6. **Scolaire**: {CLASSES_SCOLAIRES} selon age enfants.
7. **Besoins Spécifiques**: Santé ({SANTE_OPTIONS}) et Inclusion (Culturel, Sportif, Aide, FLE, Assos) via tool.
8. [OBLIGATOIRE] **Profil pondération**: Suggérer parmi {WEIGHT_PROFILES}.

**FIN DE MISSION**:
- SI tous les champs [OBLIGATOIRE] collectés:
  - Produis une synthèse ultra-courte.
  - Demande confirmation pour lancer la recherche.
    - Si recherche approuvée retourne IMMEDIATEMENT `InterviewerResult` avec `is_complete` = `True`.
- SINON stocke les données déjà collectées dans `search_criteria` et continue l'entretien
"""

# --- Agent Definition ---
# We redefine the agent slightly to use dynamic instructions fully
interviewer_agent = Agent(
    get_model("interviewer"), 
    deps_type=ODISDeps,
    output_type=InterviewerResult
)

@interviewer_agent.system_prompt
async def main_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Injects dynamic values directly into the prompt using Python's string formatting."""
    
    # Serialize search criteria for the prompt
    search_criteria_json = ctx.deps.state.search_criteria.model_dump_json(indent=2, exclude_none=True)

    prompt = INTERVIEWER_SYSTEM_PROMPT_2.format(
        SEARCH_CRITERIAS=search_criteria_json,
        HEBERGEMENT_OPTIONS=str(cfg.HEBERGEMENT_OPTIONS),
        LOGEMENT_OPTIONS=str(cfg.LOGEMENT_OPTIONS),
        HOUSING_TYPE_OPTIONS=str(list(cfg.HOUSING_TYPE_OPTIONS.keys())),
        CLASSES_SCOLAIRES=str(cfg.CLASSES_SCOLAIRES),
        SANTE_OPTIONS=str(cfg.SANTE_OPTIONS),
        WEIGHT_PROFILES=str(list(cfg.WEIGHT_PROFILES.keys())),
    )
    
    return prompt

# --- Tools ---

@interviewer_agent.tool
def search_referentiels_batch_tool(ctx: RunContext[ODISDeps], searches: List[SearchQuery]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    Utilise cet outil si tu as plusieurs informations à normaliser (ex: ville + métier).
    
    Args:
        searches (List[SearchQuery]): Liste d'objets {query, domain}
    """
    return search_referentiels_batch([s.model_dump() for s in searches])

# We wrap the pure tools to make them PydanticAI compatible (inject context if needed)
# search_commune and search_referentiels are pure lookups, so we can just register them directly?
# PydanticAI tools receive `ctx: RunContext[Deps]` as first arg if typed.


# @interviewer_agent.tool
# def search_referentiels_tool(ctx: RunContext[ODISDeps], query: str, domain: str) -> List[Dict[str, Any]]:
#     """Recherche des codes officiels (Communes, Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels.
    
#     Args:
#         query (str): Mot clé de recherche.
#         domain (str): Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements', 'communes', 'housing_types'].
    
#     Returns:
#         List[Dict[str, Any]]: Liste des codes + labels officiels correspondants.
#     """
#     return search_referentiels(query, domain)

# Removed update_search_criteria_tool as it's now handled via InterviewerResult.search_criteria