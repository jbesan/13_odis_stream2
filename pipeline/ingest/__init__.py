"""Modular ingestion pipeline package for ODIS.

This package provides domain cleaners, acquisition fetchers, execution runner,
and configuration contracts, maintaining 100% backward compatibility with
existing callers, tests, and run contexts.
"""

from __future__ import annotations

import sys
import types
from typing import Any

from google import genai
from google.cloud import bigquery

from pipeline.common import (
    PipelineLogger,
    fetch_remote_metadata_datagouv,
    is_cache_valid,
    load_config,
    load_dataset,
    validate_dataset_contract,
)
from pipeline.emplois_inclusion_ingest import run_ingestion as run_inclusion_ingest
from pipeline.ft_live_ingest import get_token as get_ft_token, run_etl
from pipeline.ingest.cleaners import (
    clean_associations,
    clean_bpe,
    clean_caf,
    clean_codes_postaux,
    clean_communes,
    clean_departements,
    clean_electoral_history,
    clean_formations,
    clean_hebergement_rna,
    clean_housing_occupation,
    clean_inclusion_jobs,
    clean_live_jobs,
    clean_log_soc_delay,
    clean_lovac,
    clean_loyers,
    clean_mob_durable,
    clean_mob_transports_pub,
    clean_nomenclature_waldec,
    clean_odace_gares,
    clean_odace_rent,
    clean_political,
    clean_population,
    clean_population_active,
    clean_population_details,
    clean_refugee_associations,
    clean_rpls,
    clean_salesforce_jaccueille,
    clean_sante_apl,
    clean_school_effectifs,
    clean_services_inclusion,
    clean_structures_inclusion,
    clean_ter_insecurite,
    compute_rna_rag_counts,
    fetch_rna_rag_stats,
    get_inclusion_jobs_status,
    get_live_jobs_status,
    transform_housing_occupation_odace,
)
from pipeline.ingest.config import (
    clear_domain_contracts_cache,
    get_domain_contracts,
    load_domain_contracts_yaml,
)
from pipeline.ingest import context
from pipeline.ingest.fetcher import fetch_rome_referential, fetch_source
from pipeline.ingest.helpers import (
    _artifact_observed_at,
    _data_project,
    _load_source_contract,
    _materialize_employment_output_pair,
    _materialize_external_output,
    _materialize_rag_cache,
    _rna_rag_table_id,
    _save_rag_cache,
    resolve_codgeo,
)
from pipeline.ingest.runner import MutedPipelineLogger, run_clean_step_safely
from pipeline.odace_client import get_odace_client
from pipeline.run_context import PipelineRunError


def main(argv: Any = None) -> None:
    """Execute the ingestion pipeline CLI (lazy-loaded to avoid runpy module collision)."""
    from pipeline.ingest.main import main as _main

    return _main(argv)


# Export context paths at module level for initial bindings
CONFIG_FILE = context.CONFIG_FILE
CACHE_DIR = context.CACHE_DIR
CLEAN_DIR = context.CLEAN_DIR
OUTPUT_DIR = context.OUTPUT_DIR
STATUS_FILE = context.STATUS_FILE
SHARED_CLEAN_DIR = context.SHARED_CLEAN_DIR
FT_SHARED_OUTPUT_PATH = context.FT_SHARED_OUTPUT_PATH
FT_COVERAGE_SHARED_OUTPUT_PATH = context.FT_COVERAGE_SHARED_OUTPUT_PATH
INCLUSION_SHARED_OUTPUT_PATH = context.INCLUSION_SHARED_OUTPUT_PATH
INCLUSION_COVERAGE_SHARED_OUTPUT_PATH = context.INCLUSION_COVERAGE_SHARED_OUTPUT_PATH
INCLUSION_OUTPUT_PATH = context.INCLUSION_OUTPUT_PATH
DATA_CONTRACTS_FILE = context.DATA_CONTRACTS_FILE
DOMAIN_CONTRACTS_FILE = context.DOMAIN_CONTRACTS_FILE


