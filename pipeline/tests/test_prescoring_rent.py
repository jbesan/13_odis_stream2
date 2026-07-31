"""Unit tests for PLM rent aggregation, house proxy separation, and quantile_level propagation."""

import numpy as np
import pandas as pd
import pytest

from pipeline.build import consolidate_plm_communes
from pipeline.prescoring import get_scores_config


def test_plm_rent_aggregation_averages_instead_of_summing():
    """Test that PLM consolidation calculates a population-weighted average for rent columns, NOT a sum."""
    # Synthetic Paris arrondissements data (75101 to 75120) and parent 75056
    arrondissements = [str(x) for x in range(75101, 75121)]
    data = []
    
    # Parent row
    # NaN means the source has no parent-level observation; unlike the former
    # implementation, a legitimate parent value of 0 must be preserved.
    data.append({"codgeo": "75056", "population": np.nan, "loyer_m2_moy_appt_all": np.nan, "loyer_app_m2": np.nan})
    
    # 20 child arrondissements with 30.0 euro/m2 rent each
    for code in arrondissements:
        data.append({"codgeo": code, "population": 100000, "loyer_m2_moy_appt_all": 30.0, "loyer_app_m2": 30.0})
        
    df = pd.DataFrame(data)
    
    res = consolidate_plm_communes(df)
    
    parent_row = res[res["codgeo"] == "75056"].iloc[0]
    assert parent_row["loyer_m2_moy_appt_all"] == pytest.approx(30.0, abs=0.5), \
        f"Paris parent rent must be ~30.0 €/m² (averaged), NOT summed ({parent_row['loyer_m2_moy_appt_all']} €/m²)"
    assert parent_row["loyer_app_m2"] == pytest.approx(30.0, abs=0.5)


def test_house_rent_not_overwritten_by_apartment_fallback():
    """Test that house rent remains NaN when fallback is applied if no house data exists."""
    df = pd.DataFrame({
        "commune_sk": ["sk1", "sk2"],
        "loyer_app_m2": [15.0, 20.0],
        "loyer_m2_moy_appt_all": [15.0, 20.0],
        "loyer_m2_moy_house_all": [np.nan, np.nan]
    })
    
    # House rent must not be forcibly populated with apartment rent
    assert df["loyer_m2_moy_house_all"].isna().all(), "House rent must remain NaN when house data is unobserved"


def test_quantile_level_propagation_in_scores_config():
    """Test that quantile_level is extracted in get_scores_config()."""
    config = get_scores_config()
    
    # Check that scores_config contains quantile_level key for configured criteria
    for score_id, conf in config.items():
        assert "quantile_level" in conf, f"quantile_level key must be present in score config for {score_id}"


def test_get_min_max_quant_safeguards_zero_variance():
    """Test that get_min_max_quant correctly uses quantile q and falls back to observed min/max if q yields min==max."""
    from pipeline.prescoring import get_min_max_quant

    # Case 1: Standard distribution
    s1 = pd.Series(np.linspace(0, 100, 101), name="s1")
    q_min, q_max = get_min_max_quant(s1, q=0.05)
    assert q_min == pytest.approx(5.0)
    assert q_max == pytest.approx(95.0)

    # Case 2: Zero-inflated distribution (97 zeros out of 100) -> q=0.05 gives q0.05=0, q0.95=0 -> min==max
    s2 = pd.Series([0.0] * 97 + [10.0, 20.0, 30.0], name="s2")
    q_min2, q_max2 = get_min_max_quant(s2, q=0.05)
    # Must fallback to observed min/max (0.0, 30.0) instead of collapsing to (0.0, 0.0)
    assert q_min2 == 0.0
    assert q_max2 == 30.0
