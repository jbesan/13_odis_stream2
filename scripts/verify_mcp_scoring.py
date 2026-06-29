import os
import sys

# Add app to path
sys.path.append(os.path.abspath(os.path.join(os.getcwd(), 'app')))

from services.mcp_server import _compute_top_cities_logic
from core.models import SearchCriterias

def test_scoring_with_profile():
    # Mock criteria for Zacharie (from config.py demo scenario 1)
    criteria = SearchCriterias(
        commune_actuelle="33063", # Bordeaux
        loc_search_area="departement",
        nb_adultes=1,
        weight_profile="Famille"
    )

    print(f"--- Testing scoring with profile: {criteria.weight_profile} ---")
    try:
        results = _compute_top_cities_logic(criteria)
        if "cities" in results:
            print(f"✅ Success! Found {len(results['cities'])} cities.")
            for city in results['cities']:
                print(f"   - {city['name']} (Score: {city['score']:.2f})")
            
            # Check if detailed scores are present
            if len(results['cities']) > 0:
                first_city = results['cities'][0]
                if "detailed_scores" in first_city:
                    print("✅ Detailed scores found.")
                else:
                    print("❌ Detailed scores missing.")
        else:
            print(f"❌ Error: {results.get('error', 'Unknown error')}")
    except Exception as e:
        print(f"❌ Exception: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    test_scoring_with_profile()
