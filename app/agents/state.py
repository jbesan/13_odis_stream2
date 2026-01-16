from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from core.models import SearchCriterias

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
    found_jobs: List[Dict[str, Any]] = Field(default_factory=list) # Stocke les dernières offres trouvées
    briefing: str = ""
    
    
    # Trace of the conversation
    history: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Technical Metadata
    active_agent: str = "interviewer"
    last_action: Optional[str] = None
    workflow_phase: str = "DISCOVERY" # 'DISCOVERY', 'SCORING', 'DECORATION'
    focus_city: Optional[str] = None # The city currently being investigated (e.g. for Scout)
    
    # Token Tracking (Total)
    total_tokens_sent: int = 0
    total_tokens_received: int = 0
    
    # Granular Tracking for Cost Estimation
    tokens_g3_input: int = 0
    tokens_g3_output: int = 0
    tokens_g25_input: int = 0
    tokens_g25_output: int = 0
    
    def get_search_criterias_model(self) -> Optional[SearchCriterias]:
        """Convert dict to Pydantic model once mandatory fields are present."""
        try:
            return SearchCriterias(**self.search_criteria)
        except Exception:
            return None
