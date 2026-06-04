import warnings
import os
from dataclasses import dataclass
from typing import List, Dict, Any, Union, Optional, TypedDict, Literal

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
AGG_ASSOCIATIONS_FILE = 'odis_associations_agg.parquet'
AGG_FORMATIONS_FILE = 'odis_formations_agg.parquet'
CCAS_FILE = 'odis_ccas.parquet'
REFUGEE_ASSOCIATIONS_FILE = 'odis_refugee_associations.parquet'
LIVE_JOBS_FILE = 'odis_ft_jobs_agg.parquet'
SIAE_JOBS_FILE = 'odis_inclusion_jobs.parquet'
SIAE_STRUCTURES_FILE = 'odis_inclusion_structures.parquet'
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
HEBERGEMENT_OPTIONS = [
    "Location avec Intermédiation",
    "Centres d'Hébergement (CHRS, CPH)",
    "Foyers & Pensions de Famille",
    "Chez l'habitant"
]
LOGEMENT_OPTIONS = ['Location', 'Logement Social']
SANTE_OPTIONS = ["Aucun", "Hopital", 'Maternité', "Soutien Psychologique & Addictologie"]
POIDS_OPTIONS = [0.0, 0.25, 0.5, 0.75, 1.0]
HOUSING_TYPE_OPTIONS = {
    "appt_all": "Appartement (Tous types)",
    "appt_t1_t2": "Appartement (T1 & T2)",
    "appt_t3_p": "Appartement (T3+)",
    "house_all": "Maison"
}

# --- Population Target Options (F-50) ---
POPULATION_TARGET_OPTIONS = [5000, 10000, 20000, 50000, 100000, 150000, 200000]
CITY_SIZE_MAPPING = {
    "🚜 Commune rurale": {"mu": 5000, "sigma": 2500},
    "🏡 Bourg": {"mu": 20000, "sigma": 10000},
    "🏘️ Petite Ville": {"mu": 50000, "sigma": 25000},
    "🏙️ Ville moyenne": {"mu": 150000, "sigma": 75000}
}
DEFAULT_MU = 50000
DEFAULT_SIGMA = 25000

# --- Weight Profiles (F-15) ---
WEIGHT_PROFILES = {
    "Équilibré": {
        "poids_emploi": 0.5, "poids_logement": 0.5, "poids_education": 0.5,
        "poids_inclusion": 0.5, "poids_sante": 0.5, "poids_mobilite": 0.5, "poids_territoire": 1.0
    },
    "Famille": {
        "poids_emploi": 0.25, "poids_logement": 1.0, "poids_education": 1.0,
        "poids_inclusion": 0.25, "poids_sante": 0.5, "poids_mobilite": 0.5, "poids_territoire": 1.0
    },
    "Santé": {
        "poids_emploi": 0.25, "poids_logement": 0.75, "poids_education": 0.0,
        "poids_inclusion": 0.75, "poids_sante": 1.0, "poids_mobilite": 0.75, "poids_territoire": 1.0
    },
    "Économique": {
        "poids_emploi": 1.0, "poids_logement": 0.75, "poids_education": 0.25,
        "poids_inclusion": 0.5, "poids_sante": 0.25, "poids_mobilite": 0.25, "poids_territoire": 1.0
    }
}

# --- Organization Profiles (F-54) ---
class OrgProfile(TypedDict):
    name: str
    description: str
    zone_type: Literal["departement", "bassin_de_vie"]
    default_zones: List[str]
    defaults: Dict[str, Any]

ORGANIZATION_PROFILES: Dict[str, OrgProfile] = {
    "jaccueille": {
        "name": "J'Accueille",
        "description": "J'Accueille est un programme de cohabitation solidaire qui met en relation des personnes réfugiées à la recherche d'un logement et des particuliers disposant d'une chambre libre.",
        "zone_type": "departement",
        "default_zones": [
            "01", "13", "22", "26", "30", "31", "33", "34", "35", "37", 
            "38", "40", "42", "44", "64", "69", "72", "75", "76", "77", 
            "78", "81", "91", "92", "93", "94", "95"
        ],
        "defaults": {
            "hebergement_cible": ["Chez l'habitant"],
            "org_boosts": {
                "heb_jaccueille_score": 3.0
            }
        }
    },
    "emile_aura": {
        "name": "EMILE Auvergne-Rhône-Alpes",
        "description": "EMILE est un programme d’accompagnement renforcé à la mobilité géographique qui permet aux personnes en précarité de logement, volontaires et résidant en zones tendues, d’accéder à l’emploi et au logement dans un nouveau territoire d’accueil.",
        "zone_type": "departement",
        "default_zones": [
            "01", "03","15","69"
        ],
        "defaults": {
            "org_boosts": {
                "inc_siae_density_scaled": 3.0
            }
        }
    }
}

