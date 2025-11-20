import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
import pytest
import numpy as np

from app import scoring
from app import config as cfg
from app.config import ScoringConfig

@pytest.fixture
def sample_data():
    """Creates a sample GeoDataFrame for testing."""
    data = {
        'codgeo': ['75056', '69123', '13055', '33063', '64445'],
        'libgeo': ['Paris', 'Lyon', 'Marseille', 'Bordeaux', 'Pau'],
        'dep_code': ['75', '69', '13', '33', '64'],
        'reg_code': ['11', '84', '93', '75', '75'],
        'population': [2148271, 513275, 861635, 257068, 77130],
        'geometry': [
            Polygon([(2.224, 48.816), (2.469, 48.816), (2.469, 48.902), (2.224, 48.902)]),
            Polygon([(4.8, 45.7), (4.9, 45.7), (4.9, 45.8), (4.8, 45.8)]),
            Polygon([(5.3, 43.2), (5.4, 43.2), (5.4, 43.3), (5.3, 43.3)]),
            Polygon([(-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838)]),
            Polygon([(-0.333, 43.3), (-0.333, 43.3), (-0.333, 43.3), (-0.333, 43.3)])
        ],
        'codgeo_voisins': [['92050', '93001'], ['69001'], ['13001'], ['33062'], ['64001']],
        'epci_code': ['200054781', '200046977', '200054807', '200023384', '246401722'],
        'met': [100, 50, 70, 80, 60],
        'pop_be': [1000, 500, 700, 800, 400],
        'be_codfap_top': [[ 'F1', 'G2'], ['F1'], ['G2'], ['H3'], ['H3']],
        'codes_formations': [['123'], ['456'], ['123', '456'], [], []],
        'rp_5+pieces': [10, 20, 15, 12, 10],
        'log_rp': [100, 200, 150, 120, 90],
        'log_soc_inoccupes': [5, 2, 3, 4, 2],
        'log_soc_total': [50, 20, 30, 40, 20],
        'log_vac': [10, 5, 8, 6, 4],
        'log_total': [100, 50, 80, 60, 40],
        'risque_fermeture': [1, 0, 2, 0, 0],
        'ecoles_ct': [10, 5, 15, 8, 6],
        'svc_incl_count': [5, 3, 4, 2, 3],
        'pol_num': [1, 2, 3, 4, 1],
    }
    gdf = gpd.GeoDataFrame(data, crs="EPSG:4326")
    gdf = gdf.set_index('codgeo')
    return gdf

@pytest.fixture
def sample_scores_cat():
    """Creates a sample scores_cat DataFrame for testing."""
    data = {
        'score': ['met_scaled', 'met_match_adult1_scaled', 'form_match_adult1_scaled', 'log_5p_scaled', 'log_soc_inoc_scaled', 'log_vac_scaled', 'classes_ferm_scaled', 'reloc_dist_scaled', 'reloc_epci_scaled', 'besoins_match_scaled', 'svc_incl_scaled', 'pol_scaled'],
        'cat': ['emploi', 'emploi', 'emploi', 'logement', 'logement', 'logement', 'education', 'relocalisation', 'relocalisation', 'soutien', 'soutien', 'politique'],
        'metric': ['met_ratio', 'met_match_adult1', 'form_match_adult1', 'log_5p_ratio', 'log_soc_inoc_ratio', 'log_vac_ratio', 'risque_fermeture_ratio', 'dist_current_loc', 'epci_code', 'besoins_match', 'svc_incl_ratio', 'pol_num'],
        'incl_binome': [True, True, True, False, False, False, True, False, False, True, True, False]
    }
    return pd.DataFrame(data)

@pytest.fixture
def sample_incl_index():
    """Creates a sample incl_index DataFrame for testing."""
    data = {
        'codgeo': ['75056', '69123', '13055', '33063', '64445'],
        'key': [{'cat1_serv1'}, {'cat1_serv2'}, {'cat1_serv1', 'cat1_serv2'}, set(), set()]
    }
    df = pd.DataFrame(data)
    df = df.set_index('codgeo')
    return df

