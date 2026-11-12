"""Employment and jobs cleaners (France Travail live offers and Les emplois de l'inclusion)."""

from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Any, Dict

import pandas as pd
import requests

from pipeline.common import PipelineLogger, load_config
from pipeline.emplois_inclusion_ingest import run_ingestion as run_inclusion_ingest
from pipeline.ft_live_ingest import run_etl
from pipeline.ingest import context
from pipeline.ingest.helpers import (
    _artifact_observed_at,
    _materialize_employment_output_pair,
)
from pipeline.run_context import PipelineRunError


def get_live_jobs_status() -> Dict[str, Any]:
    """Checks the age of Live Jobs data in cache and deployed data."""
    import pipeline.ingest as ingest

    cache_path = ingest.OUTPUT_DIR / "odis_ft_jobs_agg.parquet"
    data_path = Path("data/odis_ft_jobs_agg.parquet")

    ttl_days = load_config(ingest.CONFIG_FILE)["local_files"]["france_travail_live"][
        "ttl_days"
    ]

    files = [cache_path, ingest.FT_SHARED_OUTPUT_PATH, data_path]
    coverage_files = [
        ingest.OUTPUT_DIR / ingest.FT_COVERAGE_SHARED_OUTPUT_PATH.name,
        ingest.FT_COVERAGE_SHARED_OUTPUT_PATH,
        Path("data") / ingest.FT_COVERAGE_SHARED_OUTPUT_PATH.name,
    ]
    mtimes = []
    for f in files:
        if f.exists():
            mtimes.append(f.stat().st_mtime)

    if not mtimes:
        return {
            "age_days": None,
            "within_ttl": False,
            "exists": False,
            "coverage_exists": False,
            "ttl_days": ttl_days,
        }

    newest_mtime = max(mtimes)
    age_days = (time.time() - newest_mtime) / (24 * 3600)
    coverage_exists = any(
        path.exists() and path.stat().st_size > 0 for path in coverage_files
    )

    return {
        "age_days": age_days,
        "within_ttl": age_days < ttl_days and coverage_exists,
        "exists": True,
        "coverage_exists": coverage_exists,
        "ttl_days": ttl_days,
    }


def clean_live_jobs(
    config: Dict[str, Any], logger: PipelineLogger, skip: bool = False
) -> None:
    """Fetches and aggregates Live Job offers from France Travail."""
    import pipeline.ingest as ingest

    logger.log_step("clean_live_jobs", "STARTED")

    status = ingest.get_live_jobs_status()
    should_run = not skip

    if should_run:
        if status["within_ttl"]:
            logging.info(
                f"Live Jobs: Data is {status['age_days']:.1f} days old (TTL={status['ttl_days']}). Skipping fetch."
            )
            should_run = False
        elif not status["exists"]:
            logging.info("Live Jobs: No existing data found.")
        else:
            logging.info(
                f"Live Jobs: Data is {status['age_days']:.1f} days old (TTL expired)."
            )

    if should_run:
        logging.info("Live Jobs: Running France Travail ingest...")
        path = run_etl()
        candidate_path = ingest.OUTPUT_DIR / ingest.FT_SHARED_OUTPUT_PATH.name
        if path and _materialize_employment_output_pair(
            Path(path), ingest.FT_COVERAGE_SHARED_OUTPUT_PATH
        ):
            logger.log_source(
                "france_travail_live",
                "REFRESHED",
                candidate_path,
                observed_at=_artifact_observed_at(Path(path)),
            )
            logger.log_step(
                "clean_live_jobs", "COMPLETED", {"path": str(candidate_path)}
            )
        else:
            logger.log_step("clean_live_jobs", "FAILED")
            raise PipelineRunError(
                "France Travail ingest produced no candidate artefact"
            )
    else:
        candidate_path = ingest.OUTPUT_DIR / ingest.FT_SHARED_OUTPUT_PATH.name
        if not skip and _materialize_employment_output_pair(
            ingest.FT_SHARED_OUTPUT_PATH, ingest.FT_COVERAGE_SHARED_OUTPUT_PATH
        ):
            logger.log_source(
                "france_travail_live",
                "CACHED",
                candidate_path,
                observed_at=_artifact_observed_at(ingest.FT_SHARED_OUTPUT_PATH),
            )
            logger.log_step(
                "clean_live_jobs",
                "COMPLETED",
                {"path": str(candidate_path), "cached": True},
            )
        elif skip:
            logger.log_source("france_travail_live", "SKIPPED")
            logger.log_step("clean_live_jobs", "SKIPPED")
        else:
            reason = (
                "coverage evidence is missing"
                if status.get("exists") and not status.get("coverage_exists")
                else "cache is unavailable"
            )
            raise PipelineRunError(
                f"France Travail {reason}; refresh is required before publication"
            )


