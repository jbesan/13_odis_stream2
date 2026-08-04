import warnings

import numpy as np
import pandas as pd
import pytest

from pipeline.prescoring import (
    aggregate_commune_metrics_by_bv,
    apply_configured_raw_missingness,
    apply_configured_score_missingness,
    get_min_max_quant,
    get_scores_config,
    process_scaling,
    safe_ratio,
    scale_series,
)


def test_raw_missingness_follows_catalog_policy():
    df = pd.DataFrame({"zero_raw": [np.nan, 1.0], "exclude_raw": [np.nan, 1.0]})
    scores_config = {
        "zero_scaled": {
            "source_metric": "zero_raw",
            "missing_strategy": "zero",
        },
        "exclude_scaled": {
            "source_metric": "exclude_raw",
            "missing_strategy": "exclude",
        },
    }

    apply_configured_raw_missingness(df, scores_config)

    assert df["zero_raw"].tolist() == [0.0, 1.0]
    assert pd.isna(df.loc[0, "exclude_raw"])


def test_score_missingness_follows_catalog_policy():
    df = pd.DataFrame({"zero_scaled": [np.nan, 1.0], "exclude_scaled": [np.nan, 1.0]})
    scores_config = {
        "zero_scaled": {"missing_strategy": "zero", "computation": "precomputed"},
        "missing_zero_scaled": {
            "missing_strategy": "zero",
            "computation": "precomputed",
        },
        "exclude_scaled": {
            "missing_strategy": "exclude",
            "computation": "precomputed",
        },
        "live_zero_scaled": {"missing_strategy": "zero", "computation": "live"},
    }

    apply_configured_score_missingness(df, scores_config)

    assert df["zero_scaled"].tolist() == [0.0, 1.0]
    assert (df["missing_zero_scaled"] == 0.0).all()
    assert "live_zero_scaled" not in df.columns
    assert pd.isna(df.loc[0, "exclude_scaled"])


def test_safe_ratio_preserves_unavailable_observations():
    result = safe_ratio(
        pd.Series([4.0, np.nan, 2.0, 3.0]),
        pd.Series([2.0, 2.0, 0.0, np.nan]),
    )

    assert result.iloc[0] == 2.0
    assert result.iloc[1:].isna().all()


def test_scale_series_zero_variance():
    """Verify that zero variance returns a NaN series safeguard."""
    s = pd.Series([0.0, 0.0, 0.0, 0.0])
    scaled = scale_series(s, min_b=0.0, max_b=0.0, col_name="test_col")
    assert scaled.isna().all(), "Zero variance scaling should default to NaN"


def test_get_min_max_quant_ignores_nans():
    """Verify that get_min_max_quant ignores NaNs and calculates quantiles on valid values."""
    data = [np.nan] * 98 + [50.0, 100.0]
    s = pd.Series(data)
    min_b, max_b = get_min_max_quant(s, q=0.05)
    assert min_b == 52.5
    assert max_b == 97.5


def test_process_scaling_sparse_metric_with_nans():
    """Verify process_scaling on a sparse metric with NaNs (like CAF data)."""
    # 98 NaNs and 2 valid values (40.0 and 80.0)
    df = pd.DataFrame({"edu_pe_tx_couverture": [np.nan] * 98 + [40.0, 80.0]})
    process_scaling(df, "edu_pe_tx_couverture", "edu_petite_enfance_scaled")
    assert "edu_petite_enfance_scaled" in df.columns
    # NaNs should remain NaN so scoring excludes them from denominator
    assert df["edu_petite_enfance_scaled"].isna().sum() == 98
    assert df["edu_petite_enfance_scaled"].iloc[-1] == 1.0
    assert df["edu_petite_enfance_scaled"].iloc[-2] == 0.0


def test_jaccueille_scores_are_calculated_live_from_published_salesforce_data():
    scores_config = get_scores_config()

    assert scores_config["heb_jaccueille_accueillants_score"]["computation"] == "live"
    assert scores_config["heb_jaccueille_prospects_score"]["computation"] == "live"


def test_bv_aggregation_is_weighted_without_fragmenting_intermediate_columns():
    communes = pd.DataFrame(
        {
            "bassin_de_vie": ["A", "A", "B"],
            "population": [100.0, 50.0, 200.0],
            "score_a": [1.0, np.nan, 0.5],
            "score_b": [0.0, 1.0, np.nan],
            "raw_count": [2.0, 3.0, 5.0],
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", pd.errors.PerformanceWarning)
        result = aggregate_commune_metrics_by_bv(
            communes, ["score_a", "score_b"], ["raw_count"]
        )

    assert result.loc["A", "score_a"] == 1.0
    assert result.loc["A", "score_b"] == pytest.approx(1 / 3)
    assert result.loc["B", "score_a"] == 0.5
    assert pd.isna(result.loc["B", "score_b"])
    assert result.loc["A", "raw_count"] == 5.0
    assert not any(column.endswith(("_w", "_population")) for column in communes)


def test_scale_series_preserves_nan():
    """Verify scale_series preserves NaN values and clips valid values."""
    s = pd.Series([10.0, np.nan, 20.0, 30.0])
    scaled = scale_series(s, min_b=10.0, max_b=30.0, inverted=False)
    assert pd.isna(scaled[1])
    assert scaled[0] == 0.0
    assert scaled[2] == 0.5
    assert scaled[3] == 1.0

    # Inverted scaling should also preserve NaN
    scaled_inv = scale_series(s, min_b=10.0, max_b=30.0, inverted=True)
    assert pd.isna(scaled_inv[1])
    assert scaled_inv[0] == 1.0
    assert scaled_inv[2] == 0.5
    assert scaled_inv[3] == 0.0
