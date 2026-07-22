import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from utils import data_loader


def test_load_referentiels_raw_speed_and_keys():
    """Tests that load_referentiels_raw returns lightweight referentiels without loading heavy odis dataframe."""
    data = data_loader.load_referentiels_raw()
    
    # Check essential Tier 1 referentiel keys
    assert "referentiels_raw" in data
    assert "depcom_df" in data
    assert "coddep_set" in data
    assert "scores_cat" in data
    assert "rome_index" in data
    assert "codformations_index" in data
    assert "inclusion_services_index" in data
    assert "waldec_index" in data
    
    # Check that depcom_df has rows and correct structure
    depcom_df = data["depcom_df"]
    assert isinstance(depcom_df, pd.DataFrame)
    assert not depcom_df.empty
    assert "libgeo" in depcom_df.columns
    assert "dep_code" in depcom_df.columns
    
    # Check Tier 2 placeholders are empty
    assert data["odis"].empty
    assert data["pois"].empty


def test_get_app_data_tier1_vs_tier2():
    """Tests get_app_data with load_heavy=False vs load_heavy=True."""
    tier1_data = data_loader.get_app_data(load_heavy=False)
    assert tier1_data["odis"].empty
    assert not tier1_data["depcom_df"].empty

    tier2_data = data_loader.get_app_data(load_heavy=True)
    assert not tier2_data["odis"].empty
    assert not tier2_data["pois"].empty
