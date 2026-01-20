
import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
import config as cfg
from .state import ODISGraphState, ODISDeps
from core.models import SearchCriterias
from .agent_config import get_model
# Import the pure tools
from .tools import search_referentiels

logger = logging.getLogger("interviewer_agent_v2")

# --- Structured Output ---
# Though Interviewer mainly talks, defining a structure helps if we want to extract final data.
class InterviewerResult(BaseModel):
    response: str
    search_criteria: Optional[SearchCriterias] = None
    model_config = ConfigDict(populate_by_name=True)

# --- Prompt ---
# We keep the core prompt but adapt it for PydanticAI (system_prompt)
INTERVIEWER_SYSTEM_PROMPT = """
**Rôle** : Tu es l'Interviewer ODIS. Ta mission est de collecter les besoins d'un réfugié (et éventuellement sa famille) pour sa mobilité en France.
**Ton** : Empathique, professionnel, direct (tutoiement).

**Directives de Collecte (Ordre Prioritaire)** :
1. **Commune Actuelle** [OBLIGATOIRE] : Utilise `search_referentiels` (domain='communes').
2. **Zone de Recherche** [OBLIGATOIRE] : Identifie la zone cible (Dépt par défaut). Si spécifique, cherche le code via `search_referentiels` et règle `loc_search_area` ('departement', 'region', 'france') et `loc_search_code`.
3. **Foyer** [OBLIGATOIRE] : Nb adultes/enfants, si une grossesse est en cours, note le nombre d'enfants attendus.
4. **Projet Pro/Formations** : Cherche codes ROME (`rome_codes`) ou Formation (`formation_codes`) via `search_referentiels`.
5. **Logement** : {HEBERGEMENT_OPTIONS} et {LOGEMENT_OPTIONS}.
6. **Éducation** : {CLASSES_SCOLAIRES}.
7. **Besoins Spécifiques** : {SANTE_OPTIONS}, Passions, Religion, etc.
8. **Support à l'inclusion** : Utilise `search_referentiels` (domain='inclusion_services' ou 'waldec_codes').
9. **Profil de pondération** [OBLIGATOIRE] : Fais une proposition parmis {WEIGHT_PROFILES} selon les informations collectées et demande confirmation en expliquant ton choix.

**Directives Techniques** :
- **ENRICHISSEMENT** : Remplis toujours les champs de `search_criteria` avec des objets JSON `{{"code": "...", "label": "..."}}` complets (pas de texte comme "CriteriaItem(...)").
- Ne t'arrête pas tant que tu n'as pas toutes les informations [OBLIGATOIRES] et pose des questions pour obtenir les autres éléments facultatifs.
- **TRANSITION** : Une fois les données [OBLIGATOIRES] acquises, demande confirmation : "J'ai suffisamment de critères, on lance la recherche ou voulez-vous ajouter d'autres besoins ?"
- **SORTIE** : Remplis `InterviewerResult`. Tes messages texte vont dans `response`.
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
    filtered_areas_values = [v for k, v in cfg.LOC_SEARCH_AREA_OPTIONS.items() if k != 'custom']
    
    # We use a mapping for the format call
    # This avoids multiple .replace() calls and is more PydanticAI-idiomatic if we were using template strings,
    # but here we just return the formatted string.
    return INTERVIEWER_SYSTEM_PROMPT.format(
        BRIEFING=ctx.deps.state.briefing or "(Pas de briefing)",
        CLASSES_SCOLAIRES=str(cfg.CLASSES_SCOLAIRES),
        HEBERGEMENT_OPTIONS=str(cfg.HEBERGEMENT_OPTIONS),
        LOGEMENT_OPTIONS=str(cfg.LOGEMENT_OPTIONS),
        SANTE_OPTIONS=str(cfg.SANTE_OPTIONS),
        WEIGHT_PROFILES=str(list(cfg.WEIGHT_PROFILES.keys())),
        LOC_SEARCH_AREAS=", ".join(filtered_areas_values),
        WEIGHT_PROFILE=ctx.deps.state.search_criteria.weight_profile or 'Non défini',
        LOC_SEARCH_AREA=ctx.deps.state.search_criteria.loc_search_area or 'Non définie'
    )

# --- Tools ---

# We wrap the pure tools to make them PydanticAI compatible (inject context if needed)
# search_commune and search_referentiels are pure lookups, so we can just register them directly?
# PydanticAI tools receive `ctx: RunContext[Deps]` as first arg if typed.


@interviewer_agent.tool
def search_referentiels_tool(ctx: RunContext[ODISDeps], query: str, domain: str) -> List[Dict[str, Any]]:
    """Recherche des codes officiels (Communes, Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels.
    
    Args:
        query (str): Mot clé de recherche.
        domain (str): Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements', 'communes'].
    
    Returns:
        List[Dict[str, Any]]: Liste des codes + labels officiels correspondants.
    """
    return search_referentiels(query, domain)

# Removed update_search_criteria_tool as it's now handled via InterviewerResult.search_criteria