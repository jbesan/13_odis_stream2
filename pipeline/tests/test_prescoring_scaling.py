import pandas as pd
import numpy as np
import pytest
from pipeline.prescoring import scale_series, get_min_max_quant, process_scaling

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
    df = pd.DataFrame({
        "edu_pe_tx_couverture": [np.nan] * 98 + [40.0, 80.0]
    })
    process_scaling(df, "edu_pe_tx_couverture", "edu_petite_enfance_scaled")
    assert "edu_petite_enfance_scaled" in df.columns
    # NaNs should remain NaN so scoring excludes them from denominator
    assert df["edu_petite_enfance_scaled"].isna().sum() == 98
    assert df["edu_petite_enfance_scaled"].iloc[-1] == 1.0
    assert df["edu_petite_enfance_scaled"].iloc[-2] == 0.0

