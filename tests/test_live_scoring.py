import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'app'))

import pandas as pd
import geopandas as gpd
import numpy as np
import core.scoring as scoring
from core.models import ScoringConfig

from shapely.geometry import Point

def test_live_jobs_scoring():
    # 1. Setup Mock Data
    df_all_communes = gpd.GeoDataFrame({
        'codgeo': ['33063', '33000', '99999'],
        'bassin_de_vie': ['33301', '33301', '99301'],
        'population': [5000, 250000, 1000],
        'epci_code': ['243300310', '243300310', '999999999'],
        'dep_code': ['33', '33', '99'],
        'reg_code': ['75', '75', '99'],
        'geometry': [Point(0,0), Point(0.1, 0.1), Point(1,1)]
    }).set_index('codgeo')

    live_jobs_data = pd.DataFrame({
        'commune': ['33063', '33063', '33000'],
        'romeCode': ['M1805', 'M1805', 'M1805'],
        'romeLibelle': ['Dev', 'Dev', 'Dev'],
        'total_postes': [2, 3, 10], # 5 for 33063, 10 for 33000
        'nb_offres_tension': [1, 0, 5]
    })

    fap_rome_mapping = pd.DataFrame([
        {'code': 'T2A60', 'label': 'M1805'} # FAP T2A60 -> ROME M1805
    ])

    scores_cat = pd.DataFrame([
        {'score': 'met_match_adult1_scaled', 'min_bound': 0, 'max_bound': 10, 'cat': 'emploi', 'weight': 2.0, 'metric': 'met_match_adult1'},
        {'score': 'met_match_adult1_bdv_scaled', 'min_bound': 0, 'max_bound': 50, 'cat': 'emploi', 'weight': 1.0, 'metric': 'met_match_adult1_bdv'},
        {'score': 'met_match_adult1_tension_scaled', 'min_bound': 0, 'max_bound': 5, 'cat': 'emploi', 'weight': 1.0, 'metric': 'met_match_adult1_tension'}
    ])

    df_bv_geo = gpd.GeoDataFrame({
        'codgeo': ['33301', '99301'],
        'geometry': [Point(0,0), Point(1,1)]
    }).set_index('codgeo')

    # 2. Instantiate Engine
    engine = scoring.ScoringEngine(
        df_all_communes=df_all_communes,
        df_bv_geo=df_bv_geo,
        df_area_geo=None,
        scores_cat=scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        bmo_vertical=pd.DataFrame(columns=['codgeo', 'fap_code']),
        formations_data=pd.DataFrame(columns=['codgeo', 'formation_code']),
        live_jobs_data=live_jobs_data,
        global_stats={}
    )

    # 3. Configure Search (Searching for FAP T2A60)
    config = ScoringConfig(
        poids_emploi=1,
        poids_logement=1,
        poids_education=1,
        poids_inclusion=1,
        poids_mobilité=1,
        poids_sante=1,
        criteria_weights={},
        commune_actuelle='99999',
        loc_search_area='france',
        nb_adultes=1,
        nb_enfants=0,
        hebergement_cible=[],
        logement='Location',
        codes_metiers=[['M1805']], 
        codes_formations=[[]],
        classe_enfants=[],
        besoin_sante='Aucun',
        inc_services_add_selection=[],
        inc_services_core_selection=[],
        inc_asso_add_selection=[]
    )

    # 4. Run Scoring
    scored = engine.run(config)

    # 5. Assertions
    print("\n--- Results ---")
    print(f"Columns available: {scored.columns.tolist()}")
    try:
        print(scored[['met_match_adult1', 'met_match_adult1_scaled', 'met_match_adult1_bdv', 'met_match_adult1_bdv_scaled', 'met_match_adult1_tension', 'met_match_adult1_tension_scaled']])
    except KeyError as e:
        print(f"KeyError: {e}")
    
    # Commune 33063 should have 5 jobs
    assert scored.loc['33063', 'met_match_adult1'] == 5
    assert scored.loc['33063', 'met_match_adult1_scaled'] == 0.5 # 5/10
    
    # Bassin de Vie 33301 contains both, so 15 jobs total
    assert scored.loc['33063', 'met_match_adult1_bdv'] == 15
    assert scored.loc['33063', 'met_match_adult1_bdv_scaled'] == 0.3 # 15/50
    
    # Tension for 33063 (1)
    assert scored.loc['33063', 'met_match_adult1_tension'] == 1
    assert scored.loc['33063', 'met_match_adult1_tension_scaled'] == 0.2 # 1/5

    # Check 33000 (10 jobs, 5 tension)
    assert scored.loc['33000', 'met_match_adult1'] == 10
    assert scored.loc['33000', 'met_match_adult1_tension_scaled'] == 1.0
    
    print("\n✅ Live jobs scoring logic verified!")

if __name__ == "__main__":
    test_live_jobs_scoring()
