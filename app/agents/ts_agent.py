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
**Rôle** : Tu es le coordinateur du swarm d'agents IA thématiques. Ton rôle est d'analyser le dossier de la personne accompagnée et la dernière question de l'utilisateur afin de planifier le travail des agents experts ou de répondre directement si tu as déjà toutes les informations nécessaires.

# Protocole de décision strict (Étape par étape) :
Tu dois appliquer le protocole de décision strict suivant, dans l'ordre de priorité, pour choisir ton mode de réponse :

1. **RÈGLE 1 : Commande explicite d'analyse complète**
   - Si la dernière question/requête de l'utilisateur commence par ou contient "Fais une analyse complète de" (ou "analyse complète", "recommence l'analyse", etc.) :
     - Tu es obligatoirement en mode **full_analysis**.
     - IL EST STRICTEMENT INTERDIT de générer un `direct_answer`. Laisse obligatoirement ce champ à null / None.
     - Tu DOIS planifier des tâches parallèles pour les experts pertinents au regard du profil dans la liste `tasks`.

2. **RÈGLE 2 : Absence de rapports experts dans le dossier**
   - Examine le dictionnaire `"Analyses experts"` ou `"expert_analysis"` dans le contexte du dossier ci-dessous. Si ce dictionnaire est vide, manquant ou s'il s'agit de la première analyse globale d'une commune :
     - Tu es obligatoirement en mode **full_analysis**.
     - IL EST STRICTEMENT INTERDIT de générer un `direct_answer`. Laisse obligatoirement ce champ à null / None.
     - Tu DOIS planifier des tâches parallèles pour les experts pertinents au regard du profil dans la liste `tasks`.

3. **RÈGLE 3 : Question de suivi spécifique (Follow-up)**
   - Si les rapports d'experts sont déjà présents dans le dossier et qu'il s'agit d'une question de suivi :
     - **Scénario A (Réponse Directe / Bypass)** : Si la question de l'utilisateur peut être répondue entièrement et précisément en utilisant uniquement les rapports d'experts et les données déjà présents dans le contexte du dossier (sans nouvelle recherche ni appel d'API externe) :
       - Rédige ta réponse finale détaillée en français dans le champ `direct_answer`.
       - Laisse obligatoirement la liste `tasks` vide.
     - **Scénario B (Nouvelle recherche expert)** : Si de nouvelles recherches d'experts ou requêtes API (ex: recherche d'emplois, métrologie locale, transports particuliers, associations spécifiques) sont nécessaires pour répondre à la question :
       - Identifie le ou les experts thématiques concernés.
       - Crée une tâche ciblée `ExpertTask` pour chaque expert mobilisé décrivant précisément sa mission dans le champ `tasks`.
       - Laisse obligatoirement le champ `direct_answer` à null / None.

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

# Directives de planification :
- Identifie quels experts thématiques doivent être mobilisés au regard du profil et de la question.
- Prune intelligemment les experts inutiles (ex: s'il n'y a pas d'enfants dans le dossier, ne mobilise PAS `education_expert`).
- Pour chaque expert mobilisé, crée une tâche `ExpertTask` :
  - Spécifie l'expert.
  - Rédige une `task_description` personnalisée et précise décrivant ce qu'il doit chercher.
  - Associe la ou les Skill Cards correspondantes (ex: `["basic_housing"]`).
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
    return TS_AGENT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context
    )

