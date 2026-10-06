"""Pydantic schemas validating pipeline domain contracts and taxonomies.

This module defines models for all declarative configurations in
``pipeline/domain_contracts.yaml``, providing typing and validation
for BPE codes, political nuances, demographic cuts, and ETL thresholds.
"""

from __future__ import annotations

from typing import Dict, List
from pydantic import BaseModel, ConfigDict, Field


class BpeEducationCategory(BaseModel):
    """Configuration for an education indicator extraction."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Internal indicator name (e.g. is_maternelle)")
    codes: List[str] = Field(
        ..., description="BPE equipment codes matching this category"
    )
    output_col: str = Field(..., description="Output aggregated column name")


class BpeEducationConfig(BaseModel):
    """Configuration for BPE education indicators and POI labels."""

    model_config = ConfigDict(extra="forbid")

    categories: List[BpeEducationCategory]
    labels: Dict[str, str] = Field(
        ..., description="POI display labels by equipment code"
    )


class BpePattern(BaseModel):
    """Regex pattern for BPE equipment parsing (e.g. D710)."""

    model_config = ConfigDict(extra="forbid")

    regex: str
    sub_type: str


class BpeColumnMapping(BaseModel):
    """Mapping from sub_type to indicator column name and parquet column."""

    model_config = ConfigDict(extra="forbid")

    indicator: str
    output_col: str


class BpeHebergementConfig(BaseModel):
    """Configuration for BPE accommodation and housing indicators."""

    model_config = ConfigDict(extra="forbid")

    exact_types: Dict[str, str] = Field(
        ..., description="Direct mapping from equipment code to subtype"
    )
    d710_patterns: List[BpePattern] = Field(
        ..., description="Regex patterns for D710 equipment disambiguation"
    )
    columns: Dict[str, BpeColumnMapping] = Field(
        ..., description="Aggregation mapping per accommodation subtype"
    )


class BpeSanteIndicator(BaseModel):
    """Health indicator extraction descriptor."""

    model_config = ConfigDict(extra="forbid")

    code: str
    indicator: str
    output_col: str
    label: str


class BpeSanteConfig(BaseModel):
    """Configuration for BPE health equipment."""

    model_config = ConfigDict(extra="forbid")

    indicators: List[BpeSanteIndicator]


class BpeActionSocialeIndicator(BaseModel):
    """Social action indicator extraction descriptor."""

    model_config = ConfigDict(extra="forbid")

    code: str
    indicator: str
    output_col: str
    label: str
    category: str


class BpeActionSocialeConfig(BaseModel):
    """Configuration for BPE social action equipment and town halls."""

    model_config = ConfigDict(extra="forbid")

    indicators: List[BpeActionSocialeIndicator]


class BpeGaresConfig(BaseModel):
    """Configuration for BPE train stations."""

    model_config = ConfigDict(extra="forbid")

    types: List[str]
    poi_label: str
    poi_category: str
    output_count_col: str
    output_flag_col: str


class BpeConfig(BaseModel):
    """Unified BPE equipment domain configuration."""

    model_config = ConfigDict(extra="forbid")

    target_types: List[str] = Field(
        ..., description="Equipment codes retained during preprocessing"
    )
    education: BpeEducationConfig
    hebergement: BpeHebergementConfig
    sante: BpeSanteConfig
    action_sociale: BpeActionSocialeConfig
    gares: BpeGaresConfig


class ElectoralConfig(BaseModel):
    """Configuration for political nuances, elections, and history parsing."""

    model_config = ConfigDict(extra="forbid")

    far_right_nuances: List[str]
    far_right_family: str
    election_pattern: str
    max_history_per_category: int
    election_labels: Dict[str, str]
    tour_labels: Dict[str, str]
    election_dates: Dict[str, str]
    nuance_labels: Dict[str, str]


class DemographicsActivePopulationConfig(BaseModel):
    """Configuration for active population filtering."""

    model_config = ConfigDict(extra="forbid")

    pcs_filter: str
    employment_status_codes: List[str]
    code_active: str
    code_employed: str


class DemographicsPopulationDetailsConfig(BaseModel):
    """Configuration for population age breakdown."""

    model_config = ConfigDict(extra="forbid")

    sex_filter: str
    target_age_codes: List[str]
    age_group_mapping: Dict[str, str]
    expected_columns: List[str]


class DemographicsConfig(BaseModel):
    """Demographics domain configurations."""

    model_config = ConfigDict(extra="forbid")

    active_population: DemographicsActivePopulationConfig
    population_details: DemographicsPopulationDetailsConfig


class EducationConfig(BaseModel):
    """Education domain configurations."""

    model_config = ConfigDict(extra="forbid")

    risky_school_class_threshold: float
    uai_column_candidates: List[str]
    insee_column_candidates: List[str]


class MobilityConfig(BaseModel):
    """Mobility domain configurations."""

    model_config = ConfigDict(extra="forbid")

    sustainable_modes: List[str]
    public_transport_modes: Dict[str, str]


class InclusionConfig(BaseModel):
    """Social inclusion domain configurations."""

    model_config = ConfigDict(extra="forbid")

    ccas_keywords: List[str]
    required_contact_fields: List[str]


class RnaRagConfig(BaseModel):
    """RNA BigQuery RAG hyperparameters and query prompts."""

    model_config = ConfigDict(extra="forbid")

    embedding_model: str
    output_dimensionality: int
    similarity_threshold: float
    hebergement_prompts: Dict[str, str]


class GeographyConfig(BaseModel):
    """Geographical referential configurations."""

    model_config = ConfigDict(extra="forbid")

    plm_communes: List[str] = Field(
        ..., description="INSEE codes for Paris, Marseille, and Lyon parent communes"
    )


class HousingConfig(BaseModel):
    """Housing and rent configurations."""

    model_config = ConfigDict(extra="forbid")

    odace_rent_priority: Dict[str, int] = Field(
        ..., description="Priority mapping for observation spatial scales"
    )
    default_rent_priority: int = Field(
        99, description="Fallback priority for unknown observation scales"
    )


class RunnerConfig(BaseModel):
    """ETL runner staging mappings and filename conventions."""

    model_config = ConfigDict(extra="forbid")

    cleaned_sources: List[str]
    step_to_source_map: Dict[str, str]
    clean_filenames: Dict[str, str]
    output_dir_steps: List[str] = Field(
        default_factory=list,
        description="Steps whose primary clean artifact is written to OUTPUT_DIR rather than CLEAN_DIR",
    )


class DomainContracts(BaseModel):
    """Root model validating the entire pipeline/domain_contracts.yaml catalog."""

    model_config = ConfigDict(extra="forbid")

    bpe: BpeConfig
    electoral: ElectoralConfig
    demographics: DemographicsConfig
    education: EducationConfig
    mobility: MobilityConfig
    inclusion: InclusionConfig
    rna_rag: RnaRagConfig
    geography: GeographyConfig
    housing: HousingConfig
    runner: RunnerConfig
