import pytest
import pandas as pd
import geopandas as gpd
from app.scoring import ScoringEngine, filter_communes
from app.config import ScoringConfig

def test_filter_communes_custom_code():
    # Mock data
    df = gpd.GeoDataFrame({
        'dep_code': ['33', '33', '40', '75'],
        'reg_code': ['75', '75', '75', '11'],
        'geometry': [None]*4
    }, index=['33063', '33001', '40001', '75056'])
    
    # Test Department filter
    res_dep = filter_communes(df, None, 'departement', '33', 'departement')
    assert len(res_dep) == 2
    assert '33063' in res_dep.index
    
    # Test Region filter
    res_reg = filter_communes(df, None, 'region', '75', 'region')
    assert len(res_reg) == 3
    assert '40001' in res_reg.index
    assert '75056' not in res_reg.index

def test_scoring_engine_run_custom_code(monkeypatch):
    # Mock df_all_communes
    df = gpd.GeoDataFrame({
        'dep_code': ['33', '40'],
        'reg_code': ['75', '75'],
        'geometry': [None]*2,
        'libgeo': ['Bordeaux', 'Dax']
    }, index=['33063', '40001'])
    
    engine = ScoringEngine(
        df_all_communes=df,
        df_bv_geo=pd.DataFrame(),
        df_area_geo=pd.DataFrame(),
        scores_cat=pd.DataFrame(),
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        bmo_vertical=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        codformations_index=pd.DataFrame(),
        global_stats={}
    )
    
    config = ScoringConfig(
        poids_emploi=50, poids_logement=50, poids_education=50,
        poids_inclusion=50, poids_mobilité=50, poids_sante=50,
        criteria_weights={},
        commune_actuelle='33063',
        loc_search_area='departement',
        nb_adultes=1, nb_enfants=0,
        hebergement='Location', logement='Location',
        codes_metiers=[], codes_formations=[], classe_enfants=[], besoin_sante='Aucun',
        inc_services_add_selection=[], inc_services_core_selection=[], inc_asso_add_selection=[],
        loc_custom_code='40', # Force search in 40
        loc_custom_type='departement'
    )
    
    # Mock _compute_scores to return what it's given
    monkeypatch.setattr(engine, "_compute_scores", lambda df_in, cfg_in: df_in)
    
    result = engine.run(config)
    assert len(result) == 1
    assert result.index[0] == '40001'
    
def test_scoring_engine_run_region_custom_code(monkeypatch):
    # Mock df_all_communes
    df = gpd.GeoDataFrame({
        'dep_code': ['33', '40', '75'],
        'reg_code': ['75', '75', '11'],
        'geometry': [None]*3,
        'libgeo': ['Bordeaux', 'Dax', 'Paris']
    }, index=['33063', '40001', '75056'])
    
    engine = ScoringEngine(
        df_all_communes=df,
        df_bv_geo=pd.DataFrame(),
        df_area_geo=pd.DataFrame(),
        scores_cat=pd.DataFrame(),
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        bmo_vertical=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        codformations_index=pd.DataFrame(),
        global_stats={}
    )
    
    config = ScoringConfig(
        poids_emploi=50, poids_logement=50, poids_education=50,
        poids_inclusion=50, poids_mobilité=50, poids_sante=50,
        criteria_weights={},
        commune_actuelle='75056', # Current in Paris
        loc_search_area='region',
        nb_adultes=1, nb_enfants=0,
        hebergement='Location', logement='Location',
        codes_metiers=[], codes_formations=[], classe_enfants=[], besoin_sante='Aucun',
        inc_services_add_selection=[], inc_services_core_selection=[], inc_asso_add_selection=[],
        loc_custom_code='75', # Search in Region 75
        loc_custom_type='region'
    )
    
    monkeypatch.setattr(engine, "_compute_scores", lambda df_in, cfg_in: df_in)
    
    result = engine.run(config)
    assert len(result) == 2
    assert '33063' in result.index
    assert '40001' in result.index
    assert '75056' not in result.index