# --- Map Defaults ---
DEFAULT_MAP_CENTER = [46.603354, 1.888334] # Center of France
DEFAULT_MAP_ZOOM = 10
DETAIL_MAP_ZOOM = 11

# --- Constants ---
MAX_MAP_POLYGONS = 5000
PROJECTED_CRS = "EPSG:2154"  # RGF93 / Lambert-93, suitable for metropolitan France

# --- Auth ---
# Note: ALLOWED_AUTH_DOMAINS is now managed in .streamlit/secrets.toml



# --- Inclusion Defaults ---
DEFAULT_INC_SERVICES_CORE = [
    "logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement",
    "difficultes-administratives-ou-juridiques--accompagnement-aux-demarches-administratives",
    "preparer-sa-candidature--organiser-ses-demarches-de-recherche-demploi"
]
INC_SERVICES_CHECKBOX_MAPPING = {
    "lecture-ecriture-calcul--maitriser-le-francais": "Apprendre le français (FLE)",
    "difficultes-administratives-ou-juridiques--accompagnement-pour-lacces-aux-droits": "Accompagnement aux droits",
    "difficultes-administratives-ou-juridiques--accompagnement-aux-demarches-administratives": "Accompagnement démarches administratives",
    "numerique--acceder-a-des-services-en-ligne": "Accéder aux services numériques",
    "preparer-sa-candidature--organiser-ses-demarches-de-recherche-demploi": "Accompagnement emploi",
    "logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement": "Démarches accès au logement",
    "logement-hebergement--rechercher-une-solution-dhebergement-temporaire": "Recherche d'un hébergement temporaire",
    "famille--garde-denfants": "Garde d'enfants",
    "equipement-et-alimentation--alimentation": "Aide alimentaire",
    "difficultes-financieres--acquerir-une-autonomie-budgetaire": "Autonomie budgétaire",
    "mobilite--acceder-a-un-vehicule": "Accès à un véhicule",
}


# --- Demo Scenarios ---
DEMO_DATA_DEFAULT: Dict[str, Any] = {
    'nom': None,
    'poids_emploi': 0.5,
    'poids_logement': 0.5,
    'poids_education': 0.5,
    'poids_inclusion': 0.5,
    'poids_mobilite': 0.5,
    'poids_sante': 0.5,
    'poids_territoire': 1.0,
    'departement_actuel': '33',
    'commune_actuelle': 'Bordeaux',
    'loc_search_area': 'departement',
    'hebergement_cible': ["Location avec Intermédiation"],
    'logement': 'Location',
    'sante': "Aucun",
    'nb_adultes': 1,
    'nb_enfants': 0,
    'codes_metiers': [],
    'codes_formations': [],
    'classe_enfants': [],
    'inc_services_selection': DEFAULT_INC_SERVICES_CORE,
    'inc_asso_add_selection': [],
    'loc_search_code': [],
    'ui_inc_service_fle': False,
    'type_logement': 'appt_all',
    'weight_profile': 'Équilibré',
    'besoin_sante': "Aucun",
    'notes_qualitatives': "",
    'freq_retour': '1 fois/mois',
    'target_population': DEFAULT_MU,
    'target_population_sigma': DEFAULT_SIGMA,
    'org_context': None,
    'org_strategic_locations': [],
    'org_strategic_locations_type': 'departement',
}


