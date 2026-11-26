"""Shared helpers for Gemini Decisions API offline qualification and BigQuery delta sync."""

from __future__ import annotations

import asyncio
import io
import logging
import os
from pathlib import Path
import random
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, TypeVar

from dotenv import load_dotenv
from google import genai
from google.cloud import bigquery
from google.genai import types
import httpx
import numpy as np
import polars as pl
from pydantic import BaseModel

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONSOLIDATED_PARQUET = (
    REPO_ROOT.parent
    / "rna_rag"
    / "data"
    / "results"
    / "national_v6_consolidated.parquet"
)
DEFAULT_EMBEDDINGS_PARQUET = (
    REPO_ROOT.parent
    / "rna_rag"
    / "data"
    / "results"
    / "national_v6_embeddings_768d.parquet"
)

DECISIONS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/decisions"
DECISIONS_MODEL = "gemini-decision-1"
EMBEDDING_MODEL = "gemini-embedding-001"
INPUT_COST_USD_PER_1M = 0.10

_NON_EMPLOYER_TRANCHES = ["", "NN", "00", "NONE", "NAN"]

T = TypeVar("T", bound=BaseModel)


class AsyncRateLimiter:
    """Async spacing rate limiter enforcing a target RPM ceiling."""

    def __init__(self, rpm: int) -> None:
        """Initialize the limiter.

        Args:
            rpm: Maximum requests per minute allowed.
        """
        if rpm <= 0:
            raise ValueError(f"rpm must be positive, got {rpm}")
        self._min_interval = 60.0 / float(rpm)
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def acquire(self) -> None:
        """Wait until the next request slot is available."""
        async with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                delay = self._next_allowed - now
                self._next_allowed += self._min_interval
            else:
                delay = 0.0
                self._next_allowed = now + self._min_interval
        if delay > 0.0:
            await asyncio.sleep(delay)


def resolve_gemini_api_key() -> str:
    """Resolve Google AI Studio API key from environment or local ``.env`` files.

    Returns:
        Non-empty API key string.

    Raises:
        RuntimeError: If neither ``GEMINI_API_KEY`` nor ``GOOGLE_API_KEY`` is set.
    """
    for candidate_env in (
        REPO_ROOT / "pipeline" / ".env",
        REPO_ROOT / "app" / ".env",
        REPO_ROOT / ".env",
    ):
        if candidate_env.exists():
            load_dotenv(candidate_env, override=False)

    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not api_key or not api_key.strip():
        raise RuntimeError(
            "Missing GEMINI_API_KEY or GOOGLE_API_KEY in environment or app/.env"
        )
    return api_key.strip()


def format_association_input(row: Mapping[str, Any]) -> str:
    """Build the compact text input sent to ``gemini-decision-1``.

    Args:
        row: Association dictionary containing V6 text and metadata fields.

    Returns:
        Formatted multiline description string.
    """
    title = str(row.get("title") or "").strip()
    titre_court = str(row.get("titre_court") or "").strip()
    waldec = str(row.get("waldec_labels_hierarchical") or "").strip()
    objet = str(row.get("objet") or "").strip()
    tags = [str(t).strip() for t in (row.get("llm_tags_recherche") or []) if t]
    themes = [str(t).strip() for t in (row.get("llm_thematiques") or []) if t]

    lines: List[str] = []
    if title:
        lines.append(f"Nom: {title}")
    if titre_court and titre_court.lower() != title.lower():
        lines.append(f"Titre court: {titre_court}")
    if waldec:
        lines.append(f"WALDEC: {waldec}")
    if objet:
        lines.append(f"Objet: {objet}")
    if tags:
        lines.append(f"Mots-clés: {', '.join(tags)}")
    if themes:
        lines.append(f"Thématiques: {', '.join(themes)}")
    return "\n".join(lines) if lines else f"Association {row.get('id', '')}"


