import pandas as pd
import pytest
import numpy as np
from core.scoring import compute_category_scores
from core.models import ScoringConfig

@pytest.fixture
def mock_config():
    return ScoringConfig(
        poids_emploi=100, poids_logement=100, poids_education=100,
        poids_inclusion=100, poids_sante=100, poids_mobilité=100,
        commune_actuelle='33063',
        loc_search_area='departement',
        nb_adultes=1, nb_enfants=0,
        hebergement='Location', logement='Location',
        codes_metiers=[], codes_formations=[], classe_enfants=[],
        besoin_sante='Aucun',
        inc_services_add_selection=[],
        inc_services_core_selection=[],
        inc_asso_add_selection=[],
        criteria_weights={}
    )

def test_weighted_average(mock_config):
    # Setup Data
    df = pd.DataFrame({
        'crit1': [0.0, 1.0],
        'crit2': [1.0, 0.0]
    })
    
    # Setup Scores Cat
    scores_cat = pd.DataFrame({
        'score': ['crit1', 'crit2'],
        'cat': ['test_cat', 'test_cat'],
        'weight': [1.0, 1.0]
    })
    
    # 1. Equal Weights (Default)
    # Row 0: (0*1 + 1*1) / 2 = 0.5
    # Row 1: (1*1 + 0*1) / 2 = 0.5
    df_res = compute_category_scores(df, scores_cat, mock_config)
    assert df_res['test_cat_cat_score'].iloc[0] == 0.5
    assert df_res['test_cat_cat_score'].iloc[1] == 0.5
    
    # 2. Weighted (crit1 * 3)
    mock_config.criteria_weights = {'crit1': 3.0}
    # Row 0: (0*3 + 1*1) / 4 = 0.25
    # Row 1: (1*3 + 0*1) / 4 = 0.75
    df_res = compute_category_scores(df, scores_cat, mock_config)
    assert df_res['test_cat_cat_score'].iloc[0] == 0.25
    assert df_res['test_cat_cat_score'].iloc[1] == 0.75

def test_weighted_average_with_nan(mock_config):
    # Setup Data with NaNs
    df = pd.DataFrame({
        'crit1': [0.0, None],
        'crit2': [1.0, 1.0]
    })
    
    scores_cat = pd.DataFrame({
        'score': ['crit1', 'crit2'],
        'cat': ['test_cat', 'test_cat'],
        'weight': [1.0, 1.0]
    })
    
    # 1. Equal Weights
    # Row 0: (0+1)/2 = 0.5
    # Row 1: (NaN ignored) -> 1/1 = 1.0
    df_res = compute_category_scores(df, scores_cat, mock_config)
    assert df_res['test_cat_cat_score'].iloc[0] == 0.5
    assert df_res['test_cat_cat_score'].iloc[1] == 1.0
    
    # 2. Weighted (crit2 * 3)
    mock_config.criteria_weights = {'crit2': 3.0}
    # Row 0: (0*1 + 1*3) / 4 = 0.75
    # Row 1: (NaN + 1*3) / 3 = 1.0
    df_res = compute_category_scores(df, scores_cat, mock_config)
    assert df_res['test_cat_cat_score'].iloc[0] == 0.75
    assert df_res['test_cat_cat_score'].iloc[1] == 1.0
