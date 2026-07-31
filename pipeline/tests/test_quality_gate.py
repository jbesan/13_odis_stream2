"""Tests for candidate-only quality contracts."""

import numpy as np
import pandas as pd
import pytest
import yaml

from pipeline.prescoring import scale_series
from pipeline.quality_gate import QualityGateFailureError, run_quality_gate


def _write_contracts(tmp_path):
    contracts = {
        "version": 1,
        "release": {
            "required_artifacts": [
                "odis_communes.parquet",
                "odis_bassins_de_vie.parquet",
            ]
        },
        "communes": {
            "primary_key": "codgeo",
            "min_rows": 3,
            "required_columns": [
                "codgeo",
                "population",
                "dep_code",
                "reg_code",
                "epci_code",
                "bassin_de_vie",
            ],
            "geography": {
                "dep_code": 0.9,
                "reg_code": 0.9,
                "epci_code": 0.9,
                "bassin_de_vie": 0.9,
            },
            "scoring": {"min_non_null_fraction": 0.5},
        },
        "joins": [
            {
                "source_column": "bassin_de_vie",
                "target_artifact": "odis_bassins_de_vie.parquet",
                "target_key": "bassin_de_vie",
                "max_orphan_fraction": 0.0,
            }
        ],
    }
    scores = {"scores": [{"id": "metric_scaled", "computation": "precomputed"}]}
    contracts_path = tmp_path / "contracts.yaml"
    scores_path = tmp_path / "scores.yaml"
    contracts_path.write_text(yaml.safe_dump(contracts), encoding="utf-8")
    scores_path.write_text(yaml.safe_dump(scores), encoding="utf-8")
    return contracts_path, scores_path


def _valid_communes():
    return pd.DataFrame(
        {
            "codgeo": ["00001", "00002", "00003"],
            "population": [100, 200, 300],
            "dep_code": ["01", "01", "01"],
            "reg_code": ["84", "84", "84"],
            "epci_code": ["200000001", "200000001", "200000001"],
            "bassin_de_vie": ["BV1", "BV1", "BV2"],
            "metric_scaled": [0.1, 0.5, 0.9],
        }
    )


def test_scale_series_zero_variance_returns_nan():
    assert scale_series(pd.Series([0.0, 0.0]), 0.0, 0.0, True, "zero").isna().all()


def test_quality_gate_derives_score_checks_and_validates_join(tmp_path):
    contracts_path, scores_path = _write_contracts(tmp_path)
    _valid_communes().to_parquet(tmp_path / "odis_communes.parquet", index=False)
    pd.DataFrame({"bassin_de_vie": ["BV1", "BV2"]}).to_parquet(
        tmp_path / "odis_bassins_de_vie.parquet", index=False
    )

    summary = run_quality_gate(
        output_dir=tmp_path,
        contracts_path=contracts_path,
        scores_config_path=scores_path,
        check_release_artifacts=True,
    )

    assert summary["status"] == "PASSED"
    assert any(check["name"] == "score.metric_scaled" for check in summary["checks"])


def test_quality_gate_rejects_missing_or_constant_score_metric(tmp_path):
    contracts_path, scores_path = _write_contracts(tmp_path)
    df = _valid_communes()
    df["metric_scaled"] = 1.0
    df.to_parquet(tmp_path / "odis_communes.parquet", index=False)

    with pytest.raises(QualityGateFailureError, match="score.metric_scaled"):
        run_quality_gate(
            output_dir=tmp_path,
            contracts_path=contracts_path,
            scores_config_path=scores_path,
        )


def test_quality_gate_rejects_geographic_orphans(tmp_path):
    contracts_path, scores_path = _write_contracts(tmp_path)
    df = _valid_communes()
    df.loc[2, "bassin_de_vie"] = "MISSING"
    df.to_parquet(tmp_path / "odis_communes.parquet", index=False)
    pd.DataFrame({"bassin_de_vie": ["BV1"]}).to_parquet(
        tmp_path / "odis_bassins_de_vie.parquet", index=False
    )

    with pytest.raises(QualityGateFailureError, match="join.bassin_de_vie"):
        run_quality_gate(
            output_dir=tmp_path,
            contracts_path=contracts_path,
            scores_config_path=scores_path,
            check_release_artifacts=True,
        )


def test_quality_gate_never_replaces_failed_candidate(tmp_path):
    contracts_path, scores_path = _write_contracts(tmp_path)
    candidate = _valid_communes()
    candidate["metric_scaled"] = np.nan
    path = tmp_path / "odis_communes.parquet"
    candidate.to_parquet(path, index=False)

    with pytest.raises(QualityGateFailureError):
        run_quality_gate(
            output_dir=tmp_path,
            contracts_path=contracts_path,
            scores_config_path=scores_path,
        )
    assert pd.read_parquet(path).equals(candidate)
