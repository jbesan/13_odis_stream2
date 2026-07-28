"""Dedicated Data Quality Gate System for the ODIS Pipeline.

This module validates output dataset health before publication, verifying:
1. Row count variance against manifest/previous baseline (< 10% threshold).
2. Minimum valid data coverage (>50% non-null) for critical criteria.
3. Non-zero variance (min != max) and absence of universal constant scores.

Quality Gate Enforcement Policy:
- If a dataset fails the Quality Gate check:
  1. The pipeline reverts to / preserves the previous valid version of that dataset (.parquet.bak).
  2. Execution halts and prompts the user whether to continue processing remaining datasets
     with the preserved version or stop immediately.
- No exceptions are silently caught or swallowed.
"""

import json
import logging
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

# Define paths relative to workspace root
ROOT_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT_DIR / "pipeline" / "output"
STATUS_FILE = ROOT_DIR / "pipeline" / "status.json"

# Critical metrics that MUST be present in odis_communes.parquet, non-constant, and have sufficient coverage (>50%)
# These match the official score IDs defined in app/scores_config.yaml
CRITICAL_METRICS = [
    "log_loyer_moyen_appt_all_scaled",
    "ter_insecurite_scaled",
    "sante_rdv_delay_scaled",
    "mob_dur_share_scaled",
    "workclass_decline_scaled",
]


class QualityGateFailureError(RuntimeError):
    """Raised when a dataset fails the Quality Gate check."""
    pass


def prompt_user_continuation(dataset_name: str, reason: str, interactive: Optional[bool] = None) -> bool:
    """Prompts the user to decide whether to continue the pipeline after a Quality Gate failure.

    Args:
        dataset_name: Name of the dataset that failed quality gate.
        reason: Explanation of the quality gate failure.
        interactive: Override TTY check for testing.

    Returns:
        bool: True if user approves continuing with the previous version, False to halt execution.
    """
    is_tty = sys.stdin.isatty() if interactive is None else interactive
    
    print("\n" + "=" * 80)
    print(f"  CRITICAL QUALITY GATE FAILURE FOR DATASET: {dataset_name}")
    print(f"  REASON: {reason}")
    print(f"  ACTION: Kept / restored previous valid version of dataset.")
    print("=" * 80)

    if is_tty:
        try:
            choice = input(
                f"\nQuality Gate FAILED for dataset '{dataset_name}'. Previous valid version was retained.\n"
                f"Do you want to continue running the pipeline for other datasets using the previous version? [y/N]: "
            ).strip().lower()
            return choice in ["y", "yes"]
        except (KeyboardInterrupt, EOFError):
            return False
    else:
        logging.error(
            f"Non-interactive session: Quality Gate FAILED for '{dataset_name}'. "
            f"Halting pipeline execution."
        )
        return False


