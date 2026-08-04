"""Versioned, non-mutating release quality contracts.

The gate validates a candidate only.  It never restores, overwrites, or marks
an artefact as deployable: ``pipeline.etl`` owns those state transitions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from pipeline.employment_coverage import METROPOLITAN_DEPARTMENTS


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONTRACT_PATH = Path(__file__).with_name("data_contracts.yaml")
DEFAULT_SCORES_CONFIG_PATH = ROOT_DIR / "app" / "scores_config.yaml"


class QualityGateFailureError(RuntimeError):
    """Raised when a candidate release violates one or more data contracts."""

    def __init__(self, message: str, *, summary: dict[str, Any] | None = None):
        super().__init__(message)
        self.summary = summary


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Contract file must contain a mapping: {path}")
    return payload


def _add_check(
    checks: list[dict[str, Any]], name: str, passed: bool, **details: Any
) -> None:
    checks.append({"name": name, "status": "PASSED" if passed else "FAILED", **details})


def _score_definitions(scores_config_path: Path) -> list[dict[str, Any]]:
    scores = _load_yaml(scores_config_path).get("scores", [])
    if not isinstance(scores, list):
        raise ValueError("scores_config.yaml must define a 'scores' list")
    return [score for score in scores if isinstance(score, dict)]


def run_quality_gate(
    communes_path: Path | None = None,
    *,
    output_dir: Path | None = None,
    contracts_path: Path = DEFAULT_CONTRACT_PATH,
    scores_config_path: Path = DEFAULT_SCORES_CONFIG_PATH,
    check_release_artifacts: bool = False,
    baseline_rows: int | None = None,
    max_row_variance_pct: float = 0.10,
) -> dict[str, Any]:
    """Validate a candidate against versioned output and score contracts.

    ``check_release_artifacts`` is enabled by the ETL once build has produced
    all release files. Unit tests and standalone validation may scope the gate
    to a single communes dataset.
    """
    contracts = _load_yaml(Path(contracts_path))
    communes_contract = contracts["communes"]
    communes_path = Path(
        communes_path or Path(output_dir or "") / "odis_communes.parquet"
    )
    output_dir = Path(output_dir or communes_path.parent)
    checks: list[dict[str, Any]] = []

    if not communes_path.exists():
        raise QualityGateFailureError(f"Missing communes artefact: {communes_path}")
    communes = pd.read_parquet(communes_path, engine="fastparquet")
    rows = len(communes)

    min_rows = int(communes_contract["min_rows"])
    _add_check(
        checks, "communes.minimum_rows", rows >= min_rows, actual=rows, minimum=min_rows
    )

    required_columns = communes_contract["required_columns"]
    missing_columns = [
        column for column in required_columns if column not in communes.columns
    ]
    _add_check(
        checks,
        "communes.required_columns",
        not missing_columns,
        missing=missing_columns,
    )

    primary_key = communes_contract["primary_key"]
    if primary_key in communes.columns:
        nulls = int(communes[primary_key].isna().sum())
        duplicates = int(communes[primary_key].duplicated().sum())
        _add_check(
            checks,
            "communes.primary_key",
            nulls == 0 and duplicates == 0,
            nulls=nulls,
            duplicates=duplicates,
        )

    for column, threshold in communes_contract.get("geography", {}).items():
        if column not in communes.columns:
            continue
        coverage = float(communes[column].notna().mean()) if rows else 0.0
        _add_check(
            checks,
            f"communes.geography.{column}",
            coverage >= float(threshold),
            coverage=coverage,
            minimum=float(threshold),
        )

    if baseline_rows:
        delta = abs(rows - baseline_rows) / baseline_rows
        _add_check(
            checks,
            "communes.row_variance",
            delta <= max_row_variance_pct,
            baseline=baseline_rows,
            variance=delta,
            maximum=max_row_variance_pct,
        )

    min_metric_coverage = float(communes_contract["scoring"]["min_non_null_fraction"])
    score_metrics: list[dict[str, Any]] = []
    for score in _score_definitions(Path(scores_config_path)):
        score_id = score.get("id")
        if not score_id or score.get("computation", "precomputed") == "live":
            continue
        if score_id not in communes.columns:
            _add_check(checks, f"score.{score_id}", False, reason="missing column")
            continue
        valid = communes[score_id].dropna()
        coverage = len(valid) / rows if rows else 0.0
        has_variance = valid.nunique() > 1
        passed = coverage >= min_metric_coverage and has_variance
        _add_check(
            checks,
            f"score.{score_id}",
            passed,
            coverage=coverage,
            minimum_coverage=min_metric_coverage,
            distinct_values=int(valid.nunique()),
        )
        score_metrics.append({"id": score_id, "coverage": coverage})

    if check_release_artifacts:
        for artifact in contracts["release"]["required_artifacts"]:
            path = output_dir / artifact
            _add_check(
                checks,
                f"release.{artifact}",
                path.exists() and path.stat().st_size > 0 if path.exists() else False,
                path=str(path),
            )

        for join in contracts.get("joins", []):
            source = join["source_column"]
            target_path = output_dir / join["target_artifact"]
            if source not in communes.columns or not target_path.exists():
                _add_check(
                    checks, f"join.{source}", False, reason="missing source or target"
                )
                continue
            target = pd.read_parquet(target_path, engine="fastparquet")
            target_key = join["target_key"]
            if target_key not in target.columns:
                _add_check(checks, f"join.{source}", False, reason="missing target key")
                continue
            populated = communes[source].dropna().astype(str)
            orphan_fraction = (
                float((~populated.isin(target[target_key].dropna().astype(str))).mean())
                if len(populated)
                else 0.0
            )
            maximum = float(join["max_orphan_fraction"])
            _add_check(
                checks,
                f"join.{source}",
                orphan_fraction <= maximum,
                orphan_fraction=orphan_fraction,
                maximum=maximum,
            )

        for coverage_name in (
            "odis_ft_jobs_coverage.parquet",
            "odis_inclusion_jobs_coverage.parquet",
        ):
            if coverage_name not in contracts["release"]["required_artifacts"]:
                continue
            coverage_path = output_dir / coverage_name
            check_name = f"coverage.{coverage_name}"
            if not coverage_path.exists():
                _add_check(checks, check_name, False, reason="missing artifact")
                continue
            coverage = pd.read_parquet(coverage_path, engine="fastparquet")
            if not {"department", "status", "pages_expected", "pages_retrieved"}.issubset(
                coverage.columns
            ):
                _add_check(checks, check_name, False, reason="missing coverage columns")
                continue
            successful = coverage.loc[coverage["status"] == "success"]
            complete_departments = set(successful["department"].astype(str))
            pages_complete = bool(
                (successful["pages_retrieved"] >= successful["pages_expected"]).all()
            )
            nonempty_departments = (
                bool((successful["offers_count"] > 0).all())
                if coverage_name == "odis_ft_jobs_coverage.parquet"
                and "offers_count" in successful.columns
                else True
            )
            _add_check(
                checks,
                check_name,
                complete_departments == set(METROPOLITAN_DEPARTMENTS)
                and pages_complete
                and nonempty_departments,
                completed_departments=len(complete_departments),
                expected_departments=len(METROPOLITAN_DEPARTMENTS),
                pages_complete=pages_complete,
                nonempty_departments=nonempty_departments,
            )

    failures = [check for check in checks if check["status"] == "FAILED"]
    summary = {
        "status": "PASSED" if not failures else "FAILED",
        "contract_version": contracts["version"],
        "communes": {"rows": rows, "columns": len(communes.columns)},
        "score_metrics": score_metrics,
        "checks": checks,
    }
    if failures:
        names = ", ".join(check["name"] for check in failures)
        raise QualityGateFailureError(f"Quality gate failed: {names}", summary=summary)
    return summary
