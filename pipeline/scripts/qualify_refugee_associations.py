#!/usr/bin/env python3
"""Qualify RNA V6 refugee-support associations using Gemini Decisions API.

Implements Stage 1 (candidate pool from ``Personnes exilées`` tag, regex match,
and optional 768d semantic Top-K) and Stage 2 (``gemini-decision-1`` REST calls
on ``/v1beta/decisions`` with incremental ``(id, text_source_sha256)`` caching).

Usage (from repository root)::

    uv run python pipeline/scripts/qualify_refugee_associations.py \
        --bdv 33063 \
        --with-768d-topk 150 \
        --output pipeline/cache/p0_bordeaux_refugee_decisions.parquet
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import io
import logging
import os
from pathlib import Path
import random
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from google import genai
from google.cloud import bigquery
from google.genai import types
import httpx
import numpy as np
import polars as pl
from pydantic import BaseModel, Field

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
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
DEFAULT_OUTPUT_PARQUET = (
    REPO_ROOT / "pipeline" / "cache" / "refugee_v6_verified.parquet"
)
DEFAULT_JSONL_CACHE = (
    REPO_ROOT / "pipeline" / "cache" / "refugee_decisions_cache.jsonl"
)

DECISIONS_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/decisions"
DECISIONS_MODEL = "gemini-decision-1"
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_QUERY = (
    "accueil hébergement accompagnement juridique social et intégration en France "
    "des réfugiés demandeurs d'asile et mineurs non accompagnés"
)
INPUT_COST_USD_PER_1M = 0.10

REFUGEE_REGEX_PATTERN = (
    r"réfugi|asile|exil|mineurs?\s+(?:non\s+accompagn|isol)|sans[- ]?papier|"
    r"primo[- ]?arriv|\bmna\b|\bresf\b|\bcada\b|migrant|"
    r"dispositif\s+national\s+d.accueil|déplac[eé]e?s?\s+par\s+force"
)

_NON_EMPLOYER_TRANCHES = ["", "NN", "00", "NONE", "NAN"]

QUESTIONS_SPEC: Dict[str, Any] = {
    "is_refugee_targeted": {
        "type": "noul",
        "instructions": (
            "Cette association s'adresse-t-elle explicitement (à titre principal "
            "ou via un dispositif dédié) aux personnes exilées, réfugiées, "
            "demandeuses d'asile, migrantes ou mineures non accompagnées (MNA) "
            "en France ?"
        ),
        "criteria": {
            "true_description": (
                "Accueil, hébergement, aide matérielle/alimentaire, accès aux "
                "droits/asile, accompagnement social, juridique ou linguistique "
                "(FLE), défense des droits ou intégration citoyenne en France "
                "visant explicitement les personnes exilées, réfugiées, "
                "demandeuses d'asile, migrantes, déplacées par force ou MNA."
            ),
            "false_description": (
                "Aide généraliste sans ciblage exil/migrants, promotion LGBTI "
                "ou entrepreneuriat sans lien avec l'asile (ex. APOTH & OSE), "
                "ou solidarité/humanitaire s'exerçant exclusivement à l'étranger."
            ),
        },
    },
    "primary_role": {
        "type": "choice",
        "instructions": "Quel est le rôle principal de cette association ?",
        "criteria": {
            "accompagnement_accueil": (
                "Dispositif concret d'accueil, hébergement (CADA, IML, DNA), "
                "aide d'urgence, accès aux droits/asile, santé, "
                "FLE/alphabétisation, parrainage ou insertion "
                "socio-professionnelle en France."
            ),
            "amicale_culture_culte": (
                "Amicale d'une communauté nationale/diaspora, promotion "
                "culturelle/folklorique, culte, entraide funéraire/obsèques "
                "ou loisirs entre ressortissants sans dispositif d'accueil social."
            ),
            "autre": (
                "Tout autre domaine (sport, entreprise, loisirs, événementiel, "
                "titre honorifique/historique, humanitaire hors de France)."
            ),
        },
    },
}


class RefugeeDecisionRecord(BaseModel):
    """Structured output record from a ``gemini-decision-1`` evaluation."""

    id: str
    text_source_sha256: str
    p_noul: float = Field(ge=0.0, le=1.0)
    noul_confidence: float = Field(ge=0.0, le=1.0)
    role_choice: str
    role_confidence: float = Field(ge=0.0, le=1.0)
    p_accompagnement_accueil: float = Field(ge=0.0, le=1.0)
    p_amicale_culture_culte: float = Field(ge=0.0, le=1.0)
    p_autre: float = Field(ge=0.0, le=1.0)
    input_tokens: int = Field(ge=0)
    evaluated_at: str
    model_version: str = DECISIONS_MODEL


class AsyncRateLimiter:
    """Simple async spacing rate limiter enforcing a target RPM ceiling."""

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


def format_association_input(row: Dict[str, Any]) -> str:
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
) -> pl.DataFrame:
    """Load the active social non-international association universe deduplicated by ``id``.

    Args:
        consolidated_parquet: Path to ``national_v6_consolidated.parquet``.
        bdv_codes: Optional list of Bassin de Vie codes to restrict the scan.

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

    df = (
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
    return df


def compute_768d_topk_scores(
    embeddings_parquet: Path,
    universe_ids: Sequence[str],
    api_key: str,
    top_k: int,
) -> Tuple[Dict[str, float], set[str]]:
    """Compute cosine similarity against 768d embeddings for ``universe_ids``.

    Args:
        embeddings_parquet: Path to ``national_v6_embeddings_768d.parquet``.
        universe_ids: Candidate universe IDs to score.
        api_key: Google AI Studio API key for embedding the search query.
        top_k: Number of top-scoring IDs to select.

    Returns:
        Tuple of ``(score_by_id, topk_id_set)``.

    Raises:
        FileNotFoundError: If ``embeddings_parquet`` does not exist.
    """
    if not embeddings_parquet.exists():
        raise FileNotFoundError(
            f"Embeddings 768d parquet not found: {embeddings_parquet}"
        )
    if top_k <= 0 or not universe_ids:
        return {}, set()

    logger.info("Embedding semantic reference query via %s...", EMBEDDING_MODEL)
    client = genai.Client(api_key=api_key)
    embed_resp = client.models.embed_content(
        model=EMBEDDING_MODEL,
        contents=EMBEDDING_QUERY,
        config=types.EmbedContentConfig(
            task_type="RETRIEVAL_QUERY",
            output_dimensionality=768,
        ),
    )
    q_vec = np.asarray(embed_resp.embeddings[0].values, dtype=np.float32)
    q_norm = q_vec / max(float(np.linalg.norm(q_vec)), 1e-12)

    id_list = list(dict.fromkeys(universe_ids))
    emb_df = (
        pl.scan_parquet(embeddings_parquet)
        .filter(pl.col("id").is_in(id_list))
        .select(["id", "embedding_gemini_768"])
        .collect()
    )
    if emb_df.is_empty():
        logger.warning("No matching IDs found in 768d embeddings parquet.")
        return {}, set()

    matched_ids = emb_df["id"].to_list()
    mat = np.asarray(emb_df["embedding_gemini_768"].to_list(), dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    sims = (mat / norms) @ q_norm

    order = np.argsort(-sims)
    score_by_id = {matched_ids[i]: float(sims[i]) for i in order}
    topk_ids = {matched_ids[i] for i in order[:top_k]}
    if len(order) > 0:
        min_topk_sim = float(sims[order[min(top_k, len(order)) - 1]])
        max_sim = float(sims[order[0]])
        logger.info(
            "768d cosine scoring complete: %d vectors scored, top-%d sim range [%.4f .. %.4f]",
            len(matched_ids),
            min(top_k, len(matched_ids)),
            min_topk_sim,
            max_sim,
        )
    return score_by_id, topk_ids


def build_candidate_pool(
    universe_df: pl.DataFrame,
    *,
    score_by_id: Dict[str, float],
    topk_ids: set[str],
) -> pl.DataFrame:
    """Filter ``universe_df`` to Stage 1 candidates (Exilés ∪ Regex ∪ Top-K 768d).

    Args:
        universe_df: Deduplicated active social non-international universe.
        score_by_id: Optional cosine similarity lookup from 768d embeddings.
        topk_ids: Optional set of Top-K 768d IDs.

    Returns:
        Polars DataFrame of Stage 1 candidates with provenance flags.
    """
    annotated = universe_df.with_columns(
        pl.col("llm_publics_cibles")
        .list.contains("Personnes exilées")
        .fill_null(False)
        .alias("in_exiles_tag"),
        (
            pl.col("title").fill_null("")
            + " "
            + pl.col("titre_court").fill_null("")
            + " "
            + pl.col("objet").fill_null("")
            + " "
            + pl.col("llm_tags_recherche").list.join(" ").fill_null("")
        )
        .str.to_lowercase()
        .str.contains(REFUGEE_REGEX_PATTERN)
        .fill_null(False)
        .alias("in_regex_match"),
        pl.col("id").is_in(list(topk_ids)).alias("in_topk_768d"),
        pl.col("id").replace_strict(score_by_id, default=None).alias("cosine_768d"),
    )
    return annotated.filter(
        pl.col("in_exiles_tag") | pl.col("in_regex_match") | pl.col("in_topk_768d")
    )


def load_jsonl_cache(cache_path: Path) -> Dict[Tuple[str, str], RefugeeDecisionRecord]:
    """Load incremental JSONL decision cache keyed by ``(id, text_source_sha256)``.

    Args:
        cache_path: Path to the JSONL cache file.

    Returns:
        Dictionary mapping ``(id, text_source_sha256)`` to ``RefugeeDecisionRecord``.
    """
    cache: Dict[Tuple[str, str], RefugeeDecisionRecord] = {}
    if not cache_path.exists():
        return cache

    with cache_path.open("r", encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                rec = RefugeeDecisionRecord.model_validate_json(stripped)
            except Exception as exc:
                logger.error(
                    "Invalid JSONL cache line %d in %s: %s", line_no, cache_path, exc
                )
                raise
            cache[(rec.id, rec.text_source_sha256)] = rec
    return cache


async def evaluate_one_association(
    client: httpx.AsyncClient,
    limiter: AsyncRateLimiter,
    semaphore: asyncio.Semaphore,
    api_key: str,
    row: Dict[str, Any],
    *,
    max_retries: int = 5,
) -> RefugeeDecisionRecord:
    """Call ``/v1beta/decisions`` for one association with retry on transient errors.

    Args:
        client: Shared async HTTPX client.
        limiter: RPM spacing limiter.
        semaphore: Concurrency semaphore.
        api_key: Google AI Studio API key.
        row: Candidate association row dictionary.
        max_retries: Maximum retry attempts on 429/5xx/network errors.

    Returns:
        Validated ``RefugeeDecisionRecord``.

    Raises:
        RuntimeError: On non-retryable HTTP errors or exhausted retries.
    """
    asso_id = str(row["id"])
    sha = str(row.get("text_source_sha256") or "")
    payload = {
        "model": DECISIONS_MODEL,
        "input": format_association_input(row),
        "questions": QUESTIONS_SPEC,
    }
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
            body = resp.json()
            answers = body["answers"]
            noul_ans = answers["is_refugee_targeted"]
            role_ans = answers["primary_role"]
            probs = role_ans["probabilities"]
            usage = body.get("usage") or {}
            return RefugeeDecisionRecord(
                id=asso_id,
                text_source_sha256=sha,
                p_noul=float(noul_ans["noul"]),
                noul_confidence=float(noul_ans["confidence"]),
                role_choice=str(role_ans["choice"]),
                role_confidence=float(role_ans["confidence"]),
                p_accompagnement_accueil=float(probs["accompagnement_accueil"]),
                p_amicale_culture_culte=float(probs["amicale_culture_culte"]),
                p_autre=float(probs["autre"]),
                input_tokens=int(usage.get("total_input_tokens", 0)),
                evaluated_at=datetime.now(UTC).isoformat(),
            )

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


async def run_decisions_batch(
    pending_rows: Iterable[Dict[str, Any]],
    *,
    api_key: str,
    cache_path: Path,
    rpm: int,
    concurrency: int,
) -> List[RefugeeDecisionRecord]:
    """Evaluate all pending candidates asynchronously and persist to JSONL cache.

    Args:
        pending_rows: Candidate row dictionaries not yet cached.
        api_key: Google AI Studio API key.
        cache_path: JSONL cache path appended line-by-line as responses arrive.
        rpm: Target request-per-minute ceiling.
        concurrency: Max concurrent HTTP requests.

    Returns:
        List of newly evaluated ``RefugeeDecisionRecord`` objects.
    """
    rows_list = list(pending_rows)
    if not rows_list:
        return []

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    limiter = AsyncRateLimiter(rpm=rpm)
    semaphore = asyncio.Semaphore(concurrency)
    results: List[RefugeeDecisionRecord] = []
    total = len(rows_list)
    completed = 0

    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(
                evaluate_one_association(client, limiter, semaphore, api_key, row)
            )
            for row in rows_list
        ]
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


def compute_verified_flag(df: pl.DataFrame) -> pl.DataFrame:
    """Compute the deterministic ``is_refugee_verified`` boolean column.

    Calibrated from P0 (Bordeaux 33063 + Saint-Étienne 42218 + A/B micro-test):
    ``p_noul >= 0.65`` captures multi-mission operators with explicit migrant
    clauses (e.g. ALFA 3A at 0.678), while ``p_accompagnement_accueil >= 0.70``
    filters residual diaspora/community amicales.

    Args:
        df: DataFrame containing ``p_noul``, ``role_choice``, and
            ``p_accompagnement_accueil`` columns.

    Returns:
        DataFrame with ``is_refugee_verified`` appended.
    """
    return df.with_columns(
        (
            (pl.col("p_noul") >= 0.65)
            & (pl.col("role_choice") == "accompagnement_accueil")
            & (pl.col("p_accompagnement_accueil") >= 0.70)
        ).alias("is_refugee_verified")
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Qualify RNA V6 refugee associations with gemini-decision-1."
    )
    parser.add_argument(
        "--consolidated-parquet",
        type=Path,
        default=DEFAULT_CONSOLIDATED_PARQUET,
        help="Path to national_v6_consolidated.parquet.",
    )
    parser.add_argument(
        "--embeddings-parquet",
        type=Path,
        default=DEFAULT_EMBEDDINGS_PARQUET,
        help="Path to national_v6_embeddings_768d.parquet.",
    )
    parser.add_argument(
        "--bdv",
        nargs="+",
        default=None,
        help="Optional Bassin de Vie codes (e.g. 33063) for targeted calibration.",
    )
    parser.add_argument(
        "--with-768d-topk",
        type=int,
        default=0,
        help="Include Top-K cosine candidates from 768d embeddings (0 disables).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PARQUET,
        help="Output Parquet path.",
    )
    parser.add_argument(
        "--jsonl-cache",
        type=Path,
        default=DEFAULT_JSONL_CACHE,
        help="Incremental JSONL checkpoint path keyed by (id, text_source_sha256).",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore existing JSONL cache and re-evaluate all candidates.",
    )
    parser.add_argument(
        "--rate-limit-rpm",
        type=int,
        default=900,
        help="Maximum requests per minute (Tier 1 limit is 1500 RPM).",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=15,
        help="Maximum concurrent in-flight HTTP requests.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build candidate pool and print diagnostics without calling Decisions API.",
    )
    parser.add_argument(
        "--upload-bq",
        action="store_true",
        help="Upload output Parquet to BigQuery (rna_rag.refugee_v6_verified).",
    )
    parser.add_argument(
        "--bq-project",
        default=os.getenv("ODIS_DATA_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or "odis-stream2-app",
        help="Target GCP project ID when --upload-bq is enabled.",
    )
    parser.add_argument(
        "--bq-table",
        default="rna_rag.refugee_v6_verified",
        help="Target BigQuery dataset.table when --upload-bq or --from-bq is enabled.",
    )
    parser.add_argument(
        "--from-bq",
        action="store_true",
        help=(
            "Query BigQuery directly for un-evaluated (id, text_source_sha256) delta "
            "candidates and MERGE updates into --bq-table (no-op if delta is 0)."
        ),
    )
    parser.add_argument(
        "--bq-v6-table",
        default="rna_rag.national_v6_consolidated",
        help="Source BigQuery V6 dataset.table when --from-bq is enabled.",
    )
    parser.add_argument(
        "--bq-location",
        default="europe-west1",
        help="BigQuery dataset location.",
    )
    return parser.parse_args(argv)


def fetch_bq_candidate_delta(
    client: bigquery.Client,
    *,
    project_id: str,
    v6_table: str,
    verified_table: str,
    location: str = "europe-west1",
) -> pl.DataFrame:
    """Fetch un-evaluated Stage 1 candidate rows via BigQuery ``NOT EXISTS`` anti-join.

    Args:
        client: Initialized BigQuery client.
        project_id: GCP project ID hosting both tables.
        v6_table: Source V6 ``dataset.table`` identifier.
        verified_table: Qualified target ``dataset.table`` identifier.
        location: BigQuery dataset location.

    Returns:
        Polars DataFrame of candidate associations missing ``(id, text_source_sha256)``
        in ``verified_table``.
    """
    sql = f"""
    WITH candidates AS (
        SELECT
            id,
            text_source_sha256,
            is_siege,
            adrs_codeinsee,
            bdv_codeinsee,
            title,
            titre_court,
            objet,
            waldec_labels_hierarchical,
            tranche_effectifs,
            date_decla,
            llm_tags_recherche,
            llm_thematiques,
            llm_publics_cibles,
            ('Personnes exilées' IN UNNEST(llm_publics_cibles)) AS in_exiles_tag,
            REGEXP_CONTAINS(
                LOWER(CONCAT(
                    COALESCE(title, ''), ' ',
                    COALESCE(titre_court, ''), ' ',
                    COALESCE(objet, ''), ' ',
                    ARRAY_TO_STRING(llm_tags_recherche, ' ')
                )),
                @regex_pattern
            ) AS in_regex_match
        FROM `{project_id}.{v6_table}`
        WHERE llm_action_sociale = TRUE
          AND (etat_admin IS NULL OR etat_admin != 'F')
          AND (llm_perimetre_action IS NULL OR llm_perimetre_action != 'international')
        QUALIFY ROW_NUMBER() OVER (
            PARTITION BY id
            ORDER BY
                CASE
                    WHEN UPPER(TRIM(COALESCE(tranche_effectifs, ''))) NOT IN ('', 'NN', '00', 'NONE', 'NAN') THEN 0
                    ELSE 1
                END,
                COALESCE(is_siege, FALSE) DESC,
                COALESCE(date_decla, '') DESC
        ) = 1
    )
    SELECT
        c.*,
        FALSE AS in_topk_768d,
        CAST(NULL AS FLOAT64) AS cosine_768d
    FROM candidates AS c
    WHERE (c.in_exiles_tag OR c.in_regex_match)
      AND NOT EXISTS (
          SELECT 1
          FROM `{project_id}.{verified_table}` AS v
          WHERE v.id = c.id
            AND COALESCE(v.text_source_sha256, '') = COALESCE(c.text_source_sha256, '')
      )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("regex_pattern", "STRING", REFUGEE_REGEX_PATTERN)
        ]
    )
    arrow_tbl = client.query(sql, job_config=job_config, location=location).to_arrow()
    return pl.DataFrame(arrow_tbl)


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


def sync_refugee_verified_delta_from_bq(
    *,
    project_id: str,
    v6_table: str = "rna_rag.national_v6_consolidated",
    verified_table: str = "rna_rag.refugee_v6_verified",
    location: str = "europe-west1",
    rate_limit_rpm: int = 900,
    concurrency: int = 15,
    jsonl_cache: Optional[Path] = DEFAULT_JSONL_CACHE,
    dry_run: bool = False,
) -> int:
    """Incrementally qualify and ``MERGE`` new/modified associations in BigQuery.

    Returns immediately (no-op, without requiring a Gemini API key) when no
    ``(id, text_source_sha256)`` delta exists between ``v6_table`` and
    ``verified_table``.

    Args:
        project_id: GCP project ID.
        v6_table: Source V6 ``dataset.table``.
        verified_table: Target qualified ``dataset.table``.
        location: BigQuery dataset location.
        rate_limit_rpm: Max Gemini Decisions RPM.
        concurrency: Max concurrent HTTP requests.
        jsonl_cache: Optional local JSONL checkpoint path.
        dry_run: When True, report delta count without calling Decisions API.

    Returns:
        Number of delta rows discovered (or evaluated and merged).
    """
    client = bigquery.Client(project=project_id)
    delta_df = fetch_bq_candidate_delta(
        client,
        project_id=project_id,
        v6_table=v6_table,
        verified_table=verified_table,
        location=location,
    )
    if delta_df.is_empty():
        logger.info(
            "✅ [RNA V6 Refugees] BigQuery delta is 0 (%s up to date). No-op.",
            verified_table,
        )
        return 0

    logger.info(
        "📡 [RNA V6 Refugees] Found %d delta candidates to qualify vs %s.",
        delta_df.height,
        verified_table,
    )
    if dry_run:
        return delta_df.height

    api_key = resolve_gemini_api_key()
    cache: Dict[Tuple[str, str], RefugeeDecisionRecord] = (
        load_jsonl_cache(jsonl_cache) if jsonl_cache else {}
    )
    delta_rows = delta_df.to_dicts()
    pending_rows = [
        row
        for row in delta_rows
        if (str(row["id"]), str(row.get("text_source_sha256") or "")) not in cache
    ]
    new_records = asyncio.run(
        run_decisions_batch(
            pending_rows,
            api_key=api_key,
            cache_path=jsonl_cache or DEFAULT_JSONL_CACHE,
            rpm=rate_limit_rpm,
            concurrency=concurrency,
        )
    )
    for rec in new_records:
        cache[(rec.id, rec.text_source_sha256)] = rec

    records_for_delta = [
        cache[(str(row["id"]), str(row.get("text_source_sha256") or ""))].model_dump()
        for row in delta_rows
    ]
    decisions_df = pl.DataFrame(records_for_delta)
    merged_delta = compute_verified_flag(
        delta_df.join(decisions_df, on=["id", "text_source_sha256"], how="inner")
    )
    merge_delta_to_bigquery(
        client,
        merged_delta,
        project_id=project_id,
        verified_table=verified_table,
        location=location,
    )
    return merged_delta.height


def upload_parquet_to_bigquery(
    parquet_path: Path,
    *,
    project_id: str,
    dataset_table: str,
    location: str = "europe-west1",
) -> None:
    """Load the qualified Parquet table into BigQuery (``WRITE_TRUNCATE``).

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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the qualification pipeline."""
    args = parse_args(argv)
    if args.from_bq:
        sync_refugee_verified_delta_from_bq(
            project_id=args.bq_project,
            v6_table=args.bq_v6_table,
            verified_table=args.bq_table,
            location=args.bq_location,
            rate_limit_rpm=args.rate_limit_rpm,
            concurrency=args.concurrency,
            jsonl_cache=None if args.no_resume else args.jsonl_cache,
            dry_run=args.dry_run,
        )
        return 0

    api_key = resolve_gemini_api_key()

    logger.info("Loading universe from %s (bdv=%s)...", args.consolidated_parquet, args.bdv)
    universe_df = build_universe_df(args.consolidated_parquet, bdv_codes=args.bdv)
    logger.info("Universe deduplicated IDs: %d", universe_df.height)

    score_by_id: Dict[str, float] = {}
    topk_ids: set[str] = set()
    if args.with_768d_topk > 0:
        score_by_id, topk_ids = compute_768d_topk_scores(
            args.embeddings_parquet,
            universe_df["id"].to_list(),
            api_key=api_key,
            top_k=args.with_768d_topk,
        )

    candidates_df = build_candidate_pool(
        universe_df,
        score_by_id=score_by_id,
        topk_ids=topk_ids,
    )
    n_exiles = int(candidates_df["in_exiles_tag"].sum())
    n_regex_only = int(
        candidates_df.filter(~pl.col("in_exiles_tag") & pl.col("in_regex_match")).height
    )
    n_topk_only = int(
        candidates_df.filter(
            ~pl.col("in_exiles_tag")
            & ~pl.col("in_regex_match")
            & pl.col("in_topk_768d")
        ).height
    )
    logger.info(
        "Stage 1 Candidate Pool: %d IDs (Personnes exilées=%d, +Regex only=%d, +768d Top-K only=%d)",
        candidates_df.height,
        n_exiles,
        n_regex_only,
        n_topk_only,
    )

    if args.dry_run:
        sample_rows = candidates_df.head(2).to_dicts()
        for row in sample_rows:
            logger.info("Sample prompt [%s]:\n%s", row["id"], format_association_input(row))
        return 0

    cache: Dict[Tuple[str, str], RefugeeDecisionRecord] = (
        {} if args.no_resume else load_jsonl_cache(args.jsonl_cache)
    )
    candidate_rows = candidates_df.to_dicts()
    pending_rows = [
        row
        for row in candidate_rows
        if (str(row["id"]), str(row.get("text_source_sha256") or "")) not in cache
    ]
    logger.info(
        "Cache hits: %d | Pending API evaluations: %d",
        len(candidate_rows) - len(pending_rows),
        len(pending_rows),
    )

    new_records = asyncio.run(
        run_decisions_batch(
            pending_rows,
            api_key=api_key,
            cache_path=args.jsonl_cache,
            rpm=args.rate_limit_rpm,
            concurrency=args.concurrency,
        )
    )
    for rec in new_records:
        cache[(rec.id, rec.text_source_sha256)] = rec

    records_for_pool = [
        cache[(str(row["id"]), str(row.get("text_source_sha256") or ""))].model_dump()
        for row in candidate_rows
    ]
    decisions_df = pl.DataFrame(records_for_pool)
    merged_df = candidates_df.join(
        decisions_df, on=["id", "text_source_sha256"], how="inner"
    )
    merged_df = compute_verified_flag(merged_df).sort(
        ["is_refugee_verified", "p_noul", "p_accompagnement_accueil"],
        descending=[True, True, True],
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    merged_df.write_parquet(args.output)
    logger.info("Saved %d qualified rows to %s", merged_df.height, args.output)

    total_tokens = int(merged_df["input_tokens"].sum())
    new_tokens = sum(r.input_tokens for r in new_records)
    est_cost_new = new_tokens * INPUT_COST_USD_PER_1M / 1_000_000.0
    n_verified = int(merged_df["is_refugee_verified"].sum())
    n_topk_only_verified = int(
        merged_df.filter(
            ~pl.col("in_exiles_tag")
            & ~pl.col("in_regex_match")
            & pl.col("in_topk_768d")
            & pl.col("is_refugee_verified")
        ).height
    )
    n_borderline = int(
        merged_df.filter((pl.col("p_noul") >= 0.45) & (pl.col("p_noul") <= 0.75)).height
    )
    logger.info(
        "Summary: verified=%d/%d (%.1f%%) | topk768d-only verified=%d | "
        "borderline(0.45..0.75)=%d | total_tokens=%d | new_api_cost=$%.4f",
        n_verified,
        merged_df.height,
        100.0 * n_verified / max(merged_df.height, 1),
        n_topk_only_verified,
        n_borderline,
        total_tokens,
        est_cost_new,
    )
    if args.upload_bq:
        upload_parquet_to_bigquery(
            args.output,
            project_id=args.bq_project,
            dataset_table=args.bq_table,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
