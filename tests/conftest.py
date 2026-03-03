import sys
import os
import pytest

# Add app directory to sys.path to support app-rooted imports during tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

import warnings
import pandas as pd
import geopandas as gpd

# Suppress annoying third-party deprecation warnings
warnings.filterwarnings("ignore", category=DeprecationWarning, module="google.genai")
warnings.filterwarnings("ignore", category=DeprecationWarning, module="google.genai.types")
from shapely.geometry import Polygon
import config as cfg
from core.models import ScoringConfig
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
        'bassin_de_vie': ['1', '2', '3', '4', '4'], # Added for BdV scoring tests
        'population': [2148271, 513275, 861635, 257068, 77130],
        'geometry': [
            Polygon([(2.224, 48.816), (2.469, 48.816), (2.469, 48.902), (2.224, 48.902)]),
            Polygon([(4.8, 45.7), (4.9, 45.7), (4.9, 45.8), (4.8, 45.8)]),
            Polygon([(5.3, 43.2), (5.4, 43.2), (5.4, 43.3), (5.3, 43.3)]),
            Polygon([(-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838)]),
            Polygon([(-0.333, 43.3), (-0.333, 43.3), (-0.333, 43.3), (-0.333, 43.3)])
        ],
        'epci_code': ['200054781', '200046977', '200054807', '200023384', '246401722'],
        'met': [100, 50, 70, 80, 60],
        'pop_be': [1000, 500, 700, 800, 400],
        'be_codfap_top': [[ 'F1', 'G2'], ['F1'], ['G2'], ['H3'], ['H3']],
        'codes_formations': [['123'], ['456'], ['123', '456'], [], []],
        'rp_5+pieces': [10, 20, 15, 12, 10],
        'log_rp': [100, 200, 150, 120, 90],
        'log_vac': [10, 5, 8, 6, 4],
        # New vacant housing metric
        'pp_vacant_plus_2ans_25': [0.05, 0.02, 0.03, 0.04, 0.01],
        # New inclusion metrics
        'lien_social_count': [10, 5, 8, 6, 4],
        'lien_social_density': [5.0, 2.5, 4.0, 3.0, 2.0],
        # New housing rent metrics
        'loyer_m2_moy_appartement_toutes': [15.0, 20.0, 18.0, 16.0, 12.0],
        'loyer_m2_moy_appartement_t1_t2': [16.0, 21.0, 19.0, 17.0, 13.0],
        'loyer_m2_moy_appartement_t3_plus': [14.0, 19.0, 17.0, 15.0, 11.0],
        'loyer_m2_moy_maison_toutes': [12.0, 15.0, 14.0, 13.0, 10.0],
        'log_loyer_moyen_scaled_appartement_toutes': [0.5, 0.2, 0.3, 0.4, 0.8],
        'log_loyer_moyen_scaled_appartement_t1_t2': [0.5, 0.2, 0.3, 0.4, 0.8],
        'log_loyer_moyen_scaled_appartement_t3_plus': [0.5, 0.2, 0.3, 0.4, 0.8],
        'log_loyer_moyen_scaled_maison_toutes': [0.5, 0.2, 0.3, 0.4, 0.8],
    }
    gdf = gpd.GeoDataFrame(data, crs="EPSG:4326")
    
    # Project to EPSG:2154 (Lambert-93) as per new pipeline standard
    gdf = gdf.to_crs(cfg.PROJECTED_CRS)
    
    # Add centroid column (as build.py does)
    gdf['centroid'] = gdf.geometry.centroid
    
    gdf = gdf.set_index('codgeo')
    return gdf.copy()

