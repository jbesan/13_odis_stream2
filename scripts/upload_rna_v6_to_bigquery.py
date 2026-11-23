#!/usr/bin/env python3
"""Upload National RNA V6 consolidated parquets to GCS and isolated BigQuery tables.

Workflow:
1. Validate target BigQuery table names (hard guard against overwriting legacy
   ``rna_rag`` or ``rna_rag_clustered`` production tables).
2. Upload ``national_v6_consolidated.parquet`` (2,137,399 rows) and
   ``referentiel_tags_canoniques_n50.parquet`` (19,175 rows) to GCS with MD5
   idempotency checks.
3. Load both Parquet files into isolated BigQuery tables with
   ``enable_list_inference=True`` (native ``ARRAY<STRING>`` columns) and
   clustering ``(bdv_codeinsee, llm_action_sociale)`` on the consolidated table.
4. Optionally upload and load ``national_v6_embeddings_768d.parquet`` into a
   separate table clustered by ``id`` when ``--with-embeddings`` is passed.
5. Verify row counts, clustering, native ``REPEATED STRING`` list schemas,
   smoke SQL ``UNNEST`` queries, and confirm ``rna_rag_clustered`` is untouched.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv
from google.api_core.exceptions import NotFound
from google.cloud import bigquery, storage

REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = REPO_ROOT / "app" / ".env"
if _ENV_FILE.exists():
    load_dotenv(_ENV_FILE, override=False)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("rna_v6_uploader")

DEFAULT_SOURCE_DIR = Path(
    os.getenv("RNA_V6_SOURCE_DIR")
    or (REPO_ROOT.parent / "rna_rag" / "data" / "results")
)
DEFAULT_PROJECT_ID = (
    os.getenv("ODIS_DATA_PROJECT")
    or os.getenv("GOOGLE_CLOUD_PROJECT")
    or "odis-stream2-app"
)
DEFAULT_DATASET_ID = "rna_rag"
DEFAULT_LOCATION = "europe-west1"
DEFAULT_GCS_BUCKET = os.getenv("GCS_DATASETS_BUCKET") or "odis-stream2-app-data-euw1"
DEFAULT_GCS_PREFIX = "sources/rna_v6"

CONSOLIDATED_FILENAME = "national_v6_consolidated.parquet"
TAGS_REF_FILENAME = "referentiel_tags_canoniques_n50.parquet"
EMBEDDINGS_FILENAME = "national_v6_embeddings_768d.parquet"

FORBIDDEN_TARGET_TABLES = frozenset({"rna_rag", "rna_rag_clustered"})
_TABLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")

EXPECTED_CONSOLIDATED_ROWS = 2_137_399
EXPECTED_SIEGE_ROWS = 2_056_255
EXPECTED_SUCCURSALE_ROWS = 81_144
EXPECTED_TAGS_REF_ROWS = 19_175
EXPECTED_CONSOLIDATED_LIST_COLS = (
    "llm_tags_recherche",
    "llm_tags_normalises",
    "llm_thematiques",
    "llm_publics_cibles",
)
EXPECTED_CONSOLIDATED_CLUSTERING = ("bdv_codeinsee", "llm_action_sociale")
EMBEDDING_COLUMN = "embedding_gemini_768"
EXPECTED_EMBEDDING_DIM = 768


def validate_target_table_name(table_name: str) -> None:
    """Validate that a BigQuery target table name is safe and non-legacy.

    Args:
        table_name: Unqualified BigQuery table name.

    Raises:
        ValueError: If the name is empty, malformed, or targets a protected
            legacy production table (``rna_rag`` or ``rna_rag_clustered``).
    """
    cleaned = table_name.strip() if isinstance(table_name, str) else ""
    if not cleaned or not _TABLE_NAME_PATTERN.fullmatch(cleaned):
        raise ValueError(
            f"Invalid BigQuery table name '{table_name}'. Must match ^[A-Za-z0-9_]+$."
        )
    if cleaned in FORBIDDEN_TARGET_TABLES:
        raise ValueError(
            f"Target table '{cleaned}' is protected; refusing to overwrite legacy production tables."
        )


def compute_md5_base64(file_path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    """Compute base64-encoded MD5 digest matching GCS ``blob.md5_hash``.

    Args:
        file_path: Local file path to hash.
        chunk_size: Streaming read chunk size in bytes.

    Returns:
        Base64-encoded MD5 digest string.
    """
    digest = hashlib.md5()
    with open(file_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return base64.b64encode(digest.digest()).decode("ascii")


def upload_file_to_gcs(
    storage_client: storage.Client | None,
    local_path: Path,
    bucket_name: str,
    blob_path: str,
    *,
    dry_run: bool = False,
) -> str:
    """Upload a local Parquet artifact to GCS if missing or MD5-changed.

    Args:
        storage_client: Authenticated Google Cloud Storage client (may be None
            only when ``dry_run=True``).
        local_path: Local source file path.
        bucket_name: Target GCS bucket name.
        blob_path: Object path inside the bucket.
        dry_run: When True, validate the local file without calling GCS.

    Returns:
        The ``gs://`` URI of the uploaded (or already matching) object.

    Raises:
        FileNotFoundError: If ``local_path`` does not exist.
        ValueError: If ``storage_client`` is None when ``dry_run=False``.
    """
    if not local_path.is_file():
        raise FileNotFoundError(f"Source parquet file not found: {local_path}")

    gcs_uri = f"gs://{bucket_name}/{blob_path}"
    size_mb = local_path.stat().st_size / (1024 * 1024)

    if dry_run:
        logger.info("[DRY RUN] Would upload %s (%.2f MB) -> %s", local_path.name, size_mb, gcs_uri)
        return gcs_uri

    if storage_client is None:
        raise ValueError("storage_client must be provided when dry_run is False.")

    bucket = storage_client.bucket(bucket_name)
    blob = bucket.blob(blob_path)

    if blob.exists():
        blob.reload()
        local_md5 = compute_md5_base64(local_path)
        if blob.md5_hash == local_md5:
            logger.info("GCS object %s already up-to-date (MD5=%s); skipping upload.", gcs_uri, local_md5)
            return gcs_uri

    logger.info("Uploading %s (%.2f MB) -> %s ...", local_path.name, size_mb, gcs_uri)
    blob.chunk_size = 16 * 1024 * 1024
    blob.upload_from_filename(str(local_path), timeout=600)
    logger.info("Uploaded %s successfully.", gcs_uri)
    return gcs_uri


def build_parquet_load_job_config(
    clustering_fields: Sequence[str] | None = None,
) -> bigquery.LoadJobConfig:
    """Build a BigQuery Parquet LoadJobConfig with native list inference enabled.

    Args:
        clustering_fields: Optional ordered clustering column names.

    Returns:
        Configured ``bigquery.LoadJobConfig`` instance.
    """
    parquet_options = bigquery.ParquetOptions()
    parquet_options.enable_list_inference = True

    return bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
        clustering_fields=list(clustering_fields) if clustering_fields else None,
        parquet_options=parquet_options,
    )


def load_parquet_to_bigquery(
    bq_client: bigquery.Client | None,
    gcs_uri: str,
    table_id: str,
    *,
    location: str,
    clustering_fields: Sequence[str] | None = None,
    dry_run: bool = False,
) -> None:
    """Load a GCS Parquet object into a BigQuery table.

    Args:
        bq_client: Authenticated BigQuery client (may be None only when
            ``dry_run=True``).
        gcs_uri: Source ``gs://`` Parquet URI.
        table_id: Fully-qualified destination ``project.dataset.table`` ID.
        location: BigQuery dataset geographic location.
        clustering_fields: Optional clustering column names.
        dry_run: When True, log intended operation without executing the job.

    Raises:
        ValueError: If ``bq_client`` is None when ``dry_run=False``.
    """
    if dry_run:
        logger.info(
            "[DRY RUN] Would load %s -> %s (location=%s, clustering=%s)",
            gcs_uri,
            table_id,
            location,
            list(clustering_fields) if clustering_fields else None,
        )
        return

    if bq_client is None:
        raise ValueError("bq_client must be provided when dry_run is False.")

    job_config = build_parquet_load_job_config(clustering_fields=clustering_fields)
    logger.info(
        "Loading %s -> %s (clustering=%s) ...",
        gcs_uri,
        table_id,
        list(clustering_fields) if clustering_fields else None,
    )
    load_job = bq_client.load_table_from_uri(
        gcs_uri,
        table_id,
        job_config=job_config,
        location=location,
    )
    load_job.result()
    logger.info("Loaded %s successfully (output_rows=%s).", table_id, load_job.output_rows)


def _assert_repeated_string_fields(
    table: bigquery.Table,
    expected_cols: Sequence[str],
) -> None:
    """Assert that every expected list column was loaded as native REPEATED STRING."""
    schema_map = {field.name: field for field in table.schema}
    for col in expected_cols:
        if col not in schema_map:
            raise RuntimeError(f"Expected column '{col}' missing from {table.full_table_id}.")
        field = schema_map[col]
        if field.field_type != "STRING" or field.mode != "REPEATED":
            raise RuntimeError(
                f"Column '{col}' in {table.full_table_id} has type={field.field_type}, "
                f"mode={field.mode}; expected native REPEATED STRING (ARRAY<STRING>)."
            )


def verify_loaded_tables(
    bq_client: bigquery.Client,
    consolidated_table_id: str,
    tags_ref_table_id: str,
    *,
    project: str,
    dataset: str,
    location: str,
) -> dict[str, int]:
    """Verify row counts, clustering, native ARRAY<STRING> schemas, and smoke SQL.

    Args:
        bq_client: Authenticated BigQuery client.
        consolidated_table_id: Fully-qualified consolidated table ID.
        tags_ref_table_id: Fully-qualified canonical tags reference table ID.
        project: GCP project ID.
        dataset: BigQuery dataset ID.
        location: BigQuery dataset location.

    Returns:
        Dictionary of verified counts across V6 tables and legacy ``rna_rag_clustered``.

    Raises:
        RuntimeError: If any schema, clustering, row count, or SQL assertion fails.
    """
    consolidated_table = bq_client.get_table(consolidated_table_id)
    if consolidated_table.num_rows != EXPECTED_CONSOLIDATED_ROWS:
        raise RuntimeError(
            f"Row count mismatch on {consolidated_table_id}: "
            f"got {consolidated_table.num_rows}, expected {EXPECTED_CONSOLIDATED_ROWS}."
        )

    actual_clustering = list(consolidated_table.clustering_fields or [])
    if actual_clustering != list(EXPECTED_CONSOLIDATED_CLUSTERING):
        raise RuntimeError(
            f"Clustering mismatch on {consolidated_table_id}: "
            f"got {actual_clustering}, expected {list(EXPECTED_CONSOLIDATED_CLUSTERING)}."
        )

    _assert_repeated_string_fields(consolidated_table, EXPECTED_CONSOLIDATED_LIST_COLS)

    tags_ref_table = bq_client.get_table(tags_ref_table_id)
    if tags_ref_table.num_rows != EXPECTED_TAGS_REF_ROWS:
        raise RuntimeError(
            f"Row count mismatch on {tags_ref_table_id}: "
            f"got {tags_ref_table.num_rows}, expected {EXPECTED_TAGS_REF_ROWS}."
        )
    _assert_repeated_string_fields(tags_ref_table, ("exemples_bruts",))

    smoke_sql = f"""
    SELECT
        COUNTIF(is_siege = TRUE) AS siege_rows,
        COUNTIF(is_siege = FALSE) AS succursale_rows,
        COUNTIF(
            llm_action_sociale = TRUE
            AND (llm_perimetre_action IS NULL OR llm_perimetre_action != 'international')
        ) AS action_sociale_fr_rows,
        COUNTIF(
            llm_action_sociale = TRUE
            AND 'Personnes exilées' IN UNNEST(llm_publics_cibles)
            AND (llm_perimetre_action IS NULL OR llm_perimetre_action != 'international')
        ) AS refug_fr_rows,
        COUNT(
            DISTINCT IF(
                'volley ball' IN UNNEST(llm_tags_normalises),
                bdv_codeinsee,
                NULL
            )
        ) AS volley_bdv_count
    FROM `{consolidated_table_id}`
    """
    rows = list(bq_client.query(smoke_sql, location=location).result())
    if not rows:
        raise RuntimeError(f"Smoke query returned no rows for {consolidated_table_id}.")
    smoke_row = rows[0]

    siege_rows = int(smoke_row["siege_rows"])
    succursale_rows = int(smoke_row["succursale_rows"])
    action_sociale_fr_rows = int(smoke_row["action_sociale_fr_rows"])
    refug_fr_rows = int(smoke_row["refug_fr_rows"])
    volley_bdv_count = int(smoke_row["volley_bdv_count"])

    if siege_rows != EXPECTED_SIEGE_ROWS:
        raise RuntimeError(
            f"Siege row count mismatch: got {siege_rows}, expected {EXPECTED_SIEGE_ROWS}."
        )
    if succursale_rows != EXPECTED_SUCCURSALE_ROWS:
        raise RuntimeError(
            f"Succursale row count mismatch: got {succursale_rows}, expected {EXPECTED_SUCCURSALE_ROWS}."
        )
    if action_sociale_fr_rows <= 0 or refug_fr_rows <= 0 or volley_bdv_count <= 0:
        raise RuntimeError(
            f"Smoke SQL assertions failed: action_sociale_fr={action_sociale_fr_rows}, "
            f"refug_fr={refug_fr_rows}, volley_bdv={volley_bdv_count}."
        )

    legacy_table_id = f"{project}.{dataset}.rna_rag_clustered"
    legacy_table = bq_client.get_table(legacy_table_id)
    legacy_rows = int(legacy_table.num_rows or 0)

    summary = {
        "total_rows": int(consolidated_table.num_rows),
        "siege_rows": siege_rows,
        "succursale_rows": succursale_rows,
        "tags_ref_rows": int(tags_ref_table.num_rows),
        "action_sociale_fr_rows": action_sociale_fr_rows,
        "refug_fr_rows": refug_fr_rows,
        "volley_bdv_count": volley_bdv_count,
        "legacy_rna_rag_clustered_rows": legacy_rows,
    }
    logger.info("Verification summary: %s", summary)
    return summary


def verify_embeddings_table(
    bq_client: bigquery.Client,
    embeddings_table_id: str,
) -> int:
    """Verify row count and clustering of the optional V6 embeddings table.

    Args:
        bq_client: Authenticated BigQuery client.
        embeddings_table_id: Fully-qualified embeddings table ID.

    Returns:
        Verified row count.

    Raises:
        RuntimeError: If row count or clustering mismatches.
    """
    table = bq_client.get_table(embeddings_table_id)
    if table.num_rows != EXPECTED_SIEGE_ROWS:
        raise RuntimeError(
            f"Embeddings row count mismatch on {embeddings_table_id}: "
            f"got {table.num_rows}, expected {EXPECTED_SIEGE_ROWS}."
        )
    if list(table.clustering_fields or []) != ["id"]:
        raise RuntimeError(
            f"Clustering mismatch on {embeddings_table_id}: "
            f"got {table.clustering_fields}, expected ['id']."
        )
    return int(table.num_rows)


def _table_exists(bq_client: bigquery.Client, table_id: str) -> bool:
    """Return True when ``table_id`` exists in BigQuery, False on NotFound."""
    try:
        bq_client.get_table(table_id)
        return True
    except NotFound:
        logger.info("Table %s does not exist yet in BigQuery.", table_id)
        return False


def merge_embeddings_into_consolidated(
    bq_client: bigquery.Client | None,
    consolidated_table_id: str,
    embeddings_table_id: str,
    *,
    location: str = DEFAULT_LOCATION,
    dry_run: bool = False,
) -> int:
    """Merge ``embedding_gemini_768`` from staging table into consolidated table.

    Rebuilds ``consolidated_table_id`` in place with ``CLUSTER BY bdv_codeinsee,
    llm_action_sociale`` so semantic queries scan only target BDV clusters and
    non-embedding queries pay zero columnar scan cost.

    Args:
        bq_client: Authenticated BigQuery client (optional when ``dry_run=True``).
        consolidated_table_id: Target consolidated table ID.
        embeddings_table_id: Staging embeddings table ID keyed by ``id``.
        location: BigQuery job location.
        dry_run: If True, log SQL plan without executing BigQuery jobs.

    Returns:
        Resulting consolidated row count (0 when ``dry_run=True``).
    """
    clustering_sql = ", ".join(EXPECTED_CONSOLIDATED_CLUSTERING)
    if dry_run:
        logger.info(
            "[DRY RUN] Would merge %s.%s into %s (CLUSTER BY %s).",
            embeddings_table_id,
            EMBEDDING_COLUMN,
            consolidated_table_id,
            clustering_sql,
        )
        return 0

    if bq_client is None:
        raise ValueError("bq_client must be provided when dry_run is False.")

    existing_table = bq_client.get_table(consolidated_table_id)
    has_embedding_col = any(
        field.name == EMBEDDING_COLUMN for field in existing_table.schema
    )
    select_clause = (
        f"c.* EXCEPT ({EMBEDDING_COLUMN}), e.{EMBEDDING_COLUMN}"
        if has_embedding_col
        else f"c.*, e.{EMBEDDING_COLUMN}"
    )
    sql = (
        f"CREATE OR REPLACE TABLE `{consolidated_table_id}`\n"
        f"CLUSTER BY {clustering_sql} AS\n"
        f"SELECT {select_clause}\n"
        f"FROM `{consolidated_table_id}` AS c\n"
        f"LEFT JOIN `{embeddings_table_id}` AS e USING (id)"
    )
    logger.info(
        "Merging %s into %s (CLUSTER BY %s) ...",
        embeddings_table_id,
        consolidated_table_id,
        clustering_sql,
    )
    job = bq_client.query(sql, location=location)
    job.result()
    updated = bq_client.get_table(consolidated_table_id)
    logger.info("Merged table %s has %s rows.", consolidated_table_id, updated.num_rows)
    return int(updated.num_rows or 0)


def verify_merged_embeddings(
    bq_client: bigquery.Client,
    consolidated_table_id: str,
    *,
    location: str = DEFAULT_LOCATION,
) -> dict[str, int]:
    """Verify embedding column schema, completeness, and clustering after merge.

    Args:
        bq_client: Authenticated BigQuery client.
        consolidated_table_id: Consolidated BigQuery table ID.
        location: BigQuery job location.

    Returns:
        Dictionary with ``total_rows``, ``null_embeddings``, and ``invalid_dim_rows``.

    Raises:
        RuntimeError: If clustering, schema, row count, or vector dimensions fail validation.
    """
    table = bq_client.get_table(consolidated_table_id)
    if table.num_rows != EXPECTED_CONSOLIDATED_ROWS:
        raise RuntimeError(
            f"Row count mismatch on {consolidated_table_id} after merge: "
            f"got {table.num_rows}, expected {EXPECTED_CONSOLIDATED_ROWS}."
        )
    actual_clustering = list(table.clustering_fields or [])
    if actual_clustering != list(EXPECTED_CONSOLIDATED_CLUSTERING):
        raise RuntimeError(
            f"Clustering mismatch on {consolidated_table_id} after merge: "
            f"got {actual_clustering}, expected {list(EXPECTED_CONSOLIDATED_CLUSTERING)}."
        )
    emb_field = next(
        (field for field in table.schema if field.name == EMBEDDING_COLUMN),
        None,
    )
    if (
        emb_field is None
        or emb_field.mode != "REPEATED"
        or emb_field.field_type not in {"FLOAT", "FLOAT64"}
    ):
        raise RuntimeError(
            f"Invalid schema for {EMBEDDING_COLUMN} on {consolidated_table_id}: {emb_field}."
        )

    sql = (
        f"SELECT\n"
        f"  COUNT(*) AS total_rows,\n"
        f"  COUNTIF({EMBEDDING_COLUMN} IS NULL) AS null_embeddings,\n"
        f"  COUNTIF(ARRAY_LENGTH({EMBEDDING_COLUMN}) != {EXPECTED_EMBEDDING_DIM}) AS invalid_dim_rows\n"
        f"FROM `{consolidated_table_id}`"
    )
    rows = list(bq_client.query(sql, location=location).result())
    row = dict(rows[0])
    total_rows = int(row["total_rows"])
    null_embeddings = int(row["null_embeddings"])
    invalid_dim_rows = int(row["invalid_dim_rows"])
    if total_rows != EXPECTED_CONSOLIDATED_ROWS:
        raise RuntimeError(
            f"SQL row count mismatch on {consolidated_table_id}: {total_rows}."
        )
    if null_embeddings != 0:
        raise RuntimeError(
            f"Found {null_embeddings} NULL {EMBEDDING_COLUMN} rows in {consolidated_table_id}."
        )
    if invalid_dim_rows != 0:
        raise RuntimeError(
            f"Found {invalid_dim_rows} rows with dimension != {EXPECTED_EMBEDDING_DIM}."
        )
    return {
        "total_rows": total_rows,
        "null_embeddings": null_embeddings,
        "invalid_dim_rows": invalid_dim_rows,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments for the RNA V6 uploader."""
    parser = argparse.ArgumentParser(
        description="Upload RNA V6 consolidated parquets to GCS and isolated BigQuery tables."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help=f"Directory containing V6 Parquet deliverables (default: {DEFAULT_SOURCE_DIR})",
    )
    parser.add_argument(
        "--project",
        default=DEFAULT_PROJECT_ID,
        help=f"GCP Project ID (default: {DEFAULT_PROJECT_ID})",
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET_ID,
        help=f"BigQuery dataset ID (default: {DEFAULT_DATASET_ID})",
    )
    parser.add_argument(
        "--location",
        default=DEFAULT_LOCATION,
        help=f"BigQuery dataset location (default: {DEFAULT_LOCATION})",
    )
    parser.add_argument(
        "--bucket",
        default=DEFAULT_GCS_BUCKET,
        help=f"GCS bucket name (default: {DEFAULT_GCS_BUCKET})",
    )
    parser.add_argument(
        "--gcs-prefix",
        default=DEFAULT_GCS_PREFIX,
        help=f"GCS prefix inside bucket (default: {DEFAULT_GCS_PREFIX})",
    )
    parser.add_argument(
        "--consolidated-table",
        default="national_v6_consolidated",
        help="Destination BigQuery table for consolidated data (default: national_v6_consolidated)",
    )
    parser.add_argument(
        "--tags-ref-table",
        default="referentiel_tags_canoniques_n50",
        help="Destination BigQuery table for canonical tags reference (default: referentiel_tags_canoniques_n50)",
    )
    parser.add_argument(
        "--embeddings-table",
        default="national_v6_embeddings_768d",
        help="Destination BigQuery table for optional embeddings (default: national_v6_embeddings_768d)",
    )
    parser.add_argument(
        "--with-embeddings",
        action="store_true",
        help="Also upload and load national_v6_embeddings_768d.parquet into a separate table clustered by id",
    )
    parser.add_argument(
        "--merge-embeddings",
        action="store_true",
        help=(
            "Upload/load national_v6_embeddings_768d.parquet into a staging table, merge "
            "embedding_gemini_768 into consolidated table (CLUSTER BY bdv_codeinsee, llm_action_sociale), "
            "verify 0 NULLs, and drop the staging table unless --with-embeddings is also set."
        ),
    )
    parser.add_argument(
        "--reload-consolidated",
        action="store_true",
        help="Force reloading consolidated and tags-ref tables when used with --merge-embeddings",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip GCS upload and load directly from existing gs:// URIs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and log planned operations without calling GCS or BigQuery",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the RNA V6 GCS + BigQuery upload and verification workflow."""
    args = parse_args(argv)
    start_time = time.perf_counter()

    validate_target_table_name(args.consolidated_table)
    validate_target_table_name(args.tags_ref_table)
    need_embeddings = args.with_embeddings or args.merge_embeddings
    if need_embeddings:
        validate_target_table_name(args.embeddings_table)
        if args.embeddings_table in {args.consolidated_table, args.tags_ref_table}:
            raise ValueError("Embeddings table name must be distinct from consolidated and tags-ref tables.")
    if args.consolidated_table == args.tags_ref_table:
        raise ValueError("Consolidated and tags-ref table names must be distinct.")

    prefix = args.gcs_prefix.strip("/")
    consolidated_table_id = f"{args.project}.{args.dataset}.{args.consolidated_table}"
    tags_ref_table_id = f"{args.project}.{args.dataset}.{args.tags_ref_table}"
    embeddings_table_id = f"{args.project}.{args.dataset}.{args.embeddings_table}"

    logger.info("Project=%s | Dataset=%s (%s)", args.project, args.dataset, args.location)
    logger.info("Targets: %s, %s", consolidated_table_id, tags_ref_table_id)

    storage_client = None if args.dry_run or args.skip_upload else storage.Client(project=args.project)
    bq_client = None if args.dry_run else bigquery.Client(project=args.project)

    load_base_tables = not args.merge_embeddings or args.reload_consolidated
    if args.merge_embeddings and not load_base_tables and bq_client is not None:
        load_base_tables = not _table_exists(bq_client, consolidated_table_id)

    targets: list[tuple[str, str, Sequence[str] | None]] = []
    if load_base_tables:
        targets.extend(
            [
                (CONSOLIDATED_FILENAME, consolidated_table_id, EXPECTED_CONSOLIDATED_CLUSTERING),
                (TAGS_REF_FILENAME, tags_ref_table_id, None),
            ]
        )
    if need_embeddings:
        targets.append((EMBEDDINGS_FILENAME, embeddings_table_id, ("id",)))

    for filename, table_id, clustering in targets:
        blob_path = f"{prefix}/{filename}" if prefix else filename
        gcs_uri = (
            f"gs://{args.bucket}/{blob_path}"
            if args.skip_upload
            else upload_file_to_gcs(
                storage_client,
                args.source_dir / filename,
                args.bucket,
                blob_path,
                dry_run=args.dry_run,
            )
        )
        load_parquet_to_bigquery(
            bq_client,
            gcs_uri,
            table_id,
            location=args.location,
            clustering_fields=clustering,
            dry_run=args.dry_run,
        )

    if args.merge_embeddings:
        if bq_client is not None:
            verify_embeddings_table(bq_client, embeddings_table_id)
        merge_embeddings_into_consolidated(
            bq_client,
            consolidated_table_id,
            embeddings_table_id,
            location=args.location,
            dry_run=args.dry_run,
        )

    if bq_client is not None:
        summary: dict[str, int] = {}
        if load_base_tables or _table_exists(bq_client, tags_ref_table_id):
            summary.update(
                verify_loaded_tables(
                    bq_client,
                    consolidated_table_id,
                    tags_ref_table_id,
                    project=args.project,
                    dataset=args.dataset,
                    location=args.location,
                )
            )
        if args.merge_embeddings:
            summary.update(
                verify_merged_embeddings(
                    bq_client,
                    consolidated_table_id,
                    location=args.location,
                )
            )
            if not args.with_embeddings:
                logger.info("Dropping staging embeddings table %s ...", embeddings_table_id)
                bq_client.delete_table(embeddings_table_id, not_found_ok=True)
        elif args.with_embeddings:
            emb_rows = verify_embeddings_table(bq_client, embeddings_table_id)
            summary["embeddings_rows"] = emb_rows
        logger.info("Verification complete: %s", summary)

    elapsed = time.perf_counter() - start_time
    logger.info("Done in %.1fs.", elapsed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