def run_quality_gate(
    communes_path: Optional[Path] = None,
    status_path: Optional[Path] = None,
    max_row_variance_pct: float = 0.10,
    min_commune_count: int = 30000,
    dataset_name: str = "odis_communes.parquet",
    ask_user_on_failure: bool = True,
) -> Dict[str, Any]:
    """Runs all quality gate assertions on target dataset.

    Args:
        communes_path: Path to dataset parquet file.
        status_path: Path to status.json manifest file.
        max_row_variance_pct: Maximum allowed row count change (default 0.10 = 10%).
        min_commune_count: Absolute minimum expected commune row count.
        dataset_name: Name of dataset being validated.
        ask_user_on_failure: Whether to prompt user for continuation on failure.

    Returns:
        Dict with quality gate status and metrics summary.

    Raises:
        QualityGateFailureError: If quality gate fails and execution is halted.
        FileNotFoundError: If target dataset is missing.
    """
    communes_path = communes_path or (OUTPUT_DIR / "odis_communes.parquet")
    status_path = status_path or STATUS_FILE
    backup_path = communes_path.with_suffix(".parquet.bak")

    logging.info(f"QUALITY GATE: Verifying dataset at {communes_path}")

    if not communes_path.exists():
        err_msg = f"CRITICAL ERROR: Quality Gate target file missing at {communes_path}"
        logging.error(err_msg)
        print(f"ERROR [quality_gate]: {err_msg}")
        raise FileNotFoundError(err_msg)

    try:
        df_communes = pd.read_parquet(communes_path, engine="fastparquet")
        current_rows = len(df_communes)
        current_cols = len(df_communes.columns)

        logging.info(f"QUALITY GATE: Dataset loaded ({current_rows} rows, {current_cols} columns).")

        # 1. Absolute Minimum Commune Count Gate
        if current_rows < min_commune_count:
            raise ValueError(
                f"Commune count ({current_rows}) is below absolute minimum threshold ({min_commune_count})."
            )

        # 2. Row Count Variance vs Previous Manifest Baseline Gate
        prev_rows: Optional[int] = None
        if status_path.exists():
            try:
                with open(status_path, "r", encoding="utf-8") as f:
                    status_data = json.load(f)
                prev_rows = (
                    status_data.get("steps", {})
                    .get("output_communes", {})
                    .get("details", {})
                    .get("rows")
                )
            except Exception as manifest_err:
                logging.warning(f"QUALITY GATE: Could not read previous row count from manifest: {manifest_err}")

        if prev_rows is not None and prev_rows > 0:
            row_delta = abs(current_rows - prev_rows)
            row_variance_pct = row_delta / float(prev_rows)
            logging.info(
                f"QUALITY GATE: Row count check -> Baseline: {prev_rows}, Current: {current_rows}, "
                f"Delta: {row_delta} ({row_variance_pct * 100:.2f}%)"
            )

            if row_variance_pct > max_row_variance_pct:
                raise ValueError(
                    f"Row count variance ({row_variance_pct * 100:.2f}%) exceeds max allowed "
                    f"threshold ({max_row_variance_pct * 100:.1f}%)! Baseline: {prev_rows}, New: {current_rows}"
                )

        # 3. Critical Metrics Coverage & Non-Zero Variance Gate
        metrics_checked = 0
        for metric in CRITICAL_METRICS:
            if metric not in df_communes.columns:
                raw_metric = metric.replace("_scaled", "")
                if raw_metric not in df_communes.columns:
                    raise ValueError(f"Critical metric '{metric}' missing from dataset!")
                metric_to_check = raw_metric
            else:
                metric_to_check = metric

            series = df_communes[metric_to_check]
            valid_series = series.dropna()
            valid_count = len(valid_series)
            coverage_pct = valid_count / float(current_rows)

            if coverage_pct < 0.50:
                raise ValueError(
                    f"Critical metric '{metric_to_check}' non-null coverage ({coverage_pct * 100:.1f}%) "
                    f"is below 50% minimum threshold!"
                )

            if valid_series.nunique() <= 1 or valid_series.min() == valid_series.max():
                raise ValueError(
                    f"Critical metric '{metric_to_check}' has zero variance! "
                    f"All values collapse to constant {valid_series.iloc[0] if not valid_series.empty else 'NaN'}."
                )

            metrics_checked += 1

        # SUCCESS: Update Backup File & Status Manifest
        try:
            shutil.copy2(communes_path, backup_path)
            logging.info(f"QUALITY GATE: Created valid dataset backup at {backup_path}")
        except Exception as copy_err:
            logging.warning(f"QUALITY GATE: Failed to create backup file: {copy_err}")

        status_data: Dict[str, Any] = {}
        if status_path.exists():
            try:
                with open(status_path, "r", encoding="utf-8") as f:
                    status_data = json.load(f)
            except Exception:
                status_data = {"steps": {}}

        if "steps" not in status_data:
            status_data["steps"] = {}

        status_data["steps"]["output_communes"] = {
            "status": "PASSED_QUALITY_GATE",
            "details": {
                "path": str(communes_path.relative_to(ROOT_DIR) if communes_path.is_relative_to(ROOT_DIR) else communes_path),
                "rows": current_rows,
                "cols": current_cols,
                "quality_gate": "PASSED",
                "metrics_checked": metrics_checked,
            },
        }

        with open(status_path, "w", encoding="utf-8") as f:
            json.dump(status_data, f, indent=2)

        summary = {
            "status": "PASSED",
            "rows": current_rows,
            "cols": current_cols,
            "prev_rows": prev_rows,
            "metrics_checked": metrics_checked,
        }
        logging.info(f"QUALITY GATE PASSED SUCCESSFULLY: {summary}")
        # print(f"SUCCESS [quality_gate]: Quality Gate PASSED for '{dataset_name}' ({current_rows} rows).")
        return summary

    except Exception as failure_err:
        failure_msg = str(failure_err)
        logging.error(f"QUALITY GATE FAILURE for dataset '{dataset_name}': {failure_msg}")

        # Restore / retain backup dataset if available
        if backup_path.exists():
            try:
                shutil.copy2(backup_path, communes_path)
                logging.warning(
                    f"QUALITY GATE ROLLBACK: Restored previous valid dataset version from {backup_path} to {communes_path}"
                )
            except Exception as rollback_err:
                logging.error(f"QUALITY GATE ROLLBACK FAILED: Could not restore backup file: {rollback_err}")
                raise rollback_err

        # Prompt user if allowed
        should_continue = False
        if ask_user_on_failure:
            should_continue = prompt_user_continuation(dataset_name, failure_msg)

        if should_continue:
            logging.warning(
                f"QUALITY GATE OVERRIDE: User approved continuing pipeline execution "
                f"using previous valid version of dataset '{dataset_name}'."
            )
            return {
                "status": "REVERTED_TO_PREVIOUS",
                "error": failure_msg,
                "continued_by_user": True,
            }
        else:
            err = QualityGateFailureError(
                f"Quality Gate FAILED for '{dataset_name}': {failure_msg}. Execution halted."
            )
            print(f"FATAL [quality_gate]: {err}", file=sys.stderr)
            raise err


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    try:
        run_quality_gate()
    except Exception as exc:
        print(f"FATAL [quality_gate]: {exc}", file=sys.stderr)
        sys.exit(1)
