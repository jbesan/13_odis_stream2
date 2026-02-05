
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

class SearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(..., description="Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements', 'communes', 'housing_types'].")

class InterviewerResult(BaseModel):
    response: str
    search_criteria: Optional[SearchCriterias] = None
    is_complete: bool = Field(False, description="Closes the interview process.")
    model_config = ConfigDict(populate_by_name=True)

# --- Prompt ---
INTERVIEWER_SYSTEM_PROMPT = """
**Rôle**: Interviewer qui collecte un projet de vie d'une personne (ou famille de) réfugié pour leur relocalisation dans la ville/commune idéale. Tu interragis avec leur Travailleur Social assigné.
**Objectif**: Compléter les critères de réinstallation ({SEARCH_CRITERIAS}).
**Style**: Direct, professionnel, itératif (1 thème/message).

**RÈGLES D'OR**:
1. Vérifie TOUJOURS les données existantes avant de questionner et ne demande JAMAIS une info déjà présente dans le contexte.
2. Utilise TOUJOURS le tool `search_referentiels_batch` pour normaliser UN ou PLUSIEURS inputs en un seul appel (ex: commune + métier)
3. Ne présente JAMAIS les codes techniques (INSEE, ROME, Formation) à l'utilisateur.

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
  - **PHASE DE VALIDATION** : Dès que l'utilisateur confirme par un signal positif (ex: "oui", "go", "lance", "ok", "c'est bon", "top", "on y va", "action"), tu DOIS retourner IMMÉDIATEMENT `InterviewerResult` avec `is_complete` = `True`.
  - Si l'utilisateur demande une modification, mets à jour `search_criteria` et continue l'échange.
- SINON stocke les données déjà collectées dans `search_criteria` et continue l'entretien
"""

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

    prompt = INTERVIEWER_SYSTEM_PROMPT.format(
        SEARCH_CRITERIAS=search_criteria_json,
        HEBERGEMENT_OPTIONS=str(cfg.HEBERGEMENT_OPTIONS),
        LOGEMENT_OPTIONS=str(cfg.LOGEMENT_OPTIONS),
        HOUSING_TYPE_OPTIONS=str(list(cfg.HOUSING_TYPE_OPTIONS.keys())),
        CLASSES_SCOLAIRES=str(cfg.CLASSES_SCOLAIRES),
        SANTE_OPTIONS=str(cfg.SANTE_OPTIONS),
        WEIGHT_PROFILES=str(list(cfg.WEIGHT_PROFILES.keys())),
    )
    
    return prompt

@interviewer_agent.tool
def search_referentiels_batch_tool(ctx: RunContext[ODISDeps], searches: List[SearchQuery]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    Utilise cet outil si tu as plusieurs informations à normaliser (ex: ville + métier).
    
    Args:
        searches (List[SearchQuery]): Liste d'objets {query, domain}. Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements', 'communes', 'housing_types'].
    """
    return search_referentiels_batch([s.model_dump() for s in searches])