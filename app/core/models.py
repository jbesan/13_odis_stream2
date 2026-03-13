from dataclasses import dataclass
from typing import List, Dict, Any, Union, Optional, Set
import hashlib
from pydantic import BaseModel, Field, ConfigDict, model_validator

class CriteriaItem(BaseModel):
    """
    Representation of a criteria item with both code and label.
    """
    code: str = Field(..., description="Technical code (e.g. INSEE, ROME, WALDEC)")
    label: str = Field(..., description="Human-readable label (e.g. 'Bordeaux', 'Boulangerie')")

    model_config = ConfigDict(populate_by_name=True, revalidate_instances='never', frozen=True)

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        return data

class SearchCriterias(BaseModel):
    """
    Criteria for searching and scoring cities based on user needs.
    This model forces the use of enriched items (CriteriaItem) for key fields.
    """
    commune_actuelle: Optional[CriteriaItem] = Field(None, description="Enriched current city (code + label)")
    loc_search_area: str = Field("", description="scope for search area ('departement', 'region', 'france')")
    loc_search_code: Optional[str] = Field(None, description="Explicit code for targeted search area (Reg or Dep)")
    
    nb_adultes: int = Field(1, description="Number of adults in the household")
    nb_enfants: int = Field(0, description="Number of children in the household")
    
    classe_enfants: List[str] = Field(default_factory=list, description="School levels needed (e.g. ['Maternelle', 'Collège'])")
    
    # Force CriteriaItem for enriched fields
    codes_metiers: List[List[CriteriaItem]] = Field(default_factory=list, description="List of list of enriched ROME codes")
    codes_formations: List[List[CriteriaItem]] = Field(default_factory=list, description="List of list of enriched training codes")
    
    inc_services_add_selection: List[CriteriaItem] = Field(default_factory=list, description="List of additional relevant inclusion services codes")
    inc_asso_add_selection: List[CriteriaItem] = Field(default_factory=list, description="List of additional relevant association codes (hobbies, support, etc.)")
    
    hebergement_cible: List[str] = Field(default_factory=list, description="Preferred accommodation types")
    logement: Optional[str] = Field(None, description="Housing type (e.g. 'Logement Social')")
    type_logement: Optional[CriteriaItem] = Field(None, description="Enriched housing type (e.g., 'Appartement', 'Maison')")
    besoin_sante: Optional[str] = Field(None, description="Specific health need (e.g. 'Maternité')")

    # Core Inclusion Services
    inc_services_core_selection: List[CriteriaItem] = Field(default_factory=list, description="List of core inclusion services codes")

    # Qualitative notes (free text indices for Scout and Synthesis)
    notes_qualitatives: List[str] = Field(default_factory=list, description="List of qualitative notes (e.g. ['Famille libanaise', 'Passions: échecs'])")

    # Final scoring priority
    weight_profile: str = Field("", description="Scoring weights profile from ['Famille', 'Santé', 'Économique', 'Équilibré']")
    criteria_weights: Dict[str, float] = Field(default_factory=dict, description="Custom weights for specific criteria")
    
    # Global Category Weights
    poids_emploi: float = Field(0.0, description="Weight for employment criteria")
    poids_logement: float = Field(0.0, description="Weight for housing criteria")
    poids_education: float = Field(0.0, description="Weight for education criteria")
    poids_inclusion: float = Field(0.0, description="Weight for inclusion criteria")
    poids_mobilité: float = Field(0.0, description="Weight for mobility")
    poids_sante: float = Field(0.0, description="Weight for health criteria")

    active_criteria: Optional[Set[str]] = Field(None, description="Set of active criteria computed by the engine")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True, revalidate_instances='never')

    @model_validator(mode='before')
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, 'model_dump') else data.__dict__
        return data

    @model_validator(mode='before')
    @classmethod
    def fix_stringified_items(cls, data: Any) -> Any:
        """
        Fail-safe: detect and fix "CriteriaItem(code='...', label='...')" strings 
        that might have been produced by the LLM or serialization glitches.
        """
        if not isinstance(data, dict):
            return data
            
        import re
        # Pattern to catch CriteriaItem(code='...', label='...')
        pattern = r"CriteriaItem\(code=['\"]([^'\"]+)['\"],\s*label=['\"]([^'\"]+)['\"]\)"
        
        def _fix_value(v):
            if isinstance(v, str):
                match = re.search(pattern, v)
                if match:
                    return {"code": match.group(1), "label": match.group(2)}
                # Optimization: if it's a raw string, wrap it to match CriteriaItem schema
                return {"code": v, "label": v}
            if isinstance(v, list):
                return [_fix_value(i) for i in v]
            return v
            
        fields_to_fix = [
            'commune_actuelle', 'codes_metiers', 'codes_formations', 
            'inc_services_add_selection', 'inc_asso_add_selection', 
            'inc_services_core_selection', 'type_logement'
        ]
        
        for f in fields_to_fix:
            if f in data and data[f]:
                data[f] = _fix_value(data[f])
                
        return data

    def compute_hash(self) -> str:
        """Computes a stable MD5 hash for search criteria to detect changes."""
        criteria_json = self.model_dump_json()
        return hashlib.md5(criteria_json.encode()).hexdigest()