class _IngestModule(types.ModuleType):
    """Subclass of ModuleType to intercept path modifications and proxy to context.py."""

    _CONTEXT_VARS = {
        "CONFIG_FILE",
        "CACHE_DIR",
        "CLEAN_DIR",
        "OUTPUT_DIR",
        "STATUS_FILE",
        "SHARED_CLEAN_DIR",
        "FT_SHARED_OUTPUT_PATH",
        "FT_COVERAGE_SHARED_OUTPUT_PATH",
        "INCLUSION_SHARED_OUTPUT_PATH",
        "INCLUSION_COVERAGE_SHARED_OUTPUT_PATH",
        "INCLUSION_OUTPUT_PATH",
        "DATA_CONTRACTS_FILE",
        "DOMAIN_CONTRACTS_FILE",
    }

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._CONTEXT_VARS or hasattr(context, name):
            setattr(context, name, value)
        super().__setattr__(name, value)

    def __getattribute__(self, name: str) -> Any:
        if name in _IngestModule._CONTEXT_VARS:
            return getattr(context, name)
        return super().__getattribute__(name)


# Dynamically patch this module's class to transparently intercept mutations
sys.modules[__name__].__class__ = _IngestModule

__all__ = [
    "CACHE_DIR",
    "CLEAN_DIR",
    "CONFIG_FILE",
    "DATA_CONTRACTS_FILE",
    "DOMAIN_CONTRACTS_FILE",
    "FT_COVERAGE_SHARED_OUTPUT_PATH",
    "FT_SHARED_OUTPUT_PATH",
    "INCLUSION_COVERAGE_SHARED_OUTPUT_PATH",
    "INCLUSION_OUTPUT_PATH",
    "INCLUSION_SHARED_OUTPUT_PATH",
    "MutedPipelineLogger",
    "OUTPUT_DIR",
    "PipelineLogger",
    "PipelineRunError",
    "SHARED_CLEAN_DIR",
    "STATUS_FILE",
    "_artifact_observed_at",
    "_data_project",
    "_load_source_contract",
    "_materialize_employment_output_pair",
    "_materialize_external_output",
    "_materialize_rag_cache",
    "_rna_rag_table_id",
    "_save_rag_cache",
    "bigquery",
    "clean_associations",
    "clean_bpe",
    "clean_caf",
    "clean_codes_postaux",
    "clean_communes",
    "clean_departements",
    "clean_electoral_history",
    "clean_formations",
    "clean_hebergement_rna",
    "clean_housing_occupation",
    "clean_inclusion_jobs",
    "clean_live_jobs",
    "clean_log_soc_delay",
    "clean_lovac",
    "clean_loyers",
    "clean_mob_durable",
    "clean_mob_transports_pub",
    "clean_nomenclature_waldec",
    "clean_odace_gares",
    "clean_odace_rent",
    "clean_political",
    "clean_population",
    "clean_population_active",
    "clean_population_details",
    "clean_refugee_associations",
    "clean_rpls",
    "clean_salesforce_jaccueille",
    "clean_sante_apl",
    "clean_school_effectifs",
    "clean_services_inclusion",
    "clean_structures_inclusion",
    "clean_ter_insecurite",
    "clear_domain_contracts_cache",
    "compute_rna_rag_counts",
    "context",
    "fetch_remote_metadata_datagouv",
    "fetch_rna_rag_stats",
    "fetch_rome_referential",
    "fetch_source",
    "genai",
    "get_domain_contracts",
    "get_ft_token",
    "get_inclusion_jobs_status",
    "get_live_jobs_status",
    "get_odace_client",
    "is_cache_valid",
    "load_config",
    "load_dataset",
    "load_domain_contracts_yaml",
    "main",
    "resolve_codgeo",
    "run_clean_step_safely",
    "run_etl",
    "run_inclusion_ingest",
    "transform_housing_occupation_odace",
    "validate_dataset_contract",
]
