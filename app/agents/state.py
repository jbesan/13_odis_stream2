import json
import logging
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, ConfigDict, model_validator
from google import genai
from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem

logger = logging.getLogger(__name__)


class UsageStats(BaseModel):
    """Cumulative usage statistics for the graph."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    breakdown: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict
    )  # {node_name: {model_id, in_tokens, out_tokens, cost, ...}}

    model_config = ConfigDict(revalidate_instances="never")

    @model_validator(mode="before")
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, "model_dump") else data.__dict__
        return data

    def merge(self, other: "UsageStats") -> None:
        """Merges other usage statistics into this instance."""
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.cost_usd += other.cost_usd
        if other.breakdown:
            for k, v in other.breakdown.items():
                self.breakdown[k] = v


def compute_criteria_hash(criteria: SearchCriterias) -> str:
    """Helper to compute a stable hash for search criteria."""
    if not criteria:
        return ""
    return criteria.compute_hash()


@dataclass
class ExpertList:
    """DTO for the Spreading pattern to route to parallel experts."""

    experts: list[str]


@dataclass
class GraphState:
    """Global Graph State for the pydantic-graph MapReduce pipeline."""

    search_criteria: SearchCriterias = field(default_factory=SearchCriterias)
    search_results: Optional[SearchResultsData] = None
    focus_city: Optional[CommuneResult] = None
    criteria_hash: Optional[str] = None
    execution_mode: Literal["full_analysis", "specific_ask"] = "full_analysis"
    odis_brief: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    interaction_id: str = "unknown"
    username: str = "unknown"
    usage: UsageStats = field(default_factory=UsageStats)
    active_skills: List[str] = field(default_factory=list)
    expert_tasks: Dict[str, str] = field(
        default_factory=dict
    )  # Maps expert domain -> tailored task/mission
    expert_skill_instructions: Dict[str, str] = field(
        default_factory=dict
    )  # Maps expert domain -> skill cards instructions

    def __post_init__(self):
        # Sync briefing from criteria if not explicitly set
        if (
            not self.odis_brief
            and self.search_criteria
            and self.search_criteria.odis_brief
        ):
            self.odis_brief = self.search_criteria.odis_brief

        # Convert string focus_city to CommuneResult for robustness
        if isinstance(self.focus_city, str):
            from core.models import CommuneResult

            self.focus_city = CommuneResult(name=self.focus_city, codgeo="")


@dataclass
class ODISDeps:
    state: GraphState  # Shared States/Data
    client: genai.Client | None = None  # Shared Client

    # Allow arbitrary types for genai.Client
    class Meta:
        arbitrary_types_allowed = True


class ODISContextBuilder:
    """
    Centralized service for building LLM-ready context blocks for all ODIS sub-agents.

    This class enforces an allow-list per agent and uses Pydantic field descriptions
    as human-readable JSON keys to maximize LLM readability and minimize token usage.

    All public methods return a JSON string ready to be injected into a prompt.
    """

    # -------------------------------------------------------------------------
    # PUBLIC ENTRY POINT
    # -------------------------------------------------------------------------

    @classmethod
    def agent_context(cls, state: "GraphState", agent_name: str) -> str:
        """
        Assembles the full dynamic context block for a given agent.

        Args:
            state: The current GraphState.
            agent_name: One of 'synthesizer', 'refiner', 'ts_agent', 'job_hunter',
                        'housing_expert', 'mobility_expert', 'healthcare_expert',
                        'education_expert', 'social_integration_expert', 'interviewer', 'router', 'job_curator'.

        Returns:
            A formatted JSON string ready to inject into a system prompt.
        """
        visibility_key = f"agent_{agent_name}"

        # 1. Resolve raw Pydantic instances from GraphState
        criteria = state.search_criteria

        focus_city = None
        if state.focus_city and state.search_results:
            focus_city = state.search_results.get_by_code(state.focus_city.codgeo)

        current_geo = state.search_results.current_geo if state.search_results else None
        commune_pressentie = (
            state.search_results.commune_pressentie if state.search_results else None
        )

        ctx = {}

        # 2. Build filtered contexts using _auto_build_context
        if state.odis_brief and agent_name != "interviewer":
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief

        if agent_name == "synthesizer":
            if criteria and criteria.notes_qualitatives:
                ctx["Notes qualitatives"] = criteria.notes_qualitatives

        if criteria:
            key = (
                "Critères identifiés"
                if agent_name == "interviewer"
                else "Critères de recherche"
            )
            ctx[key] = cls._auto_build_context(criteria, visibility_key)

        if focus_city:
            ctx["Ville analysée"] = cls._auto_build_context(focus_city, visibility_key)
        elif state.focus_city:
            ctx["Ville analysée"] = cls._auto_build_context(
                state.focus_city, visibility_key
            )

        current_geo_field = SearchResultsData.model_fields.get("current_geo")
        if current_geo and current_geo_field:
            extra = current_geo_field.json_schema_extra or {}
            visibility = extra.get("odis_visibility", [])
            if visibility_key in visibility or "all" in visibility:
                ctx["Ville actuelle (référence)"] = cls._auto_build_context(
                    current_geo, visibility_key
                )

        if commune_pressentie and (
            not focus_city or commune_pressentie.codgeo != focus_city.codgeo
        ):
            ctx["Commune pressentie (pour comparaison)"] = cls._auto_build_context(
                commune_pressentie, visibility_key
            )

        # 3. Handle specific collections
        results_field = SearchResultsData.model_fields.get("results")
        if results_field and state.search_results and state.search_results.results:
            extra = results_field.json_schema_extra or {}
            visibility = extra.get("odis_visibility", [])
            if visibility_key in visibility or "all" in visibility:
                ctx["Top 5 communes identifiées (Détails métriques)"] = [
                    {"Rang": i + 1, **cls._auto_build_context(r, visibility_key)}
                    for i, r in enumerate(state.search_results.results[:5])
                ]

        # 4. Handle Router / TS_AGENT specific target city name
        if agent_name in ("router", "ts_agent"):
            if state.focus_city:
                ctx["Ville cible"] = (
                    f"{state.focus_city.name} ({state.focus_city.codgeo})"
                )
            else:
                ctx["Ville cible"] = "Non définie"

        # 5. Handle conversation messages
        if state.messages:
            if agent_name == "refiner":
                ctx["Historique récent"] = state.messages[-5:]
            elif agent_name == "interviewer":
                ctx["Dernier message utilisateur"] = state.messages[-1].get(
                    "content", ""
                )
            elif agent_name != "job_curator":
                ctx["Dernière question"] = state.messages[-1].get("content", "")

        result = json.dumps(ctx, ensure_ascii=False, indent=2)
        logger.debug(f"[CTX] {agent_name} context assembled ({len(result)} chars)")
        return result

    @classmethod
    def _auto_build_context(cls, model: Any, visibility_key: str) -> Any:
        """
        Recursively builds context, filtering Pydantic models by visibility.
        Handles dicts, lists, Pydantic BaseModels, and specific special types.
        """
        if model is None:
            return None

        # 1. Special Case: CriteriaItem (Simplify to Label for LLM)
        if isinstance(model, CriteriaItem):
            return model.label

        # 2. Special Case: CommuneScoreDetail (Compact representation for agents)
        if model.__class__.__name__ == "CommuneScoreDetail":
            if visibility_key.startswith("agent_"):
                label = getattr(model, "label", "N/A")
                vkpi = getattr(model, "valeur_kpi", None)
                unit = getattr(model, "unit", "")
                score = getattr(model, "score_normalise", 0.0)
                weight = getattr(model, "relative_weight", 0.0)

                if vkpi is not None:
                    unit_clean = unit.strip()
                    kpi_str = f"{vkpi} {unit_clean}" if unit_clean else f"{vkpi}"
                else:
                    kpi_str = "N/A"
                return f"{label}: {kpi_str}, score: {round(float(score), 2)}, poids relatif: {weight}%"
            # For non-agent visibility (like UI/PDF), fall through to normal recursion

        # 3. Special Case: AssociationDetail (Compact representation for agents)
        if model.__class__.__name__ == "AssociationDetail":
            if visibility_key.startswith("agent_"):
                asso_id = getattr(model, "id", "")
                name = getattr(model, "name", "")
                desc = getattr(model, "description", "") or ""
                return f"{asso_id} | {name} | {desc}"
            # For non-agent visibility, fall through to normal recursion

        # 4. Handle Pydantic BaseModel
        if isinstance(model, BaseModel):
            ctx = {}
            for name, field in model.__class__.model_fields.items():
                extra = field.json_schema_extra
                if not isinstance(extra, dict):
                    continue

                visibility = extra.get("odis_visibility", [])
                if visibility_key not in visibility and "all" not in visibility:
                    continue

                val = getattr(model, name)
                label = field.description or name

                if val is None:
                    continue

                ctx[label] = cls._auto_build_context(val, visibility_key)
            return ctx

        # 5. Handle List
        if isinstance(model, list):
            return [cls._auto_build_context(item, visibility_key) for item in model]

        # 6. Handle Dict
        if isinstance(model, dict):
            return {
                k: cls._auto_build_context(v, visibility_key) for k, v in model.items()
            }

        return model

    @classmethod
    def _process_value(cls, val: Any, visibility_key: str) -> Any:
        """Redirects to _auto_build_context for backward compatibility and testing."""
        return cls._auto_build_context(val, visibility_key)
