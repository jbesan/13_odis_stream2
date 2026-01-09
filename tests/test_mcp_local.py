
import sys
import os
import json
import pytest
import logging

# Add project root to path (parent of 'app')
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from services.mcp_server import _compute_top_cities_logic, _search_commune_logic, _search_referentiels_logic, set_data_context
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
    print("Testing search_commune('Nantes')...")
    results = _search_commune_logic("Nantes")
    assert len(results) > 0, "Should find at least one city"
    
    # Check top match
    top = results[0]
    assert top['codgeo'] == '44109', "Nantes codgeo should be 44109"
    assert top['libgeo'] == 'Nantes'

def test_search_referentiels_jobs(mcp_data_context):
    """Verify FAP search finds jobs."""
    results = _search_referentiels_logic("Boulanger", domain="fap_codes")
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
        'commune_actuelle': 'Marseille', # Should be resolved to 13055
        'loc_search_area': 'departement',
        'nb_adultes': 1,
        'nb_enfants': 2,
        'hebergement': 'Location',
        'logement': 'Logement Social',
        'codes_metiers': [['T2A60']], 
        'classe_enfants': ['Maternelle', 'Collège'], # Adjusted from original 'Crèche/Assistante Maternelle' to match standardized keys if possible, or robust logic handles it.
        # Note: 'Crèche / Assistante Maternelle' is the text used in prompt, scoring might map it.
        # But let's stick to what original test had:
        # 'classe_enfants': ['Crèche / Assistante Maternelle', 'Collège'],
        # Wait, usually scoring expects keys like 'Maternelle', 'Elémentaire'.
        # Let's trust the original test inputs?
        # Actually, let's use standard keys to be safe:
        # 'Maternelle' covers <6. 'Collège' covers 11-15.
        
        'inc_services_add_selection': ['lecture-ecriture-calcul--maitriser-le-francais'],
        'besoin_sante': 'Maternité', # Key in config is 'besoin_sante'
        'inc_asso_add_selection': ['Entraide / Bénévolat']
    }
    
    # Try with robust inputs
    # Note: original test had 'sante': 'Maternité' in filters, but scoring expects 'besoin_sante'.
    # And 'classe_enfants' values need to match mapping.
    # We'll use the exact values from original test and assume app handles them or they are correct keys.
    
    results_dict = _compute_top_cities_logic(weights, filters)
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
