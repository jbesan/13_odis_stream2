from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from models import SearchCriterias

class UserProfile(BaseModel):
    """Basic extraction of user identity and raw initial request."""
    name: Optional[str] = None
    raw_request: str = ""

class AgentContext(BaseModel):
    """The 'Golden Record' shared between all agents."""
    user_profile: UserProfile = Field(default_factory=UserProfile)
    
    # We store the latest valid criteria. 
    # Partial updates will be merged here.
    search_criteria: Dict[str, Any] = Field(default_factory=dict)
    top_cities: List[Dict[str, Any]] = Field(default_factory=list) # Stocke le dernier Top 3
    
    
    # Trace of the conversation
    history: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Technical Metadata
    active_agent: str = "interviewer"
    last_action: Optional[str] = None
    workflow_phase: str = "DISCOVERY" # 'DISCOVERY', 'SCORING', 'DECORATION'
    
    def get_search_criterias_model(self) -> Optional[SearchCriterias]:
        """Convert dict to Pydantic model once mandatory fields are present."""
        try:
            return SearchCriterias(**self.search_criteria)
        except Exception:
            return None
