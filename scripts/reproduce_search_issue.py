
import sys
import os
import pandas as pd
import logging

# Add app to path
sys.path.append(os.path.join(os.getcwd(), 'app'))

from services.mcp_server import _search_referentiels_logic, set_data_context
from utils.data_loader import load_all_data_raw

# Configure Logging
logging.basicConfig(level=logging.INFO)

def test_reproduction():
    print("--- Starting Reproduction Test ---")
    
    # Load data
    context = load_all_data_raw()
    set_data_context(context)
    
    # Test cases
    queries = ["", " ", ".", "Bordeaux"]
    
    for q in queries:
        print(f"\nTesting query: '{q}'")
        results = _search_referentiels_logic(q)
        print(f"Results found: {len(results)}")
        if results:
            print(f"Top result: {results[0]}")
        
        # If query is empty/whitespace, it SHOULD return 0 results if filtering works
        if q in ["", " ", "."] and len(results) > 0:
            print(f"FAILED: query '{q}' returned {len(results)} results (expected 0/few filtered)")
        elif q == "Bordeaux" and len(results) == 0:
            print(f"FAILED: query '{q}' returned 0 results (expected matches)")
        else:
            print(f"SUCCESS: query '{q}' behaved as expected (current state of code)")

if __name__ == "__main__":
    test_reproduction()
