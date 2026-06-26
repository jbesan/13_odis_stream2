
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
    
    results = _search_referentiels_logic("fêtes", domain="waldec_codes")
    assert isinstance(results, list), "Expected list of results"
    assert len(results) > 0, "Expected non-empty search results for query 'fêtes'"
    for r in results:
        assert isinstance(r, dict), "Each result must be a dictionary"
        assert "code" in r, "Result missing 'code' key"
        assert "label" in r, "Result missing 'label' key"
        assert isinstance(r["code"], str) and len(r["code"]) > 0
        assert isinstance(r["label"], str) and len(r["label"]) > 0

if __name__ == "__main__":
    test_waldec()
