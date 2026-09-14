import logging
import os
import tomllib
import warnings
from typing import Any, Dict, List, Optional, Set, Literal
from pydantic import BaseModel, Field, ConfigDict

logger = logging.getLogger(__name__)

# Suppress annoying warnings from third-party libraries (especially in Python 3.14+)
warnings.filterwarnings("ignore", module="langchain_core.*")


# Get the directory of the current file (app/)
APP_DIR: str = os.path.dirname(os.path.abspath(__file__))
# Get the project root directory (one level up)
PROJECT_ROOT: str = os.path.dirname(APP_DIR)

# Load environment variables from .env if present
from dotenv import load_dotenv

# Try loading from app/.env (Priority)
load_dotenv(os.path.join(APP_DIR, ".env"))
# Try loading from root .env (Fallback/Override depending on behavior, but good to have both)
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

ASSETS_DIR: str = os.path.join(APP_DIR, "ui", "assets")


# --- Constants ---
VERSION = "0.2.0"
ODIS_APP_VERSION: str = os.getenv("ODIS_APP_VERSION", VERSION)
ODIS_DEPLOYMENT_ENV: str = os.getenv("ODIS_DEPLOYMENT_ENV", "local")

# --- File Paths ---
ODIS_FILE = "odis_communes.parquet"
POIS_FILE = "odis_pois.parquet"
REFERENTIELS_FILE = "odis_referentiels.parquet"
BV_FILE = "odis_bassins_de_vie.parquet"
AGG_ASSOCIATIONS_FILE = "odis_associations_agg.parquet"
AGG_FORMATIONS_FILE = "odis_formations_agg.parquet"
CCAS_FILE = "odis_ccas.parquet"
REFUGEE_ASSOCIATIONS_FILE = "odis_refugee_associations.parquet"
LIVE_JOBS_FILE = "odis_ft_jobs_agg.parquet"
SIAE_JOBS_FILE = "odis_inclusion_jobs.parquet"
SIAE_STRUCTURES_FILE = "odis_inclusion_structures.parquet"
SALESFORCE_JACCUEILLE_BDV_FILE = "salesforce_jaccueille_bdv.parquet"
SCORES_CAT_FILE = "scores_config.yaml"

# Paris, Lyon, Marseille Global Codes -> Arrondissement Prefix
PLM_MAPPING = {"75056": "751", "69123": "693", "13055": "132"}
# Salesforce Reports
SF_REPORT_ACCUEILLANTS_URL: str = os.getenv(
    "SF_REPORT_ACCUEILLANTS_URL",
    "https://jaccueille.lightning.force.com/lightning/r/Report/00OJv00000EHteLMAT/view",
)
SF_REPORT_PROSPECTS_URL: str = os.getenv(
    "SF_REPORT_PROSPECTS_URL",
    "https://jaccueille.lightning.force.com/lightning/r/Report/00OJv00000EHuqXMAT/view",
)

# --- Data Columns ---
BV_CODE_COL = "bassin_de_vie"
BV_NAME_COL = "libelle_bassin_de_vie"


# --- UI Options ---
NOMBRE_ADULTES_OPTIONS = [1, 2]
NOMBRE_ENFANTS_OPTIONS = [0, 1, 2, 3, 4, 5]
CLASSES_SCOLAIRES = [
    "Crèche / Assistante Maternelle",
    "Maternelle",
    "Elémentaire",
    "Collège",
    "Lycée",
]
LOC_SEARCH_AREA_OPTIONS = {
    "departement": "Département",
    "region": "Région",
    "france": "France Métropolitaine",
}

# --- Metropolitan Geographic Referentials (INSEE COG) ---
# 13 Metropolitan Regions (excluding DROM 01, 02, 03, 04, 06)
METROPOLITAN_REGION_CODES: List[str] = [
    "11",  # Île-de-France
    "24",  # Centre-Val de Loire
    "27",  # Bourgogne-Franche-Comté
    "28",  # Normandie
    "32",  # Hauts-de-France
    "44",  # Grand Est
    "52",  # Pays de la Loire
    "53",  # Bretagne
    "75",  # Nouvelle-Aquitaine
    "76",  # Occitanie
    "84",  # Auvergne-Rhône-Alpes
    "93",  # Provence-Alpes-Côte d'Azur
    "94",  # Corse
]

