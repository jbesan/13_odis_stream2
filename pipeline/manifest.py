"""Build the immutable provenance manifest for one pipeline candidate."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from pipeline.common import (
    CACHE_DIR,
    CLEAN_DIR,
    CONFIG_FILE,
    OUTPUT_DIR,
    STATUS_FILE,
    load_config,
)
from pipeline.odace_client import OdaceClient

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST_PATH = OUTPUT_DIR / "data_manifest.json"
DEFAULT_SCORES_CONFIG_PATH = ROOT_DIR / "app" / "scores_config.yaml"
DEFAULT_CONTRACTS_PATH = ROOT_DIR / "pipeline" / "data_contracts.yaml"


class ArtifactManifestItem(BaseModel):
    """A local artifact that can be verified after publication."""

    name: str
    sha256: str
    size_bytes: int
    row_count: Optional[int] = None
    column_schema: Optional[Dict[str, str]] = None


class SourceManifestItem(BaseModel):
    source_key: str = Field(..., description="Technical identifier in sources.yaml")
    name: str = Field(..., description="Display name / label of the data source")
    method: str = Field(..., description="Ingestion method")
    odace_table: Optional[str] = None
    annee_reference: Optional[int] = None
    doc_url: Optional[str] = None
    certified: bool = False
    ttl_days: Optional[int] = None
    acquisition_status: str = "unknown"
    acquired_at: Optional[str] = None
    age_days: Optional[float] = None
    fallback_used: bool = False
    row_count: Optional[int] = None
    artifact: Optional[ArtifactManifestItem] = None


class DataManifest(BaseModel):
    schema_version: int = 2
    manifest_version: str
    created_at: str
    pipeline_run_id: str
    git_commit: Optional[str] = None
    runtime_image: Optional[str] = None
    configuration: Dict[str, str]
    quality_gate: Dict[str, Any]
    total_sources: int
    sources: List[SourceManifestItem] = Field(default_factory=list)
    outputs: List[ArtifactManifestItem] = Field(default_factory=list)
    quality_report: Optional[ArtifactManifestItem] = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parquet_metadata(path: Path) -> tuple[Optional[int], Optional[Dict[str, str]]]:
    if path.suffix.lower() != ".parquet":
        return None, None
    try:
        import pyarrow.parquet as pq

        parquet = pq.ParquetFile(path)
        return parquet.metadata.num_rows, {
            field.name: str(field.type) for field in parquet.schema_arrow
        }
    except Exception as exc:  # pragma: no cover - defensive for malformed inputs
        logger.warning("Could not inspect parquet artifact %s: %s", path, exc)
        return None, None


def artifact_metadata(path: Path, *, name: Optional[str] = None) -> ArtifactManifestItem:
    """Return portable integrity metadata without loading whole datasets."""
    if not path.is_file():
        raise FileNotFoundError(f"Manifest artifact is missing: {path}")
    row_count, schema = _parquet_metadata(path)
    return ArtifactManifestItem(
        name=name or path.name,
        sha256=_sha256(path),
        size_bytes=path.stat().st_size,
        row_count=row_count,
        column_schema=schema,
    )


def _git_commit() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT_DIR,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _canonical_digest(payload: Dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


class DataManifestBuilder:
    def __init__(
        self,
        sources_config: Optional[Dict[str, Any]] = None,
        local_files_config: Optional[Dict[str, Any]] = None,
        odace_client: Optional[OdaceClient] = None,
        output_path: Optional[Path] = None,
        run_id: str = "local-unversioned",
        quality_gate: Optional[Dict[str, Any]] = None,
        release_artifacts: Optional[List[str]] = None,
        quality_report_path: Optional[Path] = None,
    ):
        self.output_path = Path(output_path) if output_path else DEFAULT_MANIFEST_PATH
        if sources_config is None:
            full_config = load_config(CONFIG_FILE)
            sources_config = full_config.get("sources", {})
            local_files_config = full_config.get("local_files", {})
        self.sources_config = sources_config
        self.local_files_config = local_files_config or {}
        self.odace_client = odace_client
        self.run_id = run_id
        self.quality_gate = quality_gate or {}
        self.release_artifacts = release_artifacts or []
        self.quality_report_path = quality_report_path
        self.pipeline_state = self._load_pipeline_state()
        self.source_outcomes = self.pipeline_state.get("sources", {})
        self.step_statuses = self.pipeline_state.get("steps", {})

    def _load_pipeline_state(self) -> Dict[str, Any]:
        if not STATUS_FILE.exists():
            return {}
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read pipeline state %s: %s", STATUS_FILE, exc)
            return {}

    def build(self) -> DataManifest:
        source_catalog = {**self.sources_config, **self.local_files_config}
        sources = [
            self._process_source(source_key, source_meta)
            for source_key, source_meta in source_catalog.items()
        ]
        outputs = [
            artifact_metadata(self.output_path.parent / filename, name=filename)
            for filename in self.release_artifacts
        ]
        quality_report = (
            artifact_metadata(self.quality_report_path)
            if self.quality_report_path and self.quality_report_path.exists()
            else None
        )
        configuration = {
            "sources_yaml_sha256": _sha256(Path(CONFIG_FILE)),
            "scores_config_yaml_sha256": _sha256(DEFAULT_SCORES_CONFIG_PATH),
            "data_contracts_yaml_sha256": _sha256(DEFAULT_CONTRACTS_PATH),
        }
        created_at = datetime.now(timezone.utc).isoformat()
        content = {
            "schema_version": 2,
            "pipeline_run_id": self.run_id,
            "git_commit": _git_commit(),
            "runtime_image": os.getenv("RUNTIME_IMAGE_DIGEST"),
            "configuration": configuration,
            "quality_gate": self.quality_gate,
            "sources": [source.model_dump() for source in sources],
            "outputs": [output.model_dump() for output in outputs],
            "quality_report": quality_report.model_dump() if quality_report else None,
        }
        manifest = DataManifest(
            **content,
            manifest_version=f"v2-{_canonical_digest(content)[:16]}",
            created_at=created_at,
            total_sources=len(sources),
        )
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(manifest.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            "Generated manifest %s for run %s with %d sources at %s",
            manifest.manifest_version,
            self.run_id,
            len(sources),
            self.output_path,
        )
        return manifest

    def _process_source(
        self, source_key: str, meta: Dict[str, Any]
    ) -> SourceManifestItem:
        use_odace = meta.get("use_odace", False)
        odace_table = meta.get("odace_table")
        name = meta.get("description") or source_key
        annee_reference = meta.get("annee_reference")
        doc_url = meta.get("doc_url")
        certified = False
        row_count = None
        method = "Data Platform Odace" if use_odace else "Export Open Data"
        if not use_odace and meta.get("provider") == "bigquery":
            method = "BigQuery"
        elif not use_odace and meta.get("format") == "api":
            method = "API"
        elif not use_odace and (meta.get("datagouv_resource_id") or "data.gouv" in (
            meta.get("doc_url") or ""
        ).lower()):
            method = "Export Data.gouv.fr"

        if use_odace and odace_table and self.odace_client:
            detail = self.odace_client.fetch_silver_table_detail(odace_table)
            if detail:
                name = detail.get("description_fr") or name
                annee_reference = detail.get("annee_reference") or annee_reference
                certified = bool(detail.get("certified", False))
                schema = detail.get("schema", {})
                if isinstance(schema, dict):
                    row_count = schema.get("row_count")
                if not doc_url:
                    upstream_sources = detail.get("sources", [])
                    if upstream_sources and isinstance(upstream_sources[0], dict):
                        doc_url = upstream_sources[0].get("doc_url")

        if not doc_url and meta.get("datagouv_resource_id"):
            doc_url = f"https://www.data.gouv.fr/fr/datasets/r/{meta['datagouv_resource_id']}"
        elif not doc_url:
            doc_url = meta.get("url")

        outcome = self._source_outcome(source_key, odace_table)
        source_path = self._source_path(source_key, meta, outcome)
        source_artifact = artifact_metadata(source_path) if source_path else None
        if row_count is None and source_artifact:
            row_count = source_artifact.row_count
        acquired_at = outcome.get("timestamp") if outcome else None
        if not acquired_at and source_path:
            acquired_at = datetime.fromtimestamp(
                source_path.stat().st_mtime, tz=timezone.utc
            ).isoformat()
        age_days = _age_days(acquired_at)

        return SourceManifestItem(
            source_key=source_key,
            name=name,
            method=method,
            odace_table=odace_table if use_odace else None,
            annee_reference=annee_reference,
            doc_url=doc_url,
            certified=certified,
            ttl_days=meta.get("ttl_days"),
            acquisition_status=outcome.get("status", "unknown") if outcome else "unknown",
            acquired_at=acquired_at,
            age_days=age_days,
            fallback_used=(outcome or {}).get("status") == "fallback_last_good",
            row_count=row_count,
            artifact=source_artifact,
        )

    def _source_outcome(
        self, source_key: str, odace_table: Optional[str]
    ) -> Dict[str, Any]:
        candidates = [source_key]
        if odace_table:
            candidates.extend([f"odace_{odace_table}", f"odace_query_{odace_table}"])
        for candidate in candidates:
            outcome = self.source_outcomes.get(candidate)
            if isinstance(outcome, dict):
                return outcome
        return {}

    def _source_path(
        self, source_key: str, meta: Dict[str, Any], outcome: Dict[str, Any]
    ) -> Optional[Path]:
        reported = outcome.get("file")
        if reported:
            path = Path(reported)
            if path.is_file():
                return path
        candidates: List[Path] = []
        local_name = meta.get("local_name")
        if local_name:
            candidates.extend([CACHE_DIR / local_name, CLEAN_DIR / local_name])
        if meta.get("use_odace") and meta.get("odace_table"):
            candidates.append(CACHE_DIR / f"odace_{meta['odace_table']}.parquet")
        candidates.append(CLEAN_DIR / f"{source_key}.parquet")
        return next((path for path in candidates if path.is_file()), None)


def _age_days(timestamp: Optional[str]) -> Optional[float]:
    if not timestamp:
        return None
    try:
        observed_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return round((datetime.now(timezone.utc) - observed_at).total_seconds() / 86400, 3)


def generate_manifest(
    output_path: Optional[Path] = None,
    odace_client: Optional[OdaceClient] = None,
    **kwargs: Any,
) -> DataManifest:
    """Build and write a provenance manifest for a single candidate run."""
    return DataManifestBuilder(
        odace_client=odace_client, output_path=output_path, **kwargs
    ).build()


def validate_manifest_for_deployment(
    manifest_path: Path, *, run_id: str, required_artifacts: List[str]
) -> DataManifest:
    """Ensure a candidate manifest belongs to its run and describes its outputs."""
    try:
        manifest = DataManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Invalid candidate manifest: {manifest_path}") from exc
    if manifest.pipeline_run_id != run_id:
        raise ValueError("Candidate manifest does not belong to the requested run")
    if manifest.quality_report is None:
        raise ValueError("Candidate manifest is missing its quality report metadata")
    indexed_outputs = {artifact.name: artifact for artifact in manifest.outputs}
    for filename in required_artifacts:
        artifact = indexed_outputs.get(filename)
        path = manifest_path.parent / filename
        if artifact is None or not path.is_file() or artifact != artifact_metadata(path, name=filename):
            raise ValueError(f"Manifest output integrity check failed: {filename}")
    return manifest
