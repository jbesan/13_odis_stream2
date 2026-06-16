import logging
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings

logger = logging.getLogger("ts_agent")

class ExpertTask(BaseModel):
    expert: Literal[
        'job_hunter', 
        'housing_expert', 
        'mobility_expert', 
        'healthcare_expert', 
        'education_expert', 
        'social_integration_expert'
    ] = Field(..., description="L'expert thématique ciblé.")
    task_description: str = Field(
        ..., 
        description="Description très spécifique et ciblée de la mission de l'expert pour cette étape. Exemple : 'Recherche les structures CADA de la ville et les associations locales pour une personne réfugiée seule.'"
    )
    skill_cards: List[str] = Field(
        default_factory=list, 
        description="Liste des identifiants de Skill Cards à charger pour cette mission (ex: ['basic_housing'])."
    )

class SwarmPlan(BaseModel):
    direct_answer: Optional[str] = Field(
        None, 
        description="Si la dernière question de l'utilisateur peut être résolue avec les données existantes. Laisser obligatoirement vide (null/None) si le mode actuel est 'full_analysis' ou si des tâches d'experts sont planifiées."
    )
    tasks: List[ExpertTask] = Field(
        default_factory=list, 
        description="Liste des tâches à exécuter en parallèle par les agents experts. Laisse vide si 'direct_answer' est fourni."
    )

TS_AGENT_SYSTEM_PROMPT = """
Tu es le TS_AGENT (Travailleur Social Coordinateur) d'ODIS. 
Ton rôle est d'analyser le dossier de la personne accompagnée et la dernière question de l'utilisateur afin de planifier le travail des agents experts ou de répondre directement si tu as déjà toutes les informations nécessaires.

# Directives de planification :
{EXECUTION_MODE_INSTRUCTION}

# Contexte du dossier :
```json
{DATA_CONTEXT}
```

# Répertoire des Skill Cards disponibles :
Voici les Skill Cards que tu peux affecter aux experts :
- `basic_housing` (Expert: housing_expert) : Analyse générale du logement, loyer m², structures d'hébergement.
- `basic_mobility` (Expert: mobility_expert) : Transports locaux, temps de trajet, gratuité ou tarifs solidaires.
- `basic_healthcare` (Expert: healthcare_expert) : Accès aux soins, PMI, hôpitaux, besoins de santé spécifiques.
- `basic_education` (Expert: education_expert) : Écoles locales, inscription scolaire, crèches.
- `basic_social` (Expert: social_integration_expert) : CCAS local, associations d'aide aux réfugiés, démarches d'intégration.
- `basic_jobs` (Expert: job_hunter) : Recherche d'offres d'emploi France Travail et structures SIAE.

# Tes Directives :
1. **Évaluation pour Réponse Directe** :
   - Inspecte l'historique et la dernière question dans le contexte.
   - Si la question de l'utilisateur peut être répondue en utilisant les informations ou analyses d'experts existantes dans le dossier (sans avoir besoin de faire de nouvelles requêtes API ou recherches de terrain), rédige ta réponse complète en français dans le champ `direct_answer`. Ne crée AUCUNE tâche dans `tasks`.
2. **Planification du Swarm** :
   - Si de nouvelles recherches ou analyses sur la Ville analysée sont nécessaires (par exemple lors de la première analyse globale d'une commune, ou pour approfondir un point absent du cache) :
     - Identifie quels experts thématiques doivent être mobilisés.
     - Prune intelligemment les experts inutiles (ex: s'il n'y a pas d'enfants dans le dossier, ne mobilise PAS `education_expert`).
     - Pour chaque expert mobilisé, crée une tâche `ExpertTask` :
       - Spécifie l'expert.
       - Rédige une `task_description` personnalisée et précise (brief de mission) décrivant ce qu'il doit chercher.
       - Associe la ou les Skill Cards correspondantes (ex: `["basic_housing"]`).
3. **Cas de l'Analyse Initiale globale** :
   - Si la dernière question/instruction est de faire une première analyse complète de la ville (ex: "Analyse Marseille"), mobilise par défaut tous les experts pertinents au regard du profil (ex: logement, mobilité, intégration sociale par défaut ; emploi si adultes ; éducation si enfants ; santé si besoin de santé exprimé). Rédige une mission d'analyse globale pour chacun.
"""

ts_agent = Agent(
    get_model("ts_agent"),
    model_settings=get_model_settings("ts_agent"),
    deps_type=ODISDeps,
    output_type=SwarmPlan
)

@ts_agent.system_prompt
async def ts_agent_instructions(ctx: RunContext[ODISDeps]) -> str:
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "ts_agent")
    
    execution_mode = ctx.deps.state.execution_mode
    if execution_mode == "full_analysis":
        mode_instruction = (
            "CRITICAL WARNING: CURRENT MODE IS 'full_analysis' (Initial global city analysis).\n"
            "YOU ARE STRICTLY FORBIDDEN TO GENERATE A 'direct_answer'.\n"
            "You MUST plan parallel expert tasks in the 'tasks' list field to analyze the selected city.\n"
            "Leave the 'direct_answer' field set to null / None. Do NOT attempt to synthesize or answer directly yourself."
        )
    else:
        mode_instruction = (
            "CURRENT MODE IS 'specific_ask' (Follow-up specific question).\n"
            "If the user's question can be fully and accurately answered using the existing expert reports and data already present in the context, formulate your final answer in French in the 'direct_answer' field and leave the 'tasks' list empty.\n"
            "Otherwise, if new expert research is required, plan the tasks in the 'tasks' list and leave 'direct_answer' set to null / None."
        )
        
    return TS_AGENT_SYSTEM_PROMPT.format(
        DATA_CONTEXT=data_context,
        EXECUTION_MODE_INSTRUCTION=mode_instruction
    )
