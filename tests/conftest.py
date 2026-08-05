import os

os.environ.setdefault("GOOGLE_API_KEY", "dummy_placeholder_for_tests")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
# Runtime code must receive this explicitly in Cloud Run; tests keep a stable
# bucket name because their storage clients are mocked or their fixtures rely
# on the published development release.
os.environ.setdefault("GCS_DATASETS_BUCKET", "odis-stream2-eu")
import sys
from types import ModuleType
import warnings

import geopandas as gpd
import opentelemetry.trace as otel_trace
import pandas as pd
import pytest
from shapely.geometry import Polygon

# Add project root and app directory to sys.path to support imports during tests
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../app")))

import config as cfg
from core.models import SearchCriterias, CriteriaItem
from utils.data_loader import load_scores_config_as_df

# --- Telemetry & Logfire Setup ---
run_evals = os.getenv("RUN_EVALS", "false").lower() == "true"
enable_logfire = os.getenv("ENABLE_LOGFIRE", "false").lower() == "true" or run_evals

if enable_logfire:
    os.environ["LOGFIRE_SEND_TO_LOGFIRE"] = "true"
    import logfire

    print("\n🔥 [LOGFIRE-TEST] Logfire enabled: configuring for test environment...")
    logfire.configure(
        environment="test", service_name="odis-stream2", send_to_logfire=True
    )
    logfire.instrument_pydantic_ai()
    logfire.instrument_httpx()
else:

    class MockSpan:
        _span = otel_trace.INVALID_SPAN
        context = otel_trace.INVALID_SPAN.get_span_context()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def set_attribute(self, *args, **kwargs):
            pass

        def record_exception(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return lambda *args, **kwargs: None

    class MockAttr:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            return self

        def __getattr__(self, attr):
            if attr.startswith("__"):
                raise AttributeError(attr)
            return self

        def span(self, *args, **kwargs):
            return MockSpan()

    class MockLogfireModule(ModuleType):
        def instrument(self, *args, **kwargs):
            if len(args) == 1 and callable(args[0]):
                return args[0]

            def decorator(func):
                return func

            return decorator

        def span(self, *args, **kwargs):
            return MockSpan()

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return MockAttr

    sys.modules["logfire"] = MockLogfireModule("logfire")


# --- Warning Suppressions ---
warnings.filterwarnings("ignore", category=DeprecationWarning, module="google.genai")
warnings.filterwarnings(
    "ignore", category=DeprecationWarning, module="google.genai.types"
)


# --- Test Fixtures ---


@pytest.fixture
def sample_data():
    """Creates a sample GeoDataFrame for testing."""
    data = {
        "codgeo": ["75056", "69123", "13055", "33063", "64445"],
        "libgeo": ["Paris", "Lyon", "Marseille", "Bordeaux", "Pau"],
        "dep_code": ["75", "69", "13", "33", "64"],
        "reg_code": ["11", "84", "93", "75", "75"],
        "bassin_de_vie": ["1", "2", "3", "4", "4"],
        "population": [2148271, 513275, 861635, 257068, 77130],
        "geometry": [
            Polygon(
                [(2.224, 48.816), (2.469, 48.816), (2.469, 48.902), (2.224, 48.902)]
            ),
            Polygon([(4.8, 45.7), (4.9, 45.7), (4.9, 45.8), (4.8, 45.8)]),
            Polygon([(5.3, 43.2), (5.4, 43.2), (5.4, 43.3), (5.3, 43.3)]),
            Polygon(
                [(-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838)]
            ),
            Polygon([(-0.333, 43.3), (-0.333, 43.3), (-0.333, 43.3), (-0.333, 43.3)]),
        ],
        "epci_code": ["200054781", "200046977", "200054807", "200023384", "246401722"],
        "met": [100, 50, 70, 80, 60],
        "pop_be": [1000, 500, 700, 800, 400],
        "be_codfap_top": [["F1", "G2"], ["F1"], ["G2"], ["H3"], ["H3"]],
        "codes_formations": [["123"], ["456"], ["123", "456"], [], []],
        "rp_5+pieces": [10, 20, 15, 12, 10],
        "log_rp": [100, 200, 150, 120, 90],
        "log_vac": [10, 5, 8, 6, 4],
        "pp_vacant_plus_2ans_25": [0.05, 0.02, 0.03, 0.04, 0.01],
        "lien_social_count": [10, 5, 8, 6, 4],
        "lien_social_density": [5.0, 2.5, 4.0, 3.0, 2.0],
        "loyer_m2_moy_appartement_toutes": [15.0, 20.0, 18.0, 16.0, 12.0],
        "loyer_m2_moy_appartement_t1_t2": [16.0, 21.0, 19.0, 17.0, 13.0],
        "loyer_m2_moy_appartement_t3_plus": [14.0, 19.0, 17.0, 15.0, 11.0],
        "loyer_m2_moy_maison_toutes": [12.0, 15.0, 14.0, 13.0, 10.0],
        "log_loyer_moyen_appt_all_scaled": [0.5, 0.2, 0.3, 0.4, 0.8],
        "log_loyer_moyen_appt_t1_t2_scaled": [0.5, 0.2, 0.3, 0.4, 0.8],
        "log_loyer_moyen_appt_t3_p_scaled": [0.5, 0.2, 0.3, 0.4, 0.8],
        "log_loyer_moyen_house_all_scaled": [0.5, 0.2, 0.3, 0.4, 0.8],
    }
    gdf = gpd.GeoDataFrame(data, crs="EPSG:4326")
    gdf = gdf.to_crs(cfg.PROJECTED_CRS)
    gdf["centroid"] = gdf.geometry.centroid
    gdf = gdf.set_index("codgeo")
    return gdf.copy()


@pytest.fixture
def live_scores_cat():
    """Loads the live scores configuration from scores_config.yaml."""
    return load_scores_config_as_df(os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE))


