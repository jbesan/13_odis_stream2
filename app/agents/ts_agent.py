import logging
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from .state import GraphState, ODISDeps, ODISContextBuilder
from .agent_config import get_model, get_model_settings, get_swarm_boilerplate

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
{SWARM_BOILERPLATE}
**Rôle** : Tu es le COORDINATEUR du swarm d'agents IA thématiques. Ton rôle est d'analyser le dossier de la personne accompagnée et la dernière question de l'utilisateur afin de PLANIFIER le travail des agents experts mais pas de faire le travail toi-même. 
Dans de rares cas tu pourras répondre directement (voir protocole ci-dessous).

# Protocole de décision strict (Étape par étape) :
- SI la dernière question/requête de l'utilisateur commence par ou contient "Fais une analyse complète de" (ou "analyse complète", "recommence l'analyse", etc.) :
    - Tu es obligatoirement en mode `full_analysis`.
    - Planifie le travail du Swarm d'experts (Voir directives de planifications)
- SINON, SI tu es certain que la question de l'utilisateur peut être répondue entièrement et précisément en utilisant uniquement les rapports d'experts et les données déjà présents dans le contexte du dossier (sans nouvelle recherche ni appel d'API externe) :
    - Tu es obligatoirement en mode `direct_answer`.
    - Rédige ta réponse finale détaillée en français dans le champ `direct_answer`.
    - Laisse obligatoirement la liste `tasks` vide.
- SINON, de nouvelles recherches d'experts sont nécessaires pour répondre à la question
    - Tu es obligatoirement en mode `specific_ask`.
    - Planifie le travail du Swarm d'experts (Voir directives de planifications).

# Directives de planification du swarm d'experts:
1. Identifie quels experts thématiques doivent être mobilisés au regard du profil et de la question (ex: s'il n'y a pas d'enfants dans le dossier, ne mobilise PAS `education_expert`).
2. Identifie les Skill Cards du catalogue ci-dessous qui leur seront utiles.
3. Pour chaque expert mobilisé, crée une tâche `ExpertTask` :
  - Spécifie l'expert.
  - Rédige une `task_description` personnalisée et précise décrivant ce qu'il doit chercher.
  - Associe la ou les Skill Cards correspondantes.

# Répertoire des Skill Cards disponibles :
{SKILLS_CATALOG}

# Contexte du dossier :
```json
{DATA_CONTEXT}
```



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
    boilerplate = get_swarm_boilerplate("coordinator")
    
    from services.knowledge_store import KnowledgeStore
    store = KnowledgeStore()
    all_skills = store.get_all_skills()
    
    # Format as a clean markdown table for the LLM
    table_lines = [
        "| ID | Name | Expert (Domain) | Description |",
        "| :--- | :--- | :--- | :--- |"
    ]
    for skill in all_skills:
        name = skill.get("name", "")
        table_lines.append(f"| `{skill['id']}` | {name} | {skill['domain']} | {skill['description']} |")
    skills_catalog_str = "\n".join(table_lines)

    return TS_AGENT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context,
        SKILLS_CATALOG=skills_catalog_str
    )

