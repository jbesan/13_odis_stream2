import sys
import os
import logging

# Add PROJECT ROOT to path
sys.path.append(os.getcwd())

from app.data_loader import load_all_data_raw
from app.mcp_server import set_data_context, _search_referentiels_logic

# Configure logging
logging.basicConfig(level=logging.INFO)

def test_search():
    print("Loading data...")
    data = load_all_data_raw()
    set_data_context(data)
    
    print("\n--- Testing Search 'Boulanger' (FAP) ---")
    results = _search_referentiels_logic("Boulanger", domain="fap_codes")
    for r in results:
        print(f"[{r['code']}] {r['label']}")
        
    print("\n--- Testing Search 'Comptable' (All) ---")
    results = _search_referentiels_logic("Comptable")
    for r in results:
        print(f"[{r['type']}] [{r['code']}] {r['label']}")

if __name__ == "__main__":
    test_search()