@pytest.fixture
def sample_incl_index():
    """Creates a sample incl_index DataFrame for testing."""
    data = {
        "codgeo": ["75056", "69123", "13055", "33063", "64445"],
        "key": [
            {"cat1_serv1"},
            {"cat1_serv2"},
            {"cat1_serv1", "cat1_serv2"},
            set(),
            set(),
        ],
    }
    df = pd.DataFrame(data)
    df = df.set_index("codgeo")
    return df.copy()


@pytest.fixture
def default_config():
    """Returns a default SearchCriterias for testing."""
    return SearchCriterias(
        poids_emploi=1.0,
        poids_logement=1.0,
        poids_education=1.0,
        poids_inclusion=0.5,
        poids_sante=1.0,
        poids_mobilite=1.0,
        criteria_weights={},
        weight_profile="Équilibré",
        commune_actuelle=CriteriaItem(code="33063", label="Bordeaux"),
        loc_search_area="departement",
        loc_search_code=[],
        nb_adultes=1,
        nb_enfants=0,
        hebergement_cible=[],
        logement="Location",
        codes_metiers=[[]],
        codes_formations=[[]],
        classe_enfants=[],
        besoin_sante=[],
        inc_services_selection=[],
        inc_asso_add_selection=[],
        type_logement=CriteriaItem(
            code="appartement_toutes", label="Appartement (Toutes)"
        ),
    )


@pytest.fixture
def global_stats():
    """Returns sample global stats for testing."""
    return {
        "met_scaled": {"min": 0.0, "max": 100.0},
        "log_vac_scaled": {"min": 0.0, "max": 0.2},
        "log_soc_inoc_scaled": {"min": 0.0, "max": 0.1},
        "log_5p_scaled": {"min": 0.0, "max": 0.5},
        "edu_classes_ferm_scaled": {"min": 0.0, "max": 0.1},
        "inc_asso_core_scaled": {"min": 0.0, "max": 10.0},
        "inc_asso_add_scaled": {"min": 0.0, "max": 10.0},
        "ter_population_scaled": {"min": 0.0, "max": 100000.0},
        "edu_petite_enfance_scaled": {"min": 0.0, "max": 100.0},
    }


def pytest_sessionfinish(session, exitstatus):
    """Ensure all logfire spans are flushed before the python process exits."""
    if not enable_logfire:
        return
    try:
        import logfire

        if hasattr(logfire, "force_flush"):
            logfire.force_flush()
        elif hasattr(logfire, "flush"):
            logfire.flush()
    except Exception as e:
        import sys

        print(
            f"\n⚠️ [LOGFIRE-TEST] Failed to flush Logfire spans: {e}",
            file=sys.stderr,
            flush=True,
        )
