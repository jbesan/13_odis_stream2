import os
import json
import pytest
from pathlib import Path
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import LLMJudge
from pydantic_ai.models import ModelSettings

from agents.graph import create_odis_graph
from agents.state import GraphState, ODISDeps, compute_criteria_hash
from agents.agent_config import get_gemini_client, get_p_model
from core.models import SearchCriterias, SearchResultsData

# Skip by default to save tokens and costs unless RUN_EVALS=true is set
run_evals = os.getenv("RUN_EVALS", "false").lower() == "true"

pytestmark = [
    pytest.mark.eval,
    pytest.mark.skipif(
        not run_evals,
        reason="Evaluation tests are skipped by default. Set RUN_EVALS=true to run them.",
    ),
]


@pytest.mark.asyncio
async def test_golden_scenarios_evaluation():
    """
    Evaluation runner that executes the live graph against golden scenarios
    from golden_scenarios.json using pydantic_evals and asserts quality gates.
    """
    # 1. Load scenarios
    scenarios_file = Path(__file__).parent / "golden_scenarios.json"
    assert scenarios_file.exists(), "golden_scenarios.json is missing"

    with open(scenarios_file, "r", encoding="utf-8") as f:
        scenarios = json.load(f)

    client = get_gemini_client()
    graph = create_odis_graph()

    # 2. Build judge model instance from ODIS agent config to force Vertex AI
    judge_model = get_p_model("synthesizer", client=client)

    # 3. Iterate through scenarios
    for scenario in scenarios:
        # Rehydrate state info to be captured by run_graph_task closure
        criteria = SearchCriterias.model_validate(scenario["search_criteria"])
        search_results = SearchResultsData.model_validate(scenario["search_results"])

        target_commune = search_results.current_geo
        current_hash = compute_criteria_hash(criteria)

        # Define the task function to be evaluated by pydantic_evals
        async def run_graph_task(inputs: str) -> str:
            # Initialize GraphState with the high-fidelity criteria and results
            state = GraphState(
                search_criteria=criteria,
                search_results=search_results,
                focus_city=target_commune,
                criteria_hash=current_hash,
                execution_mode=scenario["expected_swarm_mode"],
            )
            state.messages.append({"role": "user", "content": scenario["input_query"]})

            # Execute graph
            deps = ODISDeps(state=state, client=client)

            # Hook the OnlineEvaluation capability to show up in the dedicated Logfire Live Evals panel
            from pydantic_evals.online_capability import OnlineEvaluation
            from agents.synthesizer import synthesizer_agent

            online_eval = OnlineEvaluation(evaluators=[judge])
            synthesizer_agent.root_capability.capabilities.append(online_eval)

            try:
                final_answer_end = await graph.run(state=state, deps=deps)
            finally:
                # Clean up capability to avoid leaks
                synthesizer_agent.root_capability.capabilities.remove(online_eval)

            # Verify graph state decisions during execution
            assert state.execution_mode == scenario["expected_swarm_mode"], (
                "Graph execution mode mismatch"
            )
            city_res = state.search_results.get_by_code(
                scenario["search_results"]["results"][0]["codgeo"]
            )
            assert city_res is not None, "Focus city missing in search results"

            # Verify that experts results were populated in state
            for expert in scenario["expected_experts"]:
                assert expert in city_res.expert_analysis, (
                    f"Expert analysis for '{expert}' was not generated"
                )
                assert len(city_res.expert_analysis[expert]) > 50, (
                    f"Analysis for '{expert}' is too short/empty"
                )

            # Verify that token usage metrics were captured and merged
            assert state.usage is not None
            assert state.usage.total_tokens > 0, (
                "No token usage was recorded in the state"
            )

            return (
                str(final_answer_end.data)
                if hasattr(final_answer_end, "data")
                else str(final_answer_end)
            )

        # Define the test case using candidate odis_brief as clean input representation
        case = Case(
            name=scenario["scenario_name"],
            inputs=scenario["odis_brief"],
        )

        # Build LLMJudge evaluator using the global default judge model config
        from pydantic_evals.evaluators.llm_as_a_judge import set_default_judge_model

        set_default_judge_model(judge_model)

        judge = LLMJudge(
            rubric=(
                "L'analyse fournie en sortie répond-elle de manière pertinente aux besoins de la personne décrits en entrée ? "
                "Vérifie spécifiquement si l'analyse couvre :\n"
                "1. La recherche d'emploi en mécanique automobile (car mechanic, mécanicien automobile).\n"
                "2. La garde d'enfants / petite enfance pour deux enfants (crèche / assistante maternelle / écoles).\n"
                "3. La santé (soutien psychologique / addictologie).\n"
                "4. Les aspects d'inclusion (CCAS, cours de français FLE, association de football).\n"
                "5. La proximité de la mer et d'un lieu de culte (mosquée / temple).\n"
                "Réponds par True si l'analyse couvre l'essentiel de ces besoins de manière qualitative."
            ),
            include_input=True,
            model_settings=ModelSettings(temperature=0.0),
        )

        dataset = Dataset(name="odis_eval_dataset", cases=[case], evaluators=[judge])

        # Run evaluation
        report = await dataset.evaluate(run_graph_task)
        report.print()

        # 4. Verify evaluation results and assertions
        assert len(report.failures) == 0, (
            f"Evaluation encountered runtime failures: {report.failures}"
        )
        assert len(report.cases) == 1

        eval_case_result = report.cases[0]
        assertion_result = eval_case_result.assertions.get("LLMJudge")
        assert assertion_result is not None, (
            "LLMJudge assertion result was not recorded"
        )

        # Output evaluation reasoning for transparency
        print(f"\n[EVAL REASONING] {assertion_result.reason}")
        assert assertion_result.value is True, (
            f"LLMJudge failed. Reason: {assertion_result.reason}"
        )
