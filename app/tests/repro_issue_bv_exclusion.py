import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon
from app import scoring
from app.config import ScoringConfig
from app import config as cfg

@pytest.mark.unit
def test_repro_current_bv_exclusion():
    """
    Reproduction test:
    When view_level is 'Bassins de vie', the bassin de vie containing the 
    current commune should be excluded from the results.
    
    Currently (before fix), it might be included, but with a skewed score 
    because the current commune is dropped before aggregation.
    """
    # 1. Setup Mock Data
    
    # Communes: A (Current - Bordeaux), B (Neighbor - Pessac), C (Other - Paris)
    communes_data = {
        'codgeo': ['A', 'B', 'C'],
        'libgeo': ['Bordeaux', 'Pessac', 'Paris'],
        cfg.BV_CODE_COL: ['BV1', 'BV1', 'BV2'], # A and B in BV1, C in BV2
        cfg.BV_NAME_COL: ['Bassin Bordeaux', 'Bassin Bordeaux', 'Bassin Paris'],
        'population': [1000, 1000, 1000],
        'dep_code': ['33', '33', '75'],
        'reg_code': ['75', '75', '11'],
        'epci_code': ['EPCI1', 'EPCI1', 'EPCI2'],
        'epci_nom': ['EPCI 1', 'EPCI 1', 'EPCI 2']
    }
    df_communes = gpd.GeoDataFrame(
        communes_data, 
        geometry=[
            Point(-0.5792, 44.8378), # Bordeaux
            Point(-0.6314, 44.8067), # Pessac (nearby)
            Point(2.3522, 48.8566)   # Paris (far)
        ],
        crs="EPSG:4326"
    ).set_index('codgeo')
    
    # Bassins de vie Geometry
    # BV1 around Bordeaux, BV2 around Paris
    bv_data = {
        cfg.BV_CODE_COL: ['BV1', 'BV2'],
        cfg.BV_NAME_COL: ['Bassin Bordeaux', 'Bassin Paris']
    }
    df_bv_geo = gpd.GeoDataFrame(
        bv_data,
        geometry=[
            Polygon([(-1, 44), (-1, 45), (0, 45), (0, 44)]), # Covers Bordeaux
            Polygon([(2, 48), (2, 49), (3, 49), (3, 48)])    # Covers Paris
        ],
        crs="EPSG:4326"
    ).set_index(cfg.BV_CODE_COL)
    
    # Area Geometry (Department)
    df_area_geo = gpd.GeoDataFrame(
        {'code': ['01'], 'type': ['departement']},
        geometry=[Polygon([(-1,-1), (-1,12), (12,12), (12,-1)])],
        crs="EPSG:4326"
    ).set_index(['type', 'code'])
    
    # Config
    config = ScoringConfig(
        commune_actuelle='A', # Current commune is A (in BV1)
        loc_distance_km=1000,
        poids_emploi=100,
        poids_logement=0,
        poids_education=0,
        poids_sante=0,
        poids_inclusion=0,
        poids_mobilité=0,
        nb_adultes=1,
        nb_enfants=0,
        hebergement='Location',
        logement='Location',
        codes_metiers=[[]],
        codes_formations=[[]],
        classe_enfants=[],
        besoin_sante='Aucun',
        besoins_autres={},
        socle_admin_selection=[],
        affinite_selection=[],
        binome_penalty=0.0,
        pop_min=0,
        criteria_weights={}
    )
    
    # Mock Scores Catalog
    scores_cat = pd.DataFrame({
        'cat': ['emploi'],
        'score': ['met_scaled'],
        'metric': ['met_ratio'],
        'weight': [1.0],
        'min_bound': [0.0],
        'max_bound': [1.0],
        'incl_binome': [False]
    })
    
    # Mock Global Stats
    global_stats = {'met_scaled': {'min': 0.0, 'max': 1.0}}
    
    # Mock Inclusion Index & Associations (Empty)
    incl_index = pd.DataFrame()
    associations_data = pd.DataFrame(columns=['codgeo', 'id_waldec', 'count'])
    
    # Add dummy score column to communes
    df_communes['met_ratio'] = 0.5
    df_communes['log_vac'] = 10
    df_communes['log_total'] = 100
    df_communes['pol_num'] = 1
    df_communes['pop_be'] = 1000
    df_communes['log_soc_total'] = 100
    df_communes['log_soc_inoccupes'] = 10
    df_communes['rp_5+pieces'] = 50
    df_communes['log_rp'] = 100
    
    print(f"DEBUG: cfg.BV_CODE_COL = {cfg.BV_CODE_COL}")
    print(f"DEBUG: df_communes columns = {df_communes.columns}")
    
    # 2. Run Pipeline
    processed_gdf, _ = scoring.run_scoring_pipeline(
        config=config,
        df_all_communes=df_communes,
        df_bv_geo=df_bv_geo,
        df_area_geo=df_area_geo,
        scores_cat=scores_cat,
        incl_index=incl_index,
        associations_data=associations_data,
        global_stats=global_stats,
        view_level='Bassins de vie'
    )
    
    # 3. Assertions
    print("\nProcessed GDF Index (BV Codes):", processed_gdf[cfg.BV_CODE_COL].tolist())
    
    # We expect BV1 (containing A) to be EXCLUDED.
    # If the bug exists, BV1 might be present (possibly with a wrong score, or just present).
    assert 'BV1' not in processed_gdf[cfg.BV_CODE_COL].values, "BV1 (containing current commune) should be excluded from results"
    assert 'BV2' in processed_gdf[cfg.BV_CODE_COL].values, "BV2 should be included"
