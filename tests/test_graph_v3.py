from agents.state import ODISGraphState, compute_criteria_hash, merge_commune_artifacts
from langgraph.graph import END
from core.models import SearchCriterias

def test_criteria_hashing_stability():
    """Verify that hashing is stable and sensitive to changes."""
    c1 = SearchCriterias(commune_actuelle="Paris")
    c2 = SearchCriterias(commune_actuelle="Paris")
    c3 = SearchCriterias(commune_actuelle="Lyon")
    
    h1 = compute_criteria_hash(c1)
    h2 = compute_criteria_hash(c2)
    h3 = compute_criteria_hash(c3)
    
    assert h1 == h2, "Same criteria should yield same hash"
    assert h1 != h3, "Different criteria should yield different hash"

def test_merge_commune_artifacts_logic():
    """Verify that merging commune artifacts works across parallel branches and cities."""
    h = "hash1"
    
    # State with some initial data (normalized)
    state = {"paris": {h: {"scout": "old_scout"}}}
    
    # Update from Web for Paris
    update1 = {"Paris": {h: {"web": "new_web"}}}
    state = merge_commune_artifacts(state, update1)
    
    # Update from Scout for Lyon
    update2 = {"Lyon": {h: {"scout": "lyon_scout"}}}
    state = merge_commune_artifacts(state, update2)
    
    assert state["paris"][h]["scout"] == "old_scout"
    assert state["paris"][h]["web"] == "new_web"
    assert state["lyon"][h]["scout"] == "lyon_scout"



