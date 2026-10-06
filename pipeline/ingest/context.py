"""Runtime paths and rebindable context for the ingest pipeline."""

from __future__ import annotations

from pathlib import Path

# Base paths and files
CONFIG_FILE: Path = Path("pipeline/sources.yaml")
CACHE_DIR: Path = Path("pipeline/cache/raw")
CLEAN_DIR: Path = Path("pipeline/cache/clean")
OUTPUT_DIR: Path = Path("pipeline/cache/output")
STATUS_FILE: Path = Path("pipeline/status.json")

# Shared cache directories and file paths
SHARED_CLEAN_DIR: Path = Path("pipeline/cache/clean")

FT_SHARED_OUTPUT_PATH: Path = Path("pipeline/cache/output/odis_ft_jobs_agg.parquet")
FT_COVERAGE_SHARED_OUTPUT_PATH: Path = Path(
    "pipeline/cache/output/odis_ft_jobs_coverage.parquet"
)

INCLUSION_SHARED_OUTPUT_PATH: Path = Path(
    "pipeline/cache/output/odis_inclusion_jobs.parquet"
)
INCLUSION_COVERAGE_SHARED_OUTPUT_PATH: Path = Path(
    "pipeline/cache/output/odis_inclusion_jobs_coverage.parquet"
)
INCLUSION_OUTPUT_PATH: Path = INCLUSION_SHARED_OUTPUT_PATH

DATA_CONTRACTS_FILE: Path = Path("pipeline/data_contracts.yaml")
DOMAIN_CONTRACTS_FILE: Path = Path("pipeline/domain_contracts.yaml")
