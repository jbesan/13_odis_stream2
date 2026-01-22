
import sys
import os
import json
import pytest
import logging

# Add project root to path (parent of 'app')
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from services.mcp_server import _compute_top_cities_logic, _search_referentiels_logic, set_data_context
from utils.data_loader import load_all_data_raw

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@pytest.fixture(scope="module")
def mcp_data_context():
    """Load data once for the module (simulating Server Startup)."""
    print("Loading ODIS Data Context...")
    data = load_all_data_raw()
    set_data_context(data)
    return data

def test_search_commune(mcp_data_context):
    """Verify city search finds Nantes."""
    print("Testing search_referentiels('Nantes', domain='communes')...")
    results = _search_referentiels_logic("Nantes", domain="communes")
    assert len(results) > 0, "Should find at least one city"
    
    # Check top match
    top = results[0]
    assert top['code'] == '44109', "Nantes code should be 44109"
    assert top['label'] == 'Nantes'
    assert 'type' not in top, "Output should be trimmed"

def test_search_referentiels_jobs(mcp_data_context):
    """Verify ROME search finds jobs."""
    results = _search_referentiels_logic("Boulanger", domain="rome_codes")
    assert len(results) > 0
    # Check if any result has expected label
    found = any("Boulanger" in r['label'] for r in results)
    assert found, "Should find job labeled 'Boulanger'"

def test_search_referentiels_hobbies(mcp_data_context):
    """Verify WALDEC search finds hobbies."""
    results = _search_referentiels_logic("Football", domain="waldec_codes")
    assert len(results) > 0
    top = results[0]
    assert "football" in top['label'].lower()

def test_search_referentiels_inclusion(mcp_data_context):
    """Verify Inclusion search finds services (e.g. FLE)."""
    results = _search_referentiels_logic("Apprendre Français", domain="inclusion_services")
    assert len(results) > 0
    top = results[0]
    assert "français" in top['label'].lower() or "fle" in top['label'].lower() or "langue" in top['label'].lower()

def test_search_referentiels_housing(mcp_data_context):
    """Verify synthetic Housing Types domain."""
    results = _search_referentiels_logic("Appartement", domain="housing_types")
    assert len(results) == 3 # Toutes, T1-T2, T3+
    assert any("appt_all" == r['code'] for r in results)
    
    results_all = _search_referentiels_logic("", domain="housing_types")
    assert len(results_all) == 4 # All options

def test_compute_top_cities_execution_complex(mcp_data_context):
    """
    Test a complete user scenario (Demo 3 - Aïcha).
    Using data structure maintained in original test_mcp_local.py.
    """
    print("\nExecuting Complex Scenario (Aïcha)...")
    
    weights = {
        'emploi': 100.0,
        'logement': 100.0,
        'education': 100.0,
        'sante': 100.0,
        'inclusion': 100.0,
        'mobilité': 50.0
    }
    
    filters = {
        'commune_actuelle': {'code': '13055', 'label': 'Marseille'}, 
        'loc_search_area': 'departement',
        'nb_adultes': 1,
        'nb_enfants': 2,
        'hebergement': 'Location',
        'logement': 'Logement Social',
        'codes_metiers': [[{'code': 'M1805', 'label': 'Informatique'}]], 
        'classe_enfants': ['Maternelle', 'Collège'],
        'inc_services_add_selection': [{'code': 'lecture-ecriture-calcul--maitriser-le-francais', 'label': 'Français'}],
        'besoin_sante': 'Maternité',
        'inc_asso_add_selection': [{'code': '123', 'label': 'Entraide / Bénévolat'}]
    }
    
    # Try with robust inputs
    # Note: original test had 'sante': 'Maternité' in filters, but scoring expects 'besoin_sante'.
    # And 'classe_enfants' values need to match mapping.
    # We'll use the exact values from original test and assume app handles them or they are correct keys.
    
    filters['criteria_weights'] = weights
    results_dict = _compute_top_cities_logic(filters)
    results = results_dict.get('cities', [])
    
    assert len(results) > 0, "Should return results for complex scenario"
    top = results[0]
    
    print(f"Top Result: {top['name']} ({top['score']})")
    assert 'detailed_scores' in top
    assert top['score'] > 0

if __name__ == "__main__":
    # Allow running as script
    # Manually invoke py.test or just run logical flow
    sys.exit(pytest.main(["-v", __file__]))
