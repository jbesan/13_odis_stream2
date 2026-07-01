import sys
import os
import logging

sys.path.append(os.getcwd())

from utils.data_loader import load_all_data_raw
from services.mcp_server import set_data_context, _search_referentiels_logic

logging.basicConfig(level=logging.ERROR)  # Quiet logs


def test_robust_search():
    print("Loading data...")
    data = load_all_data_raw()
    set_data_context(data)

    queries = [
        ("déménagement", "rome_codes"),
        ("propreté", "rome_codes"),
        ("patrimoine", "waldec_codes"),
        ("gastronomie", "waldec_codes"),
    ]

    for q, domain in queries:
        results = _search_referentiels_logic(q, domain=domain)
        assert isinstance(results, list), (
            f"Expected list for query '{q}', got {type(results)}"
        )
        assert len(results) > 0, f"Expected non-empty search results for query '{q}'"
        for r in results:
            assert isinstance(r, dict), "Each result must be a dictionary"
            assert "code" in r, "Result missing 'code' key"
            assert "label" in r, "Result missing 'label' key"
            assert isinstance(r["code"], str) and len(r["code"]) > 0
            assert isinstance(r["label"], str) and len(r["label"]) > 0


if __name__ == "__main__":
    test_robust_search()