def get_inclusion_jobs_status() -> Dict[str, Any]:
    """Checks the age of Inclusion Jobs data in cache and deployed data."""
    import pipeline.ingest as ingest

    cache_path = ingest.OUTPUT_DIR / "odis_inclusion_jobs.parquet"
    data_path = Path("data/odis_inclusion_jobs.parquet")

    ttl_days = load_config(ingest.CONFIG_FILE)["local_files"]["inclusion_jobs"][
        "ttl_days"
    ]

    files = [cache_path, ingest.INCLUSION_SHARED_OUTPUT_PATH, data_path]
    coverage_files = [
        ingest.OUTPUT_DIR / ingest.INCLUSION_COVERAGE_SHARED_OUTPUT_PATH.name,
        ingest.INCLUSION_COVERAGE_SHARED_OUTPUT_PATH,
        Path("data") / ingest.INCLUSION_COVERAGE_SHARED_OUTPUT_PATH.name,
    ]
    mtimes = []
    for f in files:
        if f.exists():
            mtimes.append(f.stat().st_mtime)

    if not mtimes:
        return {
            "age_days": None,
            "within_ttl": False,
            "exists": False,
            "coverage_exists": False,
            "ttl_days": ttl_days,
        }

    newest_mtime = max(mtimes)
    age_days = (time.time() - newest_mtime) / (24 * 3600)
    coverage_exists = any(
        path.exists() and path.stat().st_size > 0 for path in coverage_files
    )

    return {
        "age_days": age_days,
        "within_ttl": age_days < ttl_days and coverage_exists,
        "exists": True,
        "coverage_exists": coverage_exists,
        "ttl_days": ttl_days,
    }


def clean_inclusion_jobs(
    config: Dict[str, Any], logger: PipelineLogger, skip: bool = False
) -> None:
    """Fetches job openings from Les emplois de l'inclusion."""
    import pipeline.ingest as ingest

    logger.log_step("clean_inclusion_jobs", "STARTED")

    status = ingest.get_inclusion_jobs_status()
    should_run = not skip

    if should_run:
        if status["within_ttl"]:
            logging.info(
                f"Inclusion Jobs: Data is {status['age_days']:.1f} days old (TTL={status['ttl_days']}). Skipping fetch."
            )
            should_run = False
        elif not status["exists"]:
            logging.info("Inclusion Jobs: No existing data found.")
        else:
            logging.info(
                f"Inclusion Jobs: Data is {status['age_days']:.1f} days old (TTL expired)."
            )

    if should_run:
        logging.info("Inclusion Jobs: Running Les emplois de l'inclusion ingest...")
        run_inclusion_ingest()
        if _materialize_employment_output_pair(
            ingest.INCLUSION_OUTPUT_PATH, ingest.INCLUSION_COVERAGE_SHARED_OUTPUT_PATH
        ):
            candidate_path = ingest.OUTPUT_DIR / ingest.INCLUSION_OUTPUT_PATH.name
            logger.log_source(
                "inclusion_jobs",
                "REFRESHED",
                candidate_path,
                observed_at=_artifact_observed_at(ingest.INCLUSION_OUTPUT_PATH),
            )
            logger.log_step(
                "clean_inclusion_jobs",
                "COMPLETED",
                {"path": str(candidate_path)},
            )
        else:
            logger.log_step("clean_inclusion_jobs", "FAILED")
            raise PipelineRunError(
                "Inclusion Jobs ingest produced no candidate artefact"
            )
    else:
        if not skip and _materialize_employment_output_pair(
            ingest.INCLUSION_OUTPUT_PATH, ingest.INCLUSION_COVERAGE_SHARED_OUTPUT_PATH
        ):
            candidate_path = ingest.OUTPUT_DIR / ingest.INCLUSION_OUTPUT_PATH.name
            logger.log_source(
                "inclusion_jobs",
                "CACHED",
                candidate_path,
                observed_at=_artifact_observed_at(ingest.INCLUSION_OUTPUT_PATH),
            )
            logger.log_step(
                "clean_inclusion_jobs",
                "COMPLETED",
                {"path": str(candidate_path), "cached": True},
            )
        elif skip:
            logger.log_source("inclusion_jobs", "SKIPPED")
            logger.log_step("clean_inclusion_jobs", "SKIPPED")
        else:
            reason = (
                "coverage evidence is missing"
                if status.get("exists") and not status.get("coverage_exists")
                else "cache is unavailable"
            )
            raise PipelineRunError(
                f"Inclusion Jobs {reason}; refresh is required before publication"
            )