@pytest.fixture
def default_config():
    """Returns a default ScoringConfig for testing."""
    return ScoringConfig(
        poids_emploi=100,
        poids_logement=100,
        poids_education=100,
        poids_inclusion=25,
        poids_mobilité=100,
        commune_actuelle='33063', # Bordeaux
        loc_distance_km=50,
        nb_adultes=1,
        nb_enfants=0,
        hebergement='Location',
        logement='Location',
        codes_metiers=[],
        codes_formations=[],
        classe_enfants=[],
        besoin_sante='Aucun',
        besoins_autres={},
        binome_penalty=0.5,
        pop_min=1000
    )

def test_compute_odis_score_returns_dataframe(sample_data, sample_scores_cat, default_config, sample_incl_index):
    """Tests that compute_odis_score returns a DataFrame with the expected columns."""
    
    # Arrange
    config = default_config
    config.commune_actuelle = '75056' # Paris
    config.nb_adultes = 1
    config.codes_metiers = [['F1']]
    config.codes_formations = [[]]
    config.hebergement = "Peu importe"
    config.logement = "Peu importe"
    config.classe_enfants = []
    config.besoins_autres = {}


    # Act
    result = scoring.compute_odis_score(sample_data, sample_data, sample_scores_cat, config, sample_incl_index)

    # Assert
    assert isinstance(result, pd.DataFrame)
    assert 'weighted_score' in result.columns
    assert not result.empty


def test_add_distance_to_current_loc(sample_data):
    """Tests that distance calculation is correct."""
    # Arrange
    current_codgeo = '33063' # Bordeaux

    # Act
    df_with_dist = scoring.add_distance_to_current_loc(sample_data, current_codgeo)

    # Assert
    assert 'dist_current_loc' in df_with_dist.columns
    # Distance from Bordeaux to Bordeaux should be 0
    assert df_with_dist.loc[current_codgeo]['dist_current_loc'] == 0
    # Distance from Bordeaux to Paris should be non-zero
    assert df_with_dist.loc['75056']['dist_current_loc'] > 0

def test_filter_by_distance(sample_data):
    """Tests that the distance filter works as expected."""
    # Arrange
    df_with_dist = scoring.add_distance_to_current_loc(sample_data, '33063')
    max_dist_km = 200 # A distance that should exclude far cities like Paris

    # Act
    filtered_df = scoring.filter_by_distance(df_with_dist, max_dist_km)

    # Assert
    assert '33063' in filtered_df.index # Bordeaux should be included
    assert '75056' not in filtered_df.index # Paris should be excluded

def test_compute_criteria_scores_creates_columns(sample_data, default_config, sample_incl_index):
    """Tests that criteria scores are computed and create the expected columns."""
    # Arrange
    config = default_config
    config.nb_adultes = 1
    config.codes_metiers = [['F1']]
    config.codes_formations = [[]]
    config.logement = "Logement Social"
    config.classe_enfants = ['Maternelle']

    # FIX: Add the distance column, which is a prerequisite for this function
    df_with_dist = scoring.add_distance_to_current_loc(sample_data, config.commune_actuelle)

    # Act
    scored_df = scoring.compute_criteria_scores(df_with_dist, config.__dict__, sample_incl_index, sample_data)

    # Assert
    assert 'met_match_adult1_scaled' in scored_df.columns
    assert 'log_soc_inoc_scaled' in scored_df.columns
    assert 'classes_ferm_scaled' in scored_df.columns

def test_compute_category_scores_calculates_mean(sample_data, sample_scores_cat):
    """Tests that category scores are the mean of their criteria scores."""
    # Arrange
    df = sample_data.copy()
    df['met_scaled'] = 0.5  # Mock criteria score
    df['met_match_adult1_scaled'] = 1.0  # Mock criteria score
    
    # FIX: Isolate the test to only the two mocked 'emploi' scores
    scores_to_keep = ['met_scaled', 'met_match_adult1_scaled']
    scores_cat_filtered = sample_scores_cat[sample_scores_cat['score'].isin(scores_to_keep)].copy()
    
    # Ensure only 'emploi' category is present for this test
    scores_cat_filtered['cat'] = 'emploi'

    # Act
    df_cat = scoring.compute_category_scores(df, scores_cat_filtered, binome_penalty=0.1)

    # Assert
    assert 'emploi_cat_score' in df_cat.columns
    assert df_cat.iloc[0]['emploi_cat_score'] == pytest.approx(0.75)

