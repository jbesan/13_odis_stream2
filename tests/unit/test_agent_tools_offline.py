import pytest
from pydantic_ai import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from unittest.mock import patch, MagicMock, AsyncMock

from agents.education_expert import education_expert_agent, EducationResult
from agents.healthcare_expert import healthcare_expert_agent, HealthcareResult
from agents.state import GraphState, ODISDeps
from core.models import CommuneResult

@pytest.fixture
def mock_deps():
    state = GraphState(
        odis_brief="Dossier de relocalisation",
        focus_city=CommuneResult(name="Saint-Jean-d'Angély", codgeo="17347")
    )
    mock_client = MagicMock()
    return ODISDeps(state=state, client=mock_client)

@pytest.mark.asyncio
async def test_education_agent_tool_calling_offline(mock_deps):
    """
    Verifies offline that education_expert_agent interprets intent to call
    search_places_batch_tool with correct parameters and outputs structural EducationResult.
    """
    # 1. Define model function for FunctionModel to simulate LLM calls
    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        # First turn: call the search tool
        if len(messages) == 1:
            args = {
                'queries': ['crèche', 'école primaire'],
                'location': "Saint-Jean-d'Angély, Nouvelle-Aquitaine"
            }
            return ModelResponse(parts=[ToolCallPart('search_places_batch_tool', args)])
        else:
            # Second turn: return structured response
            result_tool_name = info.output_tools[0].name if info.output_tools else 'final_result'
            args = {
                'searched': 'Recherche de crèches et écoles',
                'result': 'Analyse: 2 crèches et 3 écoles trouvées.'
            }
            return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    # 2. Patch the underlying search helper functions to avoid hitting live APIs
    with education_expert_agent.override(model=FunctionModel(call_model)):
        with patch('agents.education_expert.search_places_batch', new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [{'name': 'Crèche 1'}, {'name': 'École 1'}]
            
            result = await education_expert_agent.run(
                "Quelles sont les écoles à Saint-Jean-d'Angély ?",
                deps=mock_deps
            )
            
            # Assert tool was called with correct parameters
            mock_search.assert_called_once_with(['crèche', 'école primaire'], "Saint-Jean-d'Angély, Nouvelle-Aquitaine")
            
            # Assert structured output is correct
            assert isinstance(result.output, EducationResult)
            assert result.output.searched == 'Recherche de crèches et écoles'
            assert result.output.result == 'Analyse: 2 crèches et 3 écoles trouvées.'

@pytest.mark.asyncio
async def test_healthcare_agent_tool_calling_offline(mock_deps):
    """
    Verifies offline that healthcare_expert_agent interprets intent to call
    search_places_batch_tool with correct parameters and outputs structural HealthcareResult.
    """
    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            args = {
                'queries': ['hôpital', 'PMI'],
                'location': "Saint-Jean-d'Angély, Nouvelle-Aquitaine"
            }
            return ModelResponse(parts=[ToolCallPart('search_places_batch_tool', args)])
        else:
            result_tool_name = info.output_tools[0].name if info.output_tools else 'final_result'
            args = {
                'searched': "Recherche d'hôpitaux et PMI",
                'result': 'Analyse: 1 hôpital et 1 PMI trouvés.'
            }
            return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    with healthcare_expert_agent.override(model=FunctionModel(call_model)):
        with patch('agents.healthcare_expert.search_places_batch', new_callable=AsyncMock) as mock_search:
            mock_search.return_value = [{'name': 'Hôpital 1'}, {'name': 'PMI 1'}]
            
            result = await healthcare_expert_agent.run(
                "Y a-t-il des hôpitaux ?",
                deps=mock_deps
            )
            
            mock_search.assert_called_once_with(['hôpital', 'PMI'], "Saint-Jean-d'Angély, Nouvelle-Aquitaine")
            
            assert isinstance(result.output, HealthcareResult)
            assert result.output.searched == "Recherche d'hôpitaux et PMI"
            assert result.output.result == 'Analyse: 1 hôpital et 1 PMI trouvés.'
