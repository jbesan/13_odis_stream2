from dataclasses import dataclass
from typing import List, Dict, Any, Optional, Annotated
from pydantic import BaseModel, Field, ConfigDict
from google import genai
import operator
from core.models import SearchCriterias

class UsageStats(BaseModel):
    """Cumulative usage statistics for the graph."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

def add_usage(left: UsageStats, right: Any) -> UsageStats:
    """Reducer to sum usage stats. Handles both UsageStats objects and dicts."""
    if right is None:
        return left
    
    # Convert dict to UsageStats if needed
    if isinstance(right, dict):
        right = UsageStats(**right)
        
    return UsageStats(
        input_tokens=left.input_tokens + (right.input_tokens or 0),
        output_tokens=left.output_tokens + (right.output_tokens or 0),
        total_tokens=left.total_tokens + (right.total_tokens or 0),
        cost_usd=left.cost_usd + (right.cost_usd or 0.0)
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
SyncSearchCriterias = SearchCriterias # Alias for easier ref if needed

class UserProfile(BaseModel):
    """Basic extraction of user identity and raw initial request."""
    name: Optional[str] = None
    raw_request: str = ""

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
    top_cities: List[Dict[str, Any]] = Field(default_factory=list)
    found_jobs: List[Dict[str, Any]] = Field(default_factory=list)
    focus_city: Optional[str] = None
    
    # Logic / Meta
    briefing: str = "" # The "Brain" summary of what happened
    next_node: Optional[str] = None # Routing decision
    
    
    # Token Tracking (Optional for graph, but good for reporting)
    usage: Annotated[UsageStats, add_usage] = Field(default_factory=UsageStats)
    experts_results: Annotated[Dict[str, str], operator.ior] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

@dataclass
class ODISDeps:
    state: ODISGraphState  # Shared States/Data
    client: genai.Client         # Shared Client
    
    # Allow arbitrary types for genai.Client
    class Meta:
        arbitrary_types_allowed = True


# Legacy Context (Kept for transition if needed, or we can deprecate)
class AgentContext(BaseModel):
    """Legacy Context - To be removed after full migration."""
    pass
