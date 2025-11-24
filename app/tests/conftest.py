import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
from app.config import ScoringConfig
import copy

def pytest_addoption(parser):
    parser.addoption(
        "--update-snapshots", action="store_true", default=False, help="Update snapshot files."
    )

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
        # New education counts
        'count_maternelle': [5, 2, 3, 1, 0],
        'count_elementaire': [4, 3, 2, 1, 0],
        'count_college': [3, 1, 2, 0, 0],
        'count_lycee': [2, 1, 1, 0, 0],
        # New health counts
        'count_hopital': [2, 1, 1, 0, 0],
        'count_psy': [1, 0, 1, 0, 0],
        'count_maternite': [1, 0, 0, 0, 0],
    }
    gdf = gpd.GeoDataFrame(data, crs="EPSG:4326")
    gdf = gdf.set_index('codgeo')
    return gdf.copy()

@pytest.fixture
def sample_scores_cat():
    """Creates a sample scores_cat DataFrame for testing."""
    data = {
        'score': [
            'met_scaled', 'met_tension_scaled', 'svc_incl_scaled',
            'log_vac_scaled', 'log_soc_inoc_scaled', 'log_5p_scaled',
            'classes_ferm_scaled', 'pol_scaled', 'population_scaled',
            'met_match_adult1_scaled', 'met_match_adult2_scaled',
            'form_match_adult1_scaled', 'form_match_adult2_scaled',
            'reloc_dist_scaled', 'reloc_epci_scaled',
            'edu_structures_scaled', 'sante_structures_scaled',
            'besoins_match_scaled'
        ],
        'cat': [
            'emploi', 'emploi', 'inclusion',
            'logement', 'logement', 'logement',
            'education', 'inclusion', 'inclusion',
            'emploi', 'emploi',
            'emploi', 'emploi',
            'mobilité', 'mobilité',
            'education', 'santé',
            'inclusion'
        ],
        'metric': [
            'met_ratio', 'met_tension_ratio', 'svc_incl_ratio',
            'log_vac_ratio', 'log_soc_inoc_ratio', 'log_5p_ratio',
            'risque_fermeture_ratio', 'pol_num', 'population',
            'met_match_adult1', 'met_match_adult2',
            'form_match_adult1', 'form_match_adult2',
            'dist_current_loc', 'epci_code',
            'edu_structures_count', 'sante_structures_scaled',
            'besoins_match'
        ],
        'incl_binome': [
            True, True, True,
            True, True, True,
            True, False, False,
            True, True,
            True, True,
            False, False,
            True, True,
            False
        ]
    }
    return pd.DataFrame(data).copy()

@pytest.fixture
def sample_incl_index():
    """Creates a sample incl_index DataFrame for testing."""
    data = {
        'codgeo': ['75056', '69123', '13055', '33063', '64445'],
        'key': [{'cat1_serv1'}, {'cat1_serv2'}, {'cat1_serv1', 'cat1_serv2'}, set(), set()]
    }
    df = pd.DataFrame(data)
    df = df.set_index('codgeo')
    return df.copy()

@pytest.fixture
def default_config():
    """Returns a default ScoringConfig for testing."""
    return ScoringConfig(
        poids_emploi=100,
        poids_logement=100,
        poids_education=100,
        poids_inclusion=25,
        poids_sante=100, # Added for tests
        poids_mobilité=100,
        commune_actuelle='33063', # Bordeaux
        loc_distance_km=50,
        nb_adultes=1,
        nb_enfants=0,
        hebergement='Location',
        logement='Location',
        codes_metiers=[[]], # Ensure at least one empty list for adult 1
        codes_formations=[[]], # Ensure at least one empty list for adult 1
        classe_enfants=[],
        besoin_sante='Aucun',
        besoins_autres={},
        binome_penalty=0.5,
        pop_min=1000
    )
