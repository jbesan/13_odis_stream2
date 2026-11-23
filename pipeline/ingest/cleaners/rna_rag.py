"""RNA BigQuery RAG vector search, accommodation, and refugee associations cleaners."""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from google import genai
import pandas as pd

from pipeline.common import PipelineLogger, load_config, load_dataset
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.ingest.helpers import (
    _data_project,
    _materialize_rag_cache,
    _rna_rag_table_id,
    _rna_v6_hebergement_verified_table_id,
    _rna_v6_refugee_verified_table_id,
    _rna_v6_table_id,
    _rna_v6_tags_ref_table_id,
    _save_rag_cache,
)
from pipeline.run_context import PipelineRunError

_BQ_LOCATION = "europe-west1"
_SQL_NORMALIZE_CODGEO = """CASE
    WHEN REGEXP_CONTAINS(LPAD(TRIM(adrs_codeinsee), 5, '0'), r'^751(0[1-9]|1[0-9]|20)$') THEN '75056'
    WHEN REGEXP_CONTAINS(LPAD(TRIM(adrs_codeinsee), 5, '0'), r'^6938[1-9]$') THEN '69123'
    WHEN REGEXP_CONTAINS(LPAD(TRIM(adrs_codeinsee), 5, '0'), r'^132(0[1-9]|1[0-6])$') THEN '13055'
    ELSE LPAD(TRIM(adrs_codeinsee), 5, '0')
END"""
_INCLUSION_REFERENTIAL_CSV = (
    Path(__file__).resolve().parents[2]
    / "local"
    / "referentiel_services_inclusion.csv"
)


def _normalize_inclusion_label(label: str) -> str:
    """Normalize Data Inclusion theme labels for deterministic CSV matching.

    Args:
        label: Raw Data Inclusion theme label string.

    Returns:
        Trimmed label with straight apostrophes normalized to curly ``’`` and
        internal whitespace collapsed.
    """
    return " ".join(str(label).strip().replace("'", "’").split())


