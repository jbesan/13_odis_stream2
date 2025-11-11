import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
import pytest

from app import scoring
from app.config import ScoringConfig

@pytest.fixture
def sample_data():
    """Creates a sample GeoDataFrame for testing."""
    data = {
        'codgeo': ['75056', '69123', '13055', '33063'],
        'libgeo': ['Paris', 'Lyon', 'Marseille', 'Bordeaux'],
        'population': [2148271, 513275, 861635, 257068],
        'geometry': [
            Polygon([(2.224, 48.816), (2.469, 48.816), (2.469, 48.902), (2.224, 48.902)]),
            Polygon([(4.8, 45.7), (4.9, 45.7), (4.9, 45.8), (4.8, 45.8)]),
            Polygon([(5.3, 43.2), (5.4, 43.2), (5.4, 43.3), (5.3, 43.3)]),
            Polygon([(-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838)])
        ],
        'codgeo_voisins': [['92050', '93001'], ['69001'], ['13001'], ['33062']],
        'epci_code': ['200054781', '200046977', '200054807', '200023384'],
        'met': [100, 50, 70, 80],
        'pop_be': [1000, 500, 700, 800],
        'be_codfap_top': [[ 'F1', 'G2'], ['F1'], ['G2'], ['H3']],
        'codes_formations': [['123'], ['456'], ['123', '456'], []],
        'rp_5+pieces': [10, 20, 15, 12],
        'log_rp': [100, 200, 150, 120],
        'log_soc_inoccupes': [5, 2, 3, 4],
        'log_soc_total': [50, 20, 30, 40],
        'log_vac': [10, 5, 8, 6],
        'log_total': [100, 50, 80, 60],
        'risque_fermeture': [1, 0, 2, 0],
        'ecoles_ct': [10, 5, 15, 8],
        'svc_incl_count': [5, 3, 4, 2],
        'pol_num': [1, 2, 3, 4],
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
        'codgeo': ['75056', '69123', '13055', '33063'],
        'key': [{'cat1_serv1'}, {'cat1_serv2'}, {'cat1_serv1', 'cat1_serv2'}, set()]
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
    result = scoring.compute_odis_score(sample_data, sample_scores_cat, config, sample_incl_index)

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