def clean_rome(config: Dict[str, Any], logger: PipelineLogger) -> None:
    """Cleans and standardizes the France Travail ROME referential.

    Uses ODAS Silver table ref_rome by default, with fallbacks to France Travail API
    or static ROME json referential when configured.

    Outputs:
        CLEAN_DIR / "rome.parquet" with columns ['code', 'label'].

    Raises:
        PipelineRunError: If referential extraction fails or produces invalid data.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_rome", "STARTED")
    source_cfg = config.get("sources", {}).get("rome", {})
    output_path = context.CLEAN_DIR / "rome.parquet"
    ttl_days = source_cfg.get("ttl_days", 365)

    if source_cfg.get("use_odace", False):
        odace_table = source_cfg.get("odace_table", "ref_rome")
        try:
            client = ingest.get_odace_client(logger)
            df = client.fetch_table(odace_table, ttl_days=ttl_days)
            code_col = next((c for c in ["code_rome", "code"] if c in df.columns), None)
            label_col = next(
                (c for c in ["libelle_rome", "label", "libelle"] if c in df.columns),
                None,
            )

            if not df.empty and code_col and label_col:
                df_out = (
                    pd.DataFrame(
                        {
                            "code": df[code_col].astype(str).str.strip(),
                            "label": df[label_col].astype(str).str.strip(),
                        }
                    )
                    .dropna()
                    .drop_duplicates()
                )
                df_out.to_parquet(output_path)
                logger.log_source("rome", "FETCHED", str(output_path))
                logger.log_step(
                    "clean_rome",
                    "COMPLETED",
                    {"path": str(output_path), "rows": len(df_out), "source": "odace"},
                )
                return
            else:
                raise ValueError(
                    f"Unexpected columns or empty data in ODAS {odace_table}: {df.columns}"
                )
        except Exception as e:
            logging.error(f"❌ [ROME] Failed to fetch referential from ODAS: {e}")
            raise PipelineRunError(
                f"Odace rome did not produce a usable dataset: {e}"
            ) from e

    # Fallback to France Travail API
    try:
        token = ingest.get_ft_token()
        url = "https://api.francetravail.io/partenaire/offresdemploi/v2/referentiel/metiers"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        logging.info("📡 [ROME] Fetching referential from France Travail API...")
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        df = pd.DataFrame(data)
        if "code" in df.columns and "libelle" in df.columns:
            df_out = (
                pd.DataFrame(
                    {
                        "code": df["code"].astype(str).str.strip(),
                        "label": df["libelle"].astype(str).str.strip(),
                    }
                )
                .dropna()
                .drop_duplicates()
            )
            df_out.to_parquet(output_path)
            logger.log_source("rome", "FETCHED", str(output_path))
            logger.log_step(
                "clean_rome",
                "COMPLETED",
                {"path": str(output_path), "rows": len(df_out), "source": "api"},
            )
            return
        else:
            raise ValueError(f"Unexpected API data format: {df.columns}")

    except Exception as e:
        logging.error(
            f"❌ [ROME] Failed to fetch referential from API: {e}. Attempting static fallback..."
        )
        logger.log_source("rome_api_failure", "WARNING", str(e))

        try:
            from pipeline.ingest.fetcher import fetch_source

            rome_cfg = config.get("sources", {}).get("rome")
            if not rome_cfg:
                raise ValueError("No static 'rome' configuration found in sources.yaml")

            static_json_path = fetch_source("rome", rome_cfg, logger)
            if not static_json_path or not static_json_path.exists():
                raise FileNotFoundError(
                    f"Static ROME source not available at {static_json_path}"
                )

            logging.info(
                f"[ROME] Parsing static JSON referential from {static_json_path}..."
            )
            df = pd.read_json(static_json_path)

            if "code" in df.columns and "libelle" in df.columns:
                df_out = (
                    pd.DataFrame(
                        {
                            "code": df["code"].astype(str).str.strip(),
                            "label": df["libelle"].astype(str).str.strip(),
                        }
                    )
                    .dropna()
                    .drop_duplicates()
                )
                df_out.to_parquet(output_path)
                logger.log_source("rome", "FETCHED", str(output_path))
                logger.log_step(
                    "clean_rome",
                    "COMPLETED",
                    {"path": str(output_path), "rows": len(df_out), "source": "static"},
                )
                return
            else:
                raise ValueError(f"Unexpected static JSON format: {df.columns}")
        except Exception as fallback_err:
            logging.error(f"❌ [ROME] Static fallback failed: {fallback_err}")
            logger.log_source("rome", "ERROR", f"API: {e}, Fallback: {fallback_err}")
            raise PipelineRunError(
                f"ROME referential failed via both API ({e}) and static fallback ({fallback_err})"
            ) from fallback_err
