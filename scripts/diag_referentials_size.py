import sys
import os
sys.path.append(os.path.join(os.getcwd(), 'app'))

import pandas as pd
from utils.data_loader import load_all_data_raw
from services.mcp_server import _search_referentiels_logic, set_data_context

def diagnostic():
    data = load_all_data_raw()
    set_data_context(data)
    
    # Test queries that might return many/large results
    test_queries = [
        ("Boulanger", "rome_codes"),
        ("Social", "fap_codes"),
        ("Football", "waldec_codes"),
        ("FLE", "inclusion_services"),
        ("Informatique", "formation_codes")
    ]
    
    for query, domain in test_queries:
        res = _search_referentiels_logic(query, domain)
        size_chars = len(str(res))
        tokens_est = size_chars / 4
        print(f"Domain: {domain:20} | Query: {query:15} | Results: {len(res):2} | Est Tokens: {tokens_est:.0f}")

if __name__ == "__main__":
    diagnostic()
