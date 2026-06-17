import logging
from typing import List, Dict, Any, Optional
from pydantic_ai import Agent, RunContext
from pydantic import BaseModel, Field
from .state import ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings, get_swarm_boilerplate

logger = logging.getLogger("job_curator")

class CuratedJob(BaseModel):
    job_id: str = Field(
        ...,
        description="L'identifiant unique de l'offre d'emploi."
    )
    job_brief: str = Field(
        ...,
        description="Un court résumé en français de deux phrases. Phrase 1: décrit l'offre (employeur, poste, lieu). Phrase 2: explique la pertinence pour le candidat."
    )

class JobCurationResult(BaseModel):
    selected_jobs: List[CuratedJob] = Field(
        ...,
        description="Liste des offres d'emploi sélectionnées par ordre de pertinence décroissante (maximum 5)."
    )

JOB_CURATOR_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}
**Rôle** : CIP (Conseiller en Insertion Professionnelle) expert en relocalisation de réfugiés.
**Objectif** : Sélectionner et justifier les 5 meilleures offres d'emploi à partir de la liste d'offres récupérées dans le message de l'utilisateur.

# Contexte du dossier (Candidat & Ville cible) :
{DATA_CONTEXT}

**CONSIGNES DE SÉLECTION & D'ORDONNANCEMENT** :
1. Évalue attentivement chaque offre du message utilisateur par rapport aux contraintes et critères du candidat en prenant en compte les dimensions suivantes :
   - Mobilité : Privilégie fortement les offres les plus proches de la ville envisagée (même commune et si possible centre ville) surtout si le candidat n'a pas de permis de conduire ou de voiture.
   - Maîtrise de la langue : Si le candidat a des difficultés avec le français (ex: débutant, maîtrise partielle, réfugié récemment arrivé), privilégie les offres manuelles, techniques ou nécessitant peu de communication verbale/écrite.
   - Niveau d'expérience : Aligne l'expérience demandée dans l'offre avec le profil du candidat (débutant souvent préférable), évite l'intérim.
   - Adéquation avec le projet de vie : Equilibre les contraintes horaires/physiques avec celles mentionnées dans son dossier (ex: travail en journée si enfants).
2. Ordonne les offres par pertinence décroissante pour le profil accompagné et sélectionne les 5 meilleures.
3. Pour chaque offre d'emploi sélectionnée, rédige un court résumé de deux phrases (`job_brief`) qui :
   - Phrase 1 : Décrit l'offre (employeur, poste, localisation).
   - Phrase 2 : Explique la pertinence pour ce candidat spécifique (proximité, horaires, expériences, etc.).
"""

job_curator_agent = Agent(
    get_model("job_curator"),
    model_settings=get_model_settings("job_curator"),
    deps_type=ODISDeps,
    output_type=JobCurationResult
)

@job_curator_agent.system_prompt
async def job_curator_instructions(ctx: RunContext[ODISDeps]) -> str:
    state = ctx.deps.state
    data_context = ODISContextBuilder.agent_context(state, "job_curator")
    boilerplate = get_swarm_boilerplate("job_curator")

    return JOB_CURATOR_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context
    )
