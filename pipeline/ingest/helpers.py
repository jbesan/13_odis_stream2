"""Helper utilities for paths, data transformations, and RAG cache management."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
from typing import Any, Dict, Optional

import pandas as pd

from pipeline.common import PipelineLogger, load_config
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


def _data_project() -> str:
    """Resolve the GCP project ID configured for BigQuery data sources."""
    project = os.getenv("ODIS_DATA_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError(
            "ODIS_DATA_PROJECT or GOOGLE_CLOUD_PROJECT must be configured for RNA RAG"
        )
    return project


def _rna_rag_table_id(config: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the target RNA table from config without a source-project default."""
    source_config = (config or {}).get("sources", {}).get("rna_rag", {})
    table = source_config.get("table", "rna_rag.rna_rag_clustered")
    if not isinstance(table, str) or table.count(".") != 1:
        raise ValueError(
            "sources.rna_rag.table must use the dataset.table form (for example "
            "rna_rag.rna_rag_clustered)"
        )
    return f"{_data_project()}.{table}"


def _materialize_rag_cache(
    candidate_path: Path,
    shared_path: Path,
    ttl_days: int,
    logger: PipelineLogger,
    source_name: str,
) -> Optional[Dict[str, Any]]:
    """Reuse a valid shared RAG cache and materialize it into the candidate."""
    candidate_path = Path(candidate_path)
    shared_path = Path(shared_path)

    for cache_path in (candidate_path, shared_path):
        if not cache_path.exists() or cache_path.stat().st_size == 0:
            continue
        age_days = (
            datetime.now() - datetime.fromtimestamp(cache_path.stat().st_mtime)
        ).total_seconds() / 86400
        if age_days >= ttl_days:
            continue

        if cache_path != candidate_path:
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(cache_path, candidate_path)

        logger.log_source(source_name, "CACHED", cache_path)
        return {
            "path": str(candidate_path),
            "cache_status": "reused_within_ttl",
            "cache_age_days": round(age_days, 3),
            "cache_source": str(cache_path),
            "ttl_days": ttl_days,
        }

    return None


def _save_rag_cache(candidate_path: Path, shared_path: Path) -> None:
    """Backport candidate-generated RAG data into the shared cache location."""
    candidate_path = Path(candidate_path)
    shared_path = Path(shared_path)
    if candidate_path.exists() and candidate_path.stat().st_size > 0:
        shared_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(candidate_path, shared_path)


def _materialize_external_output(source_path: Path, candidate_path: Path) -> bool:
    """Materialize an externally produced artifact into the candidate output directory."""
    source_path = Path(source_path)
    candidate_path = Path(candidate_path)
    if not source_path.exists() or source_path.stat().st_size == 0:
        return False
    if source_path == candidate_path:
        return True
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, candidate_path)
    return True


def _materialize_employment_output_pair(source_path: Path, coverage_path: Path) -> bool:
    """Materialize both jobs aggregate and coverage files into candidate output directory."""
    candidate_dir = context.OUTPUT_DIR
    target_source = candidate_dir / Path(source_path).name
    target_coverage = candidate_dir / Path(coverage_path).name
    if not _materialize_external_output(source_path, target_source):
        return False
    return _materialize_external_output(coverage_path, target_coverage)


def _artifact_observed_at(path: Path) -> str:
    """Return ISO UTC timestamp of file last modified time or current time."""
    path = Path(path)
    timestamp = path.stat().st_mtime if path.exists() else datetime.now().timestamp()
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def resolve_codgeo(insee_code: Any, dept_code: Any) -> str:
    """Helper to cleanly resolve 5-digit INSEE commune codes from raw inputs.

    Args:
        insee_code: Raw commune INSEE code or suffix.
        dept_code: Raw department code.

    Returns:
        Standardized 5-character commune code.
    """
    if pd.isna(insee_code):
        return ""
    insee_str = str(insee_code).split(".")[0].strip()
    if not insee_str or insee_str.lower() == "nan":
        return ""

    dept_str = (
        str(dept_code).split(".")[0].strip().zfill(2) if not pd.isna(dept_code) else ""
    )

    # Strip dept from the start of insee_str if present
    comm_str = insee_str
    if dept_str:
        if comm_str.startswith(dept_str):
            comm_str = comm_str[len(dept_str) :]
        elif dept_str.startswith("0") and comm_str.startswith(dept_str[1:]):
            comm_str = comm_str[len(dept_str) - 1 :]

    # Clean up comm_str and pad to 3 digits
    comm_str = comm_str.strip()
    if comm_str:
        comm_str = comm_str.zfill(3)
    else:
        comm_str = "000"

    return dept_str + comm_str


def _load_source_contract(source_name: str) -> Dict[str, Any]:
    """Load one versioned source contract from the active contract catalogue."""
    contracts = load_config(str(context.DATA_CONTRACTS_FILE))
    contract = contracts.get("source_contracts", {}).get(source_name)
    if not isinstance(contract, dict):
        raise PipelineRunError(f"Missing source contract for '{source_name}'")
    return contract
