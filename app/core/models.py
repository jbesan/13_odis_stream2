from typing import List, Dict, Any, Union, Optional, Set, Literal
import functools
import hashlib
import logging
from pathlib import Path
import yaml
from pydantic import BaseModel, Field, ConfigDict, model_validator, field_validator
import config as cfg
from config import Org, User

logger = logging.getLogger(__name__)


ScoreCategory = Literal[
    "education", "emploi", "inclusion", "logement", "territoire", "sante", "mobilite"
]
ScoreComputation = Literal["precomputed", "live", "calculated"]
ScoreMissingStrategy = Literal["exclude", "zero"]


class ScoreDisplayConfigSchema(BaseModel):
    name: str
    strong_point_text: Optional[str] = None
    high_value_adjective: Optional[str] = None
    show: bool = True
    unit: Optional[str] = None
    display_factor: Optional[Union[int, float]] = 1
    tooltip: Optional[str] = None
    format: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class ScoreConfigItemSchema(BaseModel):
    id: str
    category: ScoreCategory
    source_metric: Optional[str] = None
    bdv_factor: float = 0.0
    computation: ScoreComputation
    display: Optional[ScoreDisplayConfigSchema] = None
    weight: float = 1.0
    min_bound: Optional[float] = None
    max_bound: Optional[float] = None
    quantile_level: Optional[float] = Field(None, ge=0.0, le=0.5)
    missing_strategy: ScoreMissingStrategy = "exclude"
    baseline: bool = False

    model_config = ConfigDict(extra="forbid")


class ScoresConfigFileSchema(BaseModel):
    scores: List[ScoreConfigItemSchema]

    model_config = ConfigDict(extra="forbid")

    @classmethod
    @functools.lru_cache(maxsize=1)
    def load_default(cls) -> "ScoresConfigFileSchema":
        """Loads, validates, and caches the score catalog from scores_config.yaml."""
        config_path = Path(__file__).parent.parent / "scores_config.yaml"
        if not config_path.exists():
            return cls(scores=[])
        with open(config_path, "r", encoding="utf-8") as f:
            cfg_data = yaml.safe_load(f) or {}
        return cls.model_validate(cfg_data)

    @classmethod
    def get_valid_ids(cls) -> Set[str]:
        """Returns the set of all valid criterion IDs in scores_config.yaml."""
        return {item.id for item in cls.load_default().scores}


class CriteriaItem(BaseModel):
    """
    Representation of a criteria item with both code and label.
    """

    code: str = Field(..., description="Technical ID")
    label: str = Field(
        ...,
        description="Human-readable label",
    )

    model_config = ConfigDict(
        populate_by_name=True, revalidate_instances="never", frozen=True
    )

    @model_validator(mode="before")
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, "model_dump") else data.__dict__
        return data


