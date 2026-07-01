import pandas as pd
import geopandas as gpd
from core import scoring


def get_engine(sample_data, live_scores_cat=None, sample_incl_index=None):
    return scoring.ScoringEngine(
        df_all_communes=sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=live_scores_cat if live_scores_cat is not None else pd.DataFrame(),
        incl_index=sample_incl_index
        if sample_incl_index is not None
        else pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
    )


def test_is_local_search_matching_dept(sample_data, default_config):
    engine = get_engine(sample_data)
    default_config.loc_search_code = "33"
    assert engine._is_local_search(default_config) is True


def test_is_local_search_mismatch_dept(sample_data, default_config):
    engine = get_engine(sample_data)
    default_config.loc_search_code = "75"
    assert engine._is_local_search(default_config) is False


def test_is_local_search_france_is_not_local(sample_data, default_config):
    engine = get_engine(sample_data)
    default_config.loc_search_area = "france"
    assert engine._is_local_search(default_config) is False


def test_active_criteria_relocation_search(
    sample_data, default_config, live_scores_cat, sample_incl_index
):
    engine = get_engine(sample_data, live_scores_cat, sample_incl_index)
    default_config.loc_search_area = "departement"
    default_config.loc_search_code = "75"

    active = engine._get_active_criteria(default_config)
    assert "mob_dist_current_loc_scaled" not in active
    assert "mob_epci_scaled" not in active


def test_active_criteria_local_search(
    sample_data, default_config, live_scores_cat, sample_incl_index
):
    engine = get_engine(sample_data, live_scores_cat, sample_incl_index)
    default_config.loc_search_area = "departement"
    default_config.loc_search_code = ["33"]
    default_config.freq_retour = "1 fois/semaine"

    active = engine._get_active_criteria(default_config)
    assert "mob_dist_current_loc_scaled" in active
    assert "mob_epci_scaled" in active
