import warnings
import os
from dataclasses import dataclass
from typing import List, Dict, Any, Union, Optional

# Suppress annoying warnings from third-party libraries (especially in Python 3.14+)
warnings.filterwarnings("ignore", module="langchain_core.*")



# Get the directory of the current file (app/)
APP_DIR: str = os.path.dirname(os.path.abspath(__file__))
# Get the project root directory (one level up)
PROJECT_ROOT: str = os.path.dirname(APP_DIR)

# Load environment variables from .env if present
from dotenv import load_dotenv
# Try loading from app/.env (Priority)
load_dotenv(os.path.join(APP_DIR, '.env'))
# Try loading from root .env (Fallback/Override depending on behavior, but good to have both)
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

LOCAL_DATA_PATH: str = os.path.join(PROJECT_ROOT, 'data/')
ASSETS_DIR: str = os.path.join(APP_DIR, 'ui', 'assets')

def get_data_path() -> str:
    """
    Returns the appropriate data path based on the environment.
    Since data is now included in the Docker image, we always use the local path.
    """

    return LOCAL_DATA_PATH

# --- Constants ---
VERSION = "0.2.0"

# --- File Paths ---
ODIS_FILE = 'odis_communes.parquet'
POIS_FILE = 'odis_pois.parquet'
REFERENTIELS_FILE = 'odis_referentiels.parquet'
BV_FILE = 'odis_bassins_de_vie.parquet'
AGG_METIERS_FILE = 'odis_metiers_agg.parquet'
AGG_ASSOCIATIONS_FILE = 'odis_associations_agg.parquet'
AGG_FORMATIONS_FILE = 'odis_formations_agg.parquet'
CCAS_FILE = 'odis_ccas.parquet'
ODIS_ASSO_MINI_FILE = 'odis_asso_mini.parquet'
REFUGEE_ASSOCIATIONS_FILE = 'odis_refugee_associations.parquet'
LIVE_JOBS_FILE = 'odis_live_jobs_agg.parquet'
SCORES_CAT_FILE = 'scores_config.yaml'

# Paris, Lyon, Marseille Global Codes -> Arrondissement Prefix
PLM_MAPPING = {
    '75056': '751',
    '69123': '693',
    '13055': '132'
}
# --- Data Columns ---
BV_CODE_COL = 'bassin_de_vie'
BV_NAME_COL = 'libelle_bassin_de_vie'

# --- UI Options ---
NOMBRE_ADULTES_OPTIONS = [1, 2]
NOMBRE_ENFANTS_OPTIONS = [0, 1, 2, 3, 4, 5]
CLASSES_SCOLAIRES = ['Crèche / Assistante Maternelle', 'Maternelle', 'Elémentaire', 'Collège', 'Lycée']
LOC_SEARCH_AREA_OPTIONS = {'departement': 'Département', 'region': 'Région', 'france': 'France Métropolitaine'}
HEBERGEMENT_OPTIONS = ["Chez l'habitant", 'Location', 'Foyer']
LOGEMENT_OPTIONS = ['Location', 'Logement Social']
SANTE_OPTIONS = ["Aucun", "Hopital", 'Maternité', "Soutien Psychologique & Addictologie"]
POIDS_OPTIONS = [0, 25, 50, 75, 100]
HOUSING_TYPE_OPTIONS = {
    "appt_all": "Appartement (Tous types)",
    "appt_t1_t2": "Appartement (T1 & T2)",
    "appt_t3_p": "Appartement (T3+)",
    "house_all": "Maison"
}

# --- Weight Profiles (F-15) ---
WEIGHT_PROFILES = {
    "Équilibré": {
        "poids_emploi": 50, "poids_logement": 50, "poids_education": 50,
        "poids_inclusion": 50, "poids_sante": 50, "poids_mobilité": 50
    },
    "Famille": {
        "poids_emploi": 25, "poids_logement": 100, "poids_education": 100,
        "poids_inclusion": 50, "poids_sante": 50, "poids_mobilité": 25
    },
    "Santé": {
        "poids_emploi": 25, "poids_logement": 50, "poids_education": 25,
        "poids_inclusion": 50, "poids_sante": 100, "poids_mobilité": 25
    },
    "Economique": {
        "poids_emploi": 100, "poids_logement": 25, "poids_education": 25,
        "poids_inclusion": 50, "poids_sante": 25, "poids_mobilité": 25
    }
}

# --- Map Defaults ---
DEFAULT_MAP_CENTER = [46.603354, 1.888334] # Center of France
DEFAULT_MAP_ZOOM = 10
DETAIL_MAP_ZOOM = 11

# --- Constants ---
PROJECTED_CRS = "EPSG:2154"  # RGF93 / Lambert-93, suitable for metropolitan France



# --- Inclusion Defaults ---
DEFAULT_INC_SERVICES_CORE = [
    "logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement",
    "difficultes-administratives-ou-juridiques--accompagnement-aux-demarches-administratives",
    "preparer-sa-candidature--organiser-ses-demarches-de-recherche-demploi"
]
INC_SERVICE_FLE_SLUG = 'lecture-ecriture-calcul--maitriser-le-francais'


# --- Demo Scenarios ---
DEMO_DATA_DEFAULT: Dict[str, Any] = {
    'nom': None,
    'poids_emploi': 50,
    'poids_logement': 50,
    'poids_education': 50,
    'poids_inclusion': 50,
    'poids_mobilité': 50,
    'poids_sante': 50, # Added default weight for sante
    'departement_actuel': '33',
    'commune_actuelle': 'Bordeaux',
    'loc_search_area': 'departement',
    'hebergement': 'Location',
    'logement': 'Location',
    'sante': "Aucun",
    'nb_adultes': 1,
    'nb_enfants': 0,
    'codes_metiers': [],
    'codes_formations': [],
    'classe_enfants': [],
    'inc_services_add_selection': [],
    'inc_services_add_selection': [],
    'inc_services_core_selection': DEFAULT_INC_SERVICES_CORE,
    'inc_asso_add_selection': [],
    'loc_search_code': None,
    'ui_inc_service_fle': False,
    'type_logement': 'appt_all'
}