class SearchCriterias(BaseModel):
    """
    Criteria for searching and scoring cities based on user needs.
    This model forces the use of enriched items (CriteriaItem) for key fields.
    """

    commune_actuelle: Optional[CriteriaItem] = Field(
        None,
        description="Commune actuelle de résidence",
    )
    commune_pressentie: Optional[CriteriaItem] = Field(
        None,
        description="Commune pressentie à titre de comparaison",
    )
    loc_search_area: str = Field(
        "",
        description="Zone de recherche (département, région, France)",
    )
    loc_search_code: List[str] = Field(
        default_factory=list,
        description="Codes géographiques de la zone de recherche",
    )

    nb_adultes: int = Field(
        1,
        description="Nombre d'adultes",
    )
    nb_enfants: int = Field(
        0,
        description="Nombre d'enfants",
    )

    classe_enfants: List[str] = Field(
        default_factory=list,
        description="Niveaux scolaires recherchés",
    )

    # Force CriteriaItem for enriched fields
    codes_metiers: List[List[CriteriaItem]] = Field(
        default_factory=list,
        description="Métiers ciblés par adulte",
    )
    codes_formations: List[List[CriteriaItem]] = Field(
        default_factory=list,
        description="Formations ciblées",
    )

    inc_services_selection: List[CriteriaItem] = Field(
        default_factory=list,
        description="Services d'inclusion sélectionnés",
    )
    inc_asso_add_selection: List[CriteriaItem] = Field(
        default_factory=list,
        description="Associations et centres d'intérêt",
    )

    hebergement_cible: List[str] = Field(
        default_factory=list,
        description="Hébergement souhaité",
    )
    logement: Optional[Union[List[str], str]] = Field(
        None,
        description="Type de logement (ex: Logement Social)",
    )
    type_logement: Optional[CriteriaItem] = Field(
        None,
        description="Type de bien (Appartement, Maison)",
    )
    besoin_sante: List[str] = Field(
        default_factory=list,
        description="Besoin de santé spécifique",
    )
    freq_retour: str = Field(
        "1 fois/mois",
        description="Fréquence de retour vers la commune actuelle",
    )

    # Qualitative notes (free text indices for Scout and Synthesis)
    notes_qualitatives: List[str] = Field(
        default_factory=list,
        description="Notes qualitatives sur le projet de vie",
    )

    # Final scoring priority
    weight_profile: str = Field(
        "",
        description="Profil de pondération",
    )
    criteria_weights: Dict[str, float] = Field(
        default_factory=dict,
        description="Poids personnalisés par critère",
    )

    @field_validator("criteria_weights", mode="before")
    @classmethod
    def validate_criteria_weights(cls, v: Any) -> Dict[str, float]:
        if not isinstance(v, dict):
            return {}
        valid_ids = ScoresConfigFileSchema.get_valid_ids()
        if not valid_ids:
            return {str(k): float(val) for k, val in v.items()}
        sanitized = {}
        for key, weight in v.items():
            s_key = str(key)
            if s_key in valid_ids:
                sanitized[s_key] = float(weight)
            else:
                logger.warning(
                    f"Removing unknown criterion ID '{s_key}' from criteria_weights."
                )
        return sanitized

    # Global Category Weights
    poids_emploi: float = Field(
        0.0,
        description="Poids Emploi",
    )
    poids_logement: float = Field(
        0.0,
        description="Poids Logement",
    )
    poids_education: float = Field(
        0.0,
        description="Poids Éducation",
    )
    poids_inclusion: float = Field(
        0.0,
        description="Poids Inclusion",
    )
    poids_mobilite: float = Field(
        0.0,
        description="Poids Mobilité",
    )
    poids_sante: float = Field(
        0.0,
        description="Poids Santé",
    )
    poids_territoire: float = Field(
        0.0,
        description="Poids Territoire",
    )

    # Organization Context (F-54)
    org_context: Optional[str] = Field(
        None,
        description="Profil organisation",
    )
    org_strategic_locations: List[str] = Field(
        default_factory=list,
        description="Zones stratégiques (Dep/BdV)",
    )
    org_strategic_locations_type: Literal["departement", "bassin_de_vie"] = Field(
        "departement",
        description="Type de zone stratégique",
    )
    org_strategic_locations_filter: bool = Field(
        False,
        description="Restreindre aux zones opérationnelles",
    )

    # Bassin de Vie demographic target (Trapezoid bounds: a, b, c, d)
    target_city_size: Optional[str] = Field(
        default_factory=lambda: cfg.DEFAULT_CITY_SIZE,
        description="Type de territoire / bassin de vie cible",
    )
    target_population_a: int = Field(
        default_factory=lambda: cfg.DEFAULT_TRAPEZOID["a"],
        description="Plancher absolu (a)",
    )
    target_population_b: int = Field(
        default_factory=lambda: cfg.DEFAULT_TRAPEZOID["b"],
        description="Début du plateau idéal (b)",
    )
    target_population_c: int = Field(
        default_factory=lambda: cfg.DEFAULT_TRAPEZOID["c"],
        description="Fin du plateau idéal (c)",
    )
    target_population_d: int = Field(
        default_factory=lambda: cfg.DEFAULT_TRAPEZOID["d"],
        description="Plafond d'exclusion (d)",
    )
    target_population: Optional[int] = Field(
        None,
        description="Legacy target population (deprecated in favor of trapezoid bounds)",
    )
    target_population_sigma: Optional[int] = Field(
        None,
        description="Legacy target population sigma (deprecated in favor of trapezoid bounds)",
    )

    # Organization Specific Boosts (F-54 Expansion)
    org_boosts: Dict[str, float] = Field(
        default_factory=dict,
        description="Multiplier boosts for specific criteria (e.g. {'heb_jaccueille_accueillants_score': 3.0})",
    )

    # Unified Briefing (F-58)
    odis_brief: str = Field(
        default="",
        description="Synthèse narrative du dossier (Briefing)",
    )

    active_criteria: Optional[Set[str]] = Field(
        None, description="Set of active criteria computed by the engine"
    )
    active_categories: List[str] = Field(
        default_factory=list, description="List of categories with active criteria"
    )

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
        revalidate_instances="never",
    )

    @model_validator(mode="before")
    @classmethod
    def handle_redefinition(cls, data: Any) -> Any:
        if data.__class__.__name__ == cls.__name__ and not isinstance(data, cls):
            return data.model_dump() if hasattr(data, "model_dump") else data.__dict__
        return data

    @field_validator("besoin_sante", mode="before")
    @classmethod
    def validate_besoin_sante(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            return [] if v == "Aucun" or not v else [v]
        if v is None:
            return []
        return v

    @model_validator(mode="before")
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
        pattern = (
            r"CriteriaItem\(code=['\"]([^'\"]+)['\"],\s*label=['\"]([^'\"]+)['\"]\)"
        )

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
            "commune_actuelle",
            "commune_pressentie",
            "codes_metiers",
            "codes_formations",
            "inc_services_selection",
            "inc_asso_add_selection",
            "type_logement",
        ]

        for f in fields_to_fix:
            if f in data and data[f]:
                data[f] = _fix_value(data[f])

        # Fix notes_qualitatives if it's a string instead of a list
        if "notes_qualitatives" in data and isinstance(data["notes_qualitatives"], str):
            data["notes_qualitatives"] = [data["notes_qualitatives"]]

        # Synchronize trapezoid bounds from target_city_size or legacy target_population
        size_label = data.get("target_city_size") or data.get("ui_target_city_size_label")
        if size_label and size_label in cfg.CITY_SIZE_MAPPING:
            bounds = cfg.CITY_SIZE_MAPPING[size_label]
            data.setdefault("target_population_a", bounds["a"])
            data.setdefault("target_population_b", bounds["b"])
            data.setdefault("target_population_c", bounds["c"])
            data.setdefault("target_population_d", bounds["d"])
        elif data.get("target_population") is not None and "target_population_a" not in data:
            pop = data["target_population"]
            if pop <= 10000:
                bounds = cfg.CITY_SIZE_MAPPING["🚜 Commune rurale"]
            elif pop <= 30000:
                bounds = cfg.CITY_SIZE_MAPPING["🏡 Bourg"]
            elif pop <= 80000:
                bounds = cfg.CITY_SIZE_MAPPING["🏘️ Petite Ville"]
            else:
                bounds = cfg.CITY_SIZE_MAPPING["🏙️ Ville moyenne"]
            data["target_population_a"] = bounds["a"]
            data["target_population_b"] = bounds["b"]
            data["target_population_c"] = bounds["c"]
            data["target_population_d"] = bounds["d"]

        return data

    def compute_hash(self) -> str:
        """Computes a stable MD5 hash for search criteria to detect changes."""
        criteria_json = self.model_dump_json(
            exclude={"active_criteria", "active_categories", "odis_brief"}
        )
        return hashlib.md5(criteria_json.encode()).hexdigest()


