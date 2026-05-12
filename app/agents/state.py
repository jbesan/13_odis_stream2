import json
import logging
from typing import List, Dict, Any, Optional, Literal
from dataclasses import dataclass, field
from pydantic import BaseModel, Field, ConfigDict, model_validator
from google import genai
from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem, CommuneScoreDetail

logger = logging.getLogger(__name__)

class UsageStats(BaseModel):
    """Cumulative usage statistics for the graph."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    breakdown: Dict[str, Dict[str, Any]] = Field(default_factory=dict) # {node_name: {model_id, in_tokens, out_tokens, cost, ...}}

    model_config = ConfigDict(revalidate_instances='never')

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        return data

def compute_criteria_hash(criteria: SearchCriterias) -> str:
    """Helper to compute a stable hash for search criteria."""
    if not criteria:
        return ""
    return criteria.compute_hash()

class FocusCity(BaseModel):
    """Structured representation of the focus city."""
    name: str = Field("", description="Nom de la commune")
    codgeo: str = Field("", description="Code INSEE de la commune")

    model_config = ConfigDict(revalidate_instances='never')

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        return data

@dataclass
class ExpertList:
    """DTO for the Spreading pattern to route to parallel experts."""
    experts: list[str]

@dataclass
class GraphState:
    """Global Graph State for the pydantic-graph MapReduce pipeline."""
    search_criteria: SearchCriterias = field(default_factory=SearchCriterias)
    search_results: Optional[SearchResultsData] = None
    focus_city: Optional[FocusCity] = None
    criteria_hash: Optional[str] = None
    execution_mode: Literal['full_analysis', 'specific_ask'] = 'full_analysis'
    odis_brief: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    interaction_id: str = "unknown"
    username: str = "unknown"
    usage: UsageStats = field(default_factory=UsageStats)
    
    def __post_init__(self):
        # Sync briefing from criteria if not explicitly set
        if not self.odis_brief and self.search_criteria and self.search_criteria.odis_brief:
            self.odis_brief = self.search_criteria.odis_brief

FocusCity.model_rebuild()

@dataclass
class ODISDeps:
    state: GraphState  # Shared States/Data
    client: genai.Client         # Shared Client
    
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
            agent_name: One of 'synthesizer', 'refiner', 'scout',
                        'web', 'job_hunter', 'interviewer'.

        Returns:
            A formatted JSON string ready to inject into a system prompt.
        """
        builders = {
            "synthesizer": cls._synthesizer_context,
            "refiner":     cls._refiner_context,
            "scout":       cls._scout_context,
            "web":         cls._web_context,
            "job_hunter":  cls._job_hunter_context,
            "interviewer": cls._interviewer_context,
            "router":      cls._router_context,
        }
        builder_fn = builders.get(agent_name)
        if not builder_fn:
            logger.warning(f"[CTX] Unknown agent '{agent_name}' — returning empty context.")
            return "{}"

        ctx = builder_fn(state)
        result = json.dumps(ctx, ensure_ascii=False, indent=2)
        logger.debug(f"[CTX] {agent_name} context assembled ({len(result)} chars)")
        return result

    @classmethod
    def _auto_build_context(cls, model: Any, visibility_key: str) -> Dict[str, Any]:
        """
        Recursively builds a context dict from a Pydantic model, filtered by ACL visibility.

        Args:
            model: The Pydantic BaseModel instance to inspect.
            visibility_key: The consumer key to match against odis_visibility tags
                            (e.g., 'agent_scout'). Fields tagged 'all' are always included.

        Returns:
            A flat or nested dict keyed by field descriptions, ready for JSON serialization.
        """
        if not isinstance(model, BaseModel):
            return model

        ctx = {}
        # Access model_fields from the class to avoid Pydantic 2.11+ instance warning
        for name, field in model.__class__.model_fields.items():
            # 1. Check Visibility (ACL Bitmask)
            extra = field.json_schema_extra
            if not isinstance(extra, dict):
                continue
            
            visibility = extra.get("odis_visibility", [])
            if visibility_key not in visibility and "all" not in visibility:
                continue

            # 2. Get value and label
            val = getattr(model, name)
            label = field.description or name
            
            if val is None:
                continue

            # 3. Handle Special Types & Recursion
            ctx[label] = cls._process_value(val, visibility_key)
            
        return ctx

    @classmethod
    def _process_value(cls, val: Any, visibility_key: str) -> Any:
        """
        Helper to process values based on type and handle recursion.

        Args:
            val: The value to process (can be a primitive, list, dict, or BaseModel).
            visibility_key: The consumer key for visibility filtering.

        Returns:
            The processed value, simplified or recursed as needed.
        """
        # Handle CriteriaItem (Special Case: Simplify to Label for LLM)
        if isinstance(val, CriteriaItem):
             return val.label
        
        # Handle CommuneScoreDetail (Special Case: Compact string for LLM agents)
        # We use class name check to avoid issues with double imports/re-definitions
        if val.__class__.__name__ == "CommuneScoreDetail":
            if visibility_key.startswith("agent_"):
                # Use getattr to be safe with different Pydantic versions/proxies
                label = getattr(val, 'label', 'N/A')
                vkpi = getattr(val, 'valeur_kpi', None)
                unit = getattr(val, 'unit', '')
                score = getattr(val, 'score_normalise', 0.0)
                weight = getattr(val, 'relative_weight', 0.0)
                
                kpi_str = f"{vkpi}{unit}" if vkpi is not None else "N/A"
                return f"{label}: {kpi_str}, score: {round(float(score), 2)}, poids relatif: {weight}%"
            # For UI/PDF, fall through to normal recursion

        # Handle List of items (Recursive)
        if isinstance(val, list):
            return [cls._process_value(i, visibility_key) for i in val]
        
        # Handle Nested BaseModel (Recursive)
        if isinstance(val, BaseModel):
            return cls._auto_build_context(val, visibility_key)
        
        # Handle Dict (Recursive)
        if isinstance(val, dict):
            return {k: cls._process_value(v, visibility_key) for k, v in val.items()}
            
        return val

    # -------------------------------------------------------------------------
    # AGENT-SPECIFIC BUILDERS
    # -------------------------------------------------------------------------

    @classmethod
    def _synthesizer_context(cls, state: "GraphState") -> dict:
        """Focus City + Baseline (current_geo) + Expert Artifacts + Briefing."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief
            if state.search_criteria and state.search_criteria.notes_qualitatives:
                ctx["Notes qualitatives"] = state.search_criteria.notes_qualitatives
        else: 
            if state.search_criteria:
                ctx["Critères de recherche"] = cls._auto_build_context(state.search_criteria, "agent_synthesizer")

        if state.focus_city and state.search_results:
            focus = state.search_results.get_by_code(state.focus_city.codgeo)
            if focus:
                ctx["Ville analysée"] = cls._auto_build_context(focus, "agent_synthesizer")

        if state.search_results and state.search_results.current_geo:
            ctx["Ville actuelle (référence)"] = cls._auto_build_context(state.search_results.current_geo, "agent_synthesizer")

        if state.messages:
            ctx["Dernier message"] = state.messages[-1].get("content", "")

        return ctx

    @classmethod
    def _refiner_context(cls, state: "GraphState") -> dict:
        """Briefing + Search Criteria + Top 5 results with full metrics for synthesis."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief
        
        # 1. User Profile & Criteria
        if state.search_criteria:
            ctx["Situation & Critères"] = cls._auto_build_context(state.search_criteria, "agent_refiner")
        
        # 2. Results Analysis (Top 5)
        if state.search_results and state.search_results.results:
            ctx["Top 5 communes identifiées (Détails métriques)"] = [
                {
                    "Rang": i + 1,
                    **cls._auto_build_context(r, "agent_refiner")
                }
                for i, r in enumerate(state.search_results.results[:5])
            ]
        
        # 3. Conversation History
        if state.messages:
            ctx["Historique récent"] = state.messages[-5:] # Last 5 messages for context
            
        return ctx


    @classmethod
    def _scout_context(cls, state: "GraphState") -> dict:
        """City identity + criteria + existing artifact + Briefing."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief

        # Auto-build Criteria for Scout
        if state.search_criteria:
            ctx["Critères de recherche"] = cls._auto_build_context(state.search_criteria, "agent_scout")

        if state.focus_city and state.search_results:
            focus = state.search_results.get_by_code(state.focus_city.codgeo)
            if focus:
                ctx["Ville analysée"] = cls._auto_build_context(focus, "agent_scout")
                existing = focus.expert_analysis.get("scout")
                if existing:
                    ctx["Connaissances actuelles (Scout)"] = existing

        if state.messages:
            ctx["Dernière question"] = state.messages[-1].get("content", "")

        return ctx

    @classmethod
    def _web_context(cls, state: "GraphState") -> dict:
        """City identity + criteria + existing artifact + Briefing."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief

        # Auto-build Criteria for Web
        if state.search_criteria:
            ctx["Critères de recherche"] = cls._auto_build_context(state.search_criteria, "agent_web")

        if state.focus_city and state.search_results:
            focus = state.search_results.get_by_code(state.focus_city.codgeo)
            if focus:
                ctx["Ville analysée"] = cls._auto_build_context(focus, "agent_web")
                existing = focus.expert_analysis.get("web")
                if existing:
                    ctx["Connaissances actuelles (Web)"] = existing

        if state.messages:
            ctx["Dernière question"] = state.messages[-1].get("content", "")

        return ctx

    @classmethod
    def _job_hunter_context(cls, state: "GraphState") -> dict:
        """City identity + ROME codes + own existing artifact."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief

        # Auto-build Criteria for Job Hunter
        if state.search_criteria:
            ctx["Critères de recherche (Emploi)"] = cls._auto_build_context(state.search_criteria, "agent_job_hunter")

        if state.focus_city and state.search_results:
            focus = state.search_results.get_by_code(state.focus_city.codgeo)
            if focus:
                ctx["Ville analysée"] = cls._auto_build_context(focus, "agent_job_hunter")
                existing = focus.expert_analysis.get("job_hunter")
                if existing:
                    ctx["Connaissances actuelles (Job Hunter)"] = existing

        if state.messages:
            ctx["Dernière question"] = state.messages[-1].get("content", "")

        return ctx

    @classmethod
    def _interviewer_context(cls, state: "GraphState") -> dict:
        """Full criteria + last user message."""
        ctx: Dict[str, Any] = {}
        if state.search_criteria:
            ctx["Critères identifiés"] = cls._auto_build_context(state.search_criteria, "agent_interviewer")
        if state.messages:
            ctx["Dernier message utilisateur"] = state.messages[-1].get("content", "")
        return ctx

    @classmethod
    def _router_context(cls, state: "GraphState") -> dict:
        """Specific context for the Router: Briefing + Identified Cities + Focus."""
        ctx: Dict[str, Any] = {}
        if state.odis_brief:
            ctx["Résumé du dossier (Briefing)"] = state.odis_brief
            
        if state.search_results and state.search_results.results:
            ctx["Villes identifiées"] = [
                cls._auto_build_context(r, "agent_router")
                for r in state.search_results.results[:5]
            ]
        
        if state.focus_city:
            ctx["Ville cible"] = f"{state.focus_city.name} ({state.focus_city.codgeo})"
        else:
            ctx["Ville cible"] = "Non définie"
        
        if state.messages:
            ctx["Dernier message"] = state.messages[-1].get("content", "")
        return ctx

    # Legacy helper methods removed in favor of generic _auto_build_context
