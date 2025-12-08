
import pytest
import pandas as pd
import geopandas as gpd
import numpy as np
from app.scoring import compute_criteria_scores, compute_category_scores, ScoringConfig

def test_housing_scores_exclusion_logic():
    """
    Verifies that housing scores are actually DROPPED (not just NaNs) and thus excluded from category scoring.
    """
    # Create a small dummy DF with both monome and binome columns
    # We include dummy values for scores
    data = {
        'codgeo': ['A', 'B'],
        'log_vac_scaled': [0.5, 0.6],
        'log_vac_scaled_binome': [0.4, 0.4],
        'log_soc_inoc_scaled': [0.7, 0.8],
        'log_soc_inoc_scaled_binome': [0.1, 0.1],
        'log_occup_scaled': [0.9, 0.2],
        'log_occup_scaled_binome': [0.5, 0.5],
        
        # Required columns for function to run without crashing
        'met_scaled': [0.5, 0.5], 
        'inc_socle_admin_score': [0.0, 0.0],
        'inc_lien_social_score': [0.0, 0.0],
        'inc_population_scaled': [0.0, 0.0],
        'inc_pol_scaled': [0.0, 0.0],
        'dist_current_loc': [1000, 1000],
        'epci_code': ['1', '2']
    }
    df = gpd.GeoDataFrame(data, index=['A', 'B'])

    # Mock inputs
    incl_index = pd.DataFrame()
    df_all_communes = gpd.GeoDataFrame({'epci_code': ['1']}, index=['A'])
    associations_data = pd.DataFrame({'codgeo': ['A'], 'id_waldec': ['W1'], 'count': [1]})
    bmo_vertical = pd.DataFrame({'codgeo': ['A'], 'fap_code': ['F1']})
    formations_data = pd.DataFrame({'codgeo': ['A'], 'formation_code': ['F1'], 'count': [1]})
    codformations_index = pd.DataFrame()
    scores_cat = pd.DataFrame() 
    global_stats = {}

    def run_scoring(hebergement, logement):
        prefs = {
            'hebergement': hebergement,
            'logement': logement,
            'nb_adultes': 1,
            'nb_enfants': 0,
            'commune_actuelle': 'A',
            'codes_metiers': [None],
            'codes_formations': [None],
            'classe_enfants': [],
            'loc_distance_km': 20,
            'affinite_selection': [],
            'besoins_autres': [],
            'socle_admin_selection': []
        }
        
        # We work on a copy to assume fresh start each time
        df_copy = df.copy()
        
        return compute_criteria_scores(
            df_copy, prefs, incl_index, df_all_communes, associations_data, 
            bmo_vertical, formations_data, codformations_index, scores_cat, global_stats
        )

    # 1. Test: Location + Location -> Keep log_vac, Drop others
    res1 = run_scoring('Location', 'Location')
    assert 'log_vac_scaled' in res1.columns
    assert 'log_vac_scaled_binome' in res1.columns
    assert 'log_soc_inoc_scaled' not in res1.columns
    assert 'log_soc_inoc_scaled_binome' not in res1.columns
    assert 'log_occup_scaled' not in res1.columns
    assert 'log_occup_scaled_binome' not in res1.columns

    # 2. Test: Chez l'habitant + Logement Social -> Keep occup & soc_inoc. Drop vac.
    res2 = run_scoring("Chez l'habitant", 'Logement Social')
    assert 'log_vac_scaled' not in res2.columns
    assert 'log_vac_scaled_binome' not in res2.columns
    assert 'log_occup_scaled' in res2.columns
    assert 'log_occup_scaled_binome' in res2.columns
    assert 'log_soc_inoc_scaled' in res2.columns
    assert 'log_soc_inoc_scaled_binome' in res2.columns

    # 3. Test Category Scoring Impact (Integration-ish)
    # We want to verify that DROPPING the column actually excludes it from calculation.
    # We need a scores_cat that includes these columns
    scores_cat_mock = pd.DataFrame({
        'cat': ['logement', 'logement', 'logement'],
        'score': ['log_vac_scaled', 'log_soc_inoc_scaled', 'log_occup_scaled'],
        'weight': [1.0, 1.0, 1.0],
        'incl_binome': [True, False, False] # Irrelevant for this test logic
    })
    
    config_mock = ScoringConfig(
        poids_emploi=0, poids_logement=100, poids_education=0, poids_inclusion=0, poids_sante=0, 
        poids_mobilité=0, criteria_weights={}, commune_actuelle='A', loc_distance_km=20, 
        nb_adultes=1, nb_enfants=0, hebergement="Chez l'habitant", logement="Logement Social",
        codes_metiers=[], codes_formations=[], classe_enfants=[], besoin_sante="Aucun", besoins_autres=[],
        socle_admin_selection=[], affinite_selection=[], binome_penalty=0.0, pop_min=0
    )

    # Run criteria scores first logic manually
    # For Chez l'habitant + Logement Social:
    # We expect `log_vac_scaled` to be DROPPED.
    # We expect `log_soc_inoc_scaled` and `log_occup_scaled` to be kept.
    
    # Let's say we have scores:
    # log_vac = 0.5 (Should be ignored)
    # log_soc = 0.8
    # log_occup = 0.2
    # expected average = (0.8 + 0.2) / 2 = 0.5. 
    # If vac was included (0.5), avg would be (0.5+0.8+0.2)/3 = 0.5. Bad example.
    
    # Let's use differents
    # vac = 0.0. 
    # soc = 1.0. 
    # occup = 1.0.
    # desired = (1+1)/2 = 1.0.
    # if vac included: (0+1+1)/3 = 0.66.
    
    df_cat_test = gpd.GeoDataFrame({
        'log_vac_scaled': [0.0],
        'log_soc_inoc_scaled': [1.0],
        'log_occup_scaled': [1.0]
    }, index=['A'])
    
    # Simulate drop
    df_cat_test.drop(columns=['log_vac_scaled'], inplace=True)
    
    res_cat = compute_category_scores(df_cat_test, scores_cat_mock, 0.0, config_mock)
    
    # Check logement_cat_score
    score = res_cat['logement_cat_score'].iloc[0]
    assert score == 1.0, f"Expected 1.0, got {score}. Excluded column logic failed?"


if __name__ == "__main__":
    test_housing_scores_exclusion_logic()