class CommuneScoreDetail(BaseModel):
    """Represents the value and score of a specific indicator."""

    label: str = Field(
        description="Nom d'affichage lisible (ex: 'Écoles élémentaires')",
    )
    score_id: str = Field(
        description="Code interne unique (ex: 'edu_elementaire_ct')",
    )
    valeur_kpi: Optional[Union[float, int, str]] = Field(
        None,
        description="Valeur brute métier",
    )
    score_normalise: float = Field(
        description="Score de 0.0 à 1.0 issu du ScoringEngine",
    )
    unit: str = Field(
        description="Unité de la valeur brute (ex: 'habitants', '%')",
    )
    relative_weight: float = Field(
        description="Poids relatif en % dans sa catégorie",
    )
    valeur_kpi_commune: Optional[Union[float, int, str]] = Field(
        None,
        description="Valeur brute locale de la commune",
    )
    valeur_kpi_bdv: Optional[Union[float, int, str]] = Field(
        None,
        description="Valeur brute du Bassin de Vie",
    )
    score_normalise_commune: Optional[float] = Field(
        None,
        description="Score normé local de la commune (avant BdV)",
    )
    score_normalise_bdv: Optional[float] = Field(
        None,
        description="Score normé du Bassin de Vie",
    )
    bdv_factor: float = Field(
        0.0,
        description="Facteur d'influence du Bassin de Vie",
    )
    bdv_applied: bool = Field(
        False,
        description="Indique si une combinaison BdV s'applique sur ce critère",
    )
    strong_point_text: Optional[str] = Field(
        "",
        description="Phrase décrivant le point fort",
    )
    high_value_adjective: Optional[str] = Field(
        "",
        description="Adjectif pour les valeurs élevées",
    )

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class JobOfferDetail(BaseModel):
    """Represents a detailed job offer object from France Travail."""

    id: str = Field(
        ...,
        description="Identifiant unique de l'offre",
    )
    title: str = Field(
        ...,
        description="Intitulé du poste",
    )
    company: Optional[str] = Field(
        None,
        description="Nom de l'entreprise",
    )
    contract_type: str = Field(
        ...,
        description="Type de contrat (code CDD/CDI etc)",
    )
    contract_label: Optional[str] = Field(
        None,
        description="Libellé du type de contrat",
    )
    description: Optional[str] = Field(
        None,
        description="Description courte de l'offre",
    )
    location: Optional[str] = Field(
        None,
        description="Lieu de travail (libellé)",
    )
    location_insee: Optional[str] = Field(
        None,
        description="Code INSEE du lieu de travail",
    )
    salary: Optional[str] = Field(
        None,
        description="Salaire proposé",
    )
    url: Optional[str] = Field(
        None,
        description="Lien pour postuler",
    )
    rome_code: Optional[str] = Field(
        None,
        description="Code ROME de l'offre",
    )
    rome_label: Optional[str] = Field(
        None,
        description="Libellé ROME de l'offre",
    )
    job_brief: Optional[str] = Field(
        None,
        description="Synthèse de l'offre et pourquoi elle correspond au candidat",
    )
    date_creation: Optional[str] = Field(
        None,
        description="Date de création de l'offre",
    )
    work_duration: Optional[str] = Field(
        None,
        description="Durée de travail (libellé)",
    )
    experience: Optional[str] = Field(
        None,
        description="Expérience requise (libellé)",
    )

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class EmploymentMetrics(BaseModel):
    cat_score: float = Field(
        0.0,
        description="Score Emploi",
    )
    source_availability: Dict[str, Literal["available", "unavailable"]] = Field(
        default_factory=dict,
        description="Disponibilité des sources d'offres utilisées par les indicateurs emploi",
    )
    standard_jobs_total: int = Field(
        0,
        description="Total offres d'emploi",
    )
    standard_jobs_summary: Dict[str, int] = Field(
        default_factory=dict,
        description="Résumé des offres par métier",
    )
    standard_jobs_matching_total: int = Field(
        0,
        description="Offres correspondant au projet",
    )
    standard_jobs_matching_summary: Dict[str, int] = Field(
        default_factory=dict,
        description="Résumé des offres correspondantes",
    )
    top_professions: List[str] = Field(
        default_factory=list,
        description="Top métiers en tension",
    )
    inclusive_jobs_total: int = Field(
        0,
        description="Offres inclusion (SIAE) totales",
    )
    inclusive_jobs_summary: Dict[str, int] = Field(
        default_factory=dict,
        description="Résumé des offres d'inclusion par métier",
    )
    inclusive_jobs_matching_total: int = Field(
        0,
        description="Offres inclusion correspondantes",
    )
    inclusive_jobs_matching_summary: Dict[str, Any] = Field(
        default_factory=dict,
        description="Résumé des offres d'inclusion",
    )
    training_programs: List[str] = Field(
        default_factory=list,
        description="Formations disponibles",
    )
    training_programs_matching: List[str] = Field(
        default_factory=list,
        description="Formations correspondant au projet",
    )
    matching_job_offers: List[List[JobOfferDetail]] = Field(
        default_factory=list,
        description="Liste des offres d'emploi correspondantes séparées par adulte du ménage",
    )


