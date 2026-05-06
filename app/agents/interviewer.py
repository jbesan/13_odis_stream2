import logging
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext
import config as cfg
from core.models import SearchCriterias
from .agent_config import get_model, get_model_settings
from .tools import search_referentiels_batch

logger = logging.getLogger("autodetect_agent")

class SearchQuery(BaseModel):
    query: str = Field(..., description="Mot clé de recherche")
    domain: str = Field(..., description="Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements', 'communes', 'housing_types'].")

class AutoDetectionResult(BaseModel):
    response: str = Field(..., description="Un résumé court et courtois des éléments identifiés.")
    search_criteria: SearchCriterias
    model_config = ConfigDict(populate_by_name=True)

# --- Prompt ---
AUTODETECT_SYSTEM_PROMPT = """
**Rôle**: Extracteur expert de données. 
**Objectif**: Analyser un texte non structuré (notes d'entretien, email) et extraire les critères de relocalisation sous un format structuré.

**RÈGLES D'OR**:
1. Utilise TOUJOURS le tool `search_referentiels_batch` pour normaliser les lieux et métiers en codes officiels (ex: ville -> code INSEE).
2. Remplis UNIQUEMENT les champs du formulaire qui sont explicitement mentionnés dans le texte. Ne devine pas.

**DOMAINES DE NORMALISATION** (pour search_referentiels_batch):
- Départ: `Commune Actuelle` (domain: 'communes')
- Cible: `Zone de Recherche` (domain: 'regions' ou 'departements')
- Projet Pro: Métiers (domain: 'rome_codes') et Formations (domain: 'formation_codes')
- Services d'inclusion: (domain: 'inclusion_services')
- Associations: (domain: 'waldec_codes')

**Options Disponibles**:
- Hébergement: {HEBERGEMENT_OPTIONS}
- Logement: {LOGEMENT_OPTIONS}
- Type de logement: {HOUSING_TYPE_OPTIONS}
- Scolaire: {CLASSES_SCOLAIRES}
- Santé: {SANTE_OPTIONS}
- Profils de pondération: {WEIGHT_PROFILES}
"""

interviewer_agent = Agent(
    get_model("interviewer"),
    model_settings=get_model_settings("interviewer"),
    output_type=AutoDetectionResult
)

@interviewer_agent.system_prompt
async def main_instructions(ctx: RunContext) -> str:
    prompt = AUTODETECT_SYSTEM_PROMPT.format(
        HEBERGEMENT_OPTIONS=str(cfg.HEBERGEMENT_OPTIONS),
        LOGEMENT_OPTIONS=str(cfg.LOGEMENT_OPTIONS),
        HOUSING_TYPE_OPTIONS=str(list(cfg.HOUSING_TYPE_OPTIONS.keys())),
        CLASSES_SCOLAIRES=str(cfg.CLASSES_SCOLAIRES),
        SANTE_OPTIONS=str(cfg.SANTE_OPTIONS),
        WEIGHT_PROFILES=str(list(cfg.WEIGHT_PROFILES.keys())),
    )
    return prompt

@interviewer_agent.tool
async def search_referentiels_batch_tool(ctx: RunContext, searches: List[SearchQuery]) -> Dict[str, List[Dict[str, Any]]]:
    """
    Version optimisée pour effectuer plusieurs recherches de référentiels en UN SEUL tour.
    Utilise cet outil si tu as plusieurs informations à normaliser (ex: ville + métier).
    
    Args:
        searches (List[SearchQuery]): Liste d'objets {query, domain}. Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements', 'communes', 'housing_types'].
    """
    return await search_referentiels_batch([s.model_dump() for s in searches])