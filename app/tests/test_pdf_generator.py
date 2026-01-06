import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
import pytest
import types # Import types for SimpleNamespace

from app.pdf_generator import generate_pdf_report
from app.config import ScoringConfig

@pytest.fixture
def sample_session_state():
    """Creates a sample session_state object for testing."""
    config = ScoringConfig(
        poids_emploi=100,
        poids_logement=100,
        poids_education=100,
        poids_inclusion=25,
        poids_sante=100, # Added for tests
        poids_mobilité=100,
        criteria_weights={}, # Added for F-15
        commune_actuelle='33063',
        loc_search_area='departement',
        loc_custom_code=None,
        loc_custom_type=None,
        nb_adultes=1,
        nb_enfants=1,
        hebergement='Location',
        logement='Location',
        codes_metiers=[[]],
        codes_formations=[[]],
        classe_enfants=['Maternelle'],
        besoin_sante='Aucun',
        inc_services_add_selection=[],
        inc_services_core_selection=[],
        inc_asso_add_selection=[]
    )
    
    # Mock app_data with necessary dataframes
    mock_app_data = {
        'scores_cat': pd.DataFrame({
            'score': ['emploi_score', 'logement_score', 'education_score', 'inclusion_score', 'mobilité_score'],
            'cat': ['emploi', 'logement', 'education', 'inclusion', 'mobilité'],
            'score_affichage': ['Emploi', 'Logement', 'Education', 'Inclusion', 'Mobilité']
        }),
        'codfap_index': pd.DataFrame({
            'Code FAP 341': ['CODE1', 'CODE2'],
            'Intitulé FAP 341': ['Metier 1', 'Metier 2']
        }).set_index('Code FAP 341'),
        'codformations_index': pd.DataFrame({
            'index': ['FORM1', 'FORM2'],
            'libformation': ['Formation 1', 'Formation 2']
        }).set_index('index'),
        'annuaire_inclusion': pd.DataFrame({
            'codgeo': ['33063'],
            'categorie': ['social'],
            'service': ['aide-sociale']
        })
    }

    # Create a dictionary to mimic st.session_state
    session_state_mock = {
        'config': config,
        'ui_commune': 'Bordeaux',
        'binome': False,
        'app_data': mock_app_data,
        # Add other necessary session state keys that generate_pdf_report might access
        'ui_nom': "Test User", # For get_person_accompanied_str()
        'processed_gdf': None, # Will be set by the test itself
        'map_object': None, # Will be set by the test itself
        # Add any other keys that generate_pdf_report expects to find in st.session_state
    }
    return session_state_mock

@pytest.fixture
def sample_results_df():
    """Creates a sample GeoDataFrame of results for testing."""
    data = {
        'codgeo': ['75056', '69123'],
        'libgeo': ['Paris', 'Lyon'],
        'libgeo_binome': [None, None],
        'weighted_score': [0.85, 0.72],
        'emploi_cat_score': [0.9, 0.8],
        'logement_cat_score': [0.7, 0.6],
        'education_cat_score': [0.8, 0.9],
        'inclusion_cat_score': [0.9, 0.7],
        'mobilité_cat_score': [0.8, 0.8],
        'binome': [False, False],
        'population': [2000000, 500000],
        'epci_nom': ['Métropole du Grand Paris', 'Métropole de Lyon'],
        'libelle_bassin_de_vie': ['Paris', 'Lyon'],
        'geometry': [
            Polygon([(2.224, 48.816), (2.469, 48.816), (2.469, 48.902), (2.224, 48.902)]),
            Polygon([(4.8, 45.7), (4.9, 45.7), (4.9, 45.8), (4.8, 45.8)])
        ]
    }
    gdf = gpd.GeoDataFrame(data, crs="EPSG:4326")
    gdf = gdf.set_index('codgeo')
    return gdf.reset_index()


def test_generate_pdf_report(sample_session_state, sample_results_df):
    """
    Tests that generate_pdf_report runs without errors and produces a valid PDF bytes object.
    """
    # Arrange
    session_state = sample_session_state
    results_df = sample_results_df

    # Act
    # This will raise an exception if something goes wrong with fpdf2, matplotlib, etc.
    pdf_bytes = generate_pdf_report(session_state, results_df)

    # Assert
    # 1. Check that the output is a bytes object
    assert isinstance(pdf_bytes, bytes)

    # 2. Check that the bytes object is not empty
    assert len(pdf_bytes) > 0

    # 3. Check for the PDF magic number (%PDF-)
    # This is a reliable way to confirm we have a PDF file without a complex parser.
    assert pdf_bytes.startswith(b'%PDF-')
