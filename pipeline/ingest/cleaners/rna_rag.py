"""RNA BigQuery RAG vector search, accommodation, and refugee associations cleaners."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from google import genai
import pandas as pd

from pipeline.common import PipelineLogger, load_config, load_dataset
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.ingest.helpers import (
    _data_project,
    _materialize_rag_cache,
    _rna_rag_table_id,
    _save_rag_cache,
)
from pipeline.run_context import PipelineRunError


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


def clean_hebergement_rna(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Extracts accommodation-related associations from RNA using RAG (IML & Citoyen)."""
    import pipeline.ingest as ingest

    output_agg = context.CLEAN_DIR / "hebergement_rna_cols.parquet"
    shared_path = context.SHARED_CLEAN_DIR / output_agg.name
    ttl_days = config["sources"]["rna_rag"]["ttl_days"]

    cache_details = _materialize_rag_cache(
        output_agg,
        shared_path,
        ttl_days,
        logger,
        "rna_rag_hebergement",
    )
    if cache_details:
        logging.info(
            "[RNA RAG] Reusing hebergement cache (%s days old, TTL=%s).",
            cache_details["cache_age_days"],
            ttl_days,
        )
        logger.log_step("clean_hebergement_rna", "COMPLETED", cache_details)
        return

    logger.log_step("clean_hebergement_rna", "STARTED")

    domain_cfg = get_domain_contracts()
    prompts = domain_cfg.rna_rag.hebergement_prompts

    try:
        # A. IML Counts
        df_iml = ingest.compute_rna_rag_counts(prompts["iml"], config=config)
        df_iml = df_iml.rename(columns={"count": "heb_loc_iml_count"})

        # B. Citoyen Counts
        df_cit = ingest.compute_rna_rag_counts(prompts["citoyen"], config=config)
        df_cit = df_cit.rename(columns={"count": "heb_habitant_count"})

        agg = df_iml.merge(df_cit, on="codgeo", how="outer").fillna(0)
        agg["codgeo"] = agg["codgeo"].astype(str).str.zfill(5)

        agg.to_parquet(output_agg)
        _save_rag_cache(output_agg, shared_path)
        logger.log_source("rna_rag_hebergement", "REFRESHED", output_agg)
        logger.log_step(
            "clean_hebergement_rna",
            "COMPLETED",
            {"path": str(output_agg), "rows": len(agg), "cache_status": "refreshed"},
        )
    except Exception as e:
        logging.error(f"❌ [RNA RAG] Pivot failed: {e}")
        logger.log_step("clean_hebergement_rna", "ERROR", {"error": str(e)})
        raise PipelineRunError(f"RNA RAG hebergement pivot failed: {e}") from e


def clean_refugee_associations(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Filters RNA for refugee associations and augments data."""
    import pipeline.ingest as ingest

    output_path = context.CLEAN_DIR / "refugee_associations.parquet"
    shared_path = context.SHARED_CLEAN_DIR / output_path.name
    ttl_days = config["sources"]["rna_rag"]["ttl_days"]

    cache_details = _materialize_rag_cache(
        output_path,
        shared_path,
        ttl_days,
        logger,
        "rna_rag_refugee",
    )
    if cache_details:
        logging.info(
            "[RNA RAG] Reusing refugee associations cache (%s days old, TTL=%s).",
            cache_details["cache_age_days"],
            ttl_days,
        )
        logger.log_step("clean_refugee_associations", "COMPLETED", cache_details)
        return

    logger.log_step("clean_refugee_associations", "STARTED")

    try:
        client = ingest.bigquery.Client(project=_data_project())
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

    df_refug["codgeo"] = df_refug["codgeo"].astype(str).str.zfill(5)

    mapping_source = config["sources"]["bassins_de_vie"]
    mapping_path = context.CACHE_DIR / mapping_source["archive_file"]
    if mapping_path.exists():
        df_mapping = load_dataset(mapping_path, mapping_source)
        df_mapping = df_mapping.rename(
            columns={
                "Code géographique": "codgeo",
                "Bassin de vie 2022": "bassin_de_vie",
            }
        )
        if "codgeo" in df_mapping.columns and "bassin_de_vie" in df_mapping.columns:
            df_mapping["codgeo"] = df_mapping["codgeo"].astype(str).str.zfill(5)
            df_mapping["bassin_de_vie"] = (
                df_mapping["bassin_de_vie"]
                .astype(str)
                .str.replace(r"\.0$", "", regex=True)
            )
            df_refug = df_refug.merge(
                df_mapping[["codgeo", "bassin_de_vie"]], on="codgeo", how="left"
            )

    useful_cols = [
        "id",
        "codgeo",
        "bassin_de_vie",
        "name",
        "description",
        "waldec_code",
    ]
    df_out = df_refug[[c for c in useful_cols if c in df_refug.columns]].copy()

    output_path = context.CLEAN_DIR / "refugee_associations.parquet"
    df_out.to_parquet(output_path)
    _save_rag_cache(output_path, shared_path)
    logger.log_source("rna_rag_refugee", "REFRESHED", output_path)
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
