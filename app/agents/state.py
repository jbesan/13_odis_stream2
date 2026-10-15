import json
import logging
from typing import List, Dict, Any, Optional, Literal, NamedTuple, Sequence
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, ConfigDict, model_validator
from google import genai
from core.models import SearchCriterias, SearchResultsData, CommuneResult
from core.evidence import DomainArtifact as EvidenceDomainArtifact, WebSearchBatchResult
from agents.context_blocks import (
    assemble_context_blocks,
    get_default_context_registry,
)
from agents.context_renderer import (
    auto_build_context,
    dump_context,
    format_association,
    format_commune_identity,
    format_criteria,
    format_criteria_value,
    format_detail_item,
    format_inclusion_services,
    format_job_offer,
    format_model,
    resolve_focus_city,
)
from services.ai_pricing import (
    estimate_google_grounding_cost_eur,
    estimate_places_cost_eur,
)

logger = logging.getLogger(__name__)


class UsageStats(BaseModel):
    """Cumulative usage statistics for the graph."""

    input_tokens: int = 0
    input_tokens_new: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    cost_eur: float = 0.0
    token_cost_eur: float = 0.0
    requests: int = 0
    tool_calls: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_hit_ratio: float = 0.0
    grounding_queries: int = 0
    grounding_cost_eur: float = 0.0
    places_requests: int = 0
    places_cost_eur: float = 0.0
    eur_priced: bool = True
    unpriced_model_requests: int = 0
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
        self.input_tokens_new += other.input_tokens_new or max(
            other.input_tokens - other.cache_read_tokens, 0
        )
        self.output_tokens += other.output_tokens
        self.total_tokens += other.total_tokens
        self.cost_usd += other.cost_usd
        self.token_cost_eur += other.token_cost_eur
        self.requests += other.requests
        self.tool_calls += other.tool_calls
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.grounding_queries += other.grounding_queries
        self.places_requests += other.places_requests
        self.eur_priced = self.eur_priced and other.eur_priced
        self.unpriced_model_requests += other.unpriced_model_requests
        # Grounding and Places free tiers are aggregate counters. Recalculate
        # them after merging parallel expert runs instead of charging each
        # expert as if it owned a separate monthly quota.
        self.grounding_cost_eur = estimate_google_grounding_cost_eur(
            self.grounding_queries
        )
        self.places_cost_eur = estimate_places_cost_eur(self.places_requests)
        self.cost_eur = (
            self.token_cost_eur + self.grounding_cost_eur + self.places_cost_eur
            if self.eur_priced
            else 0.0
        )
        self.cache_hit_ratio = (
            self.cache_read_tokens / self.input_tokens if self.input_tokens else 0.0
        )
        if other.breakdown:
            for k, v in other.breakdown.items():
                self.breakdown[k] = v


def compute_criteria_hash(criteria: SearchCriterias) -> str:
    """Helper to compute a stable hash for search criteria."""
    if not criteria:
        return ""
    return criteria.compute_hash()


@dataclass
class AgentArtifact:
    domain: str
    result: str  # Markdown formatted string with the result
    usage: UsageStats = field(default_factory=UsageStats)
    evidence_artifact: EvidenceDomainArtifact | None = None
    sources: list[dict[str, Any]] = field(default_factory=list)


# Graph transport alias retained for Phase 1 compatibility.
DomainArtifact = AgentArtifact


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
    execution_mode: Literal["full_analysis", "specific_ask", "direct_answer"] = (
        "full_analysis"
    )
    odis_brief: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    interaction_id: str = "unknown"
    username: str = "unknown"
    organization_id: str = "unknown"
    run_id: str = "unknown"
    run_attempt: int = 1
    run_deadline_at: float | None = None
    usage: UsageStats = field(default_factory=UsageStats)
    expert_tasks: Dict[str, str] = field(
        default_factory=dict
    )  # Maps expert domain -> tailored task/mission
    expert_capabilities: Dict[str, List[str]] = field(
        default_factory=dict
    )  # Maps expert domain -> list of capability IDs for run

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
            self.focus_city = CommuneResult(name=self.focus_city, codgeo="")


