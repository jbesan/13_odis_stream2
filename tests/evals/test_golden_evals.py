import os
import json
import pytest
from pathlib import Path
from agents.graph import create_odis_graph
from agents.state import GraphState, ODISDeps, compute_criteria_hash
from agents.agent_config import get_gemini_client
from core.models import SearchCriterias, SearchResultsData, CommuneResult, CriteriaItem

# Skip by default to save tokens and costs unless RUN_EVALS=true is set
run_evals = os.getenv("RUN_EVALS", "false").lower() == "true"

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(not run_evals, reason="Evaluation tests are skipped by default. Set RUN_EVALS=true to run them.")
]

@pytest.mark.asyncio
async def test_golden_scenarios_evaluation():
    """
    Evaluation runner that executes the live graph against golden scenarios
    from golden_scenarios.json and asserts quality gates on outputs.
    """
    # Load scenarios
    scenarios_file = Path(__file__).parent / "golden_scenarios.json"
    assert scenarios_file.exists(), "golden_scenarios.json is missing"
    
    with open(scenarios_file, 'r', encoding='utf-8') as f:
        scenarios = json.load(f)
        
    client = get_gemini_client()
    graph = create_odis_graph()
    
    for scenario in scenarios:
        # 1. Rehydrate search criteria from scenario expectations
        criteria = SearchCriterias(
            commune_actuelle=CriteriaItem(code="33063", label="Bordeaux"),
            nb_adultes=1,
            nb_enfants=2,
            codes_metiers=[[CriteriaItem(code=scenario["expected_rome_code"], label="Metier Target")]],
            loc_search_area="departement",
            loc_search_code=["17"]
        )
        
        # 2. Build target commune result
        target_commune = CommuneResult(
            codgeo=scenario["required_insee_code"],
            name=scenario["expected_focus_city"],
            population=6800,
            global_score=0.8
        )
        
        current_hash = compute_criteria_hash(criteria)
        search_results = SearchResultsData(
            search_hash=current_hash,
            results=[target_commune],
            current_geo=target_commune
        )
        
        # 3. Initialize GraphState
        state = GraphState(
            search_criteria=criteria,
            search_results=search_results,
            focus_city=target_commune,
            criteria_hash=current_hash,
            execution_mode=scenario["expected_swarm_mode"]
        )
        
        # Add user query
        state.messages.append({"role": "user", "content": scenario["input_query"]})
        
        # 4. Invoke graph execution
        deps = ODISDeps(state=state, client=client)
        final_answer_end = await graph.run(state=state, deps=deps)
        
        # 5. Assert Swarm Mode & State Decisions
        assert state.execution_mode == scenario["expected_swarm_mode"], "Graph execution mode mismatch"
        
        # 6. Assert Experts results were populated
        city_res = state.search_results.get_by_code(scenario["required_insee_code"])
        assert city_res is not None, "Focus city missing in search results"
        
        for expert in scenario["expected_experts"]:
            assert expert in city_res.expert_analysis, f"Expert analysis for '{expert}' was not generated"
            analysis_text = city_res.expert_analysis[expert]
            assert len(analysis_text) > 50, f"Analysis for '{expert}' is too short/empty"
            
        # 7. Assert Synthesizer output contains key keywords
        assert final_answer_end is not None, "Graph returned no final answer"
        res_str = final_answer_end.data if hasattr(final_answer_end, "data") else str(final_answer_end)
        
        for keyword in scenario["synthesis_keywords"]:
            assert keyword.lower() in res_str.lower(), f"Synthesis is missing expected keyword: '{keyword}'"
