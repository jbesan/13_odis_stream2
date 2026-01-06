from typing import List, Optional, Union
from pydantic import BaseModel, Field

class SearchCriterias(BaseModel):
    """
    Criteria for searching and scoring cities based on user needs.
    """
    commune_actuelle: str = Field(..., description="INSEE code of the user's current city (e.g. '75056')")
    loc_search_area: str = Field(..., description="scope for search area ('departement', 'region', 'france')")
    
    nb_adultes: int = Field(1, description="Number of adults in the household")
    nb_enfants: int = Field(0, description="Number of children in the household")
    
    classe_enfants: List[str] = Field(default_factory=list, description="School levels needed (e.g. ['Maternelle', 'Collège'])")
    codes_metiers: List[List[str]] = Field(default_factory=list, description="List of list of FAP codes for jobs (e.g. [['T2A60']]")
    codes_formations: List[List[str]] = Field(default_factory=list, description="List of list of training codes")
    
    inc_services_add_selection: List[str] = Field(default_factory=list, description="List of additional inclusion need codes")
    inc_asso_add_selection: List[str] = Field(default_factory=list, description="List of additional hobby/association codes (WALDEC)")
    
    hebergement: Optional[str] = Field(None, description="Preferred accommodation type (e.g. 'Location')")
    logement: Optional[str] = Field(None, description="Housing type (e.g. 'Logement Social')")
    sante: Optional[str] = Field(None, description="Specific health need (e.g. 'Maternité')")
    
    loc_custom_code: Optional[str] = Field(None, description="Explicit code for custom search area (Reg or Dep)")
    loc_custom_type: Optional[str] = Field(None, description="Type of custom search area ('region' or 'departement')")

    # Final scoring priority
    weight_profile: str = Field("", description="Weight profile for scoring (Famille, Santé, Économique, Équilibré)")

