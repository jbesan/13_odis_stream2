import pytest
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from unittest.mock import patch, MagicMock, AsyncMock

from agents.education_expert import education_expert_agent, EducationResult
from agents.healthcare_expert import healthcare_expert_agent, HealthcareResult
from agents.housing_expert import housing_expert_agent, HousingResult
from agents.mobility_expert import mobility_expert_agent, MobilityResult
from agents.job_hunter import job_hunter_agent, JobHunterResult
from agents.social_integration_expert import (
    social_integration_expert_agent,
    SocialIntegrationResult,
)
from agents.ts_agent import ts_agent, SwarmPlan
from agents.tools import (
    JobOfferSearchQuery,
    InclusionJobSearchQuery,
    RnaSearchQuery,
    search_inclusion_jobs_batch_tool,
)
from agents.state import GraphState, ODISDeps
from core.models import CommuneResult


@pytest.fixture
def mock_deps():
    state = GraphState(
        odis_brief="Dossier de relocalisation",
        focus_city=CommuneResult(name="Saint-Jean-d'Angély", codgeo="17347"),
    )
    mock_client = MagicMock()
    return ODISDeps(state=state, client=mock_client)


@pytest.mark.asyncio
async def test_ts_agent_planning_offline(mock_deps):
    """
    Verifies offline that ts_agent coordinator correctly plans swarm tasks
    and outputs a SwarmPlan structure.
    """

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        result_tool_name = (
            info.output_tools[0].name if info.output_tools else "final_result"
        )
        args = {
            "swarm_mode": "full_analysis",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Recherche logement urgent",
                    "skill_cards": ["housing_full_analysis"],
                }
            ],
        }
        return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    with ts_agent.override(model=FunctionModel(call_model)):
        result = await ts_agent.run(
            "Fais une analyse complète pour Saint-Jean-d'Angély.", deps=mock_deps
        )
        assert isinstance(result.output, SwarmPlan)
        assert result.output.swarm_mode == "full_analysis"
        assert len(result.output.tasks) == 1
        assert result.output.tasks[0].expert == "housing_expert"
        assert result.output.tasks[0].task_description == "Recherche logement urgent"


