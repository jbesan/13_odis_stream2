"""Unit tests for Quality Gate System, Prescoring Scaling Safety, and Fallback Rent Data Handling."""

import json
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

from pipeline.prescoring import scale_series
from pipeline.quality_gate import run_quality_gate, QualityGateFailureError, prompt_user_continuation


def test_scale_series_zero_variance_returns_nan():
    """Test that zero-variance series (min == max) returns NaNs and not 1.0."""
    series = pd.Series([0.0, 0.0, 0.0, 0.0])
    scaled = scale_series(series, min_b=0.0, max_b=0.0, inverted=True, col_name="test_zero_var")
    
    assert scaled.isna().all(), "Zero variance inverted series must return all NaNs, not 1.0"


def test_scale_series_inverted_valid_values():
    """Test normal inverted scaling behavior."""
    series = pd.Series([10.0, 20.0, 30.0])
    scaled = scale_series(series, min_b=10.0, max_b=30.0, inverted=True, col_name="rent")
    
    assert list(scaled) == [1.0, 0.5, 0.0], "Inverted scaling should map min to 1.0 and max to 0.0"


def test_quality_gate_passes_on_valid_dataset(tmp_path):
    """Test quality gate passing on a valid dataset and manifest."""
    communes_file = tmp_path / "odis_communes.parquet"
    status_file = tmp_path / "status.json"

    df = pd.DataFrame({
        "commune_insee_code": [f"{i:05d}" for i in range(35000)],
        "log_loyer_moyen_appt_all_scaled": np.random.uniform(0.1, 0.9, 35000),
        "ter_insecurite_scaled": np.random.uniform(0.1, 0.9, 35000),
        "sante_rdv_delay_scaled": np.random.uniform(0.1, 0.9, 35000),
        "mob_dur_share_scaled": np.random.uniform(0.1, 0.9, 35000),
        "workclass_decline_scaled": np.random.uniform(0.1, 0.9, 35000),
    })
    df.to_parquet(communes_file, index=False, engine="fastparquet")

    status_data = {
        "steps": {
            "output_communes": {
                "status": "CREATED",
                "details": {"rows": 35000}
            }
        }
    }
    with open(status_file, "w") as f:
        json.dump(status_data, f)

    res = run_quality_gate(communes_path=communes_file, status_path=status_file, ask_user_on_failure=False)
    assert res["status"] == "PASSED"
    assert res["rows"] == 35000


def test_quality_gate_fails_on_row_count_variance(tmp_path):
    """Test quality gate failure when row count variance exceeds 10%."""
    communes_file = tmp_path / "odis_communes.parquet"
    status_file = tmp_path / "status.json"

    df = pd.DataFrame({
        "commune_insee_code": [f"{i:05d}" for i in range(30500)],
        "log_loyer_moyen_appt_all_scaled": np.random.uniform(0.1, 0.9, 30500),
        "ter_insecurite_scaled": np.random.uniform(0.1, 0.9, 30500),
        "sante_rdv_delay_scaled": np.random.uniform(0.1, 0.9, 30500),
        "mob_dur_share_scaled": np.random.uniform(0.1, 0.9, 30500),
        "workclass_decline_scaled": np.random.uniform(0.1, 0.9, 30500),
    })
    df.to_parquet(communes_file, index=False, engine="fastparquet")

    status_data = {
        "steps": {
            "output_communes": {
                "status": "CREATED",
                "details": {"rows": 35000}
            }
        }
    }
    with open(status_file, "w") as f:
        json.dump(status_data, f)

    with pytest.raises(QualityGateFailureError, match="Row count variance"):
        run_quality_gate(communes_path=communes_file, status_path=status_file, max_row_variance_pct=0.10, ask_user_on_failure=False)


def test_quality_gate_fails_on_zero_variance_metric(tmp_path):
    """Test quality gate failure when a critical metric has zero variance (constant score)."""
    communes_file = tmp_path / "odis_communes.parquet"
    status_file = tmp_path / "status.json"

    df = pd.DataFrame({
        "commune_insee_code": [f"{i:05d}" for i in range(35000)],
        "log_loyer_moyen_appt_all_scaled": [1.0] * 35000,
        "ter_insecurite_scaled": np.random.uniform(0.1, 0.9, 35000),
        "sante_rdv_delay_scaled": np.random.uniform(0.1, 0.9, 35000),
        "mob_dur_share_scaled": np.random.uniform(0.1, 0.9, 35000),
        "workclass_decline_scaled": np.random.uniform(0.1, 0.9, 35000),
    })
    df.to_parquet(communes_file, index=False, engine="fastparquet")

    with pytest.raises(QualityGateFailureError, match="zero variance"):
        run_quality_gate(communes_path=communes_file, status_path=status_file, ask_user_on_failure=False)


def test_quality_gate_rollback_and_user_continuation(tmp_path):
    """Test that when a dataset fails quality gate, the previous valid version is retained."""
    communes_file = tmp_path / "odis_communes.parquet"
    backup_file = tmp_path / "odis_communes.parquet.bak"
    status_file = tmp_path / "status.json"

    # Create previous valid version and save as backup
    df_valid = pd.DataFrame({
        "commune_insee_code": [f"{i:05d}" for i in range(35000)],
        "log_loyer_moyen_appt_all_scaled": np.random.uniform(0.1, 0.9, 35000),
        "ter_insecurite_scaled": np.random.uniform(0.1, 0.9, 35000),
        "sante_rdv_delay_scaled": np.random.uniform(0.1, 0.9, 35000),
        "mob_dur_share_scaled": np.random.uniform(0.1, 0.9, 35000),
        "workclass_decline_scaled": np.random.uniform(0.1, 0.9, 35000),
    })
    df_valid.to_parquet(backup_file, index=False, engine="fastparquet")

    # Create broken dataset (zero variance defect)
    df_broken = pd.DataFrame({
        "commune_insee_code": [f"{i:05d}" for i in range(35000)],
        "log_loyer_moyen_appt_all_scaled": [1.0] * 35000,
        "ter_insecurite_scaled": np.random.uniform(0.1, 0.9, 35000),
        "sante_rdv_delay_scaled": np.random.uniform(0.1, 0.9, 35000),
        "mob_dur_share_scaled": np.random.uniform(0.1, 0.9, 35000),
        "workclass_decline_scaled": np.random.uniform(0.1, 0.9, 35000),
    })
    df_broken.to_parquet(communes_file, index=False, engine="fastparquet")

    # Halt execution when user declines continuation
    with pytest.raises(QualityGateFailureError):
        run_quality_gate(communes_path=communes_file, status_path=status_file, ask_user_on_failure=False)

    # Verify that communes_file was restored to the valid backup dataset
    restored_df = pd.read_parquet(communes_file, engine="fastparquet")
    assert restored_df["log_loyer_moyen_appt_all_scaled"].nunique() > 1, "Rollback must restore valid non-constant dataset"