class HousingMetrics(BaseModel):
    cat_score: float = Field(
        0.0,
        description="Score Logement",
    )
    host_count: int = Field(
        0,
        description="Nombre d'accueillants J'Accueille",
    )
    price_per_sqm: Optional[float] = Field(
        None,
        description="Loyer moyen au m²",
    )
    housing_price_variants: Dict[str, Dict[str, Optional[float]]] = Field(
        default_factory=dict,
        description="Détails des loyers par type",
    )
    log_soc_delay: Optional[float] = Field(
        None,
        description="Délai moyen d'attente logement social (mois)",
    )


class EducationMetrics(BaseModel):
    cat_score: float = Field(
        0.0,
        description="Score Éducation",
    )
    facility_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Nombre d'établissements scolaires",
    )
    facility_details: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Noms des établissements par type",
    )


class HealthMetrics(BaseModel):
    cat_score: float = Field(
        0.0,
        description="Score Santé",
    )
    facility_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Nombre d'établissements de santé",
    )
    facility_details: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Noms des établissements de santé par type",
    )
    sante_rdv_delay: Optional[float] = Field(
        None,
        description="Accessibilité Potentielle Localisée (APL)",
    )


class AssociationDetail(BaseModel):
    """Represents a detailed association object from the enrichment process."""

    id: str = Field(
        ...,
        description="Identifiant unique (WALDEC)",
    )
    name: str = Field(
        ...,
        description="Nom de l'association",
    )
    description: Optional[str] = Field(
        None,
        description="Description de l'activité",
    )
    waldec_label: Optional[str] = Field(
        None,
        description="Libellé de la catégorie WALDEC",
    )
    refugee_focused: bool = Field(
        False,
        description="Si l'association est dédiée aux réfugiés",
    )

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class InclusionServiceDetail(BaseModel):
    """Represents a detailed inclusion service from the Data Inclusion API."""

    id: str = Field(
        ...,
        description="Identifiant unique du service",
    )
    name: str = Field(
        ...,
        description="Nom du service",
    )
    nom_structure: Optional[str] = Field(
        None,
        description="Nom de la structure proposant le service",
    )
    structure_id: Optional[str] = Field(
        None,
        description="Identifiant unique de la structure",
    )
    description: Optional[str] = Field(
        None,
        description="Description du service",
    )
    lien_source: Optional[str] = Field(
        None,
        description="Lien vers la fiche détaillée du service",
    )
    source: Optional[str] = Field(
        None,
        description="Source du service (ex: soliguide, dora)",
    )
    presentation_structure: Optional[str] = Field(
        None,
        description="Présentation de la structure",
    )
    distance_km: Optional[Union[int, float]] = Field(
        None,
        description="Distance à la commune cible en kilomètres",
    )
    commune_nom: Optional[str] = Field(
        None,
        description="Commune d'implantation de la structure",
    )
    code_postal: Optional[str] = Field(
        None,
        description="Code postal de la structure",
    )

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class InclusionMetrics(BaseModel):
    cat_score: float = Field(
        0.0,
        description="Score Inclusion",
    )
    asso_inclusion_count: int = Field(
        0,
        description="Nombre d'associations d'inclusion (source: RNA officiel)",
    )
    asso_inclusion_list_by_cat: Dict[str, List[AssociationDetail]] = Field(
        default_factory=dict,
        description="Associations d'inclusion par thématique (source: RNA officiel)",
    )
    asso_refugee_count: int = Field(
        0,
        description="Nombre d'associations d'aide aux réfugiés (source: RNA officiel)",
    )
    asso_refugee_list: List[AssociationDetail] = Field(
        default_factory=list,
        description="Liste des associations d'aide aux réfugiés (source: RNA officiel)",
    )
    services_grouped: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Services d'inclusion groupés par thématique (source: Data Inclusion)",
    )
    services_detailed: Dict[str, List[InclusionServiceDetail]] = Field(
        default_factory=dict,
        description="Services d'inclusion détaillés groupés par thématique (source: Data Inclusion)",
    )


