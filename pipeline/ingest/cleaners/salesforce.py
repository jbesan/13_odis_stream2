"""Salesforce J'accueille data cleaner for postal-code aggregation."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict

from pipeline.common import PipelineLogger
from pipeline.ingest import context
from pipeline.ingest.helpers import _artifact_observed_at
from pipeline.salesforce_ingest import (
    OUTPUT_PATH as SHARED_OUTPUT_PATH,
    fetch_and_aggregate_salesforce,
    get_salesforce_status,
)


def clean_salesforce_jaccueille(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Extract and aggregate Salesforce J'accueille data by postal code into CLEAN_DIR."""
    logger.log_step("clean_salesforce_jaccueille", "STARTED")
    try:
        output_path = context.CLEAN_DIR / "salesforce_jaccueille.parquet"
        cached_source = get_salesforce_status(output_path)
        if not cached_source.get("within_ttl"):
            cached_source = get_salesforce_status(SHARED_OUTPUT_PATH)

        fetch_and_aggregate_salesforce(
            force=False,
            output_path=output_path,
        )


        source_status = get_salesforce_status(output_path)
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
