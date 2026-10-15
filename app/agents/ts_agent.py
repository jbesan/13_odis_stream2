"""Coordinator Agent (TS Agent) for ODIS AI Swarm.

Responsible for analyzing user requests, determining the swarm execution mode,
and planning tasks assigned to domain experts with authorized capabilities.
"""

import logging
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_ai import Agent, RunContext

from agents.agent_config import create_agent, get_swarm_boilerplate
from agents.capability_registry import (
    CapabilityNotFoundError,
    CapabilityRegistry,
    ExpertName,
    get_default_capability_registry,
)
from agents.state import ODISContextBuilder, ODISDeps

logger = logging.getLogger("ts_agent")

class ExpertTask(BaseModel):
    """Specification of an expert task in the swarm execution plan."""

    model_config = ConfigDict(populate_by_name=True, from_attributes=True)

    expert: ExpertName = Field(..., description="L'expert thématique ciblé.")
    task_description: str = Field(
        ...,
        description=(
            "Description très spécifique et ciblée de la mission de l'expert pour cette étape. "
            "Exemple : 'Recherche les structures CADA de la ville et les associations locales pour une personne réfugiée seule.'"
        ),
    )
    capabilities: List[str] = Field(
        ...,
        min_length=1,
        description=(
            "Liste des identifiants de capabilities à charger pour cette mission (ex: ['housing_adequation_territoire']). "
            "Au moins une capability obligatoire."
        ),
    )

    @field_validator("capabilities", mode="before")
    @classmethod
    def _coerce_capabilities_list(cls, v: object) -> object:
        """Coerces single string capability input into a list."""
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        return v

    @model_validator(mode="after")
    def validate_task_capabilities(self) -> "ExpertTask":
        """Validates that all declared capabilities exist in CapabilityRegistry and belong to the expert.

        Returns:
            The validated ExpertTask instance with canonical capability identifiers.

        Raises:
            ValueError: If capabilities list is empty, contains unknown capabilities,
                or contains capabilities not authorized for the specified expert.
        """
        if not self.capabilities:
            raise ValueError("Au moins une capability est requise par tâche d'expert.")

        registry = get_default_capability_registry()
        normalized_caps: List[str] = []
        for cap_id in self.capabilities:
            if not isinstance(cap_id, str) or not cap_id.strip():
                raise ValueError(
                    "Identifiant de capability invalide (doit être une chaîne non vide)."
                )
            cleaned_id = cap_id.strip()
            try:
                spec = registry.get(cleaned_id)
            except CapabilityNotFoundError as exc:
                raise ValueError(f"Capability inconnue : {cleaned_id}") from exc

            if self.expert not in spec.experts:
                raise ValueError(
                    f"La capability '{cleaned_id}' n'est pas autorisée pour l'expert '{self.expert}' "
                    f"(experts autorisés : {sorted(spec.experts)})"
                )
            normalized_caps.append(spec.id)

        self.capabilities = list(dict.fromkeys(normalized_caps))
        return self


class SwarmPlan(BaseModel):
    """Execution plan produced by TS coordinator agent."""

    model_config = ConfigDict(populate_by_name=True)

    swarm_mode: Literal["full_analysis", "specific_ask", "direct_answer"] = Field(
        ...,
        description="Le mode de fonctionnement du swarm déterminé par le protocole de décision.",
    )
    direct_answer: Optional[str] = Field(
        None,
        description=(
            "Si la dernière question de l'utilisateur peut être résolue avec les données existantes. "
            "Requis uniquement si swarm_mode est 'direct_answer'. Laisser obligatoirement vide (null/None) sinon."
        ),
    )
    tasks: List[ExpertTask] = Field(
        default_factory=list,
        description=(
            "Liste des tâches à exécuter en parallèle par les agents experts. "
            "Requis uniquement si swarm_mode est 'full_analysis' ou 'specific_ask'. Laisser vide si swarm_mode est 'direct_answer'."
        ),
    )

    @model_validator(mode="after")
    def validate_execution_contract(self) -> "SwarmPlan":
        """Reject plans that cannot be executed safely and unambiguously.

        Returns:
            The validated SwarmPlan instance.

        Raises:
            ValueError: If execution contract conditions are violated.
        """
        if self.swarm_mode == "direct_answer":
            if not self.direct_answer or not self.direct_answer.strip():
                raise ValueError(
                    "direct_answer doit être renseignée pour le mode direct_answer"
                )
            if self.tasks:
                raise ValueError(
                    "direct_answer ne peut pas contenir de tâches d'experts"
                )
            return self

        if self.direct_answer is not None:
            raise ValueError(
                "direct_answer doit être null pour un mode d'analyse d'experts"
            )
        if not self.tasks:
            raise ValueError("Un mode d'analyse d'experts requiert au moins une tâche")

        experts = [task.expert for task in self.tasks]
        if len(experts) != len(set(experts)):
            raise ValueError("Un expert ne peut apparaître qu'une seule fois par plan")

        return self