def build_universe_df(
    consolidated_parquet: Path,
    bdv_codes: Optional[Sequence[str]] = None,
    extra_cols: Optional[Sequence[str]] = None,
) -> pl.DataFrame:
    """Load the active social non-international association universe deduplicated by ``id``.

    Args:
        consolidated_parquet: Path to ``national_v6_consolidated.parquet``.
        bdv_codes: Optional list of Bassin de Vie codes to restrict the scan.
        extra_cols: Optional additional column names to project before deduplication.

    Returns:
        Polars DataFrame deduplicated on ``id`` (preferring employed/siège/recent rows).

    Raises:
        FileNotFoundError: If ``consolidated_parquet`` does not exist.
    """
    if not consolidated_parquet.exists():
        raise FileNotFoundError(
            f"Consolidated V6 parquet not found: {consolidated_parquet}"
        )

    lf = pl.scan_parquet(consolidated_parquet).filter(
        (pl.col("llm_action_sociale") == True)  # noqa: E712
        & (pl.col("etat_admin").is_null() | (pl.col("etat_admin") != "F"))
        & (
            pl.col("llm_perimetre_action").is_null()
            | (pl.col("llm_perimetre_action") != "international")
        )
    )
    if bdv_codes:
        norm_bdvs = [str(c).strip() for c in bdv_codes if str(c).strip()]
        lf = lf.filter(pl.col("bdv_codeinsee").is_in(norm_bdvs))

    cols = [
        "id",
        "text_source_sha256",
        "is_siege",
        "adrs_codeinsee",
        "bdv_codeinsee",
        "title",
        "titre_court",
        "objet",
        "waldec_labels_hierarchical",
        "tranche_effectifs",
        "date_decla",
        "llm_tags_recherche",
        "llm_thematiques",
        "llm_publics_cibles",
    ]
    if extra_cols:
        for col_name in extra_cols:
            if col_name not in cols:
                cols.append(col_name)

    return (
        lf.select(cols)
        .with_columns(
            (
                ~pl.col("tranche_effectifs")
                .fill_null("")
                .str.strip_chars()
                .str.to_uppercase()
                .is_in(_NON_EMPLOYER_TRANCHES)
            ).alias("_has_emp")
        )
        .sort(
            ["_has_emp", "is_siege", "date_decla"],
            descending=[True, True, True],
            nulls_last=True,
        )
        .unique(subset=["id"], keep="first")
        .drop("_has_emp")
        .collect()
    )


def compute_768d_topk_scores_multi(
    embeddings_parquet: Path,
    universe_ids: Sequence[str],
    api_key: str,
    queries: Mapping[str, str],
    top_k: int,
) -> Dict[str, Tuple[Dict[str, float], set[str]]]:
    """Score ``universe_ids`` against multiple semantic queries in a single Parquet scan.

    Args:
        embeddings_parquet: Path to ``national_v6_embeddings_768d.parquet``.
        universe_ids: Candidate universe IDs to score.
        api_key: Google AI Studio API key for query embedding.
        queries: Mapping ``query_key -> query_text``.
        top_k: Number of top-scoring IDs to select per query.

    Returns:
        Mapping ``query_key -> (score_by_id, topk_id_set)``.

    Raises:
        FileNotFoundError: If ``embeddings_parquet`` does not exist.
    """
    if not embeddings_parquet.exists():
        raise FileNotFoundError(
            f"Embeddings 768d parquet not found: {embeddings_parquet}"
        )
    if top_k <= 0 or not universe_ids or not queries:
        return {k: ({}, set()) for k in queries}

    client = genai.Client(api_key=api_key)
    q_norms: Dict[str, np.ndarray] = {}
    for q_key, q_text in queries.items():
        logger.info("Embedding query '%s' via %s...", q_key, EMBEDDING_MODEL)
        embed_resp = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=q_text,
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=768,
            ),
        )
        q_vec = np.asarray(embed_resp.embeddings[0].values, dtype=np.float32)
        q_norms[q_key] = q_vec / max(float(np.linalg.norm(q_vec)), 1e-12)

    id_list = list(dict.fromkeys(universe_ids))
    emb_df = (
        pl.scan_parquet(embeddings_parquet)
        .filter(pl.col("id").is_in(id_list))
        .select(["id", "embedding_gemini_768"])
        .collect()
    )
    if emb_df.is_empty():
        logger.warning("No matching IDs found in 768d embeddings parquet.")
        return {k: ({}, set()) for k in queries}

    matched_ids = emb_df["id"].to_list()
    mat = np.asarray(emb_df["embedding_gemini_768"].to_list(), dtype=np.float32)
    norms = np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
    mat_norm = mat / norms

    out: Dict[str, Tuple[Dict[str, float], set[str]]] = {}
    for q_key, q_norm in q_norms.items():
        sims = mat_norm @ q_norm
        order = np.argsort(-sims)
        score_by_id = {matched_ids[i]: float(sims[i]) for i in order}
        topk_ids = {matched_ids[i] for i in order[:top_k]}
        if len(order) > 0:
            min_topk_sim = float(sims[order[min(top_k, len(order)) - 1]])
            max_sim = float(sims[order[0]])
            logger.info(
                "768d cosine '%s': %d scored, top-%d sim [%.4f .. %.4f]",
                q_key,
                len(matched_ids),
                min(top_k, len(matched_ids)),
                min_topk_sim,
                max_sim,
            )
        out[q_key] = (score_by_id, topk_ids)
    return out


