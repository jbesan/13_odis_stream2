import hashlib
import json
from typing import List, Dict, Any, Optional, Annotated, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from google import genai
import operator
from core.models import SearchCriterias

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
    """Reducer to sum usage stats. Handles both UsageStats objects and dicts."""
    if right is None:
        return left
    
    # Convert dict to UsageStats if needed
    if isinstance(right, dict):
        right = UsageStats(**right)
        
    # Merge breakdowns
    new_breakdown = getattr(left, 'breakdown', {}).copy()
    right_breakdown = getattr(right, 'breakdown', {})
    
    if right_breakdown:
        for node, metrics in right_breakdown.items():
            if node in new_breakdown:
                # Accumulate values if node already exists
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

def compute_criteria_hash(criteria: SearchCriterias) -> str:
    """Computes a stable MD5 hash for search criteria to detect changes."""
    # We dump to JSON with sorted keys for stability
    criteria_json = criteria.model_dump_json()
    return hashlib.md5(criteria_json.encode()).hexdigest()

def take_first_hash(left: Optional[str], right: Any) -> Optional[str]:
    """
    Reducer for criteria_hash.
    In parallel branches, all experts will return the same hash.
    This reducer simply takes the first non-null value to avoid LangGraph selection errors.
    """
    return left or right

def merge_commune_artifacts(left: Dict[str, Any], right: Any) -> Dict[str, Any]:
    """
    Reducer to merge commune artifacts.
    Structure: { "CommuneName": { "Hash": { "agent": Result } } }
    We use a deep merge approach to avoid overwriting unrelated cities or hashes.
    """
    if not right or not isinstance(right, dict):
        return left
    
    # Create a deep-ish copy of left
    new_state = left.copy()
    
    for commune, hashes in right.items():
        if commune not in new_state:
            new_state[commune] = hashes
        else:
            # Merge hashes for this commune
            commune_state = new_state[commune].copy()
            for h, agents in hashes.items():
                if h not in commune_state:
                    commune_state[h] = agents
                else:
                    # Merge agent results for this hash
                    hash_state = commune_state[h].copy()
                    hash_state.update(agents)
                    commune_state[h] = hash_state
            new_state[commune] = commune_state
            
    return new_state

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

# LangGraph State Definition
class ODISGraphState(BaseModel):
    """
    The Global State passed around the LangGraph.
    It replaces the legacy AgentContext.
    """
    # client: genai.Client = Field(default_factory=genai.Client) # Moved to ODISDeps
    messages: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list) # Chat History
    
    # Context Data
    user_profile: UserProfile = Field(default_factory=UserProfile)
    search_criteria: Annotated[SearchCriterias, merge_search_criteria] = Field(default_factory=SearchCriterias)
    
    # Results & Decisions
    scoring_results: Annotated[Dict[str, Any], operator.ior] = Field(default_factory=dict)
    commune_artifacts: Annotated[Dict[str, Any], merge_commune_artifacts] = Field(default_factory=dict)
    top_cities: List[Dict[str, Any]] = Field(default_factory=list)
    focus_city: Optional[FocusCity] = None
    
    # Execution Control (v3)
    criteria_hash: Annotated[Optional[str], take_first_hash] = None
    pending_experts: List[str] = Field(default_factory=list)
    execution_mode: Literal['full_analysis', 'specific_ask'] = 'full_analysis'
    
    # Memory
    briefing: str = "" # The "Brain" summary of what happened
    last_summarized_idx: int = 0 # Pointer to the last message summarized
    next_node: Optional[str] = None # Routing decision
    
    # Session Management (SOTA Loop)
    active_agent: Optional[str] = Field(None, description="The last active agent node name")
    is_interview_complete: bool = Field(False, description="True if Interviewer has finished collection")
    
    # Token Tracking (Optional for graph, but good for reporting)
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
            elif isinstance(val, dict) and not val.get("name") and not val.get("codgeo"):
                # Clean up empty dicts
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
