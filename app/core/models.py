from dataclasses import dataclass
from typing import List, Dict, Any, Union, Optional
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
    codes_metiers: List[List[str]] = Field(default_factory=list, description="List of list of ROME codes for jobs (e.g. [['M1805']]")
    codes_formations: List[List[str]] = Field(default_factory=list, description="List of list of training codes")
    
    inc_services_add_selection: List[str] = Field(default_factory=list, description="List of additional inclusion need codes")
    inc_asso_add_selection: List[str] = Field(default_factory=list, description="List of additional hobby/association codes (WALDEC)")
    
    hebergement: Optional[str] = Field(None, description="Preferred accommodation type (e.g. 'Location')")
    logement: Optional[str] = Field(None, description="Housing type (e.g. 'Logement Social')")
    sante: Optional[str] = Field(None, description="Specific health need (e.g. 'Maternité')")
    
    loc_custom_code: Optional[str] = Field(None, description="Explicit code for custom search area (Reg or Dep)")
    loc_custom_type: Optional[str] = Field(None, description="Type of custom search area ('region' or 'departement')")

    # Qualitative notes (free text indices for Scout and Synthesis)
    notes_qualitatives: List[str] = Field(default_factory=list, description="List of qualitative notes (e.g. ['Famille libanaise', 'Passions: échecs'])")

    # Final scoring priority
    weight_profile: str = Field("", description="Weight profile for scoring (Famille, Santé, Économique, Équilibré)")


@dataclass
class ScoringConfig:
    """
    A dataclass to hold all user preferences and scoring parameters.
    This provides type safety and autocompletion in IDEs.
    """
    # Weights
    poids_emploi: int
    poids_logement: int
    poids_education: int
    poids_inclusion: int
    poids_mobilité: int
    poids_sante: int # Added new weight for sante
    
    # Criteria Weights (F-15)
    criteria_weights: Dict[str, float]

    # Location
    commune_actuelle: str
    loc_search_area: str
    
    # Household
    nb_adultes: int
    nb_enfants: int
    
    # Preferences
    hebergement: str
    logement: str
    codes_metiers: List[List[str]]
    codes_formations: List[List[str]]
    classe_enfants: List[str]
    besoin_sante: str
    inc_services_add_selection: List[str]
    
    # Inclusion
    inc_services_core_selection: List[str]
    inc_asso_add_selection: List[str]

    # Custom Geo
    loc_custom_code: Optional[str] = None
    loc_custom_type: Optional[str] = None # 'region' or 'departement'
