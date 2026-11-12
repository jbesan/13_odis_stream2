"""Salesforce J'accueille data cleaner for postal-code aggregation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

import pandas as pd

from pipeline.common import PipelineLogger
from pipeline.ingest import context
from pipeline.ingest.helpers import _artifact_observed_at
from pipeline.odace_client import get_odace_client
from pipeline.run_context import PipelineRunError
import pipeline.salesforce_ingest as sf_ingest


def clean_salesforce_jaccueille(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Extract and aggregate Salesforce J'accueille data by postal code into CLEAN_DIR.

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.

    Raises:
        PipelineRunError: If ODAS fetch or legacy Salesforce extraction fails.
    """
    logger.log_step("clean_salesforce_jaccueille", "STARTED")
    source_cfg = config.get("sources", {}).get("salesforce_jaccueille") or config.get(
        "local_files", {}
    ).get("salesforce_jaccueille", {})
    output_path = context.CLEAN_DIR / "salesforce_jaccueille.parquet"

    if source_cfg.get("use_odace", False):
        try:
            client = get_odace_client(logger)
            odace_table = source_cfg.get("odace_table", "fact_jaccueille_code_postal")
            ttl_days = source_cfg.get("ttl_days", 7)
            df_odas = client.fetch_table(odace_table, ttl_days=ttl_days)
            if df_odas.empty:
                raise ValueError(f"ODAS export '{odace_table}' is empty")

            df_clean = (
                pd.DataFrame(
                    {
                        "code_postal": df_odas["code_postal"].astype(str).str.zfill(5),
                        "lead_count": df_odas["nb_prospects"].fillna(0).astype("int64"),
                        "contact_count": df_odas["nb_contacts"]
                        .fillna(0)
                        .astype("int64"),
                        "total_jaccueille_count": df_odas["nb_total"]
                        .fillna(0)
                        .astype("int64"),
                    }
                )
                .sort_values("code_postal")
                .reset_index(drop=True)
            )

            output_path.parent.mkdir(parents=True, exist_ok=True)
            df_clean.to_parquet(output_path, index=False)
            logger.log_source("salesforce_jaccueille", "REFRESHED", output_path)
            logger.log_step(
                "clean_salesforce_jaccueille",
                "COMPLETED",
                {"path": str(output_path), "rows": len(df_clean)},
            )
            return
        except Exception as e:
            logging.error(f"Failed to ingest Salesforce J'accueille from ODAS: {e}")
            logger.log_step("clean_salesforce_jaccueille", "FAILED", {"error": str(e)})
            raise PipelineRunError(
                f"ODAS Salesforce J'accueille cleaner failed: {e}"
            ) from e

    try:
        cached_source = sf_ingest.get_salesforce_status(output_path)
        if not cached_source.get("within_ttl"):
            cached_source = sf_ingest.get_salesforce_status(sf_ingest.OUTPUT_PATH)

        sf_ingest.fetch_and_aggregate_salesforce(
            force=False,
            output_path=output_path,
        )

        source_status = sf_ingest.get_salesforce_status(output_path)
        source_path = (
            Path(source_status["path"]) if source_status.get("path") else output_path
        )
        logger.log_source(
            "salesforce_jaccueille",
            "CACHED" if cached_source.get("within_ttl") else "REFRESHED",
            output_path,
            observed_at=_artifact_observed_at(source_path),
        )
        logger.log_step(
            "clean_salesforce_jaccueille", "COMPLETED", {"path": str(output_path)}
        )
    except Exception as e:
        logging.error(f"Failed to ingest Salesforce J'accueille data: {e}")
        logger.log_step("clean_salesforce_jaccueille", "FAILED", {"error": str(e)})
        raise
