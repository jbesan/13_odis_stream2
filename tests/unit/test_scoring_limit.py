import pytest
import pandas as pd
import numpy as np
import config as cfg
from core.scoring import ScoringEngine
from core.models import SearchCriterias, CriteriaItem
import geopandas as gpd
from shapely.geometry import Point

def test_scoring_limit_and_current_city_preservation(live_scores_cat, default_config):
    """
    Test that ScoringEngine limits results to MAX_MAP_POLYGONS (1000)
    but preserves the current city.
    """
    # 1. Create a large mock dataset (1500 rows)
    n_rows = 1500
    codgeos = [f"{i:05d}" for i in range(n_rows)]
    
    # Ensure current city is in the list
    current_city_code = default_config.commune_actuelle.code
    if current_city_code not in codgeos:
        codgeos[0] = current_city_code
    
    data = {
        'codgeo': codgeos,
        'libgeo': [f"City {i}" for i in range(n_rows)],
        'population': np.random.randint(1000, 100000, n_rows),
        'dep_code': ['33'] * n_rows,
        'reg_code': ['75'] * n_rows,
        'bassin_de_vie': ['1'] * n_rows,
        'epci_code': ['123456789'] * n_rows,
        'centroid_lon': np.random.uniform(0, 10, n_rows),
        'centroid_lat': np.random.uniform(40, 50, n_rows),
    }
    
    # Add some score columns to avoid warnings
    for score_id in live_scores_cat['score']:
        data[score_id] = np.random.rand(n_rows)
        data[f"{score_id}_bdv"] = np.random.rand(n_rows)

    df_all = pd.DataFrame(data).set_index('codgeo')
    
    # Mock GeoDataFrames
    odis_geo = gpd.GeoDataFrame(
        {'codgeo': codgeos, 'geometry': [Point(0,0)] * n_rows},
        crs="EPSG:4326"
    ).set_index('codgeo')

    engine = ScoringEngine(
        df_all_communes=df_all,
        df_bv_geo=pd.DataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(index=df_all.index),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        annuaire_ecoles=pd.DataFrame(),
        annuaire_sante=pd.DataFrame(),
        annuaire_inclusion=pd.DataFrame()
    )

    # 2. Force current city to have a very LOW score to ensure it would be cut off
    # We'll set all its scores to 0
    for col in df_all.columns:
        if 'scaled' in col or 'score' in col:
            df_all.loc[current_city_code, col] = -1.0 # Very low
            
    # 3. Run scoring
    # Force MAX_MAP_POLYGONS for test consistency
    cfg.MAX_MAP_POLYGONS = 1000
    
    results = engine.run(default_config)
    
    # 4. Assertions
    # Should be 1000 (Top 1000) + 1 (Current City which is at the bottom)
    assert len(results) == 1001
    assert current_city_code in results.index
    
    # Verify it is at the bottom
    assert results.index[-1] == current_city_code
    
    # Verify top are indeed sorted
    assert results.iloc[0]['weighted_score'] >= results.iloc[999]['weighted_score']

# Run the test
if __name__ == "__main__":
    pytest.main([__file__])