@dataclass
class ODISDeps:
    state: GraphState  # Shared States/Data
    client: genai.Client | None = None  # Shared Client
    # Direct Gemini web-search tool usage is collected per expert run.  The
    # graph merges it after the PydanticAI run completes, so parallel workers
    # never mutate the aggregate UsageStats concurrently.
    web_search_usage: Dict[str, UsageStats] = field(default_factory=dict)
    web_search_call_counts: Dict[str, int] = field(default_factory=dict)
    web_search_results: Dict[str, WebSearchBatchResult] = field(default_factory=dict)

    # Allow arbitrary types for genai.Client
    class Meta:
        arbitrary_types_allowed = True


class ExpertPromptContexts(NamedTuple):
    """Structured context components for legacy expert prompts to maximize readability and prefix caching."""

    briefing: str
    criteria: str
    commune: str
    specific: str


class ODISContextBuilder:
    """
    Centralized service for building LLM-ready context blocks for all ODIS sub-agents.

    Uses an Object-Level Domain Routing model to assemble clean, token-efficient
    envelopes for each agent without fragile attribute-level field annotations.

    All public methods return a JSON string ready to be injected into a prompt.
    """

    DOMAIN_EXPERTS = frozenset(
        {
            "job_hunter",
            "housing_expert",
            "mobility_expert",
            "healthcare_expert",
            "education_expert",
            "social_integration_expert",
        }
    )

    @classmethod
    def _format_criteria_value(cls, val: Any) -> Any:
        """Helper to recursively simplify CriteriaItem instances into labels."""
        return format_criteria_value(val)

    @classmethod
    def _format_criteria(cls, criteria: SearchCriterias) -> Dict[str, Any]:
        """Serializes SearchCriterias with readable labels, excluding internal flags and odis_brief."""
        return format_criteria(criteria)

    @classmethod
    def _format_commune_identity(cls, commune: CommuneResult) -> Dict[str, Any]:
        """Formats the base commune identification header."""
        return format_commune_identity(commune)

    @classmethod
    def _format_association(cls, item: Any) -> str:
        """Formats association item as 'ID | Nom | Description'."""
        return format_association(item)

    @classmethod
    def _format_job_offer(cls, item: Any) -> str:
        """Formats job offer as 'ID | Titre chez Entreprise (Contrat) [ROME: Code] - Description'."""
        return format_job_offer(item)

    @classmethod
    def _format_inclusion_services(
        cls, services_dict: Dict[str, List[Any]]
    ) -> Dict[str, List[str]]:
        """Formats inclusion services grouped by thématique -> list of unique structures with distance/commune."""
        return format_inclusion_services(services_dict)

    @classmethod
    def _format_detail_item(cls, item: Any) -> Any:
        """Formats individual detail items compactly."""
        return format_detail_item(item)

    @classmethod
    def _format_model(cls, model: BaseModel) -> Dict[str, Any]:
        """Formats a domain metric sub-model with human-readable descriptions and compact detail items."""
        return format_model(model)

    @classmethod
    def _focus_city(cls, state: "GraphState") -> CommuneResult | None:
        """Resolve the complete focus-city record used by expert prompts."""
        return resolve_focus_city(state)

    @classmethod
    def _dump_context(cls, context: Dict[str, Any], *, compact: bool) -> str:
        """Serialize context deterministically while preserving semantic order."""
        return dump_context(context, compact=compact)

    @classmethod
    def expert_prompt_contexts(
        cls,
        state: "GraphState",
        agent_name: str,
        capability_ids: Optional[Sequence[str]] = None,
    ) -> ExpertPromptContexts:
        """Return the stable shared prefix components and the expert-specific context.

        The shared components (briefing narrative, criteria JSON, and commune identity JSON)
        are assembled identically for every expert so Gemini can recognize the same
        prompt prefix across parallel expert runs. The expert-specific block is resolved
        dynamically from the contexts declared in active capabilities, deduplicated
        in stable order.
        """
        if agent_name not in cls.DOMAIN_EXPERTS:
            raise ValueError(f"{agent_name!r} is not a domain expert")

        registry = get_default_context_registry()
        focus_city = cls._focus_city(state)

        briefing_text = registry.get("core.briefing").render_raw(state, focus_city)

        criteria_dict = registry.get("core.criteria").render_raw(state, focus_city)
        criteria_json = cls._dump_context(criteria_dict, compact=True)

        commune_dict = registry.get("core.commune_identity").render_raw(
            state, focus_city
        )
        commune_json = cls._dump_context(commune_dict, compact=True)

        from agents.capability_registry import (
            VALID_EXPERTS,
            get_default_capability_registry,
        )

        cap_registry = get_default_capability_registry()

        # Resolve active capability identifiers for this expert run
        active_caps: Sequence[Any] = capability_ids or ()
        if not active_caps and hasattr(state, "expert_capabilities"):
            active_caps = state.expert_capabilities.get(agent_name, ())

        resolved_cap_ids: List[str] = []
        for item in active_caps:
            if isinstance(item, str):
                resolved_cap_ids.append(item)
            elif hasattr(item, "id") and getattr(item, "id"):
                resolved_cap_ids.append(str(item.id))

        if not resolved_cap_ids and agent_name in VALID_EXPERTS:
            resolved_cap_ids = [cap_registry.get_base_capability_id(agent_name)]

        # Collect and deduplicate context blocks declared by active capabilities
        context_ids: List[str] = []
        for cid in resolved_cap_ids:
            spec = cap_registry.get(cid)
            for ctx_block in spec.contexts:
                if ctx_block not in context_ids:
                    context_ids.append(ctx_block)

        specific_dict = assemble_context_blocks(
            context_ids, state, focus_city=focus_city, registry=registry
        )
        specific_json = cls._dump_context(specific_dict, compact=True)

        return ExpertPromptContexts(
            briefing=briefing_text,
            criteria=criteria_json,
            commune=commune_json,
            specific=specific_json,
        )

    @classmethod
    def agent_context(cls, state: "GraphState", agent_name: str) -> str:
        """
        Assembles the full dynamic context block for a given agent using Object-Level Domain Routing.

        Args:
            state: The current GraphState.
            agent_name: One of 'synthesizer', 'refiner', 'ts_agent', 'job_hunter',
                        'housing_expert', 'mobility_expert', 'healthcare_expert',
                        'education_expert', 'social_integration_expert', 'interviewer', 'router', 'job_curator'.

        Returns:
            A formatted JSON string ready to inject into a system prompt.
        """
        if agent_name in cls.DOMAIN_EXPERTS:
            ctxs = cls.expert_prompt_contexts(state, agent_name)
            merged: Dict[str, Any] = {}
            if state.odis_brief:
                merged["Résumé du dossier (Briefing)"] = ctxs.briefing
            if ctxs.criteria and ctxs.criteria != "{}":
                merged["Critères de recherche"] = json.loads(ctxs.criteria)
            if ctxs.commune and ctxs.commune != "{}":
                commune_data = json.loads(ctxs.commune)
                merged["Commune à analyser"] = commune_data
                merged["Commune analysée (Identité)"] = commune_data
            if ctxs.specific and ctxs.specific != "{}":
                merged.update(json.loads(ctxs.specific))
            return cls._dump_context(merged, compact=False)

        criteria = state.search_criteria
        focus_city = None
        if state.focus_city and state.search_results:
            focus_city = state.search_results.get_by_code(state.focus_city.codgeo)
        if not focus_city:
            focus_city = state.focus_city

        current_geo = state.search_results.current_geo if state.search_results else None
        commune_pressentie = (
            state.search_results.commune_pressentie if state.search_results else None
        )

        ctx = {}

        # 1. Root-Level Briefing (Injected once for all agents except interviewer)
        if state.odis_brief and agent_name != "interviewer":
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief

        # 2. Search Criteria (Universally injected for all agents)
        if criteria:
            crit_key = (
                "Critères identifiés"
                if agent_name == "interviewer"
                else "Critères de recherche"
            )
            ctx[crit_key] = cls._format_criteria(criteria)

        # 3. Target City Data by Agent Role
        if agent_name == "refiner":
            if current_geo:
                ctx["Ville actuelle (référence)"] = cls._format_commune_identity(
                    current_geo
                )
            if state.search_results and state.search_results.results:
                ctx["Top 5 communes identifiées"] = [
                    {
                        "Rang": i + 1,
                        **cls._format_commune_identity(r),
                        **(
                            {
                                "Scores thématiques": {
                                    cat: [cls._format_detail_item(d) for d in details]
                                    for cat, details in r.scores.items()
                                }
                            }
                            if r.scores
                            else {}
                        ),
                    }
                    for i, r in enumerate(state.search_results.results[:5])
                ]
            if commune_pressentie:
                ctx["Commune pressentie (pour comparaison)"] = {
                    **cls._format_commune_identity(commune_pressentie),
                    **(
                        {
                            "Scores thématiques": {
                                cat: [cls._format_detail_item(d) for d in details]
                                for cat, details in commune_pressentie.scores.items()
                            }
                        }
                        if commune_pressentie.scores
                        else {}
                    ),
                }
        elif focus_city:
            # A. Domain Experts: Identity + Dedicated Domain Metrics
            if agent_name == "education_expert":
                ctx["Commune analysée (Identité)"] = cls._format_commune_identity(
                    focus_city
                )
                ctx["Données éducation"] = cls._format_model(focus_city.education)
            elif agent_name == "housing_expert":
                ctx["Commune analysée (Identité)"] = cls._format_commune_identity(
                    focus_city
                )
                ctx["Données logement"] = cls._format_model(focus_city.housing)
            elif agent_name == "healthcare_expert":
                ctx["Commune analysée (Identité)"] = cls._format_commune_identity(
                    focus_city
                )
                ctx["Données santé"] = cls._format_model(focus_city.health)
            elif agent_name == "mobility_expert":
                ctx["Commune analysée (Identité)"] = cls._format_commune_identity(
                    focus_city
                )
                ctx["Données mobilité"] = cls._format_model(focus_city.mobility)
            elif agent_name == "job_hunter":
                ctx["Commune analysée (Identité)"] = cls._format_commune_identity(
                    focus_city
                )
                ctx["Données emploi et formation"] = cls._format_model(
                    focus_city.employment
                )
            elif agent_name == "social_integration_expert":
                ctx["Commune analysée (Identité)"] = cls._format_commune_identity(
                    focus_city
                )
                ctx["Données inclusion"] = cls._format_model(focus_city.inclusion)
                if focus_city.territoire and (
                    focus_city.territoire.ter_insecurite is not None
                    or focus_city.territoire.maire_extreme_droite
                ):
                    ctx["Données territoire (Contexte local)"] = cls._format_model(
                        focus_city.territoire
                    )

            # B. TS Coordinator: Ville cible + High-level Score Overview + Territory flags
            elif agent_name in ("router", "ts_agent"):
                ctx["Ville cible"] = f"{focus_city.name} ({focus_city.codgeo})"
                if focus_city.scores:
                    ctx["Scores thématiques"] = {
                        cat: [cls._format_detail_item(d) for d in details]
                        for cat, details in focus_city.scores.items()
                    }
                if focus_city.territoire:
                    ctx["Données territoire"] = cls._format_model(focus_city.territoire)

            # C. Synthesizer: notes qualitatives if present
            elif agent_name == "synthesizer":
                if criteria and criteria.notes_qualitatives:
                    ctx["Notes qualitatives"] = criteria.notes_qualitatives

        # 4. Message History Handling
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
    def _auto_build_context(cls, model: Any, visibility_key: str = "all") -> Any:
        """Backward-compatible helper redirecting to typed formatting."""
        return auto_build_context(model, visibility_key)

    @classmethod
    def _process_value(cls, val: Any, visibility_key: str = "all") -> Any:
        """Backward-compatible helper redirecting to _format_detail_item."""
        return format_detail_item(val)
