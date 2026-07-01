import sys
import os
import logging

# Add app to path
sys.path.append(os.path.join(os.getcwd(), "app"))

from services import mcp_server
from services.mcp_server import _search_ccas_logic, ensure_data_context

logging.basicConfig(level=logging.INFO)


def test_ccas_search():
    print("🚀 Initializing data context...")
    ensure_data_context()

    # Test 1: Direct match (Bordeaux - 33063)
    print("\n🔍 Test 1: Direct match for Bordeaux (33063)")
    results = _search_ccas_logic("33063")
    print(f"Found {len(results)} CCAS")
    for r in results:
        print(f" - {r.get('nom', 'N/A')} ({r.get('codgeo', 'N/A')})")

    # Test 2: Fallback to Bassin de Vie (Something small near Bordeaux, e.g. Bouliac - 33067)
    # Checking if Bouliac has a CCAS in the data
    ccas_df = mcp_server.DATA_CONTEXT["structures_ccas"]
    bouliac_ccas = ccas_df[ccas_df["codgeo"] == "33067"]

    print(
        f"\n🔍 Test 2: Fallback for Bouliac (33067) - has local CCAS? {not bouliac_ccas.empty}"
    )
    results = _search_ccas_logic("33067")
    print(f"Found {len(results)} CCAS (Expected to include local or BV ones)")
    for r in results:
        print(f" - {r.get('nom', 'N/A')} ({r.get('codgeo', 'N/A')})")

    # Test 3: Invalid code
    print("\n🔍 Test 3: Invalid code")
    results = _search_ccas_logic("99999")
    print(f"Found {len(results)} CCAS (Expected 0)")

    # Test 4: Strict Fallback (L'Abergement-de-Varey - 01002)
    # This commune has no CCAS but BV 01004 has CCAS
    print("\n🔍 Test 4: Strict Fallback for L'Abergement-de-Varey (01002)")
    results = _search_ccas_logic("01002")
    print(f"Found {len(results)} CCAS (Expected > 0 from BV 01004)")
    for r in results:
        print(f" - {r.get('nom', 'N/A')} ({r.get('codgeo', 'N/A')})")


if __name__ == "__main__":
    test_ccas_search()
