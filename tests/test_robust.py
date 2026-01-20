
import sys
import os
import logging

sys.path.append(os.getcwd())

from utils.data_loader import load_all_data_raw
from services.mcp_server import set_data_context, _search_referentiels_logic

logging.basicConfig(level=logging.ERROR) # Quiet logs

def test_robust_search():
    print("Loading data...")
    data = load_all_data_raw()
    set_data_context(data)
    
    queries = [
        ("Femme de ménage", "rome_codes"),
        ("Agent entretien", "rome_codes"),
        ("Club Cuisine", "waldec_codes")
    ]
    
    for q, domain in queries:
        print(f"\n--- Searching '{q}' ({domain}) ---")
        results = _search_referentiels_logic(q, domain=domain)
        for r in results:
            print(f"[{r['code']}] {r['label']}")

if __name__ == "__main__":
    test_robust_search()