@pytest.fixture
def sample_scores_cat():
    """Creates a sample scores_cat DataFrame for testing."""
    data = {
        'score': [
            'met_match_adult1_scaled', 'met_match_adult1_bdv_scaled', 'met_match_adult1_tension_scaled',
            'met_match_adult2_scaled', 'met_match_adult2_bdv_scaled', 'met_match_adult2_tension_scaled',
            'inc_services_core_scaled', 'inc_asso_core_scaled', 'inc_asso_add_scaled',
            'log_vac_scaled', 'log_soc_inoc_scaled', 'log_5p_scaled',
            'edu_classes_ferm_scaled', 'inc_pol_scaled', 'inc_population_scaled',
            'form_match_adult1_scaled', 'form_match_adult1_bdv_scaled',
            'form_match_adult2_scaled', 'form_match_adult2_bdv_scaled',
            'mob_dist_scaled', 'mob_epci_scaled',
            'edu_structures_scaled', 'sante_structures_scaled',
            'besoins_match_scaled',
            'log_loyer_moyen_scaled_appartement_toutes',
            'log_loyer_moyen_scaled_appartement_t1_t2',
            'log_loyer_moyen_scaled_appartement_t3_plus',
            'log_loyer_moyen_scaled_maison_toutes'
        ],
        'cat': [
            'emploi', 'emploi', 'emploi',
            'emploi', 'emploi', 'emploi',
            'inclusion', 'inclusion', 'inclusion',
            'logement', 'logement', 'logement',
            'education', 'inclusion', 'inclusion',
            'emploi', 'emploi',
            'emploi', 'emploi',
            'mobilité', 'mobilité',
            'education', 'santé',
            'inclusion',
            'logement', 'logement', 'logement', 'logement'
        ],
        'metric': [
            'met_match_adult1', 'met_match_adult1_bdv', 'met_match_adult1_tension',
            'met_match_adult2', 'met_match_adult2_bdv', 'met_match_adult2_tension',
            'socle_match_count', 'lien_social_density', 'affinite_density',
            'log_vac_ratio', 'log_soc_inoc_ratio', 'log_5p_ratio',
            'risque_fermeture_ratio', 'pol_num', 'population',
            'form_match_adult1', 'form_match_adult1_bdv',
            'form_match_adult2', 'form_match_adult2_bdv',
            'dist_current_loc', 'epci_code',
            'edu_structures_count', 'sante_structures_scaled',
            'besoins_match',
            'loyer_m2_moy_appartement_toutes',
            'loyer_m2_moy_appartement_t1_t2',
            'loyer_m2_moy_appartement_t3_plus',
            'loyer_m2_moy_maison_toutes'
        ],
        'incl_binome': [True] * 28, # Simplify for tests
        'weight': [1.0] * 28,
        'min_bound': [0.0] * 28,
        'max_bound': [1.0] * 28
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
        criteria_weights={}, # Added for F-15
        weight_profile="Équilibré",
        commune_actuelle='33063', # Bordeaux
        loc_search_area='departement',
        loc_search_code=None,
        nb_adultes=1,
        nb_enfants=0,
        hebergement_cible=[],
        logement='Location',
        codes_metiers=[[]], # Ensure at least one empty list for adult 1
        codes_formations=[[]], # Ensure at least one empty list for adult 1
        classe_enfants=[],
        besoin_sante='Aucun',
        inc_services_add_selection=[],
        inc_services_core_selection=[],
        inc_asso_add_selection=[],
        type_logement="appartement_toutes"
    )

@pytest.fixture
def global_stats():
    """Returns sample global stats for testing."""
    return {
        'met_scaled': {'min': 0.0, 'max': 100.0},
        'log_vac_scaled': {'min': 0.0, 'max': 0.2},
        'log_soc_inoc_scaled': {'min': 0.0, 'max': 0.1},
        'log_5p_scaled': {'min': 0.0, 'max': 0.5},
        'edu_classes_ferm_scaled': {'min': 0.0, 'max': 0.1},
        'inc_asso_core_scaled': {'min': 0.0, 'max': 10.0},
        'inc_asso_add_scaled': {'min': 0.0, 'max': 10.0},
        'inc_population_scaled': {'min': 0.0, 'max': 100000.0},
        'edu_petite_enfance_scaled': {'min': 0.0, 'max': 100.0},
    }
