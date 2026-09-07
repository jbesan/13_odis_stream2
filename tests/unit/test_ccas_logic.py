import os
import sys
import pytest

# Add app to path
sys.path.append(os.path.join(os.getcwd(), "app"))

from services.mcp_server import _search_ccas_logic, ensure_data_context


@pytest.fixture(scope="module", autouse=True)
def setup_data_context():
    """Ensure data context is loaded once for the module."""
    ensure_data_context()


def test_ccas_direct_match_bordeaux():
    """Test 1: Direct match for a commune with its own registered CCAS (Bordeaux - 33063)."""
    results = _search_ccas_logic("33063")
    assert len(results) >= 1, "Expected at least 1 CCAS for Bordeaux"
    # All returned CCAS must belong to Bordeaux
    for r in results:
        assert r.get("codgeo") == "33063"
        assert "CCAS" in r.get("nom", "") or "Centre communal" in r.get("nom", "")


def test_ccas_fallback_to_bassin_de_vie():
    """Test 2: Fallback to Bassin de Vie when commune has no direct CCAS (L'Abergement-de-Varey - 01002)."""
    results = _search_ccas_logic("01002")
    assert len(results) > 0, "Expected BV CCAS fallback for 01002"
    # None of the results should have 01002 as direct codgeo
    for r in results:
        assert r.get("codgeo") != "01002"
        assert r.get("codgeo") is not None


def test_ccas_no_ccas_in_bv_returns_empty():
    """Test 3: Isolated commune with no CCAS in its entire Bassin de Vie (Sainte-Hermine - 85223)."""
    results = _search_ccas_logic("85223")
    assert results == [], f"Expected empty list for commune without BV CCAS, got: {results}"


def test_ccas_invalid_code_returns_empty():
    """Test 4: Non-existent or invalid INSEE codes return an empty list gracefully."""
    assert _search_ccas_logic("99999") == []
    assert _search_ccas_logic("") == []
    assert _search_ccas_logic("ABCDE") == []

