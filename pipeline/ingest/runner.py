"""Blue-Green safe execution harness and muted logger for ingest pipeline cleaners."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import pandas as pd

from pipeline.common import PipelineLogger, validate_dataset_contract
from pipeline.ingest.config import get_domain_contracts
from pipeline.ingest import context
from pipeline.run_context import PipelineRunError


class MutedPipelineLogger:
    """Wrapper to prevent duplicate log_step calls from inside individual clean_* cleaners."""

    def __init__(self, real_logger: PipelineLogger):
        self.real_logger = real_logger
        self.status = getattr(real_logger, "status", None)
        self.errors: list[tuple[str, Optional[Dict[str, Any]]]] = []

    def log_step(
        self, step_name: str, status: str, details: Optional[Dict[str, Any]] = None
    ) -> None:
        if status in {"ERROR", "FAILED"}:
            self.errors.append((step_name, details))

    def log_source(
        self,
        source_name: str,
        status: str,
        file_path: Optional[str] = None,
        *,
        observed_at: Optional[str] = None,
    ) -> None:
        self.real_logger.log_source(
            source_name,
            status,
            file_path,
            observed_at=observed_at,
        )


def run_clean_step_safely(
    step_name: str,
    clean_func: Callable[..., Any],
    config: Dict[str, Any],
    logger: PipelineLogger,
    *args: Any,
    **kwargs: Any,
) -> None:
    """Executes a step cleaning function under the Blue-Green staging-and-restore pattern.

    Checks if any staging file exists (e.g. staging_{local_name} or staging_{archive_file}).
    If they exist:
      1. Validates the RAW staging dataset against config-defined schema contracts.
      2. Backs up active raw, extracted, and clean files (renaming them to .active_bak).
      3. Renames staging files to their active paths.
      4. Executes the original clean function.
      5. Validates that the resulting clean parquet file is non-empty.
      6. Commits (deletes backups) on success, or rolls back (restores backups) on failure.
    """
    import pipeline.ingest as ingest

    if step_name == "education":
        logger.log_step(f"clean_{step_name}", "SKIPPED_OPTIONAL")
        return

    domain_cfg = get_domain_contracts()
    step_to_source_map = domain_cfg.runner.step_to_source_map
    clean_filenames = domain_cfg.runner.clean_filenames

    config_key = step_to_source_map.get(step_name, step_name)
    source_cfg = config.get("sources", {}).get(config_key) or config.get(
        "local_files", {}
    ).get(config_key)

    if not source_cfg:
        logger.log_step(f"clean_{step_name}", "STARTED")
        try:
            clean_func(config, logger, *args, **kwargs)
            logger.log_step(f"clean_{step_name}", "COMPLETED")
        except Exception as e:
            logger.log_step(f"clean_{step_name}", "ERROR", {"error": str(e)})
            logging.exception(f"Error running step clean_{step_name}")
            raise e
        return

    logger.log_step(f"clean_{step_name}", "STARTED")
    muted_logger = MutedPipelineLogger(logger)

    local_name = source_cfg.get("local_name")
    path_str = source_cfg.get("path")

    if path_str:
        active_raw = Path(path_str)
        staging_raw = active_raw.parent / f"staging_{active_raw.name}"
    elif local_name:
        active_raw = context.CACHE_DIR / local_name
        staging_raw = context.CACHE_DIR / f"staging_{local_name}"
    else:
        active_raw = None
        staging_raw = None

    archive_file = source_cfg.get("archive_file")
    if archive_file:
        active_ext = context.CACHE_DIR / archive_file
        staging_ext = context.CACHE_DIR / f"staging_{archive_file}"
    else:
        active_ext = None
        staging_ext = None

    clean_filename = clean_filenames.get(step_name, f"{step_name}.parquet")
    output_dir_steps = set(domain_cfg.runner.output_dir_steps)
    if step_name in output_dir_steps:
        active_clean = context.OUTPUT_DIR / clean_filename
    else:
        active_clean = context.CLEAN_DIR / clean_filename

    is_staging = False
    if staging_raw and staging_raw.exists():
        is_staging = True
    if staging_ext and staging_ext.exists():
        is_staging = True

    if not is_staging:
        try:
            clean_func(config, muted_logger, *args, **kwargs)
            if muted_logger.errors:
                raise PipelineRunError(
                    f"Clean step '{step_name}' reported failure: {muted_logger.errors}"
                )

            if not active_clean.exists():
                raise FileNotFoundError(
                    f"Clean step did not generate the clean parquet file: {active_clean}"
                )
            df_clean = pd.read_parquet(active_clean)
            if len(df_clean) == 0:
                raise ValueError(f"Clean step '{step_name}' produced an empty dataset")
            details = {"rows": len(df_clean), "path": str(active_clean)}

            logger.log_step(f"clean_{step_name}", "COMPLETED", details)
        except Exception as e:
            logger.log_step(f"clean_{step_name}", "ERROR", {"error": str(e)})
            logging.exception(f"Error running step clean_{step_name}")
            raise PipelineRunError(f"Required clean step '{step_name}' failed") from e
        return

    logging.info(
        f"🔄 [Staging Mode] Staging files detected for '{step_name}'. Performing safe dry-run."
    )

    raw_data_path = (
        staging_ext
        if (archive_file and staging_ext and staging_ext.exists())
        else staging_raw
    )
    if raw_data_path and raw_data_path.exists():
        try:
            logging.info(
                f"📋 Validating raw schema contract for '{step_name}' using {raw_data_path.name}"
            )
            df_raw = ingest.load_dataset(raw_data_path, source_cfg)
            if not validate_dataset_contract(df_raw, step_name, source_cfg):
                raise ValueError("Raw schema contract validation failed.")
        except Exception as e:
            logging.error(
                f"❌ [INGEST FAILURE] '{step_name}' raw schema validation failed: {e}"
            )
            logger.log_step(
                f"clean_{step_name}",
                "ERROR",
                {"error": f"Raw validation failed: {str(e)}"},
            )
            if staging_raw:
                staging_raw.unlink(missing_ok=True)
            if staging_ext:
                staging_ext.unlink(missing_ok=True)
            logging.warning(f"⚠️ [ABORTED] Retained existing cache for '{step_name}'.")
            raise PipelineRunError(
                f"Required clean step '{step_name}' failed raw contract validation"
            ) from e

    backups = {}
    moved_staging = []

    try:
        if active_raw and active_raw.exists():
            bak_raw = active_raw.with_name(active_raw.name + ".active_bak")
            if bak_raw.exists():
                os.remove(bak_raw)
            os.replace(active_raw, bak_raw)
            backups[active_raw] = bak_raw

        if active_ext and active_ext.exists():
            bak_ext = active_ext.with_name(active_ext.name + ".active_bak")
            if bak_ext.exists():
                os.remove(bak_ext)
            os.replace(active_ext, bak_ext)
            backups[active_ext] = bak_ext

        if active_clean.exists():
            bak_clean = active_clean.with_name(active_clean.name + ".active_bak")
            if bak_clean.exists():
                os.remove(bak_clean)
            os.replace(active_clean, bak_clean)
            backups[active_clean] = bak_clean

        if staging_raw and staging_raw.exists():
            os.replace(staging_raw, active_raw)
            moved_staging.append((active_raw, staging_raw))

        if staging_ext and staging_ext.exists():
            os.replace(staging_ext, active_ext)
            moved_staging.append((active_ext, staging_ext))

        clean_func(config, muted_logger, *args, **kwargs)
        if muted_logger.errors:
            raise PipelineRunError(
                f"Clean step '{step_name}' reported failure: {muted_logger.errors}"
            )

        if not active_clean.exists():
            raise FileNotFoundError(
                f"Clean step did not generate the clean parquet file: {active_clean}"
            )

        df_clean = pd.read_parquet(active_clean)
        if len(df_clean) == 0:
            raise ValueError("Cleaned output dataset is empty.")

        for bak_path in backups.values():
            bak_path.unlink(missing_ok=True)
        logging.info(
            f"✅ [SUCCESS] Ingested and verified '{step_name}' successfully. Staging committed."
        )
        logger.log_step(
            f"clean_{step_name}",
            "COMPLETED",
            {"rows": len(df_clean), "path": str(active_clean)},
        )

    except Exception as e:
        logging.error(
            f"❌ [INGEST FAILURE] '{step_name}' failed validation/cleaning: {e}"
        )
        logger.log_step(f"clean_{step_name}", "ERROR", {"error": str(e)})

        active_clean.unlink(missing_ok=True)

        for active_path, _ in moved_staging:
            active_path.unlink(missing_ok=True)

        for active_path, bak_path in backups.items():
            if bak_path.exists():
                os.replace(bak_path, active_path)

        logging.warning(
            f"⚠️ [ROLLBACK COMPLETE] Reverted '{step_name}' to last known good cache."
        )
        raise PipelineRunError(f"Required clean step '{step_name}' failed") from e