# 96 Metropolitan Departments (01-19, 2A, 2B, 21-95; excluding DROM 971-976)
METROPOLITAN_DEPT_CODES: List[str] = [
    "01",
    "02",
    "03",
    "04",
    "05",
    "06",
    "07",
    "08",
    "09",
    "10",
    "11",
    "12",
    "13",
    "14",
    "15",
    "16",
    "17",
    "18",
    "19",
    "2A",
    "2B",
    "21",
    "22",
    "23",
    "24",
    "25",
    "26",
    "27",
    "28",
    "29",
    "30",
    "31",
    "32",
    "33",
    "34",
    "35",
    "36",
    "37",
    "38",
    "39",
    "40",
    "41",
    "42",
    "43",
    "44",
    "45",
    "46",
    "47",
    "48",
    "49",
    "50",
    "51",
    "52",
    "53",
    "54",
    "55",
    "56",
    "57",
    "58",
    "59",
    "60",
    "61",
    "62",
    "63",
    "64",
    "65",
    "66",
    "67",
    "68",
    "69",
    "70",
    "71",
    "72",
    "73",
    "74",
    "75",
    "76",
    "77",
    "78",
    "79",
    "80",
    "81",
    "82",
    "83",
    "84",
    "85",
    "86",
    "87",
    "88",
    "89",
    "90",
    "91",
    "92",
    "93",
    "94",
    "95",
]

METROPOLITAN_REGION_CODES_SET: Set[str] = set(METROPOLITAN_REGION_CODES)
METROPOLITAN_DEPT_CODES_SET: Set[str] = set(METROPOLITAN_DEPT_CODES)

HEBERGEMENT_OPTIONS = [
    "Location avec Intermédiation",
    "Centre d'hébergement et de réinsertion sociale (CHRS)",
    # "Centre provisoire d'hébergement (CPH)",
    # "Centre d'accueil de demandeurs d'asile (CADA)",
    "Foyer de Jeunes Travailleurs (FJT)",
    "Pensions de Famille",
    "Chez l'habitant",
]
LOGEMENT_OPTIONS = ["Location", "Logement Social"]
LOGEMENT_LABELS: Dict[str, str] = {
    "Location": "Location parc privé",
    "Logement Social": "Logement Social",
}
HEBERGEMENT_LABELS: Dict[str, str] = {opt: opt for opt in HEBERGEMENT_OPTIONS}
SANTE_OPTIONS = [
    "Hôpital",
    "Maternité",
    "Soutien Psychologique",
    "Dialyse",
    # "Maison de santé",
    "Addictologie",
    "Santé maternelle et infantile (PMI)",
]
SANTE_LABELS: Dict[str, str] = {
    "Hôpital": "Suivi à l'hôpital",
    "Maternité": "Suivi dans une maternité",
    "Soutien Psychologique": "Soutien Psychologique",
    "Dialyse": "Dialyse",
    # "Maison de santé": "Maison de santé",
    "Addictologie": "Addictologie",
    "Santé maternelle et infantile (PMI)": "Santé maternelle et infantile (PMI)",
}
POIDS_OPTIONS = [0.0, 0.25, 0.5, 0.75, 1.0]
HOUSING_TYPE_OPTIONS = {
    "appt_all": "Appartement (Tous types)",
    "appt_t1_t2": "Appartement (T1 & T2)",
    "appt_t3_p": "Appartement (T3+)",
    "house_all": "Maison",
}

