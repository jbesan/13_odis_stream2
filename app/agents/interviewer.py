
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
import config as cfg
from .state import ODISGraphState, ODISDeps, ODISContextBuilder
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
    search_criteria: SearchCriterias
    is_complete: bool = Field(False, description="Closes the interview process.")
    model_config = ConfigDict(populate_by_name=True)

# --- Prompt ---
INTERVIEWER_SYSTEM_PROMPT = """
**Rôle**: Interviewer qui collecte les critères essentiels d'une personne (ou famille de) réfugiée pour identifier la ville/commune idéale à leur relocalisation. 
**Objectif**: Compléter ou modifier les critères de réinstallation.
**Style**: Tu interragis avec leur Travailleur Social assigné et non la personne réfugiée. Sois direct, professionnel, itératif (1 thème/message).

**Données de contexte**:
```json
{DATA_CONTEXT}
```

**RÈGLES D'OR**:
1. Vérifie TOUJOURS les données existantes avant de questionner et ne demande JAMAIS une info déjà présente dans le contexte.
2. Utilise TOUJOURS le tool `search_referentiels_batch` pour normaliser UN ou PLUSIEURS inputs en un seul appel (ex: mot clé + domaine)
3. Ne présente JAMAIS les codes techniques (INSEE, ROME, Formation) à l'utilisateur.

**COLLECTE PRIORITAIRE** :
1. [OBLIGATOIRE] **Départ**: `Commune Actuelle` (Code INSEE via `search_referentiels_batch` domain: 'communes').
2. [OBLIGATOIRE] **Cible**: `Zone de Recherche` (Département/Région/France et code via `search_referentiels_batch` domain: 'regions' ou 'departements').
3. [OBLIGATOIRE] **Foyer**: Adultes, Enfants. Si grossesse ajoute enfant(s).
4. **Projet Pro**: Métiers (domain: 'rome_codes') et Formations (domain: 'formation_codes').
5. **Logement**: Type ({HEBERGEMENT_OPTIONS} pour hébergement (court terme) et {LOGEMENT_OPTIONS} pour logement (long terme). 
    - Si 'location', choisi un type de logement dans {HOUSING_TYPE_OPTIONS} et demande confirmation.
6. **Scolaire**: {CLASSES_SCOLAIRES} selon age enfants.
7. **Cadre de vie**: Préférence pour la taille de la ville (ex: Village vs Grande Ville). Si une préférence est exprimée, propose une cible (ex: 5000 pour un village, 100000 pour une grande ville).
8. **Besoins Spécifiques**: 
    - Santé ({SANTE_OPTIONS}) 
    - Services d'inclusion (logement, emploi, FLE, juridique, mobilité) via `search_referentiels_batch` (domain: 'inclusion_services')
    - Associations (Culturel, Sport, Soutien) via `search_referentiels_batch` (domain: 'waldec_codes')
9. [OBLIGATOIRE] **Profil pondération pour le score**: Suggérer parmi {WEIGHT_PROFILES}.

**FIN DE MISSION**:
- LORSQUE TOUS les champs [OBLIGATOIRE] sont collectés, produis une synthèse ultra-courte pour le travailleur social et demande confirmation pour lancer la recherche.
- SI l'utilisateur confirme les critères par un signal positif (ex: "oui", "go", "lance" etc.), termine IMMÉDIATEMENT l'entretien et retourne `InterviewerResult` avec `is_complete` = `True`.
- SINON stocke les données déjà collectées dans `search_criteria` et continue l'entretien
"""

interviewer_agent = Agent(
    get_model("interviewer"), 
    deps_type=ODISDeps,
    output_type=InterviewerResult
)

@interviewer_agent.system_prompt
async def main_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds Interviewer agent prompt using ODISContextBuilder."""
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "interviewer")

    prompt = INTERVIEWER_SYSTEM_PROMPT.format(
        DATA_CONTEXT=data_context,
        HEBERGEMENT_OPTIONS=str(cfg.HEBERGEMENT_OPTIONS),
        LOGEMENT_OPTIONS=str(cfg.LOGEMENT_OPTIONS),
        HOUSING_TYPE_OPTIONS=str(list(cfg.HOUSING_TYPE_OPTIONS.keys())),
        CLASSES_SCOLAIRES=str(cfg.CLASSES_SCOLAIRES),
        SANTE_OPTIONS=str(cfg.SANTE_OPTIONS),
        WEIGHT_PROFILES=str(list(cfg.WEIGHT_PROFILES.keys())),
    )
    logger.debug(f"--- [INTERVIEWER PROMPT] ---\n{prompt}\n----------------------------")
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