import sys
import os
import pytest
sys.path.append(os.path.join(os.getcwd(), 'app'))

import pandas as pd
import geopandas as gpd
import numpy as np
import core.scoring as scoring
from core.models import SearchCriterias

from shapely.geometry import Point

from config import DEMO_SCENARIOS, DEFAULT_INC_SERVICES_CORE

def test_live_jobs_scoring_aicha_scenario():
    """
    Thorough test using Aïcha's scenario (Demo 3):
    - 1 Adult, 2 Children (Crèche + Collège)
    - Specific inclusion services
    - Marseille (13) area
    """
    # 1. Setup Mock Data (Standardized in 4326)
    # Marseille (13055) + some surrounding communes
    df_all_communes = gpd.GeoDataFrame({
        'codgeo': ['13055', '13001', '13002', '99999'],
        'libgeo': ['Marseille', 'Aix', 'Allauch', 'Start'],
        'bassin_de_vie': ['13055', '13001', '13055', '99999'],
        'population': [870000, 140000, 20000, 1000],
        'edu_maternelle_ct': [100, 20, 5, 0],
        'edu_college_ct': [50, 10, 2, 0],
        'edu_maternelle_scaled': [0.8, 0.4, 0.1, 0.0],
        'edu_college_scaled': [0.7, 0.3, 0.05, 0.0],
        'epci_code': ['200054807', '200054807', '200054807', '99999'],
        'dep_code': ['13', '13', '13', '13'],
        'reg_code': ['93', '93', '93', '93'],
        'geometry': [Point(5.37, 43.30), Point(5.45, 43.53), Point(5.48, 43.33), Point(0,0)]
    }, crs="EPSG:4326").set_index('codgeo')

    # Mock Jobs Data for Marseille
    live_jobs_data = pd.DataFrame({
        'commune': ['13055', '13055', '13001'],
        'romeCode': ['K1302', 'K1302', 'K1302'], # Aide à domicile (Aïcha's goal)
        'total_postes': [10, 5, 2], 
        'nb_offres_tension': [5, 2, 0]
    })

    # Mock Inclusion Data
    # Aïcha needs 'lecture-ecriture-calcul--maitriser-le-francais' (FLE)
    # Plus DEFAULT_INC_SERVICES_CORE
    # 🧪 Pattern: ScoringEngine expects 'key' column (sets of services) and indexed by codgeo
    incl_index = pd.DataFrame([
        {'codgeo': '13055', 'key': {'lecture-ecriture-calcul--maitriser-le-francais', 'logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement'}, 'name': 'Marseille Services'},
        {'codgeo': '13001', 'key': {'lecture-ecriture-calcul--maitriser-le-francais'}, 'name': 'Aix Services'}
    ]).set_index('codgeo')

    scores_cat = pd.DataFrame([
        {'score': 'met_match_adult1_scaled', 'min_bound': 0, 'max_bound': 20, 'cat': 'emploi', 'weight': 1.0, 'metric': 'met_match_adult1'},
        {'score': 'inc_services_incl_scaled', 'min_bound': 0, 'max_bound': 1, 'cat': 'inclusion', 'weight': 1.0, 'metric': 'inc_services_incl_scaled'},
        {'score': 'edu_maternelle_scaled', 'min_bound': 0, 'max_bound': 5, 'cat': 'education', 'weight': 1.0, 'metric': 'edu_maternelle_ct'},
        {'score': 'edu_college_scaled', 'min_bound': 0, 'max_bound': 5, 'cat': 'education', 'weight': 1.0, 'metric': 'edu_college_ct'}
    ])

    df_bv_geo = gpd.GeoDataFrame({
        'codgeo': ['13055', '13001', '99999'],
        'geometry': [Point(5.37, 43.30), Point(5.45, 43.53), Point(0,0)]
    }, crs="EPSG:4326").set_index('codgeo')

    # 2. Instantiate Engine
    engine = scoring.ScoringEngine(
            df_all_communes=df_all_communes,
        df_bv_geo=df_bv_geo,
        scores_cat=scores_cat,
        incl_index=incl_index,
        associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']),
        formations_data=pd.DataFrame(columns=['codgeo', 'formation_code', 'count']),
        siae_jobs_data=pd.DataFrame(columns=['codgeo', 'rome']),
        live_jobs_data=pd.DataFrame({
            'commune': ['13055', '13055', '13001'],
            'romeCode': ['M1607', 'M1602', 'M1607'], # Refereced in config Aicha
            'total_postes': [10, 5, 2], 
            'nb_offres_tension': [5, 2, 0]
        }),
        global_stats={}
    )

    # 3. Use Scenario 3 (Aïcha) Criteria with proper merging
    from config import DEMO_DATA_DEFAULT, WEIGHT_PROFILES
    
    # Base defaults
    data = DEMO_DATA_DEFAULT.copy()
    # Scenario data
    scenario_3 = DEMO_SCENARIOS['3']
    data.update(scenario_3)
    
    # Apply Profile if present
    profile = data.get('weight_profile')
    if profile in WEIGHT_PROFILES:
        data.update(WEIGHT_PROFILES[profile])
        
    config = SearchCriterias(**data)
    
    # Override for test specificity
    from app.core.models import CriteriaItem
    config.commune_actuelle = CriteriaItem(code='99999', label='Start')
    config.loc_search_code = ['13']
    config.classe_enfants = ['Petite Enfance/Crêche', 'Collège']
    config.inc_services_selection = [CriteriaItem(code=s, label=s) for s in DEFAULT_INC_SERVICES_CORE]

    # 4. Run Scoring
    scored = engine.run(config)

    # 5. Assertions
    print(f"\nScored columns: {scored.columns.tolist()}")
    
    # Marseille (13055) should have high jobs score (15 jobs found in mock)
    assert '13055' in scored.index
    assert scored.loc['13055', 'met_match_adult1'] == 15
    
    # Inclusion Services: Aïcha selected FLE + DEFAULT_INC_SERVICES_CORE
    # Marseille (13055) has FLE + Logement (which is in the pool) 
    # Marseille has 2/4 matches if we count them
    assert 'inc_services_incl_scaled' in scored.columns
    assert scored.loc['13055', 'inc_services_incl_scaled'] > 0
    
    # Education: Aïcha has 2 kids (Crèche + Collège)
    # The mock doesn't have POIs for schools, so education score might be 0 but shouldn't crash
    assert 'education_cat_score' in scored.columns

    # Final Weighted Score should be present and between 0 and 1
    assert 0 <= scored.loc['13055', 'weighted_score'] <= 1
    
    print("\n✅ Aïcha's thorough scenario verified!")

if __name__ == "__main__":
    test_live_jobs_scoring_aicha_scenario()

