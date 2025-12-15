
import sys
import os
import json

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from mcp_server import _compute_top_cities_logic

def test_mcp_execution():
    print("Testing MCP Execution...")
    
    print("\n--- Test Configuration (Dummy Data) ---")
    print("These are hardcoded inputs simulating a User Persona:")
    
    # Dummy Weights
    # Scenario: Demo 3 (Aïcha) from config.py
    # Family: 1 Adult, 2 Children
    # Current: Marseille (13), Search Radius: 50km
    # Job: T2A60 (Lab Tech)
    # Needs: Social Housing, School (Creche, College), Maternity, French learning
    
    weights = {
        'emploi': 100,
        'logement': 100, # Default per SCORE_EXAMPLE.md
        'education': 100, # Default
        'sante': 100, # Default
        'inclusion': 100, # From config
        'mobilité': 50
    }
    
    filters = {
        'commune_actuelle': 'Marseille',
        'loc_distance_km': 50,
        'nb_adultes': 1,
        'nb_enfants': 2,
        'hebergement': 'Location',
        'logement': 'Logement Social',
        'codes_metiers': [['T2A60']], # List of Lists
        'classe_enfants': ['Crèche / Assistante Maternelle', 'Collège'],
        'besoins_autres': ['lecture-ecriture-calcul--maitriser-le-francais'],
        'sante': 'Maternité',
        'affinite_selection': ['Entraide / Bénévolat']
    }
    
    print(f"Weights: {json.dumps(weights, indent=2)}")
    print(f"Filters: {json.dumps(filters, indent=2)}")
    print("---------------------------------------\n")
    
    print("Function defined, calling it...")
    # This triggers lazy data loading
    try:
        results = _compute_top_cities_logic(weights, filters)
        print(f"Success! Got {len(results)} results.")
        if results:
            print("Top result example:")
            print(json.dumps(results[0], indent=2))
        else:
            print("No results returned. Check filters or data.")
            
    except Exception as e:
        print(f"FAILED: {e}")
        raise e

if __name__ == "__main__":
    test_mcp_execution()
