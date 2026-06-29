import pytest
from agents.state import GraphState, SearchResultsData, CommuneResult
from core.models import SearchCriterias, EmploymentMetrics, HousingMetrics

def test_rehydration_logic():
    """
    Tests that a raw dictionary can be correctly rehydrated into a GraphState
    without losing nested thematic data (Employment, Housing).
    """
    # 1. Mock Raw Data (JSON-like)
    raw_input = {
        "search_criteria": {
            "commune_actuelle": {"code": "33000", "label": "Bordeaux"},
            "loc_search_area": "Gironde",
            "weight_profile": "Standard"
        },
        "search_results": {
            "search_hash": "test_hash",
            "results": [
                {
                    "codgeo": "33063",
                    "name": "Bordeaux",
                    "population": 250000,
                    "global_score": 85.5,
                    "employment": {
                        "standard_jobs_total": 1200,
                        "standard_jobs_summary": {"A1234": 10},
                        "cat_score": 75.0
                    },
                    "housing": {
                        "price_per_sqm": 15.5,
                        "cat_score": 60.0
                    }
                }
            ],
            "current_geo": {
                "codgeo": "75000",
                "name": "Paris",
                "population": 2000000,
                "global_score": 0.0
            }
        },
        "focus_city": {"name": "Bordeaux", "codgeo": "33063"},
        "execution_mode": "full_analysis"
    }

    # 2. We will implement rehydrate_graph_state in utils.py
    # For now, let's see if manual validation works or if we find any issues
    from app.agents.utils import rehydrate_graph_state
    
    state = rehydrate_graph_state(raw_input)
    
    # 3. Assertions
    assert isinstance(state, GraphState)
    assert state.search_results.search_hash == "test_hash"
    assert len(state.search_results.results) == 1
    
    city = state.search_results.results[0]
    assert city.name == "Bordeaux"
    
    # CRITICAL: Check thematic data
    assert city.employment.standard_jobs_total == 1200
    assert city.housing.price_per_sqm == 15.5
    assert city.employment.cat_score == 75.0

if __name__ == "__main__":
    pytest.main([__file__])
