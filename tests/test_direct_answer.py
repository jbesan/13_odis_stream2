import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.models import SearchCriterias, CommuneResult, SearchResultsData, CriteriaItem
from agents.graph import create_odis_graph
from agents.state import GraphState, ODISDeps, compute_criteria_hash
from agents.ts_agent import ts_agent, SwarmPlan, ExpertTask
from agents.housing_expert import housing_expert_agent, HousingResult
from agents.synthesizer import synthesizer_agent

@pytest.fixture
def mock_deps():
    state = GraphState()
    state.search_criteria = SearchCriterias(
        commune_actuelle=CriteriaItem(code="13001", label="Marseille"),
        nb_adultes=1
    )
    current_hash = compute_criteria_hash(state.search_criteria)
    state.criteria_hash = current_hash
    
    marseille = CommuneResult(codgeo="13001", name="Marseille", population=800000, global_score=0.9)
    state.search_results = SearchResultsData(
        search_hash=current_hash,
        results=[marseille],
        current_geo=marseille
    )
    state.focus_city = CommuneResult(name="Marseille", codgeo="13001")
    state.messages.append({"role": "user", "content": "Quel est le loyer moyen à Marseille ?"})
    
    mock_client = MagicMock()
    return ODISDeps(state=state, client=mock_client)

@pytest.mark.asyncio
async def test_direct_answer_bypass(mock_deps):
    graph = create_odis_graph()
    mock_deps.state.execution_mode = 'specific_ask'
    
    ts_plan = SwarmPlan(direct_answer="Le loyer moyen à Marseille est de 15€/m².", tasks=[])
    mock_ts_res = MagicMock()
    mock_ts_res.output = ts_plan
    # Mocking usage to return values
    usage_mock = MagicMock()
    usage_mock.input_tokens = 10
    usage_mock.output_tokens = 10
    usage_mock.total_tokens = 20
    usage_mock.requests = 1
    mock_ts_res.usage = MagicMock(return_value=usage_mock)
    
    with patch.object(ts_agent, 'run', new_callable=AsyncMock) as mock_ts_run:
        mock_ts_run.return_value = mock_ts_res
        
        final_answer_end = await graph.run(state=mock_deps.state, deps=mock_deps)
        
        # Extract direct answer from End node
        assert hasattr(final_answer_end, "data")
        assert final_answer_end.data == "Le loyer moyen à Marseille est de 15€/m²."
        
        # Verify odis_synthesis is updated
        city_res = mock_deps.state.search_results.get_by_code("13001")
        assert city_res is not None
        assert len(city_res.odis_synthesis) > 0
        assert city_res.odis_synthesis[-1]["role"] == "assistant"
        assert city_res.odis_synthesis[-1]["content"] == "Le loyer moyen à Marseille est de 15€/m²."

@pytest.mark.asyncio
async def test_swarm_and_synthesis_flow(mock_deps):
    graph = create_odis_graph()
    
    ts_plan = SwarmPlan(
        direct_answer=None,
        tasks=[
            ExpertTask(
                expert="housing_expert",
                task_description="Vérifie le prix du logement",
                skill_cards=["basic_housing"]
            )
        ]
    )
    mock_ts_res = MagicMock()
    mock_ts_res.output = ts_plan
    usage_mock_ts = MagicMock()
    usage_mock_ts.input_tokens = 10
    usage_mock_ts.output_tokens = 10
    usage_mock_ts.total_tokens = 20
    usage_mock_ts.requests = 1
    mock_ts_res.usage = MagicMock(return_value=usage_mock_ts)
    
    housing_out = HousingResult(searched="Recherche loyer", result="Loyer moyen: 15€/m²")
    mock_housing_res = MagicMock()
    mock_housing_res.output = housing_out
    usage_mock_house = MagicMock()
    usage_mock_house.input_tokens = 10
    usage_mock_house.output_tokens = 10
    usage_mock_house.total_tokens = 20
    usage_mock_house.requests = 1
    mock_housing_res.usage = MagicMock(return_value=usage_mock_house)
    
    mock_synth_res = MagicMock()
    mock_synth_res.output = "Synthèse finale de Marseille"
    usage_mock_synth = MagicMock()
    usage_mock_synth.input_tokens = 10
    usage_mock_synth.output_tokens = 10
    usage_mock_synth.total_tokens = 20
    usage_mock_synth.requests = 1
    mock_synth_res.usage = MagicMock(return_value=usage_mock_synth)
    
    with patch.object(ts_agent, 'run', new_callable=AsyncMock) as mock_ts_run, \
         patch.object(housing_expert_agent, 'run', new_callable=AsyncMock) as mock_housing_run, \
         patch.object(synthesizer_agent, 'run', new_callable=AsyncMock) as mock_synth_run:
         
        mock_ts_run.return_value = mock_ts_res
        mock_housing_run.return_value = mock_housing_res
        mock_synth_run.return_value = mock_synth_res
        
        final_answer_end = await graph.run(state=mock_deps.state, deps=mock_deps)
        
        assert hasattr(final_answer_end, "data")
        assert final_answer_end.data == "Synthèse finale de Marseille"
        
        # Verify expert analysis was merged
        city_res = mock_deps.state.search_results.get_by_code("13001")
        assert city_res is not None
        assert "housing_expert" in city_res.expert_analysis
        assert "Loyer moyen: 15€/m²" in city_res.expert_analysis["housing_expert"]
        
        # Verify odis_synthesis is updated with the synthesizer output
        assert len(city_res.odis_synthesis) > 0
        assert city_res.odis_synthesis[-1]["role"] == "assistant"
        assert city_res.odis_synthesis[-1]["content"] == "Synthèse finale de Marseille"