# --- Bassin de Vie Demographic Sizing (Trapezoidal Membership) ---
CITY_SIZE_MAPPING = {
    "🚜 Commune rurale": {"a": 0, "b": 500, "c": 3000, "d": 8000},
    "🏡 Bourg": {"a": 2000, "b": 5000, "c": 15000, "d": 30000},
    "🏘️ Petite Ville": {"a": 10000, "b": 20000, "c": 50000, "d": 100000},
    "🏙️ Ville moyenne": {"a": 35000, "b": 70000, "c": 300000, "d": 800000},
}
DEFAULT_CITY_SIZE = "🏘️ Petite Ville"
DEFAULT_TRAPEZOID = CITY_SIZE_MAPPING[DEFAULT_CITY_SIZE]
TARGET_CITY_SIZE_OPTIONS = list(CITY_SIZE_MAPPING.keys())
DEMOGRAPHIC_MIN_FLOOR = 0.30

# --- Weight Profiles (F-15) ---
WEIGHT_PROFILES = {
    "Équilibré": {
        "poids_emploi": 0.5,
        "poids_logement": 0.5,
        "poids_education": 0.5,
        "poids_inclusion": 0.5,
        "poids_sante": 0.5,
        "poids_mobilite": 0.5,
        "poids_territoire": 1.0,
    },
    "Famille": {
        "poids_emploi": 0.25,
        "poids_logement": 1.0,
        "poids_education": 1.0,
        "poids_inclusion": 0.25,
        "poids_sante": 0.5,
        "poids_mobilite": 0.5,
        "poids_territoire": 1.0,
    },
    "Santé": {
        "poids_emploi": 0.25,
        "poids_logement": 0.75,
        "poids_education": 0.0,
        "poids_inclusion": 0.75,
        "poids_sante": 1.0,
        "poids_mobilite": 0.75,
        "poids_territoire": 1.0,
    },
    "Économique": {
        "poids_emploi": 1.0,
        "poids_logement": 0.75,
        "poids_education": 0.25,
        "poids_inclusion": 0.5,
        "poids_sante": 0.25,
        "poids_mobilite": 0.25,
        "poids_territoire": 1.0,
    },
}


# --- Organization Model & Profiles (F-54) ---
class Org(BaseModel):
    """Represents an organization profile with its default configurations and settings."""

    id: str
    name: str
    description: Optional[str] = None
    zone_type: Literal["departement", "bassin_de_vie"] = "departement"
    default_zones: List[str] = Field(default_factory=list)
    defaults: Dict[str, Any] = Field(default_factory=dict)
    ai_free_mode: bool = False
    enable_interactive_chat: bool = False
    zones_user_description: Optional[str] = Field(
        default=None,
        description="Texte explicatif destiné à l'utilisateur détaillant le périmètre ou les restrictions géographiques de l'organisation.",
    )

    model_config = ConfigDict(populate_by_name=True, revalidate_instances="never")


class User(BaseModel):
    """Represents a logged-in user and their optional organization profile."""

    username: str
    org_id: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, revalidate_instances="never")


