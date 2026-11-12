"""Employment and jobs cleaners (France Travail live offers and Les emplois de l'inclusion)."""

from __future__ import annotations

import logging
from pathlib import Path
import time
from typing import Any, Dict

import pandas as pd
import polars as pl
import requests

from pipeline.common import PipelineLogger, load_config
from pipeline.emplois_inclusion_ingest import run_ingestion as run_inclusion_ingest
from pipeline.employment_coverage import METROPOLITAN_DEPARTMENTS
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
    """Fetches and aggregates Live Job offers from France Travail (ODAS Silver or API fallback).

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.
        skip: If True, skips ingestion and logs a SKIPPED status.

    Raises:
        PipelineRunError: If ODAS fetch or legacy France Travail ingestion fails.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_live_jobs", "STARTED")

    if skip:
        logger.log_source("france_travail_live", "SKIPPED")
        logger.log_step("clean_live_jobs", "SKIPPED")
        return

    source_cfg = config.get("sources", {}).get("france_travail_live") or config.get(
        "local_files", {}
    ).get("france_travail_live", {})

    if source_cfg.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            odace_table = source_cfg.get("odace_table", "fact_offre_emploi")
            ttl_days = source_cfg.get("ttl_days", 7)
            odace_path = (
                client.fetch_table_path(odace_table, ttl_days=ttl_days)
                if hasattr(client, "fetch_table_path")
                else None
            )
            if isinstance(odace_path, Path) and odace_path.exists():
                lf_raw = pl.scan_parquet(odace_path)
            else:
                df_raw = client.fetch_table(odace_table, ttl_days=ttl_days)
                if df_raw.empty:
                    raise ValueError(f"Odace export '{odace_table}' is empty")
                lf_raw = (
                    pl.from_pandas(df_raw).lazy()
                    if isinstance(df_raw, pd.DataFrame)
                    else df_raw.lazy()
                )

            output_dir = ingest.OUTPUT_DIR
            output_dir.mkdir(parents=True, exist_ok=True)
            jobs_target = output_dir / "odis_ft_jobs_agg.parquet"
            coverage_target = output_dir / "odis_ft_jobs_coverage.parquet"

            metro_set = list(METROPOLITAN_DEPARTMENTS)

            # Normalize commune INSEE code first, then filter to metropolitan departments (96 depts)
            lf_filtered = (
                lf_raw.filter(pl.col("commune_insee_code").is_not_null())
                .with_columns(
                    pl.col("commune_insee_code")
                    .cast(pl.String)
                    .str.strip_chars()
                    .str.zfill(5)
                    .alias("commune_clean")
                )
                .filter(pl.col("commune_clean").str.len_chars() == 5)
                .with_columns(
                    pl.when(
                        pl.col("commune_clean").str.starts_with("97")
                        | pl.col("commune_clean").str.starts_with("98")
                    )
                    .then(pl.col("commune_clean").str.slice(0, 3))
                    .otherwise(pl.col("commune_clean").str.slice(0, 2))
                    .alias("dept")
                )
                .filter(pl.col("dept").is_in(metro_set))
            )

            # Load ROME referential for labels
            rome_path = context.CLEAN_DIR / "rome.parquet"
            if not rome_path.exists():
                rome_path = ingest.CLEAN_DIR / "rome.parquet"
            if not rome_path.exists():
                rome_path = context.CACHE_DIR / "odace_ref_rome.parquet"

            if rome_path.exists():
                df_rome = pl.read_parquet(rome_path).lazy()
                schema_names = df_rome.collect_schema().names()
                code_col = next(
                    (c for c in ["code", "code_rome"] if c in schema_names),
                    "code",
                )
                label_col = next(
                    (
                        c
                        for c in ["label", "libelle_rome", "libelle"]
                        if c in schema_names
                    ),
                    "label",
                )
                lf_rome_clean = df_rome.select(
                    pl.col(code_col).cast(pl.String).alias("rome_code_ref"),
                    pl.col(label_col).cast(pl.String).alias("rome_label_ref"),
                )
                lf_joined = lf_filtered.join(
                    lf_rome_clean,
                    left_on="rome_code",
                    right_on="rome_code_ref",
                    how="left",
                )
            else:
                lf_joined = lf_filtered.with_columns(pl.lit("").alias("rome_label_ref"))

            # Aggregate by (commune, romeCode, romeLibelle)
            lf_agg = (
                lf_joined.group_by(["commune_clean", "rome_code", "rome_label_ref"])
                .agg(
                    [
                        pl.len().cast(pl.Int64).alias("nb_offres"),
                        pl.col("postes_nombre")
                        .fill_null(1)
                        .cast(pl.Int64)
                        .sum()
                        .alias("total_postes"),
                        pl.col("is_offre_manque_candidats")
                        .fill_null(False)
                        .cast(pl.Int64)
                        .sum()
                        .alias("nb_offres_tension"),
                    ]
                )
                .with_columns(pl.col("rome_label_ref").fill_null("").cast(pl.String))
                .rename(
                    {
                        "commune_clean": "commune",
                        "rome_code": "romeCode",
                        "rome_label_ref": "romeLibelle",
                    }
                )
                .sort(["commune", "romeCode"])
            )
            df_agg = lf_agg.collect().to_pandas()

            # Coverage calculation per department
            df_dept_counts = (
                lf_filtered.group_by("dept")
                .agg(pl.len().alias("offers_count"))
                .collect()
                .to_pandas()
            )
            dept_counts = dict(
                zip(df_dept_counts["dept"], df_dept_counts["offers_count"])
            )

            cov_rows = [
                {
                    "department": d,
                    "status": "success",
                    "offers_count": int(dept_counts.get(d, 0)),
                    "pages_expected": None,
                    "pages_retrieved": None,
                    "error": None,
                }
                for d in METROPOLITAN_DEPARTMENTS
            ]
            df_coverage = pd.DataFrame(cov_rows)

            df_agg.to_parquet(jobs_target, index=False)
            df_coverage.to_parquet(coverage_target, index=False)

            # Sync shared cache paths if output_dir differs
            shared_dir = ingest.FT_SHARED_OUTPUT_PATH.parent
            if output_dir.resolve() != shared_dir.resolve():
                shared_dir.mkdir(parents=True, exist_ok=True)
                df_agg.to_parquet(ingest.FT_SHARED_OUTPUT_PATH, index=False)
                df_coverage.to_parquet(
                    ingest.FT_COVERAGE_SHARED_OUTPUT_PATH, index=False
                )

            logger.log_source(
                "france_travail_live",
                "REFRESHED",
                jobs_target,
                observed_at=_artifact_observed_at(jobs_target),
            )
            logger.log_step(
                "clean_live_jobs",
                "COMPLETED",
                {
                    "path": str(jobs_target),
                    "rows": len(df_agg),
                    "source": "odace",
                },
            )
            return
        except Exception as e:
            logging.error(f"❌ [France Travail Live] Failed to fetch from ODAS: {e}")
            logger.log_step("clean_live_jobs", "FAILED", {"error": str(e)})
            raise PipelineRunError(
                f"Odace france_travail_live did not produce a usable dataset: {e}"
            ) from e

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
    """Fetches job openings from Les emplois de l'inclusion (ODAS Silver or API fallback).

    Args:
        config: Pipeline configuration dictionary.
        logger: Pipeline logger instance.
        skip: If True, skips ingestion and logs a SKIPPED status.

    Raises:
        PipelineRunError: If ODAS fetch or legacy inclusion jobs ingestion fails.
    """
    import pipeline.ingest as ingest

    logger.log_step("clean_inclusion_jobs", "STARTED")

    if skip:
        logger.log_source("inclusion_jobs", "SKIPPED")
        logger.log_step("clean_inclusion_jobs", "SKIPPED")
        return

    source_cfg = config.get("sources", {}).get("inclusion_jobs") or config.get(
        "local_files", {}
    ).get("inclusion_jobs", {})
    if source_cfg.get("use_odace", False):
        try:
            client = ingest.get_odace_client(logger)
            odace_table = source_cfg.get("odace_table", "fact_siae_poste")
            ttl_days = source_cfg.get("ttl_days", 7)
            df_postes = client.fetch_table(odace_table, ttl_days=ttl_days)
            if df_postes.empty:
                raise ValueError(f"Odace export '{odace_table}' is empty")

            # 1. Fetch auxiliary structures & geo for resolution
            try:
                siae_cfg = config.get("sources", {}).get("siae_structures", {})
                df_siae = client.fetch_table(
                    "dim_siae_structure",
                    ttl_days=siae_cfg.get("ttl_days", 30),
                )
            except Exception as e_siae:
                logging.warning(
                    f"Could not fetch dim_siae_structure from ODAS: {e_siae}"
                )
                df_siae = pd.DataFrame()

            try:
                df_geo = client.fetch_table("dim_geo", ttl_days=30)
            except Exception as e_geo:
                logging.warning(f"Could not fetch dim_geo from ODAS: {e_geo}")
                df_geo = pd.DataFrame()

            sk_to_insee = {}
            if (
                not df_geo.empty
                and "commune_sk" in df_geo.columns
                and "commune_insee_code" in df_geo.columns
            ):
                sk_to_insee = dict(
                    zip(df_geo["commune_sk"], df_geo["commune_insee_code"])
                )

            siret_to_type = {}
            siret_to_name = {}
            if not df_siae.empty and "siae_structure_siret_code" in df_siae.columns:
                if "siae_type_code" in df_siae.columns:
                    siret_to_type = dict(
                        zip(
                            df_siae["siae_structure_siret_code"].astype(str),
                            df_siae["siae_type_code"].astype(str),
                        )
                    )
                label_col = (
                    "siae_structure_label"
                    if "siae_structure_label" in df_siae.columns
                    else "siae_structure_raison_sociale"
                )
                if label_col in df_siae.columns:
                    siret_to_name = dict(
                        zip(
                            df_siae["siae_structure_siret_code"].astype(str),
                            df_siae[label_col].fillna("").astype(str),
                        )
                    )

            # 2. Build df_jobs
            job_id = (
                pd.to_numeric(df_postes["poste_id"], errors="coerce")
                .fillna(0)
                .astype("int64")
            )

            commune_from_sk = (
                df_postes["commune_sk"].map(sk_to_insee)
                if "commune_sk" in df_postes.columns
                else None
            )
            if "lieu_commune_insee_code" in df_postes.columns:
                raw_codgeo = df_postes["lieu_commune_insee_code"].fillna(
                    commune_from_sk
                )
            elif commune_from_sk is not None:
                raw_codgeo = commune_from_sk
            else:
                raw_codgeo = pd.Series(None, index=df_postes.index, dtype="object")

            codgeo = ingest.normalize_code(raw_codgeo, 5).fillna("").astype(str)

            siae_siret = df_postes["structure_siret"].astype(str)
            siae_type = siae_siret.map(siret_to_type).fillna("").astype(str)
            siae_name = siae_siret.map(siret_to_name).fillna("").astype(str)
            rome = (
                df_postes["rome_code"].astype(str)
                if "rome_code" in df_postes.columns
                else df_postes.get("rome", pd.Series("", index=df_postes.index)).astype(
                    str
                )
            )
            postes = (
                pd.to_numeric(df_postes.get("postes_nombre", 1), errors="coerce")
                .fillna(1)
                .astype("int64")
            )

            intitule_poste = (
                df_postes["intitule_poste"].fillna("").astype(str)
                if "intitule_poste" in df_postes.columns
                else pd.Series("", index=df_postes.index)
            )
            description_poste = (
                df_postes["description_poste"].fillna("").astype(str)
                if "description_poste" in df_postes.columns
                else pd.Series("", index=df_postes.index)
            )
            profil_recherche = (
                df_postes["profil_recherche"].fillna("").astype(str)
                if "profil_recherche" in df_postes.columns
                else pd.Series("", index=df_postes.index)
            )
            contrat_type = (
                df_postes["contrat_type"].fillna("").astype(str)
                if "contrat_type" in df_postes.columns
                else pd.Series("", index=df_postes.index)
            )

            df_jobs = pd.DataFrame(
                {
                    "job_id": job_id,
                    "codgeo": codgeo,
                    "siae_siret": siae_siret,
                    "siae_type": siae_type,
                    "siae_name": siae_name,
                    "rome": rome,
                    "postes": postes,
                    "intitule_poste": intitule_poste,
                    "description_poste": description_poste,
                    "profil_recherche": profil_recherche,
                    "contrat_type": contrat_type,
                }
            ).drop_duplicates(subset=["job_id"], keep="first")

            # 3. Mode A sans DOM-TOM: Filter strictly to France métropolitaine
            dept_series = df_jobs["codgeo"].apply(
                lambda c: (
                    c[:3]
                    if c.startswith(("97", "98"))
                    else (
                        "2A"
                        if c.startswith("2A")
                        else ("2B" if c.startswith("2B") else c[:2])
                    )
                )
            )
            is_metro = dept_series.isin(METROPOLITAN_DEPARTMENTS)
            df_jobs = df_jobs[is_metro].copy()
            dept_series = dept_series[is_metro]

            # 4. Build df_structures
            df_struct = df_jobs[
                ["codgeo", "siae_siret", "siae_type", "siae_name"]
            ].drop_duplicates(subset=["codgeo", "siae_siret"])

            # 5. Build coverage across all 96 metropolitan departments
            dept_counts = dept_series.value_counts().to_dict()
            coverage_rows = []
            for d in sorted(list(METROPOLITAN_DEPARTMENTS)):
                coverage_rows.append(
                    {
                        "department": d,
                        "status": "success",
                        "offers_count": int(dept_counts.get(d, 0)),
                        "pages_expected": 1,
                        "pages_retrieved": 1,
                        "error": None,
                    }
                )
            df_coverage = pd.DataFrame(coverage_rows)

            # 6. Save artifacts
            output_dir = context.OUTPUT_DIR
            output_dir.mkdir(parents=True, exist_ok=True)

            jobs_target = output_dir / "odis_inclusion_jobs.parquet"
            struct_target = output_dir / "odis_inclusion_structures.parquet"
            coverage_target = output_dir / "odis_inclusion_jobs_coverage.parquet"

            df_jobs.to_parquet(jobs_target, index=False)
            df_struct.to_parquet(struct_target, index=False)
            df_coverage.to_parquet(coverage_target, index=False)

            # Also sync shared cache paths
            shared_dir = ingest.INCLUSION_OUTPUT_PATH.parent
            if output_dir.resolve() != shared_dir.resolve():
                shared_dir.mkdir(parents=True, exist_ok=True)
                df_jobs.to_parquet(ingest.INCLUSION_OUTPUT_PATH, index=False)
                df_struct.to_parquet(
                    shared_dir / "odis_inclusion_structures.parquet",
                    index=False,
                )
                df_coverage.to_parquet(
                    ingest.INCLUSION_COVERAGE_SHARED_OUTPUT_PATH,
                    index=False,
                )

            logger.log_source(
                "inclusion_jobs",
                "REFRESHED",
                jobs_target,
                observed_at=_artifact_observed_at(jobs_target),
            )
            logger.log_step(
                "clean_inclusion_jobs",
                "COMPLETED",
                {
                    "path": str(jobs_target),
                    "rows": len(df_jobs),
                    "source": "odace",
                },
            )
            return
        except Exception as e:
            logging.error(f"❌ [Inclusion Jobs] Failed to fetch from ODAS: {e}")
            raise PipelineRunError(
                f"Odace inclusion_jobs did not produce a usable dataset: {e}"
            ) from e

    status = ingest.get_inclusion_jobs_status()
    should_run = True

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
