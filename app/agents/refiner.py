import logging
import logfire
import json
from datetime import datetime
from typing import List, Dict, Any, Optional

from pydantic import BaseModel, Field, ConfigDict
from pydantic_ai import Agent, RunContext

from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings
from core.models import SearchCriterias

logger = logging.getLogger("refiner_agent")

# --- Structured Output ---
class CityPitch(BaseModel):
    codgeo: str = Field(description="Code INSEE de la commune (5 chiffres)")
    name: str = Field(description="Nom de la commune")
    pitch: str = Field(description="Résumé explicatif très concis des points forts de la commune pour le projet.")

class RefinerResult(BaseModel):
    odis_brief: str = Field(description="Synthèse narrative du dossier (Briefing). Décris la personne, sa situation et ses besoins de manière fluide.")
    global_pitch: str = Field(description="Réponse textuelle globale à afficher dans le chat (introduction des résultats).")
    pitches_per_city: List[CityPitch] = Field(
        default_factory=list, 
        description="Liste des points forts pour chaque commune du Top 5 ainsi que pour la commune pressentie de comparaison (hors Top 5) si elle est présente dans le contexte."
    )

# --- System Prompt ---
REFINER_SYSTEM_PROMPT = """
**Contexte** : Un Travailleur Social cherche la ville la plus adéquate pour une personne réfugiée (et sa famille). A partir de critères de recherches, l'outil identifie un Top 5 que l'on analyse et compare à la commune actuelle de la personne accompagnée. De plus, une ville pressentie par l'utilisateur peut également être fournie pour comparaison.
**Rôle** : Tu es le Refiner ODIS. Ton job est de synthétiser le dossier utilisateur, d'argumenter le Top 5 des Villes identifiées, et d'argumenter également la commune pressentie (si elle est fournie dans le contexte sous "Commune pressentie à évaluer (Hors Top 5, pour comparaison)").

# Données du dossier et des résultats :
```json
{DATA_CONTEXT}
```

**Instructions** :
1. **Génère le `odis_brief`** : Rédige une synthèse hyper concise et factuelle (2-3 phrases) qui décrit la situation de la personne, son projet de vie et ses contraintes. Ce briefing servira de base de connaissance pour d'autres agents experts.
2. **Génère le `global_pitch`** : Rédige une introduction engageant en une phrase pour présenter les 5 résultats au travailleur social.
3. **Analyse le Top 5** des meilleures communes identifiées, ainsi que la commune pressentie si elle est fournie dans le contexte.
4. Pour chaque ville du Top 5 et pour la commune pressentie si présente retourne `pitches_per_city`:
    a. Fournis le code INSEE exact (`codgeo`) et le nom (`name`).
    b. Rédige une liste à puces markdown de 3 à 5 points forts pertinents au regard du dossier. Utilise les chiffres et statistiques présents dans le contexte pour être concret.
5. **IMPORTANT** : Ne retourne jamais les références des données techniques internes (ex: %{{log_soc_inoc_scaled}}). Utilise un langage naturel et professionnel.
"""

# --- Agent Definition ---
refiner_agent = Agent(
    get_model("refiner"),
    model_settings=get_model_settings("refiner"),
    deps_type=ODISDeps,
    output_type=RefinerResult
)

@refiner_agent.system_prompt
async def refiner_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds Refiner agent prompt using ODISContextBuilder."""
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "refiner")
    
    prompt = REFINER_SYSTEM_PROMPT.format(
        DATA_CONTEXT=data_context
    )
    return prompt
