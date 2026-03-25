from agents.state import ODISGraphState, compute_criteria_hash, merge_search_results
from langgraph.graph import END
from core.models import SearchCriterias, CriteriaItem, SearchResultsData, CommuneResult

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

def test_merge_search_results_logic():
    """Verify that merging search results works for expert artifacts."""
    # 1. Initial state
    paris = CommuneResult(codgeo="75056", name="Paris", population=2000000, global_score=0.8)
    initial_results = SearchResultsData(
        search_hash="hash1",
        results=[paris],
        current_geo=paris
    )
    
    # 2. Update from Scout for Paris
    update1 = {
        "results": [
            {
                "codgeo": "75056",
                "expert_analysis": {"scout": "scout_report_v1"}
            }
        ]
    }
    
    state = merge_search_results(initial_results, update1)
    assert state.results[0].expert_analysis["scout"] == "scout_report_v1"
    
    # 3. Update from Web for Paris (Partial merge)
    update2 = {
        "results": [
            {
                "codgeo": "75056",
                "expert_analysis": {"web": "web_report_v1"}
            }
        ]
    }
    state = merge_search_results(state, update2)
    assert state.results[0].expert_analysis["scout"] == "scout_report_v1"
    assert state.results[0].expert_analysis["web"] == "web_report_v1"
    
    # 4. Add NEW city (Lyon)
    lyon = {
        "codgeo": "69123",
        "name": "Lyon",
        "population": 500000,
        "global_score": 0.7,
        "expert_analysis": {"scout": "lyon_scout"}
    }
    update3 = {"results": [lyon]}
    state = merge_search_results(state, update3)
    
    assert len(state.results) == 2
    assert state.get_by_code("69123").expert_analysis["scout"] == "lyon_scout"