def test_compute_weighted_score_calculates_correctly(default_config):
    """Tests that the weighted score is calculated correctly."""
    # Arrange
    data = {
        'emploi_cat_score': [0.8],
        'logement_cat_score': [0.6],
        'education_cat_score': [0.4]
    }
    df = pd.DataFrame(data)
    config = default_config
    config.poids_emploi = 100
    config.poids_logement = 50
    config.poids_education = 0 # Education is ignored

    # Act
    weighted_score = scoring.compute_weighted_score(df, config)

    # Assert
    expected_score = ((0.8 * 100) + (0.6 * 50)) / (100 + 50)
    assert weighted_score.iloc[0] == pytest.approx(expected_score)

def test_compute_odis_score_area_based_search(sample_data, sample_scores_cat, default_config, sample_incl_index):
    """Tests that area-based (departement, region) searches work correctly."""
    # --- Test Département search ---
    config_dep = default_config
    config_dep.commune_actuelle = '33063' # Bordeaux
    config_dep.loc_distance_km = 'departement'
    config_dep.nb_adultes = 1
    config_dep.codes_metiers = [[]] # Fix: ensure list has one entry for one adult
    config_dep.codes_formations = [[]] # Fix: ensure list has one entry for one adult

    # Manually filter the data for the test, as this is done in 3_Resultats.py in the app
    dep_search_data = sample_data[sample_data['dep_code'] == '33']
    result_dep = scoring.compute_odis_score(dep_search_data, sample_data, sample_scores_cat, config_dep, sample_incl_index)
    
    # Should only contain Bordeaux, as it's the only one in dep 33
    assert len(result_dep) == 1
    assert '33063' in result_dep.index

    # --- Test Région search ---
    config_reg = default_config
    config_reg.commune_actuelle = '33063' # Bordeaux
    config_reg.loc_distance_km = 'region'
    config_reg.nb_adultes = 1
    config_reg.codes_metiers = [[]] # Fix: ensure list has one entry for one adult
    config_reg.codes_formations = [[]] # Fix: ensure list has one entry for one adult

    reg_search_data = sample_data[sample_data['reg_code'] == '75']
    result_reg = scoring.compute_odis_score(reg_search_data, sample_data, sample_scores_cat, config_reg, sample_incl_index)

    # Should contain Bordeaux and Pau, as they are both in region 75
    assert len(result_reg) == 2
    assert '33063' in result_reg.index
    assert '64445' in result_reg.index

def test_aggregate_scores_by_bassin_de_vie():
    """Tests that the aggregation by 'bassin de vie' is correct."""
    # Arrange
    data = {
        'codgeo': ['C1', 'C2', 'C3'],
        'libgeo': ['Commune A', 'Commune B', 'Commune C'],
        'population': [1000, 2000, 500],
        'weighted_score': [0.8, 0.6, 0.9],
        'another_score_scaled': [0.5, 0.7, 0.2],
        cfg.BV_CODE_COL: ['BV1', 'BV1', 'BV2'],
        cfg.BV_NAME_COL: ['Bassin de Vie 1', 'Bassin de Vie 1', 'Bassin de Vie 2'],
        'epci_nom': ['EPCI 1', 'EPCI 1', 'EPCI 2'],
        'url_odis': ['url1_A', 'url1_B', 'url2_C'],
        'url_wikipedia': ['wiki_A', 'wiki_B', 'wiki_C'],
        'be_libfap_top': [['Job A', 'Job B'], ['Job B', 'Job C'], ['Job D']],
        'noms_formations': [['Form A'], ['Form B', 'Form C'], ['Form A', 'Form D']]
    }
    df = pd.DataFrame(data).set_index('codgeo')

    # Act
    result_df = scoring.aggregate_scores_by_bassin_de_vie(df)
    
    # Assert
    assert len(result_df) == 2 # Should be two bassins de vie
    
    bv1_result = result_df[result_df[cfg.BV_CODE_COL] == 'BV1'].iloc[0]
    
    # Check population aggregation
    assert bv1_result['population'] == 3000 # 1000 + 2000
    
    # Check weighted score aggregation
    expected_weighted_score_bv1 = ((0.8 * 1000) + (0.6 * 2000)) / (1000 + 2000)
    assert np.isclose(bv1_result['weighted_score'], expected_weighted_score_bv1)
    
    # Check another scaled score to be sure
    expected_another_score_bv1 = ((0.5 * 1000) + (0.7 * 2000)) / (1000 + 2000)
    assert np.isclose(bv1_result['another_score_scaled'], expected_another_score_bv1)
    
    # Check aggregation of textual and URL data
    assert bv1_result['epci_nom'] == 'EPCI 1'
    assert bv1_result['communes'] == ['C1', 'C2']
    assert bv1_result['url_odis'] == 'url1_B' # From Commune B, the most populous
    assert bv1_result['url_wikipedia'] == 'wiki_B'
    assert bv1_result['be_libfap_top'] == ['Job A', 'Job B', 'Job C']
    assert bv1_result['noms_formations'] == ['Form A', 'Form B', 'Form C']

