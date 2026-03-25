import json
from typing import List, Dict, Any, Optional, Annotated, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from google import genai
import operator
from core.models import SearchCriterias, SearchResultsData, CommuneResult

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

def add_usage(left: UsageStats, right: Any) -> UsageStats:
    if right is None:
        return left
    if isinstance(right, dict):
        right = UsageStats(**right)
        
    new_breakdown = getattr(left, 'breakdown', {}).copy()
    right_breakdown = getattr(right, 'breakdown', {})
    
    if right_breakdown:
        for node, metrics in right_breakdown.items():
            if node in new_breakdown:
                existing = new_breakdown[node]
                new_breakdown[node] = {
                    "model": metrics.get("model", existing.get("model")),
                    "input": existing.get("input", 0) + metrics.get("input", 0),
                    "output": existing.get("output", 0) + metrics.get("output", 0),
                    "total": existing.get("total", 0) + metrics.get("total", 0),
                    "cost": existing.get("cost", 0.0) + metrics.get("cost", 0.0)
                }
            else:
                new_breakdown[node] = metrics
    
    return UsageStats(
        input_tokens=left.input_tokens + (right.input_tokens or 0),
        output_tokens=left.output_tokens + (right.output_tokens or 0),
        total_tokens=left.total_tokens + (right.total_tokens or 0),
        cost_usd=left.cost_usd + (right.cost_usd or 0.0),
        breakdown=new_breakdown
    )

def merge_search_criteria(left: SearchCriterias, right: Any) -> SearchCriterias:
    """Reducer to merge search criteria updates."""
    if not right:
        return left
    
    # We want to keep existing values that are not in the update (right)
    # We dump CURRENT with exclude_unset=False to get defaults too
    current_data = left.model_dump() if left else {}
    
    if isinstance(right, dict):
        current_data.update(right)
    elif isinstance(right, SearchCriterias):
        # We only update with what was EXPLICITLY set in the new model
        update_data = right.model_dump(exclude_unset=True)
        current_data.update(update_data)
        
    return SearchCriterias(**current_data)


def take_latest_hash(left: Optional[str], right: Any) -> Optional[str]:
    """
    Ensures that the state always uses the LATEST hash provided.
    Across turns, nodes compute a NEW hash if criteria have changed.
    Within a turn, parallel nodes return the SAME next hash.
    """
    return right if right is not None else left

def compute_criteria_hash(criteria: SearchCriterias) -> str:
    """Helper to compute a stable hash for search criteria."""
    if not criteria:
        return ""
    return criteria.compute_hash()

def merge_search_results(left: Optional[SearchResultsData], right: Any) -> Optional[SearchResultsData]:
    """Reducer to merge search results and expert artifacts."""
    if right is None:
        return left
    
    if left is None:
        if isinstance(right, dict):
            return SearchResultsData(**right)
        return right

    if isinstance(right, SearchResultsData):
        right = right.model_dump(exclude_unset=True)
    
    if not isinstance(right, dict):
        return left

    new_data = left.model_dump()
    
    # 1. Merge results list by codgeo
    if "results" in right and right["results"]:
        existing_results = {str(r["codgeo"]): i for i, r in enumerate(new_data.get("results", []))}
        for new_res in right["results"]:
            cg = str(new_res.get("codgeo"))
            if cg in existing_results:
                idx = existing_results[cg]
                target = new_data["results"][idx]
                
                # Merge expert_analysis
                if "expert_analysis" in new_res and new_res["expert_analysis"]:
                    if "expert_analysis" not in target or target["expert_analysis"] is None:
                        target["expert_analysis"] = {}
                    target["expert_analysis"].update(new_res["expert_analysis"])
                
                # Update other fields (scorer_pitch, global score, etc.)
                for k, v in new_res.items():
                    if k != "expert_analysis" and v is not None:
                        target[k] = v
            else:
                new_data.setdefault("results", []).append(new_res)
                
    # 2. Update other top-level fields (global_pitch, current_geo, search_hash)
    for k, v in right.items():
        if k != "results" and v is not None:
            if k == "current_geo" and isinstance(v, dict):
                 new_data.setdefault("current_geo", {}).update(v)
            else:
                new_data[k] = v
                
    return SearchResultsData(**new_data)

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

class UserProfile(BaseModel):
    """Basic extraction of user identity and raw initial request."""
    name: Optional[str] = None
    raw_request: str = ""

    model_config = ConfigDict(revalidate_instances='never')

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        return data

class ODISGraphState(BaseModel):
    """Global Graph State."""
    messages: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    user_profile: UserProfile = Field(default_factory=UserProfile)
    search_criteria: Annotated[SearchCriterias, merge_search_criteria] = Field(default_factory=SearchCriterias)
    search_results: Annotated[Optional[SearchResultsData], merge_search_results] = None
    focus_city: Optional[FocusCity] = None
    criteria_hash: Annotated[Optional[str], take_latest_hash] = None
    pending_experts: List[str] = Field(default_factory=list)
    execution_mode: Literal['full_analysis', 'specific_ask'] = 'full_analysis'
    briefing: str = ""
    last_summarized_idx: int = 0
    next_node: Optional[str] = None
    active_agent: Optional[str] = Field(None, description="The last active agent node name")
    is_interview_complete: bool = Field(False, description="True if Interviewer has finished collection")
    usage: Annotated[UsageStats, add_usage] = Field(default_factory=UsageStats)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        revalidate_instances='never', # Crucial for Streamlit class redefinition issues
        from_attributes=True
    )

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            data = data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        
        # Robust focus_city normalization (string -> object)
        if isinstance(data, dict) and "focus_city" in data:
            val = data.get("focus_city")
            if isinstance(val, str) and val.strip():
                data["focus_city"] = {"name": val.strip(), "codgeo": ""}
            elif isinstance(val, dict):
                # Clean up if TRULY empty dict
                if not val.get("name") and not val.get("codgeo"):
                    data["focus_city"] = None
            elif val is not None:
                # If it's an object, check its name attribute if possible
                has_name = getattr(val, 'name', None)
                has_codgeo = getattr(val, 'codgeo', None)
                if not has_name and not has_codgeo:
                    data["focus_city"] = None
        return data


FocusCity.model_rebuild()
UserProfile.model_rebuild()
ODISGraphState.model_rebuild()

from dataclasses import dataclass

@dataclass
class ODISDeps:
    state: ODISGraphState  # Shared States/Data
    client: genai.Client         # Shared Client
    
    # Allow arbitrary types for genai.Client
    class Meta:
        arbitrary_types_allowed = True
