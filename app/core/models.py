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
    
    inc_services_core_selection: List[CriteriaItem] = Field(default_factory=list, description="List of core inclusion services codes (checkboxes)")
    inc_services_add_selection: List[CriteriaItem] = Field(default_factory=list, description="List of additional relevant inclusion services codes (multiselect)")
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
    
    # Population target (F-50)
    target_population: int = Field(50000, description="Target population size for the city (mu)")
    target_population_sigma: int = Field(40000, description="Tolerance (sigma) for the population size")

    active_criteria: Optional[Set[str]] = Field(None, description="Set of active criteria computed by the engine")
    active_categories: List[str] = Field(default_factory=list, description="List of categories with active criteria")

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

class EmploymentMetrics(BaseModel):
    cat_score: float = 0.0
    standard_jobs_total: int = 0
    standard_jobs_matching_total: int = 0
    top_professions: List[str] = Field(default_factory=list)
    inclusive_jobs_total: int = 0
    inclusive_jobs_summary: Dict[str, int] = Field(default_factory=dict)
    inclusive_jobs_matching_total: int = 0
    inclusive_jobs_matching_summary: Dict[str, int] = Field(default_factory=dict)
    training_programs: List[str] = Field(default_factory=list)
    standard_jobs_summary: Dict[str, int] = Field(default_factory=dict)
    standard_jobs_matching_summary: Dict[str, int] = Field(default_factory=dict)

class HousingMetrics(BaseModel):
    cat_score: float = 0.0
    host_count: int = 0
    price_per_sqm: Optional[float] = None
    odace_all_variants: Dict[str, Dict[str, Optional[float]]] = Field(default_factory=dict)


class EducationMetrics(BaseModel):
    cat_score: float = 0.0
    facility_counts: Dict[str, int] = Field(default_factory=dict)
    facility_details: Dict[str, List[str]] = Field(default_factory=dict)

class HealthMetrics(BaseModel):
    cat_score: float = Field(0.0)
    facility_counts: Dict[str, int] = Field(default_factory=dict)
    facility_details: Dict[str, List[str]] = Field(default_factory=dict)

class InclusionMetrics(BaseModel):
    cat_score: float = Field(0.0)
    services_grouped: Dict[str, List[str]] = Field(default_factory=dict)
    asso_refugee_list: List[Dict[str, Any]] = Field(default_factory=list)
    asso_inclusion_count: int = Field(0)
    asso_refugee_count: int = Field(0)
    asso_inclusion_list_by_cat: Dict[str, Any] = Field(default_factory=dict)

class MobilityMetrics(BaseModel):
    cat_score: float = Field(0.0)
    bus_stops: int = Field(0)
    tram_stops: int = Field(0)
    metro_stops: int = Field(0)
    train_stops: int = Field(0)
    total_stops: int = Field(0)
    stop_density: float = Field(0.0)

class CommuneResult(BaseModel):
    """Encapsulates identity, scores, and metadata for a specific commune."""
    # Identity
    codgeo: str = Field(description="Code INSEE (ex: '75101')")
    name: str = Field(description="Nom de la commune")
    population: int = Field(description="Population totale lissée")
    codgeo_bdv: str = Field(default="", description="Code du bassin de vie d'appartenance")
    name_bdv: str = Field(default="", description="Nom du bassin de vie d'appartenance")
    
    # Geographic data (for maps)
    centroid: Optional[Any] = Field(None, exclude=True, description="Centroid Point in 4326")
    geometry: Optional[Any] = Field(None, exclude=True, description="Geometry object (Polygon/MultiPolygon)")
    
    # Global score
    global_score: float = Field(description="Score pondéré global (0-1.0)")
    
    # Thematic scores (grouped by category)
    scores: Dict[str, List[CommuneScoreDetail]] = Field(default_factory=dict, description="Details grouped by category")
    
    # Domain specific aggregations (Strongly typed)
    employment: EmploymentMetrics = Field(default_factory=EmploymentMetrics)
    housing: HousingMetrics = Field(default_factory=HousingMetrics)
    education: EducationMetrics = Field(default_factory=EducationMetrics)
    health: HealthMetrics = Field(default_factory=HealthMetrics)
    inclusion: InclusionMetrics = Field(default_factory=InclusionMetrics)
    mobility: MobilityMetrics = Field(default_factory=MobilityMetrics)

    # Agent-generated content
    scorer_pitch: str = Field(default="", description="Short pitch from Scorer Agent")
    expert_analysis: Dict[str, str] = Field(default_factory=dict, description="Detailed reports from experts (scout, web, etc.)")
    odis_synthesis: List[Dict[str, str]] = Field(default_factory=list, description="List of messages (conversation thread) for the city analysis")
    
    @model_validator(mode='before')
    @classmethod
    def handle_odis_synthesis_type(cls, data: Any) -> Any:
        if isinstance(data, dict) and 'odis_synthesis' in data:
            val = data['odis_synthesis']
            if isinstance(val, str):
                # Convert legacy string to a list of one assistant message if not empty
                data['odis_synthesis'] = [{"role": "assistant", "content": val}] if val else []
        return data

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

class SearchResultsData(BaseModel):
    """Main payload container for search results."""
    search_hash: str = Field(description="MD5 hash of the criteria used")
    results: List[CommuneResult] = Field(default_factory=list, description="Top recommended communes in rank order")
    current_geo: CommuneResult = Field(..., description="Reference data for the user current location")
    global_pitch: str = Field(default="", description="Global introduction from Scorer Agent")
    odis_brief: str = Field(default="", description="Final briefing about the person profile")
    
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    def get_by_code(self, codgeo: str) -> Optional[CommuneResult]:
        """Helper to find a result by its INSEE code."""
        return next((c for c in self.results if c.codgeo == codgeo), None)