def test_population_score_is_calculated(sample_data, default_config, sample_incl_index):
    """Tests that the population_scaled score is calculated correctly."""
    # Arrange
    config = default_config
    # FIX: Ensure preference lists match the number of adults to prevent IndexError
    config.codes_metiers = [[]]
    config.codes_formations = [[]]
    
    # Add distance column, a prerequisite for the function
    df_with_dist = scoring.add_distance_to_current_loc(sample_data, config.commune_actuelle)

    # Act
    scored_df = scoring.compute_criteria_scores(df_with_dist, config.__dict__, sample_incl_index, sample_data)

    # Assert
    # 1. Check if the column was created
    assert 'population_scaled' in scored_df.columns

    # 2. Check if values are scaled between 0 and 1
    assert scored_df['population_scaled'].min() >= 0.0
    assert scored_df['population_scaled'].max() <= 1.0

    # 3. Check if higher population gives a higher score
    # Paris (75056) has the highest pop, Marseille (13055) is second
    score_paris = scored_df.loc['75056']['population_scaled']
    score_marseille = scored_df.loc['13055']['population_scaled']
    assert score_paris > score_marseille

def test_run_scoring_pipeline(sample_data, sample_scores_cat, default_config, sample_incl_index):
    """Tests the end-to-end scoring pipeline."""
    # Arrange
    config = default_config
    config.commune_actuelle = '33063' # Bordeaux
    config.loc_distance_km = 200
    config.nb_adultes = 1
    config.codes_metiers = [['F1']]
    config.codes_formations = [[]]
    
    # Mock BV and Area dataframes as they are needed for the pipeline
    df_bv_geo = gpd.GeoDataFrame(
        {cfg.BV_CODE_COL: ['BV1', 'BV2'], cfg.BV_NAME_COL: ['Bassin 1', 'Bassin 2']},
        geometry=[Polygon([(0,0), (1,0), (1,1), (0,1)]), Polygon([(2,2), (3,2), (3,3), (2,3)])],
        crs="EPSG:4326"
    ).set_index(cfg.BV_CODE_COL)
    
    df_area_geo = gpd.GeoDataFrame() # Not used for distance search

    # Act
    processed_gdf, unaggregated_gdf = scoring.run_scoring_pipeline(
        config=config,
        df_all_communes=sample_data,
        df_bv_geo=df_bv_geo,
        df_area_geo=df_area_geo,
        scores_cat=sample_scores_cat,
        incl_index=sample_incl_index,
        view_level='Communes'
    )

    # Assert
    assert isinstance(processed_gdf, gpd.GeoDataFrame)
    assert isinstance(unaggregated_gdf, gpd.GeoDataFrame)
    assert not processed_gdf.empty
    assert 'weighted_score' in processed_gdf.columns
    # Bordeaux should NOT be in the results (it's filtered out)
    assert '33063' not in processed_gdf['codgeo'].values
    # Pau should be in the results (it's within 200km)
    assert '64445' in processed_gdf['codgeo'].values
