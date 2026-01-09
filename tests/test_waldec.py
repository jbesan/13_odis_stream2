
import sys
import os
import logging

sys.path.append(os.getcwd())

from utils.data_loader import load_all_data_raw
from services.mcp_server import set_data_context, _search_referentiels_logic

logging.basicConfig(level=logging.INFO)

def test_waldec():
    print("Loading data...")
    data = load_all_data_raw()
    set_data_context(data)
    
    print("\n--- Testing Search 'Comités des fêtes' (WALDEC) ---")
    results = _search_referentiels_logic("fêtes", domain="waldec_codes")
    for r in results:
        print(f"[{r['code']}] {r['label']}")

if __name__ == "__main__":
    test_waldec()