class MobilityMetrics(BaseModel):
    cat_score: float = Field(
        0.0,
        description="Score Mobilité",
    )
    bus_stops: int = Field(
        0,
        description="Arrêts de bus",
    )
    tram_stops: int = Field(
        0,
        description="Arrêts de tram",
    )
    metro_stops: int = Field(
        0,
        description="Arrêts de métro",
    )
    train_stops: int = Field(
        0,
        description="Arrêts de train",
    )
    total_stops: int = Field(
        0,
        description="Total arrêts transports en commun",
    )
    stop_density: float = Field(
        0.0,
        description="Densité d'arrêts (pour 1000 hab.)",
    )
    is_same_epci: Optional[bool] = Field(
        None,
        description="Même EPCI que commune actuelle",
    )
    distance_to_current_km: Optional[float] = Field(
        None,
        description="Distance commune actuelle (km)",
    )
    mob_dur_share: Optional[float] = Field(
        None,
        description="Part des transports durables",
    )


class TerritoryMetrics(BaseModel):
    cat_score: float = Field(
        0.0,
        description="Score Territoire",
    )
    is_strategic: bool = Field(
        False,
        description="Territoire stratégique",
    )
    ter_insecurite: Optional[float] = Field(
        None,
        description="Indice d'insécurité (taux cumulé)",
    )
    maire_extreme_droite: bool = Field(
        False,
        description="Maire d'extrême droite",
    )
    electoral_history: Optional[Union[str, List[Any], Dict[str, Any]]] = Field(
        None,
        description="Historique électoral (JSON)",
    )


