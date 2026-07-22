import json
import hashlib
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from pipeline.common import CONFIG_FILE, load_config, CACHE_DIR, CLEAN_DIR, PROCESSED_DIR, STATUS_FILE
from pipeline.odace_client import OdaceClient, get_odace_client

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_PATH = PROCESSED_DIR / "data_manifest.json"


class SourceManifestItem(BaseModel):
    source_key: str = Field(..., description="Technical identifier of the source in pipeline")
    name: str = Field(..., description="Display name / label of the data source")
    method: str = Field(..., description="Ingestion method (e.g., Data Platform Odace, Export Data.gouv.fr)")
    odace_table: Optional[str] = Field(None, description="Silver table name in Odace if applicable")
    annee_reference: Optional[int] = Field(None, description="Year of reference of the data")
    last_updated: Optional[str] = Field(None, description="ISO timestamp of data file download/cache")
    row_count: Optional[int] = Field(None, description="Number of records in the dataset")
    certified: bool = Field(False, description="Whether the table is certified in Odace")
    doc_url: Optional[str] = Field(None, description="URL to dataset documentation or download")


class DataManifest(BaseModel):
    manifest_version: str = Field(..., description="Unique manifest version identifier")
    created_at: str = Field(..., description="ISO creation timestamp of the manifest")
    total_sources: int = Field(..., description="Total number of active data sources")
    sources: List[SourceManifestItem] = Field(default_factory=list, description="Catalog of data sources")


class DataManifestBuilder:
    def __init__(
        self,
        sources_config: Optional[Dict[str, Any]] = None,
        odace_client: Optional[OdaceClient] = None,
        output_path: Optional[Path] = None,
    ):
        self.output_path = Path(output_path) if output_path else DEFAULT_MANIFEST_PATH
        if sources_config is not None:
            self.sources_config = sources_config
        else:
            try:
                full_config = load_config(CONFIG_FILE)
                self.sources_config = full_config.get("sources", {})
            except Exception as e:
                logger.warning(f"DataManifestBuilder: Failed to load sources config from {CONFIG_FILE}: {e}")
                self.sources_config = {}

        self.odace_client = odace_client
        self.pipeline_status = self._load_pipeline_status()

    def _load_pipeline_status(self) -> Dict[str, Any]:
        """Loads pipeline execution status from status.json if available."""
        if STATUS_FILE.exists():
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("steps", {})
            except Exception as e:
                logger.warning(f"DataManifestBuilder: Failed to read {STATUS_FILE}: {e}")
        return {}

    def build(self) -> DataManifest:
        items: List[SourceManifestItem] = []

        for source_key, source_meta in self.sources_config.items():
            item = self._process_source(source_key, source_meta)
            if item:
                items.append(item)

        # Generate deterministic version hash
        now_iso = datetime.now(timezone.utc).isoformat()
        date_str = datetime.now(timezone.utc).strftime("%Y.%m.%d")
        payload_str = json.dumps([i.model_dump() for i in items], sort_keys=True)
        hash_digest = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()[:6]
        manifest_version = f"v{date_str}-{hash_digest}"

        manifest = DataManifest(
            manifest_version=manifest_version,
            created_at=now_iso,
            total_sources=len(items),
            sources=items,
        )

        # Save to output file
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.write_text(
            json.dumps(manifest.model_dump(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"DataManifestBuilder: Generated manifest version {manifest_version} with {len(items)} sources at {self.output_path}")

        return manifest

    def _process_source(self, source_key: str, meta: Dict[str, Any]) -> SourceManifestItem:
        use_odace = meta.get("use_odace", False)
        odace_table = meta.get("odace_table")
        name = meta.get("description") or source_key
        doc_url = meta.get("doc_url")
        annee_ref = meta.get("annee_reference")
        method = "Export Open Data"
        row_count = None
        last_updated = None
        certified = False

        if use_odace and odace_table:
            method = "Data Platform Odace"
            # Try fetching Odace Silver detail if client is available
            if self.odace_client:
                detail = self.odace_client.fetch_silver_table_detail(odace_table)
                if detail:
                    certified = detail.get("certified", False)
                    annee_ref = detail.get("annee_reference") or annee_ref
                    if detail.get("description_fr"):
                        name = detail["description_fr"]
                    
                    schema = detail.get("schema", {})
                    row_count = schema.get("row_count")

                    # Primary source hypothesis (sources[0])
                    odace_sources = detail.get("sources", [])
                    if odace_sources and isinstance(odace_sources, list) and len(odace_sources) > 0:
                        primary_source = odace_sources[0]
                        if isinstance(primary_source, dict):
                            if not doc_url and primary_source.get("doc_url"):
                                doc_url = primary_source.get("doc_url")
        else:
            if meta.get("datagouv_resource_id") or "data.gouv" in (doc_url or "").lower():
                method = "Export Data.gouv.fr"

        # Fallback for doc_url from datagouv_resource_id or url
        if not doc_url:
            if meta.get("datagouv_resource_id"):
                doc_url = f"https://www.data.gouv.fr/fr/datasets/r/{meta.get('datagouv_resource_id')}"
            elif meta.get("url"):
                doc_url = meta.get("url")

        # Check status.json for execution step status & metadata
        step_candidates = [
            f"clean_{source_key}",
            f"process_{source_key}",
            f"output_{source_key}",
            f"odace_{odace_table}" if odace_table else "",
        ]
        for step_name in step_candidates:
            if step_name and step_name in self.pipeline_status:
                step_info = self.pipeline_status[step_name]
                if not last_updated and step_info.get("timestamp"):
                    last_updated = step_info.get("timestamp")
                details = step_info.get("details", {})
                if row_count is None and isinstance(details, dict):
                    row_count = details.get("rows") or details.get("count")

        # Resolve exact file download / cache timestamp from local filesystem if status.json didn't provide last_updated
        if not last_updated:
            candidate_paths = []
            if use_odace and odace_table:
                candidate_paths.append(CACHE_DIR / f"odace_{odace_table}.parquet")
            
            local_name = meta.get("local_name")
            if local_name:
                candidate_paths.append(CACHE_DIR / local_name)
                candidate_paths.append(CLEAN_DIR / local_name)
            candidate_paths.append(CLEAN_DIR / f"{source_key}.parquet")

            for path in candidate_paths:
                if path.exists():
                    last_updated = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
                    break

        if not last_updated:
            last_updated = datetime.now(timezone.utc).isoformat()

        return SourceManifestItem(
            source_key=source_key,
            name=name,
            method=method,
            odace_table=odace_table if use_odace else None,
            annee_reference=annee_ref,
            last_updated=last_updated,
            row_count=row_count,
            certified=certified,
            doc_url=doc_url,
        )


def generate_manifest(
    output_path: Optional[Path] = None,
    odace_client: Optional[OdaceClient] = None,
) -> DataManifest:
    """Convenience helper to build and write the Data Manifest."""
    builder = DataManifestBuilder(odace_client=odace_client, output_path=output_path)
    return builder.build()
