from agents.state import compute_criteria_hash
from core.models import SearchCriterias, CriteriaItem

def test_criteria_hashing_stability():
    """Verify that hashing is stable and sensitive to changes."""
    c1 = SearchCriterias(commune_actuelle=CriteriaItem(code="75056", label="Paris"))
    c2 = SearchCriterias(commune_actuelle=CriteriaItem(code="75056", label="Paris"))
    c3 = SearchCriterias(commune_actuelle=CriteriaItem(code="69123", label="Lyon"))
    
    h1 = compute_criteria_hash(c1)
    h2 = compute_criteria_hash(c2)
    h3 = compute_criteria_hash(c3)
    
    assert h1 == h2, "Same criteria should yield same hash"
    assert h1 != h3, "Different criteria should yield different hash"
