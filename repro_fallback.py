import pandas as pd
import numpy as np
import geopandas as gpd
import sys
import os

# Ensure app is in path
sys.path.append('app')

from app.scoring import ScoringEngine, compute_category_scores
from app.config import ScoringConfig

def test_bdv_fallback():
    print("--- Test BdV Fallback Logic ---")
    
    # 1. Setup Mock Categories
    # We define a score that has bdv_factor=0.5
    scores_cat = pd.DataFrame([
        {
            'score': 'test_score', 
            'cat': 'test_cat', 
            'metric': 'raw_metric', 
            'bdv_factor': 0.5, 
            'computation': 'precomputed',
            'min_bound': 0, 'max_bound': 100,
            'weight': 1.0
        }
    ])
    
    # 2. Setup Mock Data
    # C1: Commune Score = 0.1. BdV Score = 1.0. Factor = 0.5.
    # Fallback: max(0.1, 1.0 * 0.5) = 0.5.
    
    # C2: Commune Score = 0.8. BdV Score = 1.0. Factor = 0.5.
    # Fallback: max(0.8, 1.0 * 0.5) = 0.8.
    
    df_communes = pd.DataFrame({
        'codgeo': ['C1', 'C2'],
        'test_score': [0.1, 0.8], # Simulated pre-computed score
        'test_score_bdv': [1.0, 1.0] # Simulated MERGED bdv score
    })
    
    # 3. Init Config (Without binome_penalty)
    config = ScoringConfig(
        poids_emploi=0, poids_logement=0, poids_education=0, poids_inclusion=0, poids_mobilité=0, poids_sante=0,
        criteria_weights={},
        commune_actuelle='C1', loc_distance_km=0, nb_adultes=0, nb_enfants=0,
        hebergement='', logement='', codes_metiers=[], codes_formations=[], classe_enfants=[],
        besoin_sante='', inc_services_add_selection=[], inc_services_core_selection=[], inc_asso_add_selection=[],
        pop_min=0
        # REMOVED binome_penalty
    )
    
    # 4. Trigger Calculation
    result = compute_category_scores(df_communes, scores_cat, config)
    
    print("\nResults:")
    print(result[['test_score', 'test_cat_cat_score']])
    
    # 5. Assertions
    c1_final = result.loc[0, 'test_cat_cat_score']
    c2_final = result.loc[1, 'test_cat_cat_score']
    
    expected_c1 = 0.5
    expected_c2 = 0.8
    
    print(f"C1: Got {c1_final}, Expected {expected_c1}")
    print(f"C2: Got {c2_final}, Expected {expected_c2}")
    
    if abs(c1_final - expected_c1) < 1e-6 and abs(c2_final - expected_c2) < 1e-6:
        print("SUCCESS: Fallback logic verified.")
        return True
    else:
        print("FAILURE: Scores do not match expectation.")
        return False

if __name__ == "__main__":
    if test_bdv_fallback():
        sys.exit(0)
    else:
        sys.exit(1)