DEMO_SCENARIOS = {
    "1": {
        'nom': 'Zacharie',
        'departement_actuel': '33',
        'commune_actuelle': 'Bordeaux',
        'loc_search_area': 'departement',
        'hebergement': "Chez l'habitant",
        'nb_adultes': 1,
        'nb_enfants': 0,
        'poids_mobilité': 50,
    },
    "2": {
        'nom': 'Olga & Dimitri',
        'departement_actuel': '75',
        'commune_actuelle': 'Paris',
        'loc_search_area': 'region',
        'hebergement': "Location",
        'logement': "Logement Social",
        'nb_adultes': 2,
        'nb_enfants': 2,
        'codes_metiers': [['H2206'], []],
        'codes_formations': [[], ['331', '330', '326']],
        'classe_enfants': ['Maternelle', 'Elémentaire'],
        'sante': "Maternité",
        'poids_mobilité': 0,
    },
    "3": {
        'nom': 'Aïcha',
        'departement_actuel': '13',
        'commune_actuelle': 'Marseille',
        'loc_search_area': 'departement',
        'hebergement': "Location",
        'logement': "Logement Social",
        'nb_adultes': 1,
        'nb_enfants': 2,
        'codes_metiers': [['K1302']],
        'classe_enfants': ['Crèche / Assistante Maternelle', 'Collège'],
        'inc_services_add_selection': ['lecture-ecriture-calcul--maitriser-le-francais'],
        'poids_mobilité': 50,
        'poids_inclusion': 100,
        'poids_emploi': 100,
        'sante': "Maternité",
        'inc_asso_add_selection': ['Entraide / Bénévolat'],
        'loc_search_code': None
    }
}

WALDEC_CORE_INCLUSION = [
    # --- SOCIAL & ENTRAIDE ---
    # "009",    # ACTION SOCIOCULTURELLE (Tout le niveau 1 : Centres sociaux, MJC...)
    "015070", # Apprentissage de langues, alphabétisation (CRITIQUE)
    # "015095", # Soutien scolaire
    # "019016", # Aide à l'insertion des jeunes
    # "019020", # Aide aux chômeurs
    "019025", # Aide aux réfugiés et aux immigrés (CRITIQUE)
    "020",    # ASSOCIATIONS CARITATIVES, HUMANITAIRES (Tout le niveau 1 : Secours, Alimentaire...)
    "024",    # Entraide et solidarité
    # "021",    # SERVICES FAMILIAUX (Crèches, Halte-garderie...)
    # "014030", # Associations de parents d'élèves (Indicateur de vie scolaire)
    # "014040", # Associations de locataires (Indicateur de vie de quartier)
    # "024020", # Garde d'enfants, crèches parentales
]

# 2. LE DICTIONNAIRE D'AFFINITÉS (Pour le matching "Projet de Vie")
# Clés = Ce que le TS sélectionne dans le multiselect
# Valeurs = Liste des codes WALDEC à scanner
WALDEC_INC_ASSO_ADD_MAPPING = {
    # --- PILIER SPORT ---
    "Sport (Général)": [
        "011000", # Sports, activités de plein air (Général)
        "011010"  # Multisports
    ],
    "Football / Sports Co": [
        "011120", # Football
        "011035", # Basket-ball
        "011065", # Handball
        "011145", # Rugby
        "011190"  # Volley-ball
    ],
    "Sports de Combat": [
        "011015", # Arts martiaux
        "011040", # Boxe
        "011085", # Judo
        "011090"  # Karaté
    ],
    
    # --- PILIER CULTURE & ART ---
    "Arts & Culture": [
        "006"     # CULTURE (Tout le niveau 1 : Théâtre, Musique, Danse...)
    ],
    "Musique / Chant": [
        "006030", # Chant choral, musique
        "006035"  # Groupes folkloriques
    ],
    
    # --- PILIER NATURE & MANUEL ---
    "Jardinage / Nature": [
        "007050", # Jardins ouvriers, floraux, jardins partagés (TOP INCLUSION)
        "023005"  # Amap, distribution produits bio
    ],
    "Bricolage / Création": [
        "009010", # Activités manuelles (couture, poterie...)
        "007025"  # Bricolage
    ],
    
    # --- PILIER COMMUNAUTAIRE ---
    "Lieux de Culte / Spirituel": [
        "028"     # ACTIVITES RELIGIEUSES, SPIRITUELLES (Tout le niveau 1)
    ],
    "Entraide / Bénévolat": [
        "020",    # Caritatif
        "024"     # Entraide et solidarité
    ]
}

def get_relevant_rna_codes() -> List[str]:
    """Retourne une liste plate unique de tous les codes utiles pour l'extraction SQL/Pandas"""
    all_codes = set(WALDEC_CORE_INCLUSION)
    for code_list in WALDEC_INC_ASSO_ADD_MAPPING.values():
        all_codes.update(code_list)
    return list(all_codes)
# 3. WALDEC labels for refugee associations
WALDEC_REFUGEE_LABELS = {
    "003": "Action socio-culturelle",
    "014": "Enseignement, éducation",
    "019": "Action sociale",
    "020": "Associations caritatives, humanitaires"
}
