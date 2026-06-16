from dataclasses import dataclass
from typing import List, Dict, Any, Union, Optional, Set, Literal
import hashlib
from pydantic import BaseModel, Field, ConfigDict, model_validator

class CriteriaItem(BaseModel):
    """
    Representation of a criteria item with both code and label.
    """
    code: str = Field(..., description="Technical code (e.g. INSEE, ROME, WALDEC)", json_schema_extra={"odis_visibility": ["all"]})
    label: str = Field(..., description="Human-readable label (e.g. 'Bordeaux', 'Boulangerie')", json_schema_extra={"odis_visibility": ["all"]})

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
    commune_actuelle: Optional[CriteriaItem] = Field(None, description="Commune actuelle de résidence", json_schema_extra={"odis_visibility": ["all"]})
    commune_pressentie: Optional[CriteriaItem] = Field(None, description="Commune pressentie à titre de comparaison", json_schema_extra={"odis_visibility": ["all"]})
    loc_search_area: str = Field("", description="Zone de recherche (département, région, France)", json_schema_extra={"odis_visibility": ["all"]})
    loc_search_code: List[str] = Field(default_factory=list, description="Codes géographiques de la zone de recherche", json_schema_extra={"odis_visibility": ["all"]})
    
    nb_adultes: int = Field(1, description="Nombre d'adultes", json_schema_extra={"odis_visibility": ["all"]})
    nb_enfants: int = Field(0, description="Nombre d'enfants", json_schema_extra={"odis_visibility": ["all"]})
    
    classe_enfants: List[str] = Field(default_factory=list, description="Niveaux scolaires recherchés", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_education_expert", "ui_details", "pdf_report", "agent_ts_agent"]})
    
    # Force CriteriaItem for enriched fields
    codes_metiers: List[List[CriteriaItem]] = Field(default_factory=list, description="Métiers ciblés par adulte", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_job_hunter", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    codes_formations: List[List[CriteriaItem]] = Field(default_factory=list, description="Formations ciblées", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_job_hunter", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    
    inc_services_selection: List[CriteriaItem] = Field(default_factory=list, description="Services d'inclusion sélectionnés", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_job_hunter", "agent_social_integration_expert", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    inc_asso_add_selection: List[CriteriaItem] = Field(default_factory=list, description="Associations et centres d'intérêt", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_social_integration_expert", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    
    hebergement_cible: List[str] = Field(default_factory=list, description="Hébergement souhaité", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_housing_expert", "agent_job_hunter", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    logement: Optional[str] = Field(None, description="Type de logement (ex: Logement Social)", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_housing_expert", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    type_logement: Optional[CriteriaItem] = Field(None, description="Type de bien (Appartement, Maison)", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_housing_expert", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    besoin_sante: Optional[str] = Field(None, description="Besoin de santé spécifique", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_healthcare_expert", "agent_job_hunter", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    freq_retour: str = Field("1 fois/mois", description="Fréquence de retour vers la commune actuelle", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_mobility_expert", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})


    # Qualitative notes (free text indices for Scout and Synthesis)
    notes_qualitatives: List[str] = Field(default_factory=list, description="Notes qualitatives sur le projet de vie", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_job_hunter", "agent_housing_expert", "agent_mobility_expert", "agent_healthcare_expert", "agent_education_expert", "agent_social_integration_expert", "agent_synthesizer", "pdf_report", "agent_ts_agent"]})

    # Final scoring priority
    weight_profile: str = Field("", description="Profil de pondération", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "pdf_report"]})
    criteria_weights: Dict[str, float] = Field(default_factory=dict, description="Poids personnalisés par critère", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    
    # Global Category Weights
    poids_emploi: float = Field(0.0, description="Poids Emploi", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    poids_logement: float = Field(0.0, description="Poids Logement", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    poids_education: float = Field(0.0, description="Poids Éducation", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    poids_inclusion: float = Field(0.0, description="Poids Inclusion", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    poids_mobilite: float = Field(0.0, description="Poids Mobilité", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    poids_sante: float = Field(0.0, description="Poids Santé", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    poids_territoire: float = Field(0.0, description="Poids Territoire", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    
    # Organization Context (F-54)
    org_context: Optional[str] = Field(None, description="Profil organisation", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "pdf_report"]})
    org_strategic_locations: List[str] = Field(default_factory=list, description="Zones stratégiques (Dep/BdV)", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    org_strategic_locations_type: Literal["departement", "bassin_de_vie"] = Field("departement", description="Type de zone stratégique", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    
    # Population target (F-50)
    target_population: int = Field(50000, description="Population cible", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    target_population_sigma: int = Field(25000, description="Tolerance population", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})
    
    # Organization Specific Boosts (F-54 Expansion)
    org_boosts: Dict[str, float] = Field(default_factory=dict, description="Multiplier boosts for specific criteria (e.g. {'heb_jaccueille_score': 3.0})", json_schema_extra={"odis_visibility": ["agent_refiner", "pdf_report"]})

    # Unified Briefing (F-58)
    odis_brief: str = Field(default="", description="Synthèse narrative du dossier (Briefing)", json_schema_extra={"odis_visibility": ["all"]})



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
        
        def _fix_value(v: Any) -> Any:
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
            'commune_actuelle', 'commune_pressentie', 'codes_metiers', 'codes_formations', 
            'inc_services_selection', 'inc_asso_add_selection', 'type_logement'
        ]
        
        for f in fields_to_fix:
            if f in data and data[f]:
                data[f] = _fix_value(data[f])
                
        # Fix notes_qualitatives if it's a string instead of a list
        if 'notes_qualitatives' in data and isinstance(data['notes_qualitatives'], str):
            data['notes_qualitatives'] = [data['notes_qualitatives']]
            
        return data

    def compute_hash(self) -> str:
        """Computes a stable MD5 hash for search criteria to detect changes."""
        criteria_json = self.model_dump_json(exclude={'active_criteria', 'active_categories', 'odis_brief'})
        return hashlib.md5(criteria_json.encode()).hexdigest()

class CommuneScoreDetail(BaseModel):
    """Represents the value and score of a specific indicator."""
    label: str = Field(description="Nom d'affichage lisible (ex: 'Écoles élémentaires')", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    score_id: str = Field(description="Code interne unique (ex: 'edu_elementaire_ct')", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    valeur_kpi: Optional[Union[float, int, str]] = Field(None, description="Valeur brute métier", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    score_normalise: float = Field(description="Score de 0.0 à 1.0 issu du ScoringEngine", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    unit: str = Field(description="Unité de la valeur brute (ex: 'habitants', '%')", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    relative_weight: float = Field(description="Poids relatif en % dans sa catégorie", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

class JobOfferDetail(BaseModel):
    """Represents a detailed job offer object from France Travail."""
    id: str = Field(..., description="Identifiant unique de l'offre", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    title: str = Field(..., description="Intitulé du poste", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    company: Optional[str] = Field(None, description="Nom de l'entreprise", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    contract_type: str = Field(..., description="Type de contrat (code CDD/CDI etc)", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    contract_label: Optional[str] = Field(None, description="Libellé du type de contrat", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    description: Optional[str] = Field(None, description="Description courte de l'offre", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    location: Optional[str] = Field(None, description="Lieu de travail (libellé)", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    location_insee: Optional[str] = Field(None, description="Code INSEE du lieu de travail", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    salary: Optional[str] = Field(None, description="Salaire proposé", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    url: Optional[str] = Field(None, description="Lien pour postuler", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    rome_code: Optional[str] = Field(None, description="Code ROME de l'offre", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    rome_label: Optional[str] = Field(None, description="Libellé ROME de l'offre", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    job_brief: Optional[str] = Field(None, description="Synthèse de l'offre et pourquoi elle correspond au candidat", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    date_creation: Optional[str] = Field(None, description="Date de création de l'offre", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    work_duration: Optional[str] = Field(None, description="Durée de travail (libellé)", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})
    experience: Optional[str] = Field(None, description="Expérience requise (libellé)", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

class EmploymentMetrics(BaseModel):
    cat_score: float = Field(0.0, description="Score Emploi", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    standard_jobs_total: int = Field(0, description="Total offres d'emploi", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    standard_jobs_summary: Dict[str, int] = Field(default_factory=dict, description="Résumé des offres par métier", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report"]})
    standard_jobs_matching_total: int = Field(0, description="Offres correspondant au projet", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_job_hunter", "ui_details", "pdf_report"]})
    standard_jobs_matching_summary: Dict[str, int] = Field(default_factory=dict, description="Résumé des offres correspondantes", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_job_hunter", "ui_details", "pdf_report"]})
    top_professions: List[str] = Field(default_factory=list, description="Top métiers en tension", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_job_hunter", "ui_details", "pdf_report"]})
    inclusive_jobs_total: int = Field(0, description="Offres inclusion (SIAE) totales", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_job_hunter", "ui_details", "pdf_report"]})
    inclusive_jobs_summary: Dict[str, int] = Field(default_factory=dict, description="Résumé des offres d'inclusion par métier", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_job_hunter", "ui_details", "pdf_report"]})
    inclusive_jobs_matching_total: int = Field(0, description="Offres inclusion correspondantes", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_job_hunter", "ui_details", "pdf_report"]})
    inclusive_jobs_matching_summary: Dict[str, Any] = Field(default_factory=dict, description="Résumé des offres d'inclusion", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_job_hunter", "ui_details", "pdf_report"]})
    training_programs: List[str] = Field(default_factory=list, description="Formations disponibles", json_schema_extra={"odis_visibility": ["agent_job_hunter", "ui_details", "pdf_report"]})
    training_programs_matching: List[str] = Field(default_factory=list, description="Formations correspondant au projet", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_job_hunter", "ui_details", "pdf_report"]})
    matching_job_offers: List[List[JobOfferDetail]] = Field(default_factory=list, description="Liste des offres d'emploi correspondantes séparées par adulte du ménage", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report", "agent_job_hunter"]})


class HousingMetrics(BaseModel):
    cat_score: float = Field(0.0, description="Score Logement", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    host_count: int = Field(0, description="Nombre d'accueillants J'Accueille", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_housing_expert", "ui_details", "pdf_report"]})
    price_per_sqm: Optional[float] = Field(None, description="Loyer moyen au m²", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_housing_expert", "ui_details", "pdf_report"]})
    housing_price_variants: Dict[str, Dict[str, Optional[float]]] = Field(default_factory=dict, description="Détails des loyers par type", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    log_soc_delay: Optional[float] = Field(None, description="Délai moyen d'attente (mois)", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_housing_expert", "ui_details", "pdf_report"]})

class EducationMetrics(BaseModel):
    cat_score: float = Field(0.0, description="Score Éducation", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    facility_counts: Dict[str, int] = Field(default_factory=dict, description="Nombre d'établissements scolaires", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    facility_details: Dict[str, List[str]] = Field(default_factory=dict, description="Noms des établissements par type", json_schema_extra={"odis_visibility": ["ui_details"]})

class HealthMetrics(BaseModel):
    cat_score: float = Field(0.0, description="Score Santé", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    facility_counts: Dict[str, int] = Field(default_factory=dict, description="Nombre d'établissements de santé", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    facility_details: Dict[str, List[str]] = Field(default_factory=dict, description="Noms des établissements de santé par type", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_healthcare_expert", "ui_details", "pdf_report"]})
    sante_rdv_delay: Optional[float] = Field(None, description="Accessibilité Potentielle Localisée (APL)", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_healthcare_expert", "ui_details", "pdf_report"]})

class AssociationDetail(BaseModel):
    """Represents a detailed association object from the enrichment process."""
    id: str = Field(..., description="Identifiant unique (WALDEC)", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report"]})
    name: str = Field(..., description="Nom de l'association", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report"]})
    description: Optional[str] = Field(None, description="Description de l'activité", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report"]})
    waldec_label: Optional[str] = Field(None, description="Libellé de la catégorie WALDEC", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report"]})
    refugee_focused: bool = Field(False, description="Si l'association est dédiée aux réfugiés", json_schema_extra={"odis_visibility": ["ui_details", "pdf_report"]})

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

class InclusionMetrics(BaseModel):
    cat_score: float = Field(0.0, description="Score Inclusion", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    asso_inclusion_count: int = Field(0, description="Nombre d'associations d'inclusion", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    asso_inclusion_list_by_cat: Dict[str, List[AssociationDetail]] = Field(default_factory=dict, description="Associations d'inclusion par thématique", json_schema_extra={"odis_visibility": ["agent_social_integration_expert", "ui_details", "pdf_report"]})
    asso_refugee_count: int = Field(0, description="Nombre d'associations d'aide aux réfugiés", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    asso_refugee_list: List[AssociationDetail] = Field(default_factory=list, description="Liste des associations d'aide aux réfugiés", json_schema_extra={"odis_visibility": ["agent_social_integration_expert", "ui_details", "pdf_report"]})
    services_grouped: Dict[str, List[str]] = Field(default_factory=dict, description="Services d'inclusion groupés par thématique", json_schema_extra={"odis_visibility": ["agent_social_integration_expert", "ui_details", "pdf_report"]})

class MobilityMetrics(BaseModel):
    cat_score: float = Field(0.0, description="Score Mobilité", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report", "agent_ts_agent"]})
    bus_stops: int = Field(0, description="Arrêts de bus", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_mobility_expert", "ui_details", "pdf_report"]})
    tram_stops: int = Field(0, description="Arrêts de tram", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_mobility_expert", "ui_details", "pdf_report"]})
    metro_stops: int = Field(0, description="Arrêts de métro", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_mobility_expert", "ui_details", "pdf_report"]})
    train_stops: int = Field(0, description="Arrêts de train", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_mobility_expert", "ui_details", "pdf_report"]})
    total_stops: int = Field(0, description="Total arrêts transports en commun", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_mobility_expert", "ui_details", "pdf_report"]})
    stop_density: float = Field(0.0, description="Densité d'arrêts (pour 1000 hab.)", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_mobility_expert", "ui_details", "pdf_report"]})
    is_same_epci: Optional[bool] = Field(None, description="Même EPCI que commune actuelle", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    distance_to_current_km: Optional[float] = Field(None, description="Distance commune actuelle (km)", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "ui_details", "pdf_report"]})
    mob_dur_share: Optional[float] = Field(None, description="Part des transports durables", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_mobility_expert", "ui_details", "pdf_report"]})

class TerritoryMetrics(BaseModel):
    cat_score: float = Field(0.0, description="Score Territoire", json_schema_extra={"odis_visibility": ["agent_refiner", "ui_details", "pdf_report", "agent_ts_agent"]})
    is_strategic: bool = Field(False, description="Territoire stratégique", json_schema_extra={"odis_visibility": ["agent_refiner", "ui_details", "pdf_report"]})
    ter_insecurite: Optional[float] = Field(None, description="Indice d'insécurité (taux cumulé)", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer", "agent_social_integration_expert", "ui_details", "pdf_report"]})

class CommuneResult(BaseModel):
    """Encapsulates identity, scores, and metadata for a specific commune."""
    # Identity
    codgeo: str = Field(description="Code INSEE de la commune", json_schema_extra={"odis_visibility": ["all"]})
    name: str = Field(description="Nom de la commune", json_schema_extra={"odis_visibility": ["all"]})
    population: int = Field(default=0, description="Population de la commune", json_schema_extra={"odis_visibility": ["all"]})
    codgeo_bdv: str = Field(default="", description="Code Bassin de Vie de la commune", json_schema_extra={"odis_visibility": ["all"]})
    name_bdv: str = Field(default="", description="Nom Bassin de Vie de la commune", json_schema_extra={"odis_visibility": ["all"]})
    
    # Global score
    global_score: float = Field(default=0.0, description="Score global", json_schema_extra={"odis_visibility": ["all"]})
    
    # Thematic scores (grouped by category)
    scores: Dict[str, List[CommuneScoreDetail]] = Field(default_factory=dict, description="Details grouped by category", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer"]})
    
    # Domain specific aggregations (Strongly typed)
    employment: EmploymentMetrics = Field(
        default_factory=EmploymentMetrics,
        description="Données emploi et formation",
        json_schema_extra={"odis_visibility": ["all"]}
    )
    housing: HousingMetrics = Field(
        default_factory=HousingMetrics,
        description="Données logement",
        json_schema_extra={"odis_visibility": ["all"]}
    )
    education: EducationMetrics = Field(
        default_factory=EducationMetrics,
        description="Données éducation",
        json_schema_extra={"odis_visibility": ["all"]}
    )
    health: HealthMetrics = Field(
        default_factory=HealthMetrics,
        description="Données santé",
        json_schema_extra={"odis_visibility": ["all"]}
    )
    inclusion: InclusionMetrics = Field(
        default_factory=InclusionMetrics,
        description="Données inclusion",
        json_schema_extra={"odis_visibility": ["all"]}
    )
    mobility: MobilityMetrics = Field(
        default_factory=MobilityMetrics,
        description="Données mobilité",
        json_schema_extra={"odis_visibility": ["all"]}
    )
    territoire: TerritoryMetrics = Field(
        default_factory=TerritoryMetrics,
        description="Données territoire",
        json_schema_extra={"odis_visibility": ["all"]}
    )

    # Agent-generated content
    refiner_pitch: str = Field(default="", description="Résumé du Refiner", json_schema_extra={"odis_visibility": ["all"]})
    expert_analysis: Dict[str, str] = Field(default_factory=dict, description="Analyses experts", json_schema_extra={"odis_visibility": ["all"]})
    odis_synthesis: List[Dict[str, str]] = Field(default_factory=list, description="List of messages (conversation thread) for the city analysis", json_schema_extra={"odis_visibility": ["all"]})
    
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
    search_hash: str = Field(description="MD5 hash of the criteria used", json_schema_extra={"odis_visibility": ["all"]})
    results: List[CommuneResult] = Field(default_factory=list, description="Top recommended communes in rank order", json_schema_extra={"odis_visibility": ["agent_refiner"]})
    current_geo: CommuneResult = Field(..., description="Reference data for the user current location", json_schema_extra={"odis_visibility": ["agent_refiner", "agent_synthesizer"]})
    commune_pressentie: Optional[CommuneResult] = Field(None, description="Données de la commune pressentie", json_schema_extra={"odis_visibility": ["all"]})
    global_pitch: str = Field(default="", description="Global introduction from Scorer Agent", json_schema_extra={"odis_visibility": ["all"]})
    
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    def get_by_code(self, codgeo: str) -> Optional[CommuneResult]:
        """Helper to find a result by its INSEE code."""
        if self.commune_pressentie and self.commune_pressentie.codgeo == codgeo:
            return self.commune_pressentie
        if self.current_geo and self.current_geo.codgeo == codgeo:
            return self.current_geo
        return next((c for c in self.results if c.codgeo == codgeo), None)