def load_jsonl_cache_typed(
    cache_path: Path, model_cls: type[T]
) -> Dict[Tuple[str, str], T]:
    """Load incremental JSONL decision cache keyed by ``(id, text_source_sha256)``.

    Args:
        cache_path: Path to the JSONL cache file.
        model_cls: Pydantic model class validating each cached line.

    Returns:
        Dictionary mapping ``(id, text_source_sha256)`` to model instance.
    """
    cache: Dict[Tuple[str, str], T] = {}
    if not cache_path.exists():
        return cache

    with cache_path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                rec = model_cls.model_validate_json(stripped)
            except Exception as exc:
                logger.error(
                    "Invalid JSONL cache line %d in %s: %s", line_no, cache_path, exc
                )
                raise
            cache[(getattr(rec, "id"), getattr(rec, "text_source_sha256"))] = rec
    return cache


async def post_decision_with_retry(
    client: httpx.AsyncClient,
    limiter: AsyncRateLimiter,
    semaphore: asyncio.Semaphore,
    api_key: str,
    asso_id: str,
    payload: Dict[str, Any],
    *,
    max_retries: int = 5,
) -> Dict[str, Any]:
    """Post a single ``/v1beta/decisions`` request with exponential backoff on 429/5xx.

    Args:
        client: Shared async HTTPX client.
        limiter: RPM spacing limiter.
        semaphore: Concurrency semaphore.
        api_key: Google AI Studio API key.
        asso_id: Association ID for error diagnostics.
        payload: Complete REST payload for ``/v1beta/decisions``.
        max_retries: Maximum retry attempts on transient failures.

    Returns:
        Parsed JSON response body.

    Raises:
        RuntimeError: On non-retryable HTTP errors or exhausted retries.
    """
    headers = {
        "x-goog-api-key": api_key,
        "Content-Type": "application/json",
    }
    last_error: Optional[str] = None
    for attempt in range(max_retries + 1):
        async with semaphore:
            await limiter.acquire()
            try:
                resp = await client.post(
                    DECISIONS_ENDPOINT,
                    headers=headers,
                    json=payload,
                    timeout=30.0,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = f"Transport error for {asso_id}: {exc}"
                if attempt == max_retries:
                    raise RuntimeError(last_error) from exc
                delay = min(30.0, (2.0**attempt) + random.uniform(0.0, 0.5))
                logger.warning("%s — retrying in %.1fs", last_error, delay)
                await asyncio.sleep(delay)
                continue

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code in {429, 500, 502, 503, 504}:
            last_error = f"HTTP {resp.status_code} for {asso_id}: {resp.text[:300]}"
            if attempt == max_retries:
                raise RuntimeError(last_error)
            delay = min(60.0, (2.0**attempt) * 1.5 + random.uniform(0.0, 1.0))
            logger.warning("%s — retrying in %.1fs", last_error, delay)
            await asyncio.sleep(delay)
            continue

        raise RuntimeError(
            f"Non-retryable HTTP {resp.status_code} for {asso_id}: {resp.text[:500]}"
        )

    raise RuntimeError(last_error or f"Failed evaluation for {asso_id}")


async def run_decisions_batch_generic(
    pending_rows: Iterable[Dict[str, Any]],
    *,
    api_key: str,
    questions_spec: Dict[str, Any],
    parse_response: Callable[[Dict[str, Any], Dict[str, Any]], T],
    cache_path: Path,
    rpm: int,
    concurrency: int,
) -> List[T]:
    """Evaluate pending rows asynchronously against ``/v1beta/decisions`` with JSONL checkpointing.

    Args:
        pending_rows: Candidate dictionaries not yet in cache.
        api_key: Google AI Studio API key.
        questions_spec: Decisions API ``questions`` dictionary.
        parse_response: Callable converting ``(row, response_json) -> record``.
        cache_path: JSONL checkpoint path appended line-by-line.
        rpm: Max requests per minute.
        concurrency: Max concurrent HTTP requests.

    Returns:
        List of parsed Pydantic records.
    """
    rows_list = list(pending_rows)
    if not rows_list:
        return []

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    limiter = AsyncRateLimiter(rpm=rpm)
    semaphore = asyncio.Semaphore(concurrency)
    results: List[T] = []
    total = len(rows_list)
    completed = 0

    async def _eval(client: httpx.AsyncClient, row: Dict[str, Any]) -> T:
        payload = {
            "model": DECISIONS_MODEL,
            "input": format_association_input(row),
            "questions": questions_spec,
        }
        body = await post_decision_with_retry(
            client, limiter, semaphore, api_key, str(row["id"]), payload
        )
        return parse_response(row, body)

    async with httpx.AsyncClient() as client:
        tasks = [asyncio.create_task(_eval(client, row)) for row in rows_list]
        with cache_path.open("a", encoding="utf-8") as cache_fh:
            for coro in asyncio.as_completed(tasks):
                record = await coro
                cache_fh.write(record.model_dump_json() + "\n")
                cache_fh.flush()
                results.append(record)
                completed += 1
                if completed % 50 == 0 or completed == total:
                    logger.info("Evaluated %d / %d associations", completed, total)

    return results


def upload_parquet_to_bigquery(
    parquet_path: Path,
    *,
    project_id: str,
    dataset_table: str,
    location: str = "europe-west1",
) -> None:
    """Load a qualified Parquet table into BigQuery (``WRITE_TRUNCATE``).

    Args:
        parquet_path: Local Parquet file to upload.
        project_id: Target GCP project ID.
        dataset_table: BigQuery ``dataset.table`` identifier.
        location: BigQuery dataset location.
    """
    client = bigquery.Client(project=project_id)
    table_id = f"{project_id}.{dataset_table}"
    job_config = bigquery.LoadJobConfig(
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    with parquet_path.open("rb") as fh:
        job = client.load_table_from_file(
            fh, table_id, location=location, job_config=job_config
        )
    job.result()
    logger.info("Uploaded %s to BigQuery table %s (%s)", parquet_path, table_id, location)


def merge_delta_to_bigquery(
    client: bigquery.Client,
    delta_df: pl.DataFrame,
    *,
    project_id: str,
    verified_table: str,
    location: str = "europe-west1",
) -> None:
    """Upsert evaluated delta rows into BigQuery ``verified_table`` via staging ``MERGE``.

    Args:
        client: Initialized BigQuery client.
        delta_df: Evaluated Polars DataFrame matching ``verified_table`` schema.
        project_id: Target GCP project ID.
        verified_table: Target ``dataset.table`` identifier.
        location: BigQuery dataset location.
    """
    if delta_df.is_empty():
        return

    target_id = f"{project_id}.{verified_table}"
    target_table = client.get_table(target_id)
    target_cols = [field.name for field in target_table.schema]
    aligned_df = delta_df.select(target_cols)

    staging_id = f"{target_id}_staging_{int(time.time())}"
    buf = io.BytesIO()
    aligned_df.write_parquet(buf)
    buf.seek(0)

    load_cfg = bigquery.LoadJobConfig(
        schema=target_table.schema,
        source_format=bigquery.SourceFormat.PARQUET,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    try:
        load_job = client.load_table_from_file(
            buf, staging_id, location=location, job_config=load_cfg
        )
        load_job.result()

        update_cols = [c for c in target_cols if c != "id"]
        update_clause = ", ".join(f"T.{c} = S.{c}" for c in update_cols)
        insert_cols = ", ".join(target_cols)
        insert_vals = ", ".join(f"S.{c}" for c in target_cols)
        merge_sql = f"""
        MERGE `{target_id}` AS T
        USING `{staging_id}` AS S
        ON T.id = S.id
        WHEN MATCHED THEN
            UPDATE SET {update_clause}
        WHEN NOT MATCHED THEN
            INSERT ({insert_cols}) VALUES ({insert_vals})
        """
        client.query(merge_sql, location=location).result()
        logger.info("Merged %d delta rows into %s", aligned_df.height, target_id)
    finally:
        client.delete_table(staging_id, not_found_ok=True)
