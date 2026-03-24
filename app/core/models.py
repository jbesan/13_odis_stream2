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
    poids_mobilite: float = Field(0.0, description="Weight for mobility")
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

class CommuneScoreDetail(BaseModel):
    """Represents the value and score of a specific indicator."""
    label: str = Field(description="Nom d'affichage lisible (ex: 'Écoles élémentaires')")
    score_id: str = Field(description="Code interne unique (ex: 'edu_elementaire_ct')")
    valeur_kpi: Optional[Union[float, int, str]] = Field(None, description="Valeur brute métier")
    score_normalise: float = Field(description="Score de 0.0 à 1.0 issu du ScoringEngine")
    unit: str = Field(description="Unité de la valeur brute (ex: 'habitants', '%')")
    relative_weight: float = Field(description="Poids relatif en % dans sa catégorie")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

class EmploiDetails(BaseModel):
    cat_score: float = 0.0
    ft_jobs_total: int = 0
    matching_total: int = 0
    top_metiers: List[str] = Field(default_factory=list)
    siae_total: int = 0
    siae_summary: Dict[str, int] = Field(default_factory=dict)
    siae_matching_total: int = 0
    siae_matching_summary: Dict[str, int] = Field(default_factory=dict)
    formations: List[str] = Field(default_factory=list)
    ft_jobs_summary: Dict[str, int] = Field(default_factory=dict)
    matching_jobs_summary: Dict[str, int] = Field(default_factory=dict)

class LogementDetails(BaseModel):
    cat_score: float = 0.0
    jaccueille_count: int = 0
    raw_euro_m2: Optional[float] = None
    odace_all_variants: Dict[str, Dict[str, Optional[float]]] = Field(default_factory=dict)


class EducationDetails(BaseModel):
    cat_score: float = 0.0
    counts: Dict[str, int] = Field(default_factory=dict)
    etablissements: Dict[str, List[str]] = Field(default_factory=dict)

class SanteDetails(BaseModel):
    cat_score: float = Field(0.0)
    counts: Dict[str, int] = Field(default_factory=dict)
    etablissements: Dict[str, List[str]] = Field(default_factory=dict)

class InclusionDetails(BaseModel):
    cat_score: float = Field(0.0)
    services_grouped: Dict[str, List[str]] = Field(default_factory=dict)
    refugee_asso_list: List[Dict[str, Any]] = Field(default_factory=list)
    # Merged from AssociationsDetails
    total_associations: int = Field(0)
    refugee_asso_count: int = Field(0)
    associations_summary_by_category: Dict[str, int] = Field(default_factory=dict)

class MobiliteDetails(BaseModel):
    cat_score: float = Field(0.0)
    nb_stops_bus: int = Field(0)
    nb_stops_tram: int = Field(0)
    nb_stops_metro: int = Field(0)
    nb_stops_train: int = Field(0)
    nb_stops_total: int = Field(0)
    mob_trans_pub_stop_density: float = Field(0.0)

class CommuneResult(BaseModel):
    """Encapsulates identity, scores, and metadata for a specific commune."""
    # Identity
    codgeo: str = Field(description="Code INSEE (ex: '75101')")
    name: str = Field(description="Nom de la commune")
    population: int = Field(description="Population totale lissée")
    bassin_de_vie: str = Field(description="Nom du bassin de vie d'appartenance")
    
    # Geographic coordinates (for maps)
    lat: float = Field(0.0)
    lon: float = Field(0.0)
    
    # Global score
    global_score: float = Field(description="Score pondéré global (0-1.0)")
    
    # Thematic scores (grouped by category)
    scores: Dict[str, List[CommuneScoreDetail]] = Field(default_factory=dict, description="Details grouped by category")
    
    # Domain specific aggregations (Strongly typed)
    emploi: EmploiDetails = Field(default_factory=EmploiDetails)
    logement: LogementDetails = Field(default_factory=LogementDetails)
    education: EducationDetails = Field(default_factory=EducationDetails)
    sante: SanteDetails = Field(default_factory=SanteDetails)
    inclusion: InclusionDetails = Field(default_factory=InclusionDetails)
    mobilite: MobiliteDetails = Field(default_factory=MobiliteDetails)

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

class SearchResultsData(BaseModel):
    """Main payload container for search results."""
    search_hash: str = Field(description="MD5 hash of the criteria used")
    top_communes: List[CommuneResult] = Field(default_factory=list, description="Top recommended communes")
    current_geo: Optional[CommuneResult] = Field(None, description="Reference data for the starting point")

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


