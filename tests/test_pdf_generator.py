import types
import pytest
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
from typing import Any


from core.pdf_generator import generate_pdf_report
import config as cfg
from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem, EmploymentMetrics, HousingMetrics, EducationMetrics, HealthMetrics, InclusionMetrics, MobilityMetrics

@pytest.fixture
def sample_session_state():
    """Creates a sample session_state object for testing."""
    config = SearchCriterias(
        poids_emploi=1.0,
        poids_logement=1.0,
        poids_education=1.0,
        poids_inclusion=0.25,
        poids_sante=1.0, # Added for tests
        poids_mobilite=1.0,
        criteria_weights={}, # Added for F-15
        commune_actuelle='33063',
        loc_search_area='departement',
        loc_search_code=[],
        nb_adultes=1,
        nb_enfants=1,
        hebergement_cible=[],
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

    # Define a class that supports dot access for mocking st.session_state
    class MockSessionState(dict):
        def __getattr__(self, name):
            try:
                return self[name]
            except KeyError:
                raise AttributeError(f"'MockSessionState' object has no attribute '{name}'")
        def __setattr__(self, name, value):
            self[name] = value

    session_state_mock = MockSessionState({
        'config': config,
        'ui_commune': 'Bordeaux',
        'binome': False,
        'app_data': mock_app_data,
        'ui_nom': "Test User",
        'processed_gdf': None,
        'map_object': None,
    })
    return session_state_mock

@pytest.fixture
def sample_search_results():
    """Creates a sample SearchResultsData object for testing."""
    results = [
        CommuneResult(
            codgeo='75056',
            name='Paris',
            population=2200000,
            global_score=0.85,
            geometry=Polygon([(2.224, 48.816), (2.469, 48.816), (2.469, 48.902), (2.224, 48.902)])
        ),
        CommuneResult(
            codgeo='69123',
            name='Lyon',
            population=500000,
            global_score=0.72,
            geometry=Polygon([(4.8, 45.7), (4.9, 45.7), (4.9, 45.8), (4.8, 45.8)])
        )
    ]
    
    current_geo = CommuneResult(
        codgeo='33063',
        name='Bordeaux',
        population=250000,
        global_score=0.65,
        geometry=Polygon([(-0.6, 44.8), (-0.5, 44.8), (-0.5, 44.9), (-0.6, 44.9)])
    )
    
    return SearchResultsData(
        search_hash="test_hash",
        results=results,
        current_geo=current_geo
    )


from unittest.mock import patch

def test_generate_pdf_report(sample_session_state, sample_search_results):
    """
    Tests that generate_pdf_report runs without errors and produces a valid PDF bytes object.
    """
    # Arrange
    session_state = sample_session_state
    search_results = sample_search_results

    # Act
    # Use patch to mock streamlit's session_state for the duration of the call
    with patch('core.pdf_generator.ui.st.session_state', session_state):
        pdf_bytes = generate_pdf_report(search_results, session_state.config)

    # Assert
    # 1. Check that the output is a bytes object
    assert isinstance(pdf_bytes, bytes)

    # 2. Check that the bytes object is not empty
    assert len(pdf_bytes) > 0

    # 3. Check for the PDF magic number (%PDF-)
    # This is a reliable way to confirm we have a PDF file without a complex parser.
    assert pdf_bytes.startswith(b'%PDF-')