def format_capabilities_catalog(
    registry: Optional[CapabilityRegistry] = None,
) -> str:
    """Formats registered capabilities into a deterministic markdown table for prompt injection.

    Args:
        registry: Optional CapabilityRegistry instance. Defaults to get_default_capability_registry().

    Returns:
        Formatted markdown table string with ID, Name, Experts, and Description.
    """
    if registry is None:
        registry = get_default_capability_registry()

    table_lines = [
        "| ID | Nom | Expert(s) | Description |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for spec in registry.list_all():
        experts_str = ", ".join(sorted(spec.experts))
        table_lines.append(
            f"| `{spec.id}` | {spec.name} | {experts_str} | {spec.description} |"
        )
    return "\n".join(table_lines)


TS_AGENT_SYSTEM_PROMPT = """
{SWARM_BOILERPLATE}
**Rôle** : Tu es le COORDINATEUR du swarm d'agents IA thématiques. Ton rôle est d'analyser le dossier de la personne accompagnée et la dernière question de l'utilisateur afin de PLANIFIER le travail des agents experts mais pas de faire le travail toi-même. 
Dans de rares cas tu pourras répondre directement (voir protocole ci-dessous).

# Protocole de décision strict (Étape par étape) :
- SI la dernière question/requête de l'utilisateur commence par ou contient "Fais une analyse complète de" (ou "analyse complète", "recommence l'analyse", etc.) :
    - Détermine le `swarm_mode` comme `full_analysis`.
    - Planifie le travail du Swarm d'experts (Voir directives de planifications).
    - Laisse `direct_answer` vide (null).
- SINON SI de nouvelles recherches d'experts sont nécessaires pour répondre de l'utilisateur à la question ou approfondir une analyse :
    - Détermine le `swarm_mode` comme `specific_ask`.
    - Planifie le travail du Swarm d'experts (Voir directives de planifications).
    - Laisse `direct_answer` vide (null).
- SINON, SI tu es ABSOLUMENT CERTAIN que la question de l'utilisateur peut être répondue entièrement et précisément en utilisant uniquement les rapports d'experts et les données déjà présents dans le contexte du dossier (sans nouvelle recherche ni appel d'API externe) :
    - Détermine le `swarm_mode` comme `direct_answer`.
    - Rédige ta réponse finale détaillée en français dans le champ `direct_answer`.
    - Laisse obligatoirement la liste `tasks` vide.


# Directives de planification du swarm d'experts:
1. Identifie quels experts thématiques doivent être mobilisés au regard du profil et de la question (ex: s'il n'y a pas d'enfants dans le dossier, ne mobilise PAS `education_expert`).
2. Identifie les capabilities du catalogue ci-dessous qui leur seront utiles.
3. Pour chaque expert mobilisé, crée une tâche `ExpertTask` :
  - Spécifie l'expert (`expert`).
  - Rédige une `task_description` personnalisée et précise décrivant ce qu'il doit chercher.
  - Associe obligatoirement la ou les capabilities correspondantes dans `capabilities` (au moins une capability obligatoire par tâche).

# Répertoire des Capabilities disponibles :
{CAPABILITIES_CATALOG}

# Contexte du dossier :
```json
{DATA_CONTEXT}
```
"""

ts_agent: Agent[ODISDeps, SwarmPlan] = create_agent(
    "ts_agent",
    deps_type=ODISDeps,
    output_type=SwarmPlan,
)


@ts_agent.system_prompt
async def ts_agent_instructions(ctx: RunContext[ODISDeps]) -> str:
    """Builds the dynamic system prompt for the TS coordinator agent.

    Args:
        ctx: Pydantic AI run context providing dependencies and graph state.

    Returns:
        Formatted system prompt string.
    """
    data_context = ODISContextBuilder.agent_context(ctx.deps.state, "ts_agent")
    boilerplate = get_swarm_boilerplate("coordinator")
    catalog_str = format_capabilities_catalog()

    return TS_AGENT_SYSTEM_PROMPT.format(
        SWARM_BOILERPLATE=boilerplate,
        DATA_CONTEXT=data_context,
        CAPABILITIES_CATALOG=catalog_str,
    )


# Eagerly load and validate default capability registry at boot (fail-closed)
get_default_capability_registry()

