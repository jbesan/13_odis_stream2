"""Coordinator Agent (TS Agent) for ODIS AI Swarm.

Responsible for analyzing user requests, determining the swarm execution mode,
and planning tasks assigned to domain experts with authorized capabilities.
"""

import logging
from typing import Any, Final, List, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator
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

# Backward compatibility mapping for legacy skill card references.
# Deprecated: use CapabilityRegistry instead.
SKILL_CARD_EXPERT: Final[dict[str, ExpertName]] = {
    "education_full_analysis": "education_expert",
    "healthcare_full_analysis": "healthcare_expert",
    "housing_full_analysis": "housing_expert",
    "job_extend_job_search": "job_hunter",
    "job_fetch_specific_offer": "job_hunter",
    "job_full_analysis": "job_hunter",
    "mobility_full_analysis": "mobility_expert",
    "social_full_analysis": "social_integration_expert",
    "education_adequation_territoire": "education_expert",
    "healthcare_adequation_territoire": "healthcare_expert",
    "housing_adequation_territoire": "housing_expert",
    "mobility_adequation_territoire": "mobility_expert",
    "social_adequation_territoire": "social_integration_expert",
    "job_adequation_territoire": "job_hunter",
}


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
        validation_alias=AliasChoices("capabilities", "skill_cards"),
        description=(
            "Liste des identifiants de capabilities à charger pour cette mission (ex: ['housing_adequation_territoire']). "
            "Au moins une capability obligatoire."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_skill_cards_to_capabilities(cls, data: Any) -> Any:
        """Normalizes backward-compatible 'skill_cards' parameter to 'capabilities'.

        Handles dicts and attribute-bearing objects, string-to-list coercion,
        and fallback from empty/None capabilities to skill_cards.

        Args:
            data: Raw input data (dict or object) before validation.

        Returns:
            Normalized dictionary or object with 'capabilities' populated.
        """
        if isinstance(data, dict):
            caps = data.get("capabilities")
            skills = data.get("skill_cards")
            if (
                caps is None or (isinstance(caps, (list, tuple)) and len(caps) == 0)
            ) and skills is not None:
                caps = skills
            if isinstance(caps, str):
                caps = [caps]
            if caps is not None:
                data["capabilities"] = caps
        elif hasattr(data, "__dict__") and not isinstance(data, type):
            caps = getattr(data, "capabilities", None)
            skills = getattr(data, "skill_cards", None)
            if (
                caps is None or (isinstance(caps, (list, tuple)) and len(caps) == 0)
            ) and skills is not None:
                caps = skills
            if isinstance(caps, str):
                caps = [caps]
            return {
                "expert": getattr(data, "expert", None),
                "task_description": getattr(data, "task_description", None),
                "capabilities": caps,
            }
        return data

    @property
    def skill_cards(self) -> List[str]:
        """Backward-compatible property returning capabilities.

        Returns:
            List of capability identifiers.
        """
        return self.capabilities

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
                canonical_id = registry.resolve_capability_id(cleaned_id)
                spec = registry.get(canonical_id)
            except CapabilityNotFoundError:
                raise ValueError(f"Capability inconnue : {cleaned_id}")

            if self.expert not in spec.experts:
                raise ValueError(
                    f"La capability '{cleaned_id}' n'est pas autorisée pour l'expert '{self.expert}' "
                    f"(experts autorisés : {sorted(spec.experts)})"
                )
            normalized_caps.append(canonical_id)

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

        capability_registry = get_default_capability_registry()
        for task in self.tasks:
            if not task.capabilities:
                raise ValueError(
                    f"Chaque tâche d'expert doit déclarer au moins une capability (expert: {task.expert})"
                )
            normalized_caps: List[str] = []
            for cap_id in task.capabilities:
                try:
                    canonical_id = capability_registry.resolve_capability_id(cap_id)
                    spec = capability_registry.get(canonical_id)
                except CapabilityNotFoundError:
                    raise ValueError(f"Capability inconnue : {cap_id}")
                if task.expert not in spec.experts:
                    raise ValueError(
                        f"La capability '{cap_id}' n'est pas autorisée pour l'expert '{task.expert}' "
                        f"(experts autorisés : {sorted(spec.experts)})"
                    )
                normalized_caps.append(canonical_id)
            task.capabilities = list(dict.fromkeys(normalized_caps))

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
