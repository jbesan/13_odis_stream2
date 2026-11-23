#!/usr/bin/env python3
"""Qualify RNA V6 housing-support associations using Gemini Decisions API.

Evaluates Stage 1 candidates (``IML regex`` ∪ ``Citoyen/Coloc regex`` ∪
``Rechercher une solution d’hébergement temporaire`` theme ∪ optional 768d
semantic Top-K) across four independent ``noul`` questions in a single
``gemini-decision-1`` REST call:

1. ``is_iml`` (intermédiation locative, Solibail, AIVS, bail glissant)
2. ``is_hebergement_citoyen`` (accueil chez l'habitant, cohabitation solidaire)
3. ``is_habitat_partage_pension`` (colocations solidaires, pensions de famille)
4. ``is_hebergement_collectif_urgent`` (CHRS, CHU, CADA, FJT, abris collectifs)

Usage (from repository root)::

    uv run python -m pipeline.scripts.qualify_hebergement_associations \
        --bdv 33063 42218 \
        --with-768d-topk 100 \
        --output pipeline/cache/p0_hebergement_v2_decisions.parquet \
        --jsonl-cache pipeline/cache/p0_hebergement_v2_decisions_cache.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

from google.cloud import bigquery
import polars as pl
from pydantic import BaseModel, Field

from pipeline.scripts.decisions_common import (
    DECISIONS_MODEL,
    DEFAULT_CONSOLIDATED_PARQUET,
    DEFAULT_EMBEDDINGS_PARQUET,
    INPUT_COST_USD_PER_1M,
    REPO_ROOT,
    build_universe_df,
    compute_768d_topk_scores_multi,
    format_association_input,
    load_jsonl_cache_typed,
    merge_delta_to_bigquery,
    resolve_gemini_api_key,
    run_decisions_batch_generic,
    upload_parquet_to_bigquery,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_PARQUET = (
    REPO_ROOT / "pipeline" / "cache" / "hebergement_v6_verified.parquet"
)
DEFAULT_JSONL_CACHE = (
    REPO_ROOT / "pipeline" / "cache" / "hebergement_decisions_cache.jsonl"
)

HEBERGEMENT_THEME = "Rechercher une solution d’hébergement temporaire"
HOUSING_THEMES: Tuple[str, ...] = (
    "Rechercher une solution d’hébergement temporaire",
    "Rechercher une solution d'hébergement temporaire",
    "S’informer sur les démarches liées à l’accès au logement",
    "S'informer sur les démarches liées à l'accès au logement",
    "Se maintenir dans le logement",
    "Louer un logement",
    "Changer de logement",
)

IML_REGEX_PATTERN = (
    r"\biml\b|intermédiation\s+locative|solibail|\baivs\b|baux?\s+glissants?|"
    r"sous[- ]?locat(?:ion|aire)|sous[- ]?louer|prise\s+[àa]\s+bail|"
    r"gestion\s+locative|captation\s+de\s+logements|"
    r"mandat\s+de\s+gestion\s+(?:locative|sociale|immobili[eè]re)|"
    r"agence\s+immobili[eè]re\s+(?:[àa]\s+vocation\s+)?sociale|"
    r"insertion\s+par\s+le\s+logement"
)

CITOYEN_COLOC_REGEX_PATTERN = (
    r"(?:hébergement|accueil|logement)\s+(?:citoyen|chez\s+l.?habitant)|"
    r"familles?\s+accueillantes?|"
    r"cohabitation\s+(?:intergénérationnelle|solidaire)|"
    r"colocation\s+(?:solidaire|[àa]\s+projet)|maisons?\s+partagées?|"
    r"habitat\s+(?:inclusif|partagé)|logements?\s+inclusifs?|"
    r"pensions?\s+de\s+famille|résidence\s+accueil|b[eé]guinage"
)

EMBEDDING_QUERIES: Dict[str, str] = {
    "iml": (
        "intermédiation locative solidaire IML Solibail AIVS agence immobilière "
        "à vocation sociale captation de logements privés sous-location et bail "
        "glissant pour ménages précaires"
    ),
    "citoyen": (
        "hébergement citoyen et accueil solidaire chez l'habitant cohabitation "
        "intergénérationnelle colocation solidaire maison partagée pension de "
        "famille et résidence accueil"
    ),
}

QUESTIONS_SPEC: Dict[str, Any] = {
    "is_iml": {
        "type": "noul",
        "instructions": (
            "Pratique-t-elle l'intermédiation locative (IML, AIVS, Solibail, "
            "prise à bail pour sous-louer ou mandat de gestion sociale) pour "
            "des ménages en difficulté ?"
        ),
        "criteria": {
            "true_description": (
                "Association prenant à bail des logements privés ou publics "
                "(sous-location solidaire, bail glissant, mandat de gestion "
                "sociale, réseau IML/AIVS/SOLIHA/Habitat & Humanisme) pour "
                "loger des ménages modestes ou précaires."
            ),
            "false_description": (
                "Aide financière ponctuelle au loyer, défense de locataires, "
                "gestion de centres collectifs ou foyers (CHRS, CADA, CHU, FJT, "
                "résidences sociales) sans IML, accueil chez un particulier, "
                "habitat inclusif pur, simple information logement (ADIL), aide "
                "alimentaire ou tourisme/camping."
            ),
        },
    },
    "is_hebergement_citoyen": {
        "type": "noul",
        "instructions": (
            "Organise-t-elle l'accueil ou hébergement citoyen chez l'habitant "
            "ou la cohabitation intergénérationnelle/solidaire au domicile de "
            "particuliers ?"
        ),
        "criteria": {
            "true_description": (
                "Mise en relation et accompagnement pour héberger temporairement "
                "au domicile de particuliers ou familles accueillantes en France "
                "(jeunes, exilés, personnes précarisées, étudiants ou seniors en "
                "cohabitation intergénérationnelle)."
            ),
            "false_description": (
                "Association assurant elle-même un hébergement collectif, une "
                "prise à bail ou une aide financière/urgente sans recours "
                "explicite à des particuliers hébergeurs, placement ASE en "
                "famille d'accueil, spectacles/contes/concerts chez l'habitant, "
                "gîtes/tourisme/vacances, échanges scolaires ou linguistiques "
                "internationaux (Erasmus), ou garde d'animaux."
            ),
        },
    },
    "is_habitat_partage_pension": {
        "type": "noul",
        "instructions": (
            "Gère-t-elle ou anime-t-elle des colocations solidaires, maisons "
            "partagées, pensions de famille ou habitats inclusifs pour "
            "personnes vulnérables ?"
        ),
        "criteria": {
            "true_description": (
                "Gestion ou animation de lieux de vie partagés durables à taille "
                "humaine avec projet solidaire pour personnes isolées, âgées, "
                "handicapées ou sans-abri (maisons partagées type Lazare/Simon "
                "de Cyrène/Fratello, pensions de famille / maisons relais, "
                "résidences accueil, habitats inclusifs, béguinages)."
            ),
            "false_description": (
                "Établissement médico-social classique (EHPAD, FAM, MAS), "
                "coliving commercial, résidence étudiante/FJT classique, habitat "
                "participatif purement écologique ou de voisinage sans public "
                "vulnérable, centre d'hébergement d'urgence (CHU, CHRS, CADA) "
                "ou simple club/animation sans logement."
            ),
        },
    },
    "is_hebergement_collectif_urgent": {
        "type": "noul",
        "instructions": (
            "Gère-t-elle un établissement ou dispositif d'hébergement collectif "
            "ou d'urgence (CHRS, CHU, CADA, CPH, HUDA, FJT, centre maternel, "
            "abri de nuit) ?"
        ),
        "criteria": {
            "true_description": (
                "Gestion directe de centres ou dispositifs d'hébergement "
                "d'urgence, d'insertion ou de stabilisation (CHRS, CHU, abri de "
                "nuit, lits halte soins santé), dispositifs asile/réfugiés "
                "(CADA, HUDA, CPH, PRAHDA), foyers de jeunes travailleurs (FJT) "
                "ou centres maternels."
            ),
            "false_description": (
                "Intermédiation locative en logement diffus privé, accueil "
                "citoyen chez l'habitant, EHPAD/club/animation seniors sans "
                "hébergement d'urgence, accueil de jour ou maraude/colis "
                "alimentaire sans lits, protection civile, secours ou pompiers."
            ),
        },
    },
}

DEFAULT_THRESHOLD = 0.75


class HebergementDecisionRecord(BaseModel):
    """Structured output record from a 4-question ``gemini-decision-1`` evaluation."""

    id: str
    text_source_sha256: str
    p_iml: float = Field(ge=0.0, le=1.0)
    iml_confidence: float = Field(ge=0.0, le=1.0)
    p_hebergement_citoyen: float = Field(ge=0.0, le=1.0)
    citoyen_confidence: float = Field(ge=0.0, le=1.0)
    p_habitat_partage_pension: float = Field(ge=0.0, le=1.0)
    habitat_partage_confidence: float = Field(ge=0.0, le=1.0)
    p_hebergement_collectif_urgent: float = Field(ge=0.0, le=1.0)
    collectif_urgent_confidence: float = Field(ge=0.0, le=1.0)
    input_tokens: int = Field(ge=0)
    evaluated_at: str
    model_version: str = DECISIONS_MODEL


def parse_hebergement_response(
    row: Dict[str, Any], body: Dict[str, Any]
) -> HebergementDecisionRecord:
    """Convert a ``/v1beta/decisions`` JSON response into ``HebergementDecisionRecord``.

    Args:
        row: Input association dictionary.
        body: Parsed REST response JSON body.

    Returns:
        Validated ``HebergementDecisionRecord``.
    """
    answers = body["answers"]
    ans_iml = answers["is_iml"]
    ans_cit = answers["is_hebergement_citoyen"]
    ans_hab = answers["is_habitat_partage_pension"]
    ans_col = answers["is_hebergement_collectif_urgent"]
    usage = body.get("usage") or {}
    return HebergementDecisionRecord(
        id=str(row["id"]),
        text_source_sha256=str(row.get("text_source_sha256") or ""),
        p_iml=float(ans_iml["noul"]),
        iml_confidence=float(ans_iml["confidence"]),
        p_hebergement_citoyen=float(ans_cit["noul"]),
        citoyen_confidence=float(ans_cit["confidence"]),
        p_habitat_partage_pension=float(ans_hab["noul"]),
        habitat_partage_confidence=float(ans_hab["confidence"]),
        p_hebergement_collectif_urgent=float(ans_col["noul"]),
        collectif_urgent_confidence=float(ans_col["confidence"]),
        input_tokens=int(usage.get("total_input_tokens", 0)),
        evaluated_at=datetime.now(UTC).isoformat(),
    )


def build_candidate_pool(
    universe_df: pl.DataFrame,
    *,
    scores_by_query: Optional[Dict[str, Tuple[Dict[str, float], set[str]]]] = None,
) -> pl.DataFrame:
    """Filter ``universe_df`` to Stage 1 candidates (IML regex ∪ Citoyen regex ∪ Theme ∪ 768d).

    Args:
        universe_df: Deduplicated active social non-international universe.
        scores_by_query: Optional output of ``compute_768d_topk_scores_multi`` keyed by
            ``"iml"`` and ``"citoyen"``.

    Returns:
        Polars DataFrame of Stage 1 candidates with provenance flags.
    """
    iml_scores, iml_topk = (scores_by_query or {}).get("iml", ({}, set()))
    cit_scores, cit_topk = (scores_by_query or {}).get("citoyen", ({}, set()))

    haystack = (
        pl.col("title").fill_null("")
        + " "
        + pl.col("titre_court").fill_null("")
        + " "
        + pl.col("objet").fill_null("")
        + " "
        + pl.col("llm_tags_recherche").list.join(" ").fill_null("")
    ).str.to_lowercase()

    annotated = universe_df.with_columns(
        haystack.str.contains(IML_REGEX_PATTERN).fill_null(False).alias("in_iml_regex"),
        haystack.str.contains(CITOYEN_COLOC_REGEX_PATTERN)
        .fill_null(False)
        .alias("in_cit_regex"),
        pl.any_horizontal(
            [
                pl.col("llm_thematiques").list.contains(theme).fill_null(False)
                for theme in HOUSING_THEMES
            ]
        ).alias("in_heb_theme"),
        (
            pl.col("llm_thematiques").list.contains(HEBERGEMENT_THEME).fill_null(False)
            | pl.col("llm_thematiques")
            .list.contains(HOUSING_THEMES[1])
            .fill_null(False)
        ).alias("in_heb_temp_theme"),
        pl.col("id").is_in(list(iml_topk)).alias("in_topk_768d_iml"),
        pl.col("id").is_in(list(cit_topk)).alias("in_topk_768d_cit"),
        pl.col("id")
        .replace_strict(iml_scores, default=None, return_dtype=pl.Float64)
        .alias("cosine_768d_iml"),
        pl.col("id")
        .replace_strict(cit_scores, default=None, return_dtype=pl.Float64)
        .alias("cosine_768d_cit"),
    )
    return annotated.filter(
        pl.col("in_iml_regex")
        | pl.col("in_cit_regex")
        | pl.col("in_heb_theme")
        | pl.col("in_topk_768d_iml")
        | pl.col("in_topk_768d_cit")
    )


def compute_verified_flags(
    df: pl.DataFrame, *, threshold: float = DEFAULT_THRESHOLD
) -> pl.DataFrame:
    """Compute deterministic verified boolean flags from Decisions API probabilities.

    Args:
        df: Qualified DataFrame containing the 4 ``p_*`` score columns.
        threshold: Probability threshold for verified flags.

    Returns:
        DataFrame with ``is_*_verified`` boolean columns appended.
    """
    return df.with_columns(
        (pl.col("p_iml") >= threshold).alias("is_iml_verified"),
        (pl.col("p_hebergement_citoyen") >= threshold).alias(
            "is_hebergement_citoyen_verified"
        ),
        (pl.col("p_habitat_partage_pension") >= threshold).alias(
            "is_habitat_partage_verified"
        ),
        (pl.col("p_hebergement_collectif_urgent") >= threshold).alias(
            "is_collectif_urgent_verified"
        ),
    ).with_columns(
        (
            pl.col("is_iml_verified")
            | pl.col("is_hebergement_citoyen_verified")
            | pl.col("is_habitat_partage_verified")
            | pl.col("is_collectif_urgent_verified")
        ).alias("is_hebergement_any_verified")
    )


def fetch_bq_candidate_delta(
    client: bigquery.Client,
    *,
    project_id: str,
    v6_table: str,
    verified_table: str,
    location: str = "europe-west1",
) -> pl.DataFrame:
    """Fetch un-evaluated Stage 1 housing candidates via BigQuery ``NOT EXISTS`` anti-join.

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
            llm_est_urgence,
            REGEXP_CONTAINS(
                LOWER(CONCAT(
                    COALESCE(title, ''), ' ',
                    COALESCE(titre_court, ''), ' ',
                    COALESCE(objet, ''), ' ',
                    COALESCE(ARRAY_TO_STRING(llm_tags_recherche, ' '), '')
                )),
                @iml_regex
            ) AS in_iml_regex,
            REGEXP_CONTAINS(
                LOWER(CONCAT(
                    COALESCE(title, ''), ' ',
                    COALESCE(titre_court, ''), ' ',
                    COALESCE(objet, ''), ' ',
                    COALESCE(ARRAY_TO_STRING(llm_tags_recherche, ' '), '')
                )),
                @cit_regex
            ) AS in_cit_regex,
            EXISTS (
                SELECT 1
                FROM UNNEST(llm_thematiques) AS th
                WHERE th IN UNNEST(@housing_themes)
            ) AS in_heb_theme,
            (
                @heb_theme IN UNNEST(llm_thematiques)
                OR @heb_theme_ascii IN UNNEST(llm_thematiques)
            ) AS in_heb_temp_theme
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
        FALSE AS in_topk_768d_iml,
        FALSE AS in_topk_768d_cit,
        CAST(NULL AS FLOAT64) AS cosine_768d_iml,
        CAST(NULL AS FLOAT64) AS cosine_768d_cit
    FROM candidates AS c
    WHERE (c.in_iml_regex OR c.in_cit_regex OR c.in_heb_theme)
      AND NOT EXISTS (
          SELECT 1
          FROM `{project_id}.{verified_table}` AS v
          WHERE v.id = c.id
            AND COALESCE(v.text_source_sha256, '') = COALESCE(c.text_source_sha256, '')
      )
    """
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("iml_regex", "STRING", IML_REGEX_PATTERN),
            bigquery.ScalarQueryParameter(
                "cit_regex", "STRING", CITOYEN_COLOC_REGEX_PATTERN
            ),
            bigquery.ArrayQueryParameter(
                "housing_themes", "STRING", list(HOUSING_THEMES)
            ),
            bigquery.ScalarQueryParameter("heb_theme", "STRING", HEBERGEMENT_THEME),
            bigquery.ScalarQueryParameter(
                "heb_theme_ascii", "STRING", HOUSING_THEMES[1]
            ),
        ]
    )
    arrow_tbl = client.query(sql, job_config=job_config, location=location).to_arrow()
    return pl.DataFrame(arrow_tbl)


def sync_hebergement_verified_delta_from_bq(
    *,
    project_id: str,
    v6_table: str = "rna_rag.national_v6_consolidated",
    verified_table: str = "rna_rag.hebergement_v6_verified",
    location: str = "europe-west1",
    rate_limit_rpm: int = 900,
    concurrency: int = 15,
    jsonl_cache: Optional[Path] = DEFAULT_JSONL_CACHE,
    dry_run: bool = False,
) -> int:
    """Incrementally qualify and ``MERGE`` new/modified housing candidates in BigQuery.

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
            "✅ [RNA V6 Hébergement] BigQuery delta is 0 (%s up to date). No-op.",
            verified_table,
        )
        return 0

    logger.info(
        "📡 [RNA V6 Hébergement] Found %d delta candidates to qualify vs %s.",
        delta_df.height,
        verified_table,
    )
    if dry_run:
        return delta_df.height

    api_key = resolve_gemini_api_key()
    cache = (
        load_jsonl_cache_typed(jsonl_cache, HebergementDecisionRecord)
        if jsonl_cache
        else {}
    )
    delta_rows = delta_df.to_dicts()
    pending_rows = [
        row
        for row in delta_rows
        if (str(row["id"]), str(row.get("text_source_sha256") or "")) not in cache
    ]
    new_records = asyncio.run(
        run_decisions_batch_generic(
            pending_rows,
            api_key=api_key,
            questions_spec=QUESTIONS_SPEC,
            parse_response=parse_hebergement_response,
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
    merged_delta = compute_verified_flags(
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


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        description="Qualify RNA V6 housing associations via Gemini Decisions API."
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
        help="Optional Bassin de Vie codes (e.g. 33063 42218) for P0 calibration.",
    )
    parser.add_argument(
        "--with-768d-topk",
        type=int,
        default=0,
        help="Include Top-K cosine candidates per query (iml & citoyen) from 768d embeddings.",
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
        help="Upload output Parquet to BigQuery (rna_rag.hebergement_v6_verified).",
    )
    parser.add_argument(
        "--bq-project",
        default=os.getenv("ODIS_DATA_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT")
        or "odis-stream2-app",
        help="Target GCP project ID when --upload-bq or --from-bq is enabled.",
    )
    parser.add_argument(
        "--bq-table",
        default="rna_rag.hebergement_v6_verified",
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the housing qualification pipeline.

    Args:
        argv: Optional CLI argument list.

    Returns:
        Exit status code (0 on success).
    """
    args = parse_args(argv)
    if args.from_bq:
        sync_hebergement_verified_delta_from_bq(
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

    logger.info(
        "Loading universe from %s (bdv=%s)...", args.consolidated_parquet, args.bdv
    )
    universe_df = build_universe_df(
        args.consolidated_parquet,
        bdv_codes=args.bdv,
        extra_cols=["llm_est_urgence"],
    )
    logger.info("Universe deduplicated IDs: %d", universe_df.height)

    scores_by_query: Dict[str, Tuple[Dict[str, float], set[str]]] = {}
    if args.with_768d_topk > 0:
        scores_by_query = compute_768d_topk_scores_multi(
            args.embeddings_parquet,
            universe_df["id"].to_list(),
            api_key=api_key,
            queries=EMBEDDING_QUERIES,
            top_k=args.with_768d_topk,
        )

    candidates_df = build_candidate_pool(universe_df, scores_by_query=scores_by_query)
    n_iml_rx = int(candidates_df["in_iml_regex"].sum())
    n_cit_rx = int(candidates_df["in_cit_regex"].sum())
    n_theme = int(candidates_df["in_heb_theme"].sum())
    n_topk_only = int(
        candidates_df.filter(
            ~pl.col("in_iml_regex")
            & ~pl.col("in_cit_regex")
            & ~pl.col("in_heb_theme")
            & (pl.col("in_topk_768d_iml") | pl.col("in_topk_768d_cit"))
        ).height
    )
    logger.info(
        "Stage 1 Candidate Pool: %d IDs (IML_rx=%d, Cit_rx=%d, HebTheme=%d, +768d-only=%d)",
        candidates_df.height,
        n_iml_rx,
        n_cit_rx,
        n_theme,
        n_topk_only,
    )

    if args.dry_run:
        for row in candidates_df.head(2).to_dicts():
            logger.info(
                "Sample prompt [%s]:\n%s", row["id"], format_association_input(row)
            )
        return 0

    cache = (
        {}
        if args.no_resume
        else load_jsonl_cache_typed(args.jsonl_cache, HebergementDecisionRecord)
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
        run_decisions_batch_generic(
            pending_rows,
            api_key=api_key,
            questions_spec=QUESTIONS_SPEC,
            parse_response=parse_hebergement_response,
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
    merged_df = compute_verified_flags(merged_df).sort(
        [
            "is_hebergement_any_verified",
            "p_iml",
            "p_hebergement_citoyen",
            "p_habitat_partage_pension",
        ],
        descending=[True, True, True, True],
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    merged_df.write_parquet(args.output)
    logger.info("Saved %d qualified rows to %s", merged_df.height, args.output)

    total_tokens = int(merged_df["input_tokens"].sum())
    new_tokens = sum(r.input_tokens for r in new_records)
    est_cost_new = new_tokens * INPUT_COST_USD_PER_1M / 1_000_000.0
    logger.info(
        "Summary (threshold>=%.2f): total=%d | iml=%d | citoyen=%d | "
        "habitat_partage=%d | collectif_urgent=%d | any=%d | tokens=%d | cost=$%.4f",
        DEFAULT_THRESHOLD,
        merged_df.height,
        int(merged_df["is_iml_verified"].sum()),
        int(merged_df["is_hebergement_citoyen_verified"].sum()),
        int(merged_df["is_habitat_partage_verified"].sum()),
        int(merged_df["is_collectif_urgent_verified"].sum()),
        int(merged_df["is_hebergement_any_verified"].sum()),
        total_tokens,
        est_cost_new,
    )
    if args.upload_bq:
        upload_parquet_to_bigquery(
            args.output,
            project_id=args.bq_project,
            dataset_table=args.bq_table,
            location=args.bq_location,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