class DomainReport(BaseModel):
    """Rapport structuré d'un expert thématique (Logement, Mobilité, Santé, etc.)."""

    domain_key: str = Field(
        description="Identifiant unique de l'expert (ex: housing_expert, mobility_expert)"
    )
    label: str = Field(
        description="Libellé complet de la section (ex: 🏠 Logement & Hébergement)"
    )
    short_label: str = Field(
        description="Libellé court pour l'onglet UI (ex: 🏠 Logement)"
    )
    content: str = Field(
        description="Contenu textuel Markdown de la fiche expert"
    )
    sources: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Sources et requêtes de grounding associées",
    )
    artifacts: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Artefact de preuves et lacunes typé (DomainArtifact)",
    )

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class CityAnalysisReport(BaseModel):
    """Rapport d'analyse stratégique complet et structuré d'une commune cible."""

    city_name: str = Field(
        description="Nom de la commune analysée"
    )
    city_code: str = Field(
        description="Code INSEE (CODGEO) de la commune"
    )
    avis_global: str = Field(
        description="Avis Global d'Orientation stratégique pour la commune"
    )
    domains: Dict[str, DomainReport] = Field(
        default_factory=dict,
        description="Fiches thématiques structurées indexées par domain_key",
    )
    analyse_comparative: Optional[str] = Field(
        default=None,
        description="Tableau comparatif territorial digéré et synthèse des écarts",
    )
    elements_non_verifies: Optional[str] = Field(
        default=None,
        description="Points non vérifiés, données manquantes ou vigilances signalées",
    )
    ccas_contact: Optional[str] = Field(
        default=None,
        description="Fiche contact déterministe du CCAS communal ou de proximité",
    )
    et_ensuite: Optional[str] = Field(
        default=None,
        description="Pistes d'action concrètes et prochaines étapes",
    )

    def to_flat_markdown(self) -> str:
        """Assemble the complete analysis report in sequential Markdown format.

        Order:
        1. Avis Global d'Orientation
        2. Analyses Thématiques Détaillées (fiches experts)
        3. Analyse Comparative Territoriale
        4. Éléments Non Vérifiés & Vigilances
        5. Contact du CCAS
        6. Et ensuite ? (Pistes d'action)
        """
        report_sections: list[str] = []

        # 1. Executive overview (Top)
        if self.avis_global and self.avis_global.strip():
            report_sections.append(
                f"## 🧭 Avis Global d'Orientation pour {self.city_name}\n\n{self.avis_global.strip()}"
            )

        # 2. Domain Expert Artifacts (displayed as-is without LLM rephrasing)
        expert_sections = []
        for domain in self.domains.values():
            if domain.content and domain.content.strip():
                expert_sections.append(f"### {domain.label}\n\n{domain.content.strip()}")

        if expert_sections:
            report_sections.append(
                "# 🔬 Analyses Thématiques Détaillées\n\n" + "\n\n---\n\n".join(expert_sections)
            )

        # 3. Digested territorial comparison
        if self.analyse_comparative and self.analyse_comparative.strip():
            report_sections.append(
                f"## ⚖️ Analyse Comparative Territoriale\n\n{self.analyse_comparative.strip()}"
            )

        # 4. Unverified elements / gaps
        if self.elements_non_verifies and self.elements_non_verifies.strip():
            report_sections.append(
                f"## ⚠️ Éléments Non Vérifiés & Vigilances\n\n{self.elements_non_verifies.strip()}"
            )

        # 5. Call to Action: CCAS Contact
        if self.ccas_contact and self.ccas_contact.strip():
            report_sections.append(self.ccas_contact.strip())

        # 6. Call to Action: Et ensuite ?
        if self.et_ensuite and self.et_ensuite.strip():
            report_sections.append(
                f"## ❓ Et ensuite ? (Pistes d'action)\n\n{self.et_ensuite.strip()}"
            )

        return "\n\n---\n\n".join(report_sections)

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class CommuneResult(BaseModel):
    """Encapsulates identity, scores, and metadata for a specific commune."""

    # Identity
    codgeo: str = Field(
        description="Code INSEE de la commune",
    )
    name: str = Field(description="Nom de la commune")
    population: int = Field(
        default=0,
        description="Population de la commune",
    )
    codgeo_bdv: str = Field(
        default="",
        description="Code Bassin de Vie de la commune",
    )
    name_bdv: str = Field(
        default="",
        description="Nom Bassin de Vie de la commune",
    )

    # Global score
    global_score: float = Field(
        default=0.0,
        description="Score global final = score_besoins * coeff_population_gauss",
    )
    score_besoins: float = Field(
        default=0.0,
        description="Score moyen d'adéquation aux besoins (moyenne pondérée des catégories)",
    )
    coeff_population_gauss: float = Field(
        default=1.0,
        description="Coefficient global de correspondance démographique (0.0 à 1.0)",
    )

    # Thematic scores (grouped by category)
    scores: Dict[str, List[CommuneScoreDetail]] = Field(
        default_factory=dict,
        description="Details grouped by category",
    )

    # Domain specific aggregations (Strongly typed)
    employment: EmploymentMetrics = Field(
        default_factory=EmploymentMetrics,
        description="Données emploi et formation",
    )
    housing: HousingMetrics = Field(
        default_factory=HousingMetrics,
        description="Données logement",
    )
    education: EducationMetrics = Field(
        default_factory=EducationMetrics,
        description="Données éducation",
    )
    health: HealthMetrics = Field(
        default_factory=HealthMetrics,
        description="Données santé",
    )
    inclusion: InclusionMetrics = Field(
        default_factory=InclusionMetrics,
        description="Données inclusion",
    )
    mobility: MobilityMetrics = Field(
        default_factory=MobilityMetrics,
        description="Données mobilité",
    )
    territoire: TerritoryMetrics = Field(
        default_factory=TerritoryMetrics,
        description="Données territoire",
    )

    # Agent-generated content
    refiner_pitch: str = Field(
        default="",
        description="Résumé du Refiner",
    )
    expert_analysis: Dict[str, str] = Field(
        default_factory=dict,
        description="Analyses experts",
    )
    expert_artifacts: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Artefacts experts typés et sérialisés (preuves et lacunes)",
    )
    expert_sources: Dict[str, List[Dict[str, Any]]] = Field(
        default_factory=dict,
        description="Sources applicatives consultées par fiche expert",
    )
    odis_synthesis: List[Dict[str, str]] = Field(
        default_factory=list,
        description="List of messages (conversation thread) for the city analysis",
    )
    analysis_report: Optional[CityAnalysisReport] = Field(
        default=None,
        description="Rapport d'analyse stratégique complet et structuré",
    )

    @model_validator(mode="before")
    @classmethod
    def handle_odis_synthesis_type(cls, data: Any) -> Any:
        if isinstance(data, dict) and "odis_synthesis" in data:
            val = data["odis_synthesis"]
            if isinstance(val, str):
                # Convert legacy string to a list of one assistant message if not empty
                data["odis_synthesis"] = (
                    [{"role": "assistant", "content": val}] if val else []
                )
        return data

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)


class SearchResultsData(BaseModel):
    """Main payload container for search results."""

    search_hash: str = Field(
        description="MD5 hash of the criteria used",
    )
    results: List[CommuneResult] = Field(
        default_factory=list,
        description="Top recommended communes in rank order",
    )
    current_geo: CommuneResult = Field(
        ...,
        description="Reference data for the user current location",
    )
    commune_pressentie: Optional[CommuneResult] = Field(
        None,
        description="Données de la commune pressentie",
    )
    global_pitch: str = Field(
        default="",
        description="Global introduction from Scorer Agent",
    )

    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    def get_by_code(self, codgeo: str) -> Optional[CommuneResult]:
        """Helper to find a result by its INSEE code."""
        if self.commune_pressentie and self.commune_pressentie.codgeo == codgeo:
            return self.commune_pressentie
        if self.current_geo and self.current_geo.codgeo == codgeo:
            return self.current_geo
        return next((c for c in self.results if c.codgeo == codgeo), None)