@pytest.mark.asyncio
async def test_education_agent_tool_calling_offline(mock_deps):
    """
    Verifies offline that education_expert_agent interprets intent to call
    search_places_batch_tool with correct parameters and outputs structural EducationResult.
    """

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            args = {
                "queries": ["crèche", "école primaire"],
                "location": "Saint-Jean-d'Angély, Nouvelle-Aquitaine",
            }
            return ModelResponse(parts=[ToolCallPart("search_places_batch_tool", args)])
        else:
            result_tool_name = (
                info.output_tools[0].name if info.output_tools else "final_result"
            )
            args = {
                "result": "Analyse: 2 crèches et 3 écoles trouvées.",
            }
            return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    with education_expert_agent.override(model=FunctionModel(call_model)):
        with patch(
            "agents.tools.search_places_batch", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = [{"name": "Crèche 1"}, {"name": "École 1"}]

            result = await education_expert_agent.run(
                "Quelles sont les écoles à Saint-Jean-d'Angély ?", deps=mock_deps
            )

            mock_search.assert_called_once_with(
                ["crèche", "école primaire"], "Saint-Jean-d'Angély, Nouvelle-Aquitaine"
            )
            assert isinstance(result.output, EducationResult)
            assert result.output.result == "Analyse: 2 crèches et 3 écoles trouvées."


@pytest.mark.asyncio
async def test_healthcare_agent_tool_calling_offline(mock_deps):
    """
    Verifies offline that healthcare_expert_agent interprets intent to call
    search_places_batch_tool with correct parameters and outputs structural HealthcareResult.
    """

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            args = {
                "queries": ["hôpital", "PMI"],
                "location": "Saint-Jean-d'Angély, Nouvelle-Aquitaine",
            }
            return ModelResponse(parts=[ToolCallPart("search_places_batch_tool", args)])
        else:
            result_tool_name = (
                info.output_tools[0].name if info.output_tools else "final_result"
            )
            args = {
                "result": "Analyse: 1 hôpital et 1 PMI trouvés.",
            }
            return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    with healthcare_expert_agent.override(model=FunctionModel(call_model)):
        with patch(
            "agents.tools.search_places_batch", new_callable=AsyncMock
        ) as mock_search:
            mock_search.return_value = [{"name": "Hôpital 1"}, {"name": "PMI 1"}]

            result = await healthcare_expert_agent.run(
                "Y a-t-il des hôpitaux ?", deps=mock_deps
            )

            mock_search.assert_called_once_with(
                ["hôpital", "PMI"], "Saint-Jean-d'Angély, Nouvelle-Aquitaine"
            )
            assert isinstance(result.output, HealthcareResult)
            assert result.output.result == "Analyse: 1 hôpital et 1 PMI trouvés."


@pytest.mark.asyncio
async def test_housing_agent_tool_calling_offline(mock_deps):
    """
    Verifies offline that housing_expert_agent interprets intent to call
    its batch places and route tools and outputs structural HousingResult.
    """

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            args = {
                "queries": ["CADA", "CHRS"],
                "location": "Saint-Jean-d'Angély, Nouvelle-Aquitaine",
            }
            return ModelResponse(parts=[ToolCallPart("search_places_batch_tool", args)])
        elif len(messages) == 3:  # After first tool response, call route tool
            args = {
                "origin": "Saint-Jean-d'Angély",
                "destination": "Niort",
                "mode": "transit",
            }
            return ModelResponse(parts=[ToolCallPart("compute_routes_tool", args)])
        else:
            result_tool_name = (
                info.output_tools[0].name if info.output_tools else "final_result"
            )
            args = {
                "result": "Analyse: structures identifiées et trajet 45min.",
            }
            return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    with housing_expert_agent.override(model=FunctionModel(call_model)):
        with (
            patch(
                "agents.tools.search_places_batch", new_callable=AsyncMock
            ) as mock_search,
            patch(
                "agents.tools.compute_routes",
                return_value={"duration": "45min"},
            ) as mock_routes,
        ):
            mock_search.return_value = [{"name": "CADA 1"}]

            result = await housing_expert_agent.run(
                "Quels sont les hébergements et le trajet vers Niort ?", deps=mock_deps
            )

            mock_search.assert_called_once_with(
                ["CADA", "CHRS"], "Saint-Jean-d'Angély, Nouvelle-Aquitaine"
            )
            mock_routes.assert_called_once_with(
                "Saint-Jean-d'Angély", "Niort", "transit"
            )
            assert isinstance(result.output, HousingResult)
            assert (
                result.output.result
                == "Analyse: structures identifiées et trajet 45min."
            )


@pytest.mark.asyncio
async def test_mobility_agent_tool_calling_offline(mock_deps):
    """
    Verifies offline that mobility_expert_agent interprets intent to call
    compute_routes_tool and outputs structural MobilityResult.
    """

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            args = {
                "origin": "Saint-Jean-d'Angély",
                "destination": "Bordeaux",
                "mode": "transit",
            }
            return ModelResponse(parts=[ToolCallPart("compute_routes_tool", args)])
        else:
            result_tool_name = (
                info.output_tools[0].name if info.output_tools else "final_result"
            )
            args = {
                "result": "Itinéraire trouvé: 1h30 en train.",
            }
            return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    with mobility_expert_agent.override(model=FunctionModel(call_model)):
        with patch(
            "agents.tools.compute_routes", return_value={"duration": "1h30"}
        ) as mock_routes:
            result = await mobility_expert_agent.run(
                "Combien de temps pour aller à Bordeaux en train ?", deps=mock_deps
            )

            mock_routes.assert_called_once_with(
                "Saint-Jean-d'Angély", "Bordeaux", "transit"
            )
            assert isinstance(result.output, MobilityResult)
            assert result.output.result == "Itinéraire trouvé: 1h30 en train."


@pytest.mark.asyncio
async def test_job_hunter_agent_tool_calling_offline(mock_deps):
    """
    Verifies offline that job_hunter_agent interprets intent to call
    search_job_offers_batch_tool and outputs structural JobHunterResult.
    """

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            args = {"searches": [{"location": "17347", "rome": "I1604"}]}
            return ModelResponse(
                parts=[ToolCallPart("search_job_offers_batch_tool", args)]
            )
        else:
            result_tool_name = (
                info.output_tools[0].name if info.output_tools else "final_result"
            )
            args = {
                "result": "Analyse: 3 offres trouvées.",
            }
            return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    with job_hunter_agent.override(model=FunctionModel(call_model)):
        with patch(
            "agents.tools.search_job_offers_batch", new_callable=AsyncMock
        ) as mock_jobs:
            mock_jobs.return_value = {"offres": [{"id": "123"}]}

            result = await job_hunter_agent.run(
                "Cherche des offres de mécanicien.", deps=mock_deps
            )

            mock_jobs.assert_called_once_with(
                [JobOfferSearchQuery(location="17347", rome="I1604")]
            )
            assert isinstance(result.output, JobHunterResult)
            assert result.output.result == "Analyse: 3 offres trouvées."


@pytest.mark.asyncio
async def test_social_integration_agent_tool_calling_offline(mock_deps):
    """
    Verifies offline that social_integration_expert_agent interprets intent to call
    search_rna_rag_batch_tool and outputs structural SocialIntegrationResult.
    """

    def call_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            args = {"queries": ["cours de français", "football"], "codgeo": "17347"}
            return ModelResponse(
                parts=[ToolCallPart("search_rna_rag_batch_tool", args)]
            )
        else:
            result_tool_name = (
                info.output_tools[0].name if info.output_tools else "final_result"
            )
            args = {
                "result": "Analyse: 2 associations trouvées.",
            }
            return ModelResponse(parts=[ToolCallPart(result_tool_name, args)])

    with social_integration_expert_agent.override(model=FunctionModel(call_model)):
        with patch(
            "agents.tools.search_rna_rag_batch",
            new_callable=AsyncMock,
        ) as mock_rna:
            mock_rna.return_value = [{"name": "Club Foot"}]

            result = await social_integration_expert_agent.run(
                "Cherche des cours de français et du football.", deps=mock_deps
            )

            mock_rna.assert_called_once_with(
                ["cours de français", "football"], "17347", top_k=10
            )
            assert isinstance(result.output, SocialIntegrationResult)
            assert result.output.result == "Analyse: 2 associations trouvées."


def test_inclusion_and_rna_search_query_insee_validation():
    """Verify that InclusionJobSearchQuery and RnaSearchQuery accept 5-digit and Corsican INSEE codes but reject departments and free text."""
    import pydantic

    # Valid metropolitan INSEE (5 digits) with required query
    q1 = InclusionJobSearchQuery(location="13018", query="maraichage")
    assert q1.location == "13018"
    assert q1.query == "maraichage"

    # Valid Corsican INSEE (2A/2B + 3 digits) with required query
    q2 = InclusionJobSearchQuery(location="2A004", query="espaces verts")
    assert q2.location == "2A004"
    q3 = InclusionJobSearchQuery(location="2B033", query="accueil")
    assert q3.location == "2B033"

    # Missing query - must fail validation
    with pytest.raises(pydantic.ValidationError):
        InclusionJobSearchQuery(location="13018")

    # Invalid department code (2 digits) - must fail validation
    with pytest.raises(pydantic.ValidationError):
        InclusionJobSearchQuery(location="13", query="accueil")

    # Invalid free-text / postal code with non-digits
    with pytest.raises(pydantic.ValidationError):
        InclusionJobSearchQuery(location="Marseille", query="accueil")

    # RNA validation: metropolitan and Corsican
    rna_metro = RnaSearchQuery(queries=["entraide"], codgeo="33063")
    assert rna_metro.codgeo == "33063"

    rna_corsica = RnaSearchQuery(queries=["entraide"], codgeo="2A004")
    assert rna_corsica.codgeo == "2A004"

    # RNA invalid department code
    with pytest.raises(pydantic.ValidationError):
        RnaSearchQuery(queries=["entraide"], codgeo="33")


@pytest.mark.asyncio
async def test_search_inclusion_jobs_batch_tool_execution():
    """Verify that search_inclusion_jobs_batch_tool executes queries and gathers results."""
    with patch("agents.tools._search_inclusion_jobs_logic") as mock_logic:
        mock_logic.return_value = {
            "offres": [{"id": 1, "nom": "Structure A"}],
            "total": 1,
        }

        queries = [
            InclusionJobSearchQuery(location="13018", rome="A1203", query="jardinier"),
            InclusionJobSearchQuery(location="2A004", query="accueil"),
        ]
        results = await search_inclusion_jobs_batch_tool(queries)

        assert "A1203|13018|jardinier" in results
        assert "|2A004|accueil" in results
        assert results["A1203|13018|jardinier"]["total"] == 1
        assert results["|2A004|accueil"]["total"] == 1
        assert mock_logic.call_count == 2
