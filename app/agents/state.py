import json
import logging
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, ConfigDict, model_validator
from google import genai
from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem
from core.evidence import DomainArtifact as EvidenceDomainArtifact

logger = logging.getLogger(__name__)


class UsageStats(BaseModel):
    """Cumulative usage statistics for the graph."""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    requests: int = 0
    tool_calls: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cache_hit_ratio: float = 0.0
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
        self.requests += other.requests
        self.tool_calls += other.tool_calls
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_write_tokens += other.cache_write_tokens
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
        if val is None:
            return None
        if val.__class__.__name__ == "CriteriaItem" or isinstance(val, CriteriaItem):
            return getattr(val, "label", str(val))
        if isinstance(val, list):
            return [
                cls._format_criteria_value(item) for item in val if item is not None
            ]
        if isinstance(val, dict):
            return {
                k: cls._format_criteria_value(v)
                for k, v in val.items()
                if v is not None
            }
        if isinstance(val, BaseModel):
            return cls._format_model(val)
        return val

    @classmethod
    def _format_criteria(cls, criteria: SearchCriterias) -> Dict[str, Any]:
        """Serializes SearchCriterias with readable labels, excluding internal flags and odis_brief."""
        exclude_fields = {
            "odis_brief",
            "active_criteria",
            "active_categories",
            "criteria_weights",
            "poids_emploi",
            "poids_logement",
            "poids_education",
            "poids_inclusion",
            "poids_mobilite",
            "poids_sante",
            "poids_territoire",
            "target_population_sigma",
            "org_boosts",
        }
        ctx = {}
        for name, fld in criteria.__class__.model_fields.items():
            if name in exclude_fields:
                continue
            val = getattr(criteria, name)
            if val is None:
                continue
            if isinstance(val, (list, dict)) and len(val) == 0:
                continue

            label = fld.description or name
            ctx[label] = cls._format_criteria_value(val)
        return ctx

    @classmethod
    def _format_commune_identity(cls, commune: CommuneResult) -> Dict[str, Any]:
        """Formats the base commune identification header."""
        return {
            "Code INSEE": commune.codgeo,
            "Nom": commune.name,
            "Population": commune.population,
            "Bassin de vie": commune.name_bdv or commune.codgeo_bdv or "N/A",
            "Score global": int((commune.global_score or 0.0) * 100),
        }

    @classmethod
    def _format_association(cls, item: Any) -> str:
        """Formats association item as 'ID | Nom | Description'."""
        asso_id = (getattr(item, "id", None) or "").strip()
        name = (getattr(item, "name", None) or "").strip()
        desc = (getattr(item, "description", None) or "").strip()
        parts = [p for p in [asso_id, name, desc] if p]
        return " | ".join(parts)

    @classmethod
    def _format_job_offer(cls, item: Any) -> str:
        """Formats job offer as 'ID | Titre chez Entreprise (Contrat) [ROME: Code] - Description'."""
        oid = (getattr(item, "id", None) or "").strip()
        title = (getattr(item, "title", None) or "").strip()
        comp = (getattr(item, "company", None) or "").strip()
        comp_str = f" chez {comp}" if comp else ""
        ctype = (
            getattr(item, "contract_type", None)
            or getattr(item, "contract_label", None)
            or ""
        ).strip()
        ctype_str = f" ({ctype})" if ctype else ""
        rome = (getattr(item, "rome_code", None) or "").strip()
        rome_str = f" [ROME: {rome}]" if rome else ""

        desc = (getattr(item, "description", None) or "").strip()
        if desc:
            desc_clean = " ".join(desc.split())
            if len(desc_clean) > 200:
                desc_clean = desc_clean[:197] + "..."
            desc_str = f" - {desc_clean}"
        else:
            desc_str = ""

        header = f"{title}{comp_str}{ctype_str}{rome_str}"
        if oid:
            return f"{oid} | {header}{desc_str}"
        return f"{header}{desc_str}"

    @classmethod
    def _format_inclusion_services(
        cls, services_dict: Dict[str, List[Any]]
    ) -> Dict[str, List[str]]:
        """Formats inclusion services grouped by thématique -> list of unique structures with distance/commune."""
        result: Dict[str, List[str]] = {}
        for theme, srv_list in services_dict.items():
            if not srv_list:
                continue
            structures: List[str] = []
            seen: set[str] = set()
            for srv in srv_list:
                struct_name = (
                    getattr(srv, "nom_structure", None) or "Structure locale"
                ).strip()
                dist = getattr(srv, "distance_km", None)
                commune = (getattr(srv, "commune_nom", None) or "").strip()

                dist_parts = []
                if dist is not None:
                    dist_parts.append(f"{dist} km")
                if commune:
                    dist_parts.append(commune)

                dist_str = f" ({' - '.join(dist_parts)})" if dist_parts else ""
                struct_key = f"{struct_name}{dist_str}"

                if struct_key not in seen:
                    seen.add(struct_key)
                    structures.append(struct_key)

            if structures:
                result[theme] = structures
        return result

    @classmethod
    def _format_detail_item(cls, item: Any) -> Any:
        """Formats individual detail items compactly."""
        if item is None:
            return None
        cname = item.__class__.__name__
        if cname == "CriteriaItem" or isinstance(item, CriteriaItem):
            return getattr(item, "label", str(item))

        if cname == "AssociationDetail":
            return cls._format_association(item)

        if cname == "JobOfferDetail":
            return cls._format_job_offer(item)

        if cname == "InclusionServiceDetail":
            srv_name = getattr(item, "name", "")
            struct = getattr(item, "nom_structure", "") or ""
            struct_part = f" par {struct}" if struct else ""
            dist = getattr(item, "distance_km", None)
            commune = getattr(item, "commune_nom", "") or ""
            dist_part = (
                f" (à {dist} km{f' - {commune}' if commune else ''})"
                if dist is not None
                else ""
            )
            return f"{srv_name}{struct_part}{dist_part}"

        if cname == "CommuneScoreDetail":
            label = getattr(item, "label", "N/A")
            vkpi = getattr(item, "valeur_kpi", None)
            unit = getattr(item, "unit", "")
            score = getattr(item, "score_normalise", 0.0)
            weight = getattr(item, "relative_weight", 0.0)
            if vkpi is not None:
                unit_clean = unit.strip()
                kpi_str = f"{vkpi} {unit_clean}" if unit_clean else f"{vkpi}"
            else:
                kpi_str = "N/A"
            return f"{label}: {kpi_str}, score: {round(float(score), 2)}, poids relatif: {weight}%"

        if isinstance(item, BaseModel):
            return cls._format_model(item)
        if isinstance(item, list):
            return [cls._format_detail_item(x) for x in item]
        if isinstance(item, dict):
            return {k: cls._format_detail_item(v) for k, v in item.items()}
        return item

    @classmethod
    def _format_model(cls, model: BaseModel) -> Dict[str, Any]:
        """Formats a domain metric sub-model with human-readable descriptions and compact detail items."""
        ctx = {}
        for name, fld in model.__class__.model_fields.items():
            val = getattr(model, name)
            if val is None:
                continue

            label = fld.description or name

            # Skip raw un-geocoded services_grouped; keep rich services_detailed only
            if name == "services_grouped":
                continue

            # Special Handling for refugee associations in RNA
            if name == "asso_refugee_list":
                if not val:
                    ctx[label] = (
                        "Aucune association spécifique recensée dans le Répertoire National des Associations (RNA) pour cette commune."
                    )
                else:
                    ctx[label] = cls._format_detail_item(val)
                continue

            if isinstance(val, (list, dict)) and len(val) == 0:
                continue

            # Special Handling for inclusion services grouped by structure
            if name == "services_detailed" and isinstance(val, dict):
                formatted_srvs = cls._format_inclusion_services(val)
                if formatted_srvs:
                    ctx[label] = formatted_srvs
                continue

            ctx[label] = cls._format_detail_item(val)
        return ctx

    @classmethod
    def _focus_city(cls, state: "GraphState") -> CommuneResult | None:
        """Resolve the complete focus-city record used by expert prompts."""
        focus_city = None
        if state.focus_city and state.search_results:
            focus_city = state.search_results.get_by_code(state.focus_city.codgeo)
        return focus_city or state.focus_city

    @classmethod
    def _dump_context(cls, context: Dict[str, Any], *, compact: bool) -> str:
        """Serialize context deterministically while preserving semantic order."""
        if compact:
            return json.dumps(
                context,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return json.dumps(context, ensure_ascii=False, indent=2)

    @classmethod
    def expert_prompt_contexts(
        cls, state: "GraphState", agent_name: str
    ) -> tuple[str, str]:
        """Return the stable shared prefix and the expert-specific context.

        The shared block is assembled identically for every legacy expert and
        is serialized compactly so Gemini can recognize the same prompt prefix
        across the parallel expert runs.  The expert-specific block is kept
        after it; the current mission remains the final user message and is
        therefore deliberately absent here.
        """
        if agent_name not in cls.DOMAIN_EXPERTS:
            raise ValueError(f"{agent_name!r} is not a domain expert")

        common: Dict[str, Any] = {}
        if state.odis_brief:
            common["Résumé du dossier (Briefing)"] = state.odis_brief
        if state.search_criteria:
            common["Critères de recherche"] = cls._format_criteria(
                state.search_criteria
            )

        focus_city = cls._focus_city(state)
        if focus_city:
            common["Commune analysée (Identité)"] = cls._format_commune_identity(
                focus_city
            )

        specific: Dict[str, Any] = {}
        if focus_city:
            if agent_name == "education_expert":
                specific["Données éducation"] = cls._format_model(focus_city.education)
            elif agent_name == "housing_expert":
                specific["Données logement"] = cls._format_model(focus_city.housing)
            elif agent_name == "healthcare_expert":
                specific["Données santé"] = cls._format_model(focus_city.health)
            elif agent_name == "mobility_expert":
                specific["Données mobilité"] = cls._format_model(focus_city.mobility)
            elif agent_name == "job_hunter":
                specific["Données emploi et formation"] = cls._format_model(
                    focus_city.employment
                )
            elif agent_name == "social_integration_expert":
                specific["Données inclusion"] = cls._format_model(focus_city.inclusion)
                if focus_city.territoire and (
                    focus_city.territoire.ter_insecurite is not None
                    or focus_city.territoire.maire_extreme_droite
                ):
                    specific["Données territoire (Contexte local)"] = cls._format_model(
                        focus_city.territoire
                    )

        return cls._dump_context(common, compact=True), cls._dump_context(
            specific, compact=True
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
            common_context, specific_context = cls.expert_prompt_contexts(
                state, agent_name
            )
            common = json.loads(common_context)
            specific = json.loads(specific_context)
            common.update(specific)
            return cls._dump_context(common, compact=False)

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
        if model is None:
            return None
        if isinstance(model, CriteriaItem):
            return model.label
        if isinstance(model, BaseModel):
            return cls._format_model(model)
        if isinstance(model, list):
            return [cls._auto_build_context(item, visibility_key) for item in model]
        if isinstance(model, dict):
            return {
                k: cls._auto_build_context(v, visibility_key) for k, v in model.items()
            }
        return model

    @classmethod
    def _process_value(cls, val: Any, visibility_key: str = "all") -> Any:
        """Backward-compatible helper redirecting to _format_detail_item."""
        return cls._format_detail_item(val)