DEMO_SCENARIOS = {
    "1": {
        'nom': 'Zacharie',
        'departement_actuel': '33',
        'commune_actuelle': 'Bordeaux',
        'loc_search_area': 'departement',
        'hebergement_cible': ["Chez l'habitant"],
        'nb_adultes': 1,
        'nb_enfants': 0,
        'weight_profile': 'Équilibré',
        'notes_qualitatives': "Zacharie est un jeune homme dynamique."
    },
    "2": {
        'nom': 'Olga & Dimitri',
        'departement_actuel': '75',
        'commune_actuelle': 'Paris',
        'loc_search_area': 'region',
        'loc_search_code': ['75'],
        'hebergement_cible': ["Location avec Intermédiation"],
        'logement': "Logement Social",
        'nb_adultes': 2,
        'nb_enfants': 2,
        'codes_metiers': [['H2206'], []],
        'codes_formations': [[], ['331', '330', '326']],
        'classe_enfants': ['Maternelle', 'Elémentaire'],
        'besoin_sante': "Maternité",
        'poids_mobilite': 0.0,
        'weight_profile': 'Équilibré',
        'notes_qualitatives': "Olga et Dimitri cherchent un environnement calme pour leurs enfants."
    },
    "3": {
        'nb_adultes': 1,
        'nb_enfants': 2,
        'commune_actuelle': 'Marseille',
        'hebergement_cible': ["Location avec Intermédiation", "Chez l'habitant"],
        'logement': "Logement Social",
        'departement_actuel': '13',
        'freq_retour': '1 fois/an',
        'loc_search_area': 'departement',
        'loc_search_code': ['13'],
        'codes_metiers': [["M1607", "M1602"]],
        'weight_profile': 'Famille',
        'besoin_sante': "Maternité",
        'inc_services_selection': DEFAULT_INC_SERVICES_CORE + ['lecture-ecriture-calcul--maitriser-le-francais'],
        'inc_asso_add_selection': ['006030'],
        'notes_qualitatives': "Aïcha souhaite passer son permis et cherche une boucherie Hallal à proximité"
    },
    "agir": {
        'nb_adultes': 1,
        'nb_enfants': 2,
        'codes_metiers': [["I1604"]],
        'commune_actuelle': 'Bordeaux',
        'departement_actuel': '33',
        'freq_retour': '1 fois/an',
        'loc_search_area': 'departement',
        'loc_search_code': ['17', '33', '40'],
        'hebergement_cible': ["Location avec Intermédiation"],
        'logement': 'Location',
        'type_logement': 'appt_all',
        'classe_enfants': ['Elémentaire'],
        'inc_services_selection': DEFAULT_INC_SERVICES_CORE + ['lecture-ecriture-calcul--maitriser-le-francais'],
        'inc_asso_add_selection': ['011075'],
        'weight_profile': 'Economique',
        'poids_emploi': 1.0,
        'poids_logement': 0.75,
        'poids_education': 0.5,
        'poids_inclusion': 0.5,
        'poids_sante': 0.25,
        'poids_mobilite': 0.25,
        'besoin_sante': "Soutien Psychologique & Addictologie",
        'notes_qualitatives': "Proximité d'une Mosquée et de la mer",
        'target_population': 20000,
        'target_population_sigma': 10000
    },
        "emile": {
        'nb_adultes': 1,
        'nb_enfants': 2,
        'codes_metiers': [["I1604"]],
        'commune_actuelle': 'Lyon',
        'departement_actuel': '69',
        'freq_retour': '1 fois/an',
        'loc_search_area': 'departement',
        'loc_search_code': ['15', '03', '01', '69'],
        'hebergement_cible': ["Location avec Intermédiation"],
        'logement': 'Logement Social',
        'type_logement': 'appt_all',
        'classe_enfants': ['Maternelle', 'Elémentaire'],
        'inc_services_selection': DEFAULT_INC_SERVICES_CORE + ['lecture-ecriture-calcul--maitriser-le-francais'],
        'inc_asso_add_selection': ['011075'],
        'weight_profile': 'Profil personnalisé',
        'poids_emploi': 1.0,
        'poids_logement': 1.0,
        'poids_education': 0.5,
        'poids_inclusion': 0.5,
        'poids_sante': 0.25,
        'poids_mobilite': 0.25,
        'besoin_sante': "Soutien Psychologique & Addictologie",
        'notes_qualitatives': "Peuvent se déplacer à vélo. Aiment les balades en montagne.",
        'target_population': 20000,
        'target_population_sigma': 10000
    }

}


# --- LOISIRS CATÉGORIES (WALDEC PREFIXES) ---
# Seuls les codes commençant par ces 3 digits sont conservés pour la sélection "Loisirs"
WALDEC_CATEGORIES = ["003", "006", "007", "009", "011", "013", "014", "019", "020", "021", "024"]

def get_relevant_rna_codes() -> List[str]:
    """Retourne les préfixes de catégories WALDEC utiles pour le filtrage"""
    return WALDEC_CATEGORIES
# 3. WALDEC labels for refugee associations
WALDEC_REFUGEE_LABELS = {
    "003": "Action socio-culturelle",
    "014": "Enseignement, éducation",
    "019": "Action sociale",
    "020": "Associations caritatives, humanitaires"
}
