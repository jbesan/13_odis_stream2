import pandas as pd
import pytest
from core.scoring import ScoringEngine
from core.models import SearchCriterias
import geopandas as gpd


@pytest.fixture
def mock_config():
    return SearchCriterias(
        poids_emploi=1.0,
        poids_logement=1.0,
        poids_education=1.0,
        poids_inclusion=1.0,
        poids_sante=1.0,
        poids_mobilite=1.0,
        commune_actuelle="33063",
        loc_search_area="departement",
        nb_adultes=1,
        nb_enfants=0,
        hebergement_cible=[],
        logement="Location",
        codes_metiers=[],
        codes_formations=[],
        classe_enfants=[],
        besoin_sante="Aucun",
        inc_services_selection=[],
        inc_asso_add_selection=[],
        criteria_weights={},
        active_criteria={"crit1", "crit2", "crit3", "crit4", "crit5"},
    )


def test_weighted_average(mock_config):
    # Setup Data
    df = pd.DataFrame({"crit1": [0.0, 1.0], "crit2": [1.0, 0.0]})

    # Setup Scores Cat
    scores_cat = pd.DataFrame(
        {"score": ["crit1", "crit2"], "cat": ["emploi", "emploi"], "weight": [1.0, 1.0]}
    )

    # Init engine
    engine = ScoringEngine(
        df_all_communes=gpd.GeoDataFrame(),
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
    )

    # 1. Equal Weights (Default)
    # Row 0: (0*1 + 1*1) / 2 = 0.5
    # Row 1: (1*1 + 0*1) / 2 = 0.5
    df_res = engine._compute_category_scores(df, mock_config)
    assert df_res["emploi_cat_score"].iloc[0] == 0.5
    assert df_res["emploi_cat_score"].iloc[1] == 0.5

    # 2. Weighted (crit1 * 3)
    mock_config.criteria_weights = {"crit1": 3.0}
    # Raw Row 0: (0*3 + 1*1) / 4 = 0.25
    # Raw Row 1: (1*3 + 0*1) / 4 = 0.75
    # Percentile ranks: 0.25 -> 0.5, 0.75 -> 1.0
    df_res = engine._compute_category_scores(df, mock_config)
    assert df_res["emploi_cat_score"].iloc[0] == 0.5
    assert df_res["emploi_cat_score"].iloc[1] == 1.0


def test_weighted_average_with_zeros(mock_config):
    """
    Verifies that weighted average correctly handles 0.0 values (active criteria with 0 score).
    """
    engine = ScoringEngine(
        df_all_communes=gpd.GeoDataFrame(),
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=pd.DataFrame(
            {
                "score": ["crit1", "crit2"],
                "cat": ["emploi", "emploi"],
                "weight": [1.0, 1.0],
            }
        ),
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
    )

    df = pd.DataFrame({"crit1": [0.0, 0.0], "crit2": [1.0, 0.0]})

    # Global weights from config
    mock_config.criteria_weights = {"crit2": 3.0}

    # Raw Row 0: (0*1 + 1*3) / 4 = 0.75
    # Raw Row 1: (0*1 + 0*3) / 4 = 0.0
    # Percentile ranks: 0.75 -> 1.0, 0.0 -> 0.0
    df_res = engine._compute_category_scores(df, mock_config)
    assert df_res["emploi_cat_score"].iloc[0] == 1.0
    assert df_res["emploi_cat_score"].iloc[1] == 0.0