def _load_secrets_toml() -> Dict[str, Any]:
    """Loads secrets.toml using standard library tomllib."""
    candidates = [
        os.path.join(APP_DIR, ".streamlit", "secrets.toml"),
        os.path.join(PROJECT_ROOT, ".streamlit", "secrets.toml"),
        "/app/.streamlit/secrets.toml",
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                with open(p, "rb") as f:
                    return tomllib.load(f)
            except Exception as e:
                logger.warning("Error reading secrets from %s: %s", p, e)
    return {}


def load_organization_profiles() -> Dict[str, Org]:
    """Loads organization profiles from secrets.toml.

    Returns an empty dict if no organizations are configured in secrets.
    """
    raw_secrets = _load_secrets_toml()
    raw_orgs = raw_secrets.get("organizations", {})
    if not raw_orgs:
        return {}

    try:
        if hasattr(raw_orgs, "to_dict"):
            raw_orgs = raw_orgs.to_dict()
        elif not isinstance(raw_orgs, dict):
            raw_orgs = dict(raw_orgs)

        profiles: Dict[str, Org] = {}
        for org_id, org_data in raw_orgs.items():
            if isinstance(org_data, dict) or hasattr(org_data, "items"):
                data = dict(org_data)
                data.setdefault("id", org_id)
                profiles[org_id] = Org(**data)
        return profiles
    except Exception as exc:
        logger.error(
            "Failed to parse organization profiles from secrets: %s", exc, exc_info=True
        )
        return {}


ORGANIZATION_PROFILES: Dict[str, Org] = load_organization_profiles()

# --- Map Defaults ---
DEFAULT_MAP_CENTER = [46.603354, 1.888334]  # Center of France
DEFAULT_MAP_ZOOM = 10
DETAIL_MAP_ZOOM = 11

# --- Constants ---
MAX_MAP_POLYGONS = 50000
PROJECTED_CRS = "EPSG:2154"  # RGF93 / Lambert-93, suitable for metropolitan France


def _get_auth_secret(key: str, default: Any) -> Any:
    """Read an auth configuration value from secrets.toml, with a safe fallback.

    Reads from the [auth] section or top-level of .streamlit/secrets.toml.
    Falls back to `default` when secrets are unavailable.

    Args:
        key: The key within the secrets configuration.
        default: The fallback value if the secret is unavailable.

    Returns:
        The secret value, or `default` if secrets are inaccessible.
    """
    raw_secrets = _load_secrets_toml()
    if key in raw_secrets:
        return raw_secrets[key]
    return raw_secrets.get("auth", {}).get(key, default)


# The OIDC authorization policy is supplied by Secret Manager at runtime and
# written into Streamlit's secrets.toml by generate_secrets.py.  Empty defaults
# are intentional: a Cloud Run revision without a valid policy must not gain
# access from source-controlled fallback identities.
OIDC_ALLOWED_DOMAINS: Set[str] = set(_get_auth_secret("allowed_domains", []))
OIDC_ALLOWED_EMAILS: Set[str] = set(_get_auth_secret("allowed_emails", []))
OIDC_DOMAIN_ORG_MAPPING: Dict[str, str] = dict(
    _get_auth_secret("domain_org_mapping", {})
)
OIDC_EMAIL_ORG_MAPPING: Dict[str, str] = dict(_get_auth_secret("email_org_mapping", {}))

# --- Admins Allowlist ---
ADMIN_USERS: Set[str] = set(_get_auth_secret("admin_users", ["jacques-local"]))


# --- Inclusion Defaults ---
DEFAULT_INC_SERVICES_CORE = [
    "logement-hebergement--sinformer-sur-les-demarches-liees-a-lacces-au-logement",
    "difficultes-administratives-ou-juridiques--accompagnement-aux-demarches-administratives",
    "preparer-sa-candidature--organiser-ses-demarches-de-recherche-demploi",
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
    "poids_emploi": 0.5,
    "poids_logement": 0.5,
    "poids_education": 0.5,
    "poids_inclusion": 0.5,
    "poids_mobilite": 0.5,
    "poids_sante": 0.5,
    "poids_territoire": 1.0,
    "departement_actuel": None,
    "commune_actuelle": None,
    "loc_search_area": "departement",
    "hebergement_cible": [],
    "logement": "Location",
    "sante": [],
    "nb_adultes": 1,
    "nb_enfants": 0,
    "codes_metiers": [],
    "codes_formations": [],
    "recherche_siae": True,
    "classe_enfants": [],
    "inc_services_selection": DEFAULT_INC_SERVICES_CORE,
    "inc_asso_add_selection": [],
    "loc_search_code": [],
    "type_logement": "appt_all",
    "weight_profile": "Équilibré",
    "besoin_sante": [],
    "notes_qualitatives": "",
    "freq_retour": "1 fois/an",
    "target_city_size": DEFAULT_CITY_SIZE,
    "target_population_a": DEFAULT_TRAPEZOID["a"],
    "target_population_b": DEFAULT_TRAPEZOID["b"],
    "target_population_c": DEFAULT_TRAPEZOID["c"],
    "target_population_d": DEFAULT_TRAPEZOID["d"],
    "org_context": None,
    "org_strategic_locations": [],
    "org_strategic_locations_type": "departement",
}


DEMO_SCENARIOS = {
    "1": {
        "commune_actuelle": "33063",
        "loc_search_area": "departement",
        "hebergement_cible": ["Chez l'habitant"],
        "nb_adultes": 1,
        "nb_enfants": 0,
        "weight_profile": "Équilibré",
        "notes_qualitatives": "Jeune actif à la recherche d'une solution de cohabitation solidaire.",
    },
    "2": {
        "commune_actuelle": "75056",
        "loc_search_area": "region",
        "loc_search_code": ["75"],
        "hebergement_cible": ["Location avec Intermédiation"],
        "logement": "Logement Social",
        "nb_adultes": 2,
        "nb_enfants": 2,
        "codes_metiers": [["H2206"], []],
        "codes_formations": [[], ["331", "330", "326"]],
        "classe_enfants": ["Maternelle", "Elémentaire"],
        "besoin_sante": ["Maternité"],
        "poids_mobilite": 0.0,
        "weight_profile": "Équilibré",
        "notes_qualitatives": "Famille recherchant un environnement adapté avec écoles et équipements de santé.",
    },
    "3": {
        "nb_adultes": 1,
        "nb_enfants": 2,
        "commune_actuelle": "13055",
        "hebergement_cible": ["Location avec Intermédiation", "Chez l'habitant"],
        "logement": "Logement Social",
        "freq_retour": "1 fois/an",
        "loc_search_area": "departement",
        "loc_search_code": ["13"],
        "codes_metiers": [["M1607", "M1602"]],
        "classe_enfants": ["Crèche / Assistante Maternelle", "Elémentaire"],
        "weight_profile": "Famille",
        "besoin_sante": ["Maternité"],
        "inc_services_selection": DEFAULT_INC_SERVICES_CORE
        + ["lecture-ecriture-calcul--maitriser-le-francais"],
        "inc_asso_add_selection": ["006030"],
        "notes_qualitatives": "Famille monoparentale recherchant des services de mobilité et d'accompagnement.",
    },
    "agir": {
        "nb_adultes": 1,
        "nb_enfants": 2,
        "codes_metiers": [["I1604"]],
        "commune_actuelle": "33063",
        "freq_retour": "1 fois/an",
        "loc_search_area": "departement",
        "loc_search_code": ["17", "33", "40"],
        "hebergement_cible": ["Location avec Intermédiation"],
        "logement": "Location",
        "type_logement": "appt_all",
        "classe_enfants": ["Elémentaire"],
        "inc_services_selection": DEFAULT_INC_SERVICES_CORE
        + ["lecture-ecriture-calcul--maitriser-le-francais"],
        "inc_asso_add_selection": ["011075"],
        "weight_profile": "Economique",
        "poids_emploi": 1.0,
        "poids_logement": 0.75,
        "poids_education": 0.5,
        "poids_inclusion": 0.5,
        "poids_sante": 0.25,
        "poids_mobilite": 0.25,
        "besoin_sante": ["Soutien Psychologique", "Addictologie"],
        "notes_qualitatives": "Recherche un logement et un emploi dans un cadre adapté.",
        "target_city_size": "🏡 Bourg",
        "target_population_a": CITY_SIZE_MAPPING["🏡 Bourg"]["a"],
        "target_population_b": CITY_SIZE_MAPPING["🏡 Bourg"]["b"],
        "target_population_c": CITY_SIZE_MAPPING["🏡 Bourg"]["c"],
        "target_population_d": CITY_SIZE_MAPPING["🏡 Bourg"]["d"],
    },
    "emile": {
        "nb_adultes": 1,
        "nb_enfants": 2,
        "codes_metiers": [["I1604"]],
        "commune_actuelle": "69123",
        "freq_retour": "1 fois/an",
        "loc_search_area": "departement",
        "loc_search_code": ["15", "03", "01", "69"],
        "hebergement_cible": ["Location avec Intermédiation"],
        "logement": "Logement Social",
        "type_logement": "appt_all",
        "classe_enfants": ["Maternelle", "Elémentaire"],
        "inc_services_selection": DEFAULT_INC_SERVICES_CORE
        + ["lecture-ecriture-calcul--maitriser-le-francais"],
        "inc_asso_add_selection": ["011075"],
        "weight_profile": "Profil personnalisé",
        "poids_emploi": 1.0,
        "poids_logement": 1.0,
        "poids_education": 0.5,
        "poids_inclusion": 0.5,
        "poids_sante": 0.25,
        "poids_mobilite": 0.25,
        "besoin_sante": ["Soutien Psychologique", "Addictologie"],
        "notes_qualitatives": "Accompagnement mobilité vers logement social et emploi.",
        "target_city_size": "🏡 Bourg",
        "target_population_a": CITY_SIZE_MAPPING["🏡 Bourg"]["a"],
        "target_population_b": CITY_SIZE_MAPPING["🏡 Bourg"]["b"],
        "target_population_c": CITY_SIZE_MAPPING["🏡 Bourg"]["c"],
        "target_population_d": CITY_SIZE_MAPPING["🏡 Bourg"]["d"],
    },
}


# --- LOISIRS CATÉGORIES (WALDEC PREFIXES) ---
# Seuls les codes commençant par ces 3 digits sont conservés pour la sélection "Loisirs"
WALDEC_CATEGORIES = [
    "003",
    "006",
    "007",
    "009",
    "011",
    "013",
    "014",
    "019",
    "020",
    "021",
    "024",
]


def get_relevant_rna_codes() -> List[str]:
    """Retourne les préfixes de catégories WALDEC utiles pour le filtrage"""
    return WALDEC_CATEGORIES


# 3. WALDEC labels for refugee associations
WALDEC_REFUGEE_LABELS = {
    "003": "Action socio-culturelle",
    "014": "Enseignement, éducation",
    "019": "Action sociale",
    "020": "Associations caritatives, humanitaires",
}


def is_ai_free_mode(org: Optional[Org] = None) -> bool:
    """Checks if the application is running in 'AI-free' mode.

    Returns True if ODIS_AI_FREE_MODE is set to 'true', '1' or 'yes' in environment,
    or if the provided organization setting has 'ai_free_mode' set to True.

    Args:
        org: Optional Org instance.

    Returns:
        bool: True if AI-free mode is active.
    """
    if os.environ.get("ODIS_AI_FREE_MODE", "False").lower() in ("true", "1", "yes"):
        return True

    if org and getattr(org, "ai_free_mode", False):
        return True

    return False


def is_auto_analyse_top_cities_enabled() -> bool:
    """Checks if background AI analyses should be automatically triggered for top 5 cities.

    Returns True if ODIS_AUTO_ANALYSE_TOP_CITIES is set to 'true', '1' or 'yes' in environment.
    Note that this feature is automatically disabled if AI-free mode is active.

    Returns:
        bool: True if auto analysis of top 5 cities is enabled and AI mode is active.
    """
    if is_ai_free_mode():
        return False
    return os.environ.get("ODIS_AUTO_ANALYSE_TOP_CITIES", "False").lower() in (
        "true",
        "1",
        "yes",
    )


def is_interactive_chat_enabled(
    org: Optional[Org] = None,
    search_config: Any = None,
) -> bool:
    """Checks if interactive chat under city analysis is enabled.

    Interactive chat is enabled if the provided organization profile explicitly enables it
    (or if ODIS_ENABLE_INTERACTIVE_CHAT is set to 'true' in the environment).
    It is automatically disabled if AI-free mode is active.

    Returns:
        bool: True if interactive chat is allowed for the active session.
    """
    if is_ai_free_mode(org=org):
        return False

    env_override = os.environ.get("ODIS_ENABLE_INTERACTIVE_CHAT", "").strip().lower()
    if env_override in ("true", "1", "yes"):
        return True

    if org and getattr(org, "enable_interactive_chat", False):
        return True

    org_context = getattr(search_config, "org_context", None) if search_config else None
    if org_context and org_context in ORGANIZATION_PROFILES:
        return ORGANIZATION_PROFILES[org_context].enable_interactive_chat

    return False
