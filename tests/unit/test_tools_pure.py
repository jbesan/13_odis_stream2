
import pytest
from app.agents.tools import set_focus_city, update_search_criteria

def test_set_focus_city_pure():
    """Verify set_focus_city returns a string and has no side effects (on this scope)."""
    res = set_focus_city("Paris")
    assert "Paris" in res
    assert "SUCCÈS" in res

def test_update_criteria_pure():
    """Verify update_search_criteria returns a string."""
    res = update_search_criteria({"nb_adultes": 2})
    assert "SUCCESS" in res
