# /home/jacques/odis/13_odis/eda/streamlit/config.py
from dataclasses import dataclass
from typing import List, Dict, Any, Union
import os

GCS_BUCKET_PATH: str = 'gs://odis-stream2-eu/'
# Get the directory of the current file (app/)
APP_DIR: str = os.path.dirname(os.path.abspath(__file__))
# Get the project root directory (one level up)
PROJECT_ROOT: str = os.path.dirname(APP_DIR)

LOCAL_CSV_PATH: str = os.path.join(PROJECT_ROOT, 'csv/')

def get_data_path() -> str:
    """
    Returns the appropriate data path based on the environment.
    Checks for the K_SERVICE environment variable to detect Cloud Run.
    """
    if 'K_SERVICE' in os.environ:
        return GCS_BUCKET_PATH
    else:
        return LOCAL_CSV_PATH

# --- File Paths ---
ODIS_FILE = 'odis_june_2025_jacques.parquet'
BV_FILENAME = 'insee-bassins-de-vie-2025.csv'
SCORES_CAT_FILE = 'scores_config.yaml'
METIERS_FILE = 'dares_nomenclature_fap2021.csv'
FORMATIONS_FILE = 'index_formations.csv'
ECOLES_FILE = 'annuaire_ecoles_france_mini.parquet'
MATERNITE_FILE = 'annuaire_maternites_DREES.csv'
SANTE_FILE = 'annuaire_sante_finess.parquet'
INCLUSION_FILE = 'odis_services_incl_exploded.parquet'
SNCF_FILE = 'formes-des-lignes-du-rfn.geojson'

# --- Data Columns ---
BV_CODE_COL = 'BV2022'
BV_NAME_COL = 'LIBBV2022'

# --- UI Options ---
VIEW_LEVEL_OPTIONS = ['Bassins de vie', 'Communes']
NOMBRE_ADULTES_OPTIONS = [1, 2]
NOMBRE_ENFANTS_OPTIONS = [0, 1, 2, 3, 4, 5]
CLASSES_SCOLAIRES = ['Maternelle', 'Elémentaire', 'Collège', 'Lycée']
LOC_DISTANCE_OPTIONS = {20: '20 km', 50: '50 km', 'departement': 'Département', 'region': 'Région'}
HEBERGEMENT_OPTIONS = ["Chez l'habitant", 'Location', 'Foyer']
LOGEMENT_OPTIONS = ['Location', 'Logement Social']
SANTE_OPTIONS = ["Aucun", "Hopital", 'Maternité', "Soutien Psychologique & Addictologie"]
POIDS_OPTIONS = [0, 25, 50, 100]
PENALITE_BINOME_OPTIONS = [1, 10, 25, 50, 100]
POP_MIN_OPTIONS = [0, 500, 1000, 5000, 10000]

# --- Map Defaults ---
DEFAULT_MAP_CENTER = [46.603354, 1.888334] # Center of France
DEFAULT_MAP_ZOOM = 10
DETAIL_MAP_ZOOM = 11
DEFAULT_VIEW_LEVEL = 0

# --- Constants ---
PROJECTED_CRS = "EPSG:2154"  # RGF93 / Lambert-93, suitable for metropolitan France

# --- Scoring Configuration ---
@dataclass
class ScoringConfig:
    """
    A dataclass to hold all user preferences and scoring parameters.
    This provides type safety and autocompletion in IDEs.
    """
    # Weights
    poids_emploi: int
    poids_logement: int
    poids_education: int
    poids_inclusion: int
    poids_mobilité: int
    poids_sante: int # Added new weight for sante

    # Location
    commune_actuelle: str
    loc_distance_km: Union[int, str]
    
    # Household
    nb_adultes: int
    nb_enfants: int
    
    # Preferences
    hebergement: str
    logement: str
    codes_metiers: List[List[str]]
    codes_formations: List[List[str]]
    classe_enfants: List[str]
    besoin_sante: str
    besoins_autres: Dict[str, List[str]]
    
    # Technical parameters
    binome_penalty: float
    pop_min: int

# --- Demo Scenarios ---
DEMO_DATA_DEFAULT: Dict[str, Any] = {
    'nom': None,
    'poids_emploi': 100,
    'poids_logement': 100,
    'poids_education': 100,
    'poids_inclusion': 25,
    'poids_mobilité': 100,
    'poids_sante': 100, # Added default weight for sante
    'departement_actuel': '33',
    'commune_actuelle': 'Bordeaux',
    'loc_distance_km': 50,
    'hebergement': 'Location',
    'logement': 'Location',
    'sante': "Aucun",
    'nb_adultes': 1,
    'nb_enfants': 0,
    'codes_metiers': [],
    'codes_formations': [],
    'classe_enfants': [],
    'binome_penalty': 0.5,
    'pop_min': 1000,
    'besoins_autres': {}
}

DEMO_SCENARIOS = {
    "1": {
        'nom': 'Zacharie',
        'departement_actuel': '33',
        'commune_actuelle': 'Bordeaux',
        'loc_distance_km': 50,
        'hebergement': "Chez l'habitant",
        'nb_adultes': 1,
        'nb_enfants': 0,
        'poids_mobilité': 50,
    },
    "2": {
        'nom': 'Olga & Dimitri',
        'departement_actuel': '75',
        'commune_actuelle': 'Paris',
        'loc_distance_km': 'region',
        'hebergement': "Location",
        'logement': "Logement Social",
        'nb_adultes': 2,
        'nb_enfants': 2,
        'codes_metiers': [['B2X37', 'B2X38'], []],
        'codes_formations': [[], ['331', '330', '326']],
        'classe_enfants': ['Maternelle', 'Elémentaire'],
        'sante': "Maternité",
        'poids_mobilité': 0,
    },
    "3": {
        'nom': 'Aïcha',
        'departement_actuel': '13',
        'commune_actuelle': 'Marseille',
        'loc_distance_km': 50,
        'hebergement': "Location",
        'logement': "Logement Social",
        'nb_adultes': 1,
        'nb_enfants': 2,
        'codes_metiers': [['T2A60']],
        'classe_enfants': ['Elémentaire', 'Collège'],
        'besoins_autres': {'apprendre-francais': ['-']},
        'poids_mobilité': 50,
        'poids_inclusion': 50,
        'poids_emploi': 100,
    }
}