def _load_inclusion_label_to_slug_map(
    csv_path: Path = _INCLUSION_REFERENTIAL_CSV,
) -> Dict[str, str]:
    """Load canonical mapping from Data Inclusion labels to official slugs.

    Args:
        csv_path: Path to ``referentiel_services_inclusion.csv`` (columns ``Nom,Label``).

    Returns:
        Mapping from normalized theme label to official ``Nom`` slug.

    Raises:
        PipelineRunError: If the referential CSV is missing, malformed, or empty.
    """
    if not csv_path.exists():
        raise PipelineRunError(
            f"Data Inclusion referential CSV not found at {csv_path}"
        )

    mapping: Dict[str, str] = {}
    with open(csv_path, mode="r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or {"Nom", "Label"} - set(reader.fieldnames):
            raise PipelineRunError(
                f"Invalid columns in {csv_path}: expected Nom,Label, got {reader.fieldnames}"
            )
        for row in reader:
            slug = str(row.get("Nom") or "").strip()
            raw_label = str(row.get("Label") or "").strip()
            if slug and raw_label:
                mapping[_normalize_inclusion_label(raw_label)] = slug

    if not mapping:
        raise PipelineRunError(
            f"Data Inclusion referential CSV at {csv_path} contains no valid entries"
        )
    return mapping


def compute_rna_rag_counts(
    query_text: str,
    threshold: Optional[float] = None,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Computes semantic counts for a query using BigQuery Vector Search (ML.DISTANCE)."""
    import pipeline.ingest as ingest

    domain_cfg = get_domain_contracts()
    rag_cfg = domain_cfg.rna_rag
    if threshold is None:
        threshold = rag_cfg.similarity_threshold

    project = _data_project()
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west1")
    client = ingest.bigquery.Client(project=project)
    genai_client = genai.Client(vertexai=True, project=project, location=location)

    response = genai_client.models.embed_content(
        model=rag_cfg.embedding_model,
        contents=[query_text],
        config={"output_dimensionality": rag_cfg.output_dimensionality},
    )
    query_vector = response.embeddings[0].values

    distance_threshold = 1.0 - threshold

    sql = f"""
    SELECT 
        codgeo,
        COUNT(*) as count
    FROM `{_rna_rag_table_id(config)}`
    WHERE is_inclusion_relevant = True
    AND ML.DISTANCE(ARRAY(SELECT element FROM UNNEST(embedding_128.list)), @query_vec, 'COSINE') < @dist_threshold
    GROUP BY 1
    """

    job_config = ingest.bigquery.QueryJobConfig(
        query_parameters=[
            ingest.bigquery.ArrayQueryParameter("query_vec", "FLOAT64", query_vector),
            ingest.bigquery.ScalarQueryParameter(
                "dist_threshold", "FLOAT64", distance_threshold
            ),
        ]
    )

    logging.info(f"📡 [RNA RAG] Computing semantic counts for: '{query_text}'")
    df = client.query(sql, job_config=job_config).to_dataframe()
    return df


def _query_hebergement_v6_counts(
    config: Dict[str, Any], ingest_mod: Any
) -> pd.DataFrame:
    """Query commune-level IML and citizen-hosting counts from Commun RNA V6.

    Joins active non-international social establishments in ``national_v6_consolidated``
    with verified housing flags in ``hebergement_v6_verified`` so every active branch
    (antenne) is counted in its normalized parent commune (PLM mapped to 75056/69123/13055)
    without cross-arrondissement double counting.

    Args:
        config: Pipeline configuration dictionary.
        ingest_mod: ``pipeline.ingest`` module namespace (allows test client patching).

    Returns:
        DataFrame with ``codgeo``, ``heb_loc_iml_count``, and ``heb_habitant_count``.

    Raises:
        PipelineRunError: If the BigQuery query returns zero rows.
    """
    client = ingest_mod.bigquery.Client(project=_data_project())
    v6_table = _rna_v6_table_id(config)
    hebergement_table = _rna_v6_hebergement_verified_table_id(config)
    sql = f"""
    WITH verified AS (
        SELECT
            id,
            is_iml_verified,
            is_hebergement_citoyen_verified
        FROM `{hebergement_table}`
        WHERE is_iml_verified = TRUE
           OR is_hebergement_citoyen_verified = TRUE
    ),
    matched_establishments AS (
        SELECT
            n.id,
            n.adrs_codeinsee,
            v.is_iml_verified,
            v.is_hebergement_citoyen_verified
        FROM `{v6_table}` AS n
        INNER JOIN verified AS v
            ON n.id = v.id
        WHERE n.llm_action_sociale = TRUE
          AND (n.etat_admin IS NULL OR n.etat_admin != 'F')
          AND (n.llm_perimetre_action IS NULL OR n.llm_perimetre_action != 'international')
          AND n.adrs_codeinsee IS NOT NULL
          AND LENGTH(TRIM(n.adrs_codeinsee)) > 0
    )
    SELECT
        {_SQL_NORMALIZE_CODGEO} AS codgeo,
        COUNT(DISTINCT IF(is_iml_verified = TRUE, id, NULL)) AS heb_loc_iml_count,
        COUNT(DISTINCT IF(is_hebergement_citoyen_verified = TRUE, id, NULL)) AS heb_habitant_count
    FROM matched_establishments
    GROUP BY 1
    """
    logging.info("📡 [RNA V6] Fetching multi-antenna hébergement counts from BigQuery...")
    agg = client.query(sql, location=_BQ_LOCATION).to_dataframe()
    if agg.empty:
        raise PipelineRunError("RNA V6 hébergement query returned 0 rows")
    agg["codgeo"] = agg["codgeo"].astype(str).str.strip().str.zfill(5)
    agg["heb_loc_iml_count"] = agg["heb_loc_iml_count"].fillna(0).astype("int64")
    agg["heb_habitant_count"] = agg["heb_habitant_count"].fillna(0).astype("int64")
    return agg


def clean_hebergement_rna(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Extract accommodation counts (IML & citizen hosting) from RNA V6 or legacy V5.

    Args:
        config: Pipeline configuration dictionary.
        logger: Structured pipeline logger.

    Raises:
        PipelineRunError: If the BigQuery query fails or returns empty results.
    """
    import pipeline.ingest as ingest

    use_v6 = "rna_v6" in config.get("sources", {})
    output_agg = context.CLEAN_DIR / "hebergement_rna_cols.parquet"
    shared_name = "rna_v6_hebergement_cols.parquet" if use_v6 else output_agg.name
    shared_path = context.SHARED_CLEAN_DIR / shared_name
    ttl_days = (
        int(config["sources"]["rna_v6"].get("ttl_days", 30))
        if use_v6
        else int(config["sources"]["rna_rag"]["ttl_days"])
    )
    source_key = "rna_v6_hebergement" if use_v6 else "rna_rag_hebergement"

    cache_details = _materialize_rag_cache(
        output_agg,
        shared_path,
        ttl_days,
        logger,
        source_key,
    )
    if cache_details:
        logging.info(
            "[%s] Reusing hebergement cache (%s days old, TTL=%s).",
            "RNA V6" if use_v6 else "RNA RAG",
            cache_details["cache_age_days"],
            ttl_days,
        )
        logger.log_step("clean_hebergement_rna", "COMPLETED", cache_details)
        return

    logger.log_step("clean_hebergement_rna", "STARTED")

    try:
        if use_v6:
            agg = _query_hebergement_v6_counts(config, ingest)
        else:
            domain_cfg = get_domain_contracts()
            prompts = domain_cfg.rna_rag.hebergement_prompts
            df_iml = ingest.compute_rna_rag_counts(prompts["iml"], config=config)
            df_iml = df_iml.rename(columns={"count": "heb_loc_iml_count"})
            df_cit = ingest.compute_rna_rag_counts(prompts["citoyen"], config=config)
            df_cit = df_cit.rename(columns={"count": "heb_habitant_count"})
            agg = df_iml.merge(df_cit, on="codgeo", how="outer").fillna(0)
            agg["codgeo"] = agg["codgeo"].astype(str).str.zfill(5)

        output_agg.parent.mkdir(parents=True, exist_ok=True)
        agg.to_parquet(output_agg, index=False)
        _save_rag_cache(output_agg, shared_path)
        logger.log_source(source_key, "REFRESHED", output_agg)
        logger.log_step(
            "clean_hebergement_rna",
            "COMPLETED",
            {"path": str(output_agg), "rows": len(agg), "cache_status": "refreshed"},
        )
    except Exception as e:
        logging.error(f"❌ [RNA Hébergement] Pivot failed: {e}")
        logger.log_step("clean_hebergement_rna", "ERROR", {"error": str(e)})
        raise PipelineRunError(f"RNA hebergement pivot failed: {e}") from e


def _attach_bassin_de_vie(
    df_refug: pd.DataFrame, config: Dict[str, Any]
) -> pd.DataFrame:
    """Fill missing ``bassin_de_vie`` codes using the INSEE Bassin de Vie mapping.

    Args:
        df_refug: Refugee associations DataFrame containing ``codgeo`` and optionally
            a pre-populated ``bassin_de_vie`` column.
        config: Pipeline configuration dictionary.

    Returns:
        DataFrame with ``bassin_de_vie`` normalized and missing values backfilled.
    """
    df_out = df_refug.copy()
    if "bassin_de_vie" in df_out.columns:
        df_out["bassin_de_vie"] = (
            df_out["bassin_de_vie"]
            .where(df_out["bassin_de_vie"].notna(), None)
            .astype("object")
        )

    needs_bv = "bassin_de_vie" not in df_out.columns or df_out["bassin_de_vie"].isna().any() or (
        df_out["bassin_de_vie"].astype(str).str.strip().isin(["", "None", "nan"]).any()
    )
    if not needs_bv:
        df_out["bassin_de_vie"] = (
            df_out["bassin_de_vie"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
        )
        return df_out

    df_mapping: Optional[pd.DataFrame] = None
    cleaned_bv_path = context.CLEAN_DIR / "bassins_de_vie.parquet"
    if cleaned_bv_path.exists():
        candidate = pd.read_parquet(cleaned_bv_path)
        if {"codgeo", "bassin_de_vie"}.issubset(candidate.columns):
            df_mapping = candidate[["codgeo", "bassin_de_vie"]].copy()
    else:
        mapping_source = config.get("sources", {}).get("bassins_de_vie", {})
        archive_file = mapping_source.get("archive_file")
        mapping_path = context.CACHE_DIR / archive_file if archive_file else None
        if mapping_path and mapping_path.exists():
            candidate = load_dataset(mapping_path, mapping_source).rename(
                columns={
                    "Code géographique": "codgeo",
                    "Bassin de vie 2022": "bassin_de_vie",
                }
            )
            if {"codgeo", "bassin_de_vie"}.issubset(candidate.columns):
                df_mapping = candidate[["codgeo", "bassin_de_vie"]].copy()

    if df_mapping is not None and not df_mapping.empty:
        df_mapping["codgeo"] = df_mapping["codgeo"].astype(str).str.strip().str.zfill(5)
        df_mapping["bassin_de_vie"] = (
            df_mapping["bassin_de_vie"]
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
        )
        df_mapping = df_mapping.drop_duplicates(subset=["codgeo"])
        if "bassin_de_vie" in df_out.columns:
            existing = df_out["bassin_de_vie"].replace({"": pd.NA, "None": pd.NA, "nan": pd.NA})
            mapped = df_out["codgeo"].map(df_mapping.set_index("codgeo")["bassin_de_vie"])
            df_out["bassin_de_vie"] = existing.fillna(mapped)
        else:
            df_out = df_out.merge(df_mapping, on="codgeo", how="left")

    if "bassin_de_vie" in df_out.columns:
        df_out["bassin_de_vie"] = (
            df_out["bassin_de_vie"]
            .where(df_out["bassin_de_vie"].notna(), "")
            .astype(str)
            .str.strip()
            .str.replace(r"\.0$", "", regex=True)
        )
    return df_out


def clean_refugee_associations(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Fetch and clean refugee associations from Commun RNA V6 (or legacy RNA RAG fallback).

    Args:
        config: Pipeline configuration dictionary.
        logger: Structured pipeline logger.

    Raises:
        PipelineRunError: If the BigQuery query fails or returns an empty dataset.
    """
    import pipeline.ingest as ingest

    sources = (config or {}).get("sources", {})
    use_v6 = "rna_v6" in sources
    output_path = context.CLEAN_DIR / "refugee_associations.parquet"

    if use_v6:
        shared_path = context.SHARED_CLEAN_DIR / "rna_v6_refugee_associations.parquet"
        ttl_days = sources["rna_v6"]["ttl_days"]
        source_key = "rna_v6_refugee"
    else:
        shared_path = context.SHARED_CLEAN_DIR / output_path.name
        ttl_days = sources["rna_rag"]["ttl_days"]
        source_key = "rna_rag_refugee"

    cache_details = _materialize_rag_cache(
        output_path,
        shared_path,
        ttl_days,
        logger,
        source_key,
    )
    if cache_details:
        logging.info(
            "[RNA] Reusing refugee associations cache (%s days old, TTL=%s, source=%s).",
            cache_details["cache_age_days"],
            ttl_days,
            source_key,
        )
        logger.log_step("clean_refugee_associations", "COMPLETED", cache_details)
        return

    logger.log_step("clean_refugee_associations", "STARTED")

    try:
        client = ingest.bigquery.Client(project=_data_project())
        if use_v6:
            v6_table = _rna_v6_table_id(config)
            verified_table = _rna_v6_refugee_verified_table_id(config)
            query = f"""
            WITH verified_ids AS (
                SELECT id
                FROM `{verified_table}`
                WHERE is_refugee_verified = TRUE
            )
            SELECT
                id,
                {_SQL_NORMALIZE_CODGEO} AS codgeo,
                TRIM(bdv_codeinsee) AS bassin_de_vie,
                COALESCE(NULLIF(TRIM(title), ''), NULLIF(TRIM(titre_court), ''), '') AS name,
                COALESCE(TRIM(objet), '') AS description,
                COALESCE(TRIM(objet_social1), '') AS waldec_code,
                NULLIF(TRIM(nom_etablissement), '') AS nom_etablissement,
                NULLIF(TRIM(adrs_libcommune), '') AS commune_nom,
                NULLIF(TRIM(tranche_effectifs), '') AS tranche_effectifs,
                NULLIF(TRIM(date_decla), '') AS date_decla
            FROM `{v6_table}`
            WHERE llm_action_sociale = TRUE
              AND (etat_admin IS NULL OR etat_admin != 'F')
              AND (llm_perimetre_action IS NULL OR llm_perimetre_action != 'international')
              AND adrs_codeinsee IS NOT NULL
              AND LENGTH(TRIM(adrs_codeinsee)) > 0
              AND id IN (SELECT id FROM verified_ids)
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY id, {_SQL_NORMALIZE_CODGEO}
                ORDER BY
                    CASE
                        WHEN UPPER(TRIM(COALESCE(tranche_effectifs, ''))) NOT IN ('', 'NN', '00', 'NONE', 'NAN') THEN 0
                        ELSE 1
                    END,
                    COALESCE(is_siege, FALSE) DESC,
                    COALESCE(date_decla, '') DESC
            ) = 1
            """
            logging.info("📡 [RNA V6] Fetching verified refugee associations from BigQuery...")
            df_refug = client.query(query, location=_BQ_LOCATION).to_dataframe()
        else:
            query = f"""
            SELECT
                id,
                codgeo,
                titre_court as name,
                description,
                primary_category as waldec_code
            FROM `{_rna_rag_table_id(config)}`
            WHERE is_refugee_focused = True
            """
            logging.info(
                "📡 [RNA RAG] Fetching detailed refugee associations from BigQuery..."
            )
            df_refug = client.query(query).to_dataframe()
    except Exception as e:
        logging.error(f"Failed to fetch refugee associations from BQ: {e}")
        logger.log_step("clean_refugee_associations", "ERROR", {"error": str(e)})
        raise PipelineRunError(
            f"Failed to fetch refugee associations from BQ: {e}"
        ) from e

    if df_refug.empty:
        logging.warning("No refugee associations found in BigQuery.")
        logger.log_step(
            "clean_refugee_associations", "ERROR", {"error": "Empty dataset"}
        )
        raise PipelineRunError("No refugee associations found in BigQuery")

    df_refug["codgeo"] = df_refug["codgeo"].astype(str).str.strip().str.zfill(5)
    df_refug = _attach_bassin_de_vie(df_refug, config)

    useful_cols = [
        "id",
        "codgeo",
        "bassin_de_vie",
        "name",
        "description",
        "waldec_code",
        "nom_etablissement",
        "commune_nom",
        "tranche_effectifs",
        "date_decla",
    ]
    df_out = df_refug[[c for c in useful_cols if c in df_refug.columns]].copy()

    df_out.to_parquet(output_path, index=False)
    _save_rag_cache(output_path, shared_path)
    logger.log_source(source_key, "REFRESHED", output_path)
    logger.log_step(
        "clean_refugee_associations",
        "COMPLETED",
        {"path": str(output_path), "rows": len(df_out), "cache_status": "refreshed"},
    )


def fetch_rna_rag_stats(
    logger: PipelineLogger, config: Optional[Dict[str, Any]] = None
) -> Optional[Path]:
    """Fetches RNA category counts from BigQuery with configured TTL (365 days)."""
    import pipeline.ingest as ingest

    local_path = context.CLEAN_DIR / "rna_inclusion_agg.parquet"
    shared_path = context.SHARED_CLEAN_DIR / local_path.name
    if config is None:
        config = load_config(context.CONFIG_FILE)
    ttl_days = config["sources"]["rna_rag"]["ttl_days"]

    cache_details = _materialize_rag_cache(
        local_path,
        shared_path,
        ttl_days,
        logger,
        "rna_rag",
    )
    if cache_details:
        logging.info(
            "[RNA RAG] Reusing stats cache (%s days old, TTL=%s).",
            cache_details["cache_age_days"],
            ttl_days,
        )
        logger.log_step("fetch_rna_rag_stats", "COMPLETED", cache_details)
        return local_path

    logging.info("[RNA RAG] Stats cache is missing or expired. Refreshing...")

    try:
        client = ingest.bigquery.Client(project=_data_project())
        rna_rag_table = _rna_rag_table_id(config)

        query_cats = f"""
        SELECT 
            codgeo,
            primary_category,
            COUNT(*) as count
        FROM `{rna_rag_table}`
        WHERE is_inclusion_relevant = True
        GROUP BY 1, 2
        """
        logging.info("📡 [RNA RAG] Fetching category counts from BigQuery...")
        df_cats = client.query(query_cats).to_dataframe()

        query_refug = f"""
        SELECT 
            codgeo,
            COUNT(*) as inc_asso_refug_count
        FROM `{rna_rag_table}`
        WHERE is_refugee_focused = True
        GROUP BY 1
        """
        logging.info("📡 [RNA RAG] Fetching refugee counts from BigQuery...")
        df_refug = client.query(query_refug).to_dataframe()

        if df_cats.empty:
            logging.warning("[RNA RAG] No category data returned from BigQuery.")
            return None

        df_pivot = (
            df_cats.pivot(index="codgeo", columns="primary_category", values="count")
            .fillna(0)
            .reset_index()
        )
        df_pivot.columns = [
            f"inc_rna_{col}_count" if col != "codgeo" else col
            for col in df_pivot.columns
        ]
        df_pivot["codgeo"] = df_pivot["codgeo"].astype(str).str.zfill(5)

        if not df_refug.empty:
            df_refug["codgeo"] = df_refug["codgeo"].astype(str).str.zfill(5)
            df_pivot = df_pivot.merge(df_refug, on="codgeo", how="left")
            df_pivot["inc_asso_refug_count"] = df_pivot["inc_asso_refug_count"].fillna(
                0
            )
        else:
            df_pivot["inc_asso_refug_count"] = 0

        df_pivot.to_parquet(local_path)
        _save_rag_cache(local_path, shared_path)
        logging.info(
            f"✅ [RNA RAG] Saved {len(df_pivot)} commune stats to {local_path}"
        )
        logger.log_source("rna_rag", "REFRESHED", local_path)
        logger.log_step(
            "fetch_rna_rag_stats",
            "COMPLETED",
            {
                "path": str(local_path),
                "rows": len(df_pivot),
                "cache_status": "refreshed",
            },
        )
        return local_path

    except Exception as e:
        logging.error(f"❌ [RNA RAG] BigQuery fetch failed: {e}")
        logger.log_step("fetch_rna_rag_stats", "ERROR", {"error": str(e)})
        return None


def _get_rna_v6_ttl(config: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], int]:
    """Load pipeline config if absent and extract ``sources.rna_v6.ttl_days``.

    Args:
        config: Optional pipeline configuration dictionary.

    Returns:
        Tuple of resolved config dict and integer TTL in days.
    """
    resolved = config if config is not None else load_config(context.CONFIG_FILE)
    ttl_days = int(resolved.get("sources", {}).get("rna_v6", {}).get("ttl_days", 30))
    return resolved, ttl_days


def fetch_rna_v6_commune_stats(
    logger: PipelineLogger, config: Optional[Dict[str, Any]] = None
) -> Path:
    """Fetch Commun RNA V6 commune-level inclusion & refugee counts from BigQuery.

    Counts distinct associations (``COUNT(DISTINCT id)``, covering both ``is_siege``
    and branches) with ``llm_action_sociale = TRUE`` and non-international scope.
    PLM arrondissements are mapped to their parent commune prior to ``COUNT(DISTINCT)``
    to prevent cross-arrondissement double counting.

    Args:
        logger: Structured pipeline logger.
        config: Optional pipeline configuration dictionary.

    Returns:
        Path to the materialized ``rna_v6_inclusion_agg.parquet`` in ``CLEAN_DIR``.

    Raises:
        PipelineRunError: If BigQuery fails or returns an empty dataset.
    """
    import pipeline.ingest as ingest

    config, ttl_days = _get_rna_v6_ttl(config)
    local_path = context.CLEAN_DIR / "rna_v6_inclusion_agg.parquet"
    shared_path = context.SHARED_CLEAN_DIR / local_path.name

    cache_details = _materialize_rag_cache(
        local_path, shared_path, ttl_days, logger, "rna_v6"
    )
    if cache_details:
        logging.info(
            "[RNA V6] Reusing commune stats cache (%s days old, TTL=%s).",
            cache_details["cache_age_days"],
            ttl_days,
        )
        logger.log_step("fetch_rna_v6_commune_stats", "COMPLETED", cache_details)
        return local_path

    logger.log_step("fetch_rna_v6_commune_stats", "STARTED")
    try:
        client = ingest.bigquery.Client(project=_data_project())
        v6_table = _rna_v6_table_id(config)
        verified_table = _rna_v6_refugee_verified_table_id(config)
        sql = f"""
        WITH verified_ids AS (
            SELECT id
            FROM `{verified_table}`
            WHERE is_refugee_verified = TRUE
        )
        SELECT
            {_SQL_NORMALIZE_CODGEO} AS codgeo,
            COUNT(DISTINCT id) AS lien_social_count,
            COUNT(
                DISTINCT IF(
                    (etat_admin IS NULL OR etat_admin != 'F')
                    AND id IN (SELECT id FROM verified_ids),
                    id,
                    NULL
                )
            ) AS inc_asso_refug_count
        FROM `{v6_table}`
        WHERE llm_action_sociale = TRUE
          AND (llm_perimetre_action IS NULL OR llm_perimetre_action != 'international')
          AND adrs_codeinsee IS NOT NULL
          AND LENGTH(TRIM(adrs_codeinsee)) > 0
        GROUP BY 1
        """
        logging.info("📡 [RNA V6] Fetching commune social/refugee counts from BigQuery...")
        df = client.query(sql, location=_BQ_LOCATION).to_dataframe()
    except Exception as exc:
        logger.log_step("fetch_rna_v6_commune_stats", "ERROR", {"error": str(exc)})
        raise PipelineRunError(
            f"Failed to fetch RNA V6 commune stats from BigQuery: {exc}"
        ) from exc

    if df.empty:
        logger.log_step(
            "fetch_rna_v6_commune_stats", "ERROR", {"error": "Empty dataset"}
        )
        raise PipelineRunError("RNA V6 commune stats query returned 0 rows")

    df["codgeo"] = df["codgeo"].astype(str).str.strip().str.zfill(5)
    df["lien_social_count"] = df["lien_social_count"].astype("int64")
    df["inc_asso_refug_count"] = df["inc_asso_refug_count"].astype("int64")

    local_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(local_path, index=False)
    _save_rag_cache(local_path, shared_path)
    logger.log_source("rna_v6", "REFRESHED", local_path)
    logger.log_step(
        "fetch_rna_v6_commune_stats",
        "COMPLETED",
        {"path": str(local_path), "rows": len(df), "cache_status": "refreshed"},
    )
    return local_path


def fetch_rna_v6_thematiques_agg(
    logger: PipelineLogger, config: Optional[Dict[str, Any]] = None
) -> Path:
    """Fetch 2-grain (commune & BdV) RNA V6 Data Inclusion theme counts from BigQuery.

    Maps canonical French ``llm_thematiques`` labels against
    ``referentiel_services_inclusion.csv`` slugs and fails closed if any
    returned label is unmapped.

    Args:
        logger: Structured pipeline logger.
        config: Optional pipeline configuration dictionary.

    Returns:
        Path to ``rna_v6_thematiques_agg.parquet`` in ``CLEAN_DIR``.

    Raises:
        PipelineRunError: If BigQuery fails, returns empty data, or has unmapped labels.
    """
    import pipeline.ingest as ingest

    config, ttl_days = _get_rna_v6_ttl(config)
    local_path = context.CLEAN_DIR / "rna_v6_thematiques_agg.parquet"
    shared_path = context.SHARED_CLEAN_DIR / local_path.name

    cache_details = _materialize_rag_cache(
        local_path, shared_path, ttl_days, logger, "rna_v6_thematiques"
    )
    if cache_details:
        logging.info(
            "[RNA V6] Reusing thematiques cache (%s days old, TTL=%s).",
            cache_details["cache_age_days"],
            ttl_days,
        )
        logger.log_step("fetch_rna_v6_thematiques_agg", "COMPLETED", cache_details)
        return local_path

    logger.log_step("fetch_rna_v6_thematiques_agg", "STARTED")
    label_to_slug = _load_inclusion_label_to_slug_map()

    try:
        client = ingest.bigquery.Client(project=_data_project())
        v6_table = _rna_v6_table_id(config)
        sql = f"""
        WITH base AS (
            SELECT
                id,
                {_SQL_NORMALIZE_CODGEO} AS codgeo,
                TRIM(bdv_codeinsee) AS bdv,
                TRIM(theme_label) AS theme_label
            FROM `{v6_table}`,
            UNNEST(llm_thematiques) AS theme_label
            WHERE llm_action_sociale = TRUE
              AND (llm_perimetre_action IS NULL OR llm_perimetre_action != 'international')
              AND adrs_codeinsee IS NOT NULL
              AND LENGTH(TRIM(adrs_codeinsee)) > 0
              AND theme_label IS NOT NULL
              AND LENGTH(TRIM(theme_label)) > 0
        ),
        by_commune AS (
            SELECT
                'commune' AS grain,
                codgeo AS geo_code,
                theme_label,
                COUNT(DISTINCT id) AS count
            FROM base
            GROUP BY 1, 2, 3
        ),
        by_bdv AS (
            SELECT
                'bdv' AS grain,
                bdv AS geo_code,
                theme_label,
                COUNT(DISTINCT id) AS count
            FROM base
            WHERE bdv IS NOT NULL AND LENGTH(bdv) > 0
            GROUP BY 1, 2, 3
        )
        SELECT grain, geo_code, theme_label, count FROM by_commune
        UNION ALL
        SELECT grain, geo_code, theme_label, count FROM by_bdv
        """
        logging.info(
            "📡 [RNA V6] Fetching 2-grain thematiques counts from BigQuery..."
        )
        df = client.query(sql, location=_BQ_LOCATION).to_dataframe()
    except Exception as exc:
        logger.log_step("fetch_rna_v6_thematiques_agg", "ERROR", {"error": str(exc)})
        raise PipelineRunError(
            f"Failed to fetch RNA V6 thematiques from BigQuery: {exc}"
        ) from exc

    if df.empty:
        logger.log_step(
            "fetch_rna_v6_thematiques_agg", "ERROR", {"error": "Empty dataset"}
        )
        raise PipelineRunError("RNA V6 thematiques query returned 0 rows")

    normalized_labels = df["theme_label"].map(_normalize_inclusion_label)
    df["thematique_slug"] = normalized_labels.map(label_to_slug)

    unmapped = sorted(
        set(df.loc[df["thematique_slug"].isna(), "theme_label"].astype(str))
    )
    if unmapped:
        logger.log_step(
            "fetch_rna_v6_thematiques_agg",
            "ERROR",
            {"unmapped_labels": unmapped},
        )
        raise PipelineRunError(
            f"Unmapped RNA V6 llm_thematiques labels (not in referentiel_services_inclusion.csv): {unmapped}"
        )

    df["grain"] = df["grain"].astype(str).str.strip()
    df["geo_code"] = df["geo_code"].astype(str).str.strip()
    commune_mask = df["grain"] == "commune"
    df.loc[commune_mask, "geo_code"] = df.loc[commune_mask, "geo_code"].str.zfill(5)
    df["count"] = df["count"].astype("int32")

    df_out = (
        df.groupby(["grain", "geo_code", "thematique_slug"], as_index=False)["count"]
        .sum()
        .astype({"count": "int32"})
    )

    local_path.parent.mkdir(parents=True, exist_ok=True)
    df_out.to_parquet(local_path, index=False)
    _save_rag_cache(local_path, shared_path)
    logger.log_source("rna_v6_thematiques", "REFRESHED", local_path)
    logger.log_step(
        "fetch_rna_v6_thematiques_agg",
        "COMPLETED",
        {"path": str(local_path), "rows": len(df_out), "cache_status": "refreshed"},
    )
    return local_path


def fetch_rna_v6_tags_bdv(
    logger: PipelineLogger, config: Optional[Dict[str, Any]] = None
) -> Tuple[Path, Path]:
    """Fetch Bassin de Vie tag aggregates and the canonical tags referential from BigQuery.

    Notice: leisure/interest tags intentionally omit ``llm_action_sociale = TRUE``
    so sports, culture, and hobby clubs across the full Commun RNA V6 are included.

    Args:
        logger: Structured pipeline logger.
        config: Optional pipeline configuration dictionary.

    Returns:
        Tuple of paths ``(rna_v6_tags_bdv.parquet, rna_v6_tags_ref.parquet)`` in ``CLEAN_DIR``.

    Raises:
        PipelineRunError: If either BigQuery query fails or returns empty results.
    """
    import pipeline.ingest as ingest

    config, ttl_days = _get_rna_v6_ttl(config)
    bdv_path = context.CLEAN_DIR / "rna_v6_tags_bdv.parquet"
    ref_path = context.CLEAN_DIR / "rna_v6_tags_ref.parquet"
    shared_bdv_path = context.SHARED_CLEAN_DIR / bdv_path.name
    shared_ref_path = context.SHARED_CLEAN_DIR / ref_path.name

    cache_bdv = _materialize_rag_cache(
        bdv_path, shared_bdv_path, ttl_days, logger, "rna_v6_tags_bdv"
    )
    cache_ref = _materialize_rag_cache(
        ref_path, shared_ref_path, ttl_days, logger, "rna_v6_tags_ref"
    )
    if cache_bdv and cache_ref:
        logging.info(
            "[RNA V6] Reusing tags BdV & referential cache (TTL=%s).", ttl_days
        )
        logger.log_step(
            "fetch_rna_v6_tags_bdv",
            "COMPLETED",
            {"bdv_cache": cache_bdv, "ref_cache": cache_ref},
        )
        return bdv_path, ref_path

    logger.log_step("fetch_rna_v6_tags_bdv", "STARTED")
    try:
        client = ingest.bigquery.Client(project=_data_project())
        v6_table = _rna_v6_table_id(config)
        tags_ref_table = _rna_v6_tags_ref_table_id(config)

        sql_bdv = f"""
        SELECT
            TRIM(bdv_codeinsee) AS bassin_de_vie,
            TRIM(tag_canonique) AS tag_canonique,
            COUNT(DISTINCT id) AS count
        FROM `{v6_table}`,
        UNNEST(llm_tags_normalises) AS tag_canonique
        WHERE bdv_codeinsee IS NOT NULL
          AND LENGTH(TRIM(bdv_codeinsee)) > 0
          AND tag_canonique IS NOT NULL
          AND LENGTH(TRIM(tag_canonique)) > 0
        GROUP BY 1, 2
        """
        logging.info("📡 [RNA V6] Fetching BdV canonical tag counts from BigQuery...")
        df_bdv = client.query(sql_bdv, location=_BQ_LOCATION).to_dataframe()

        sql_ref = f"""
        SELECT
            TRIM(tag_canonique) AS tag_canonique,
            CAST(occ_national AS INT64) AS occ_national,
            CAST(nb_variantes_fusionnees AS INT64) AS nb_variantes_fusionnees,
            exemples_bruts
        FROM `{tags_ref_table}`
        ORDER BY occ_national DESC, tag_canonique ASC
        """
        logging.info("📡 [RNA V6] Fetching canonical tags referential from BigQuery...")
        df_ref = client.query(sql_ref, location=_BQ_LOCATION).to_dataframe()
    except Exception as exc:
        logger.log_step("fetch_rna_v6_tags_bdv", "ERROR", {"error": str(exc)})
        raise PipelineRunError(
            f"Failed to fetch RNA V6 tags from BigQuery: {exc}"
        ) from exc

    if df_bdv.empty or df_ref.empty:
        logger.log_step(
            "fetch_rna_v6_tags_bdv",
            "ERROR",
            {"error": f"Empty dataset (bdv={len(df_bdv)}, ref={len(df_ref)})"},
        )
        raise PipelineRunError(
            f"RNA V6 tags query returned empty result (bdv={len(df_bdv)}, ref={len(df_ref)})"
        )

    df_bdv["bassin_de_vie"] = (
        df_bdv["bassin_de_vie"].astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    )
    df_bdv["tag_canonique"] = df_bdv["tag_canonique"].astype(str).str.strip()
    df_bdv["count"] = df_bdv["count"].astype("int32")

    df_ref["tag_canonique"] = df_ref["tag_canonique"].astype(str).str.strip()
    df_ref["occ_national"] = df_ref["occ_national"].astype("int64")
    df_ref["nb_variantes_fusionnees"] = df_ref["nb_variantes_fusionnees"].astype("int64")
    df_ref["exemples_bruts"] = [
        [str(item) for item in val] if val is not None else []
        for val in df_ref["exemples_bruts"]
    ]

    bdv_path.parent.mkdir(parents=True, exist_ok=True)
    df_bdv.to_parquet(bdv_path, index=False)
    df_ref.to_parquet(ref_path, index=False)
    _save_rag_cache(bdv_path, shared_bdv_path)
    _save_rag_cache(ref_path, shared_ref_path)

    logger.log_source("rna_v6_tags_bdv", "REFRESHED", bdv_path)
    logger.log_source("rna_v6_tags_ref", "REFRESHED", ref_path)
    logger.log_step(
        "fetch_rna_v6_tags_bdv",
        "COMPLETED",
        {
            "bdv_path": str(bdv_path),
            "bdv_rows": len(df_bdv),
            "ref_path": str(ref_path),
            "ref_rows": len(df_ref),
            "cache_status": "refreshed",
        },
    )
    return bdv_path, ref_path
