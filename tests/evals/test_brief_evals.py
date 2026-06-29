import os
import json
import pytest
from pathlib import Path
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import LLMJudge
from pydantic_ai.models import ModelSettings

from core.models import SearchCriterias, SearchResultsData
from agents.state import GraphState, ODISDeps, compute_criteria_hash
from agents.agent_config import get_gemini_client, get_p_model, get_model_settings
from agents.refiner import refiner_agent, RefinerResult

# Mark all tests in this file to require live Vertex AI credentials
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_EVALS", "false").lower() != "true",
    reason="RUN_EVALS=true is required to execute live AI evaluations"
)

@pytest.mark.asyncio
async def test_brief_scenarios_evaluation():
    """
    Evaluation runner that executes the Refiner Agent directly against golden scenarios
    from golden_scenarios.json using pydantic_evals and asserts quality gates.
    """
    # 1. Load scenarios
    scenarios_file = Path(__file__).parent / "golden_scenarios.json"
    assert scenarios_file.exists(), "golden_scenarios.json is missing"
    
    with open(scenarios_file, 'r', encoding='utf-8') as f:
        scenarios = json.load(f)
        
    client = get_gemini_client()
    refiner_model = get_p_model("refiner", client=client)
    judge_model = get_p_model("synthesizer", client=client)
    
    # 2. Iterate through scenarios
    import logfire
    for scenario in scenarios:
        logfire.info("Running evaluation test for scenario: {name}", name=scenario["scenario_name"])
        criteria = SearchCriterias.model_validate(scenario["search_criteria"])
        search_results = SearchResultsData.model_validate(scenario["search_results"])
        
        target_commune = search_results.current_geo
        current_hash = compute_criteria_hash(criteria)
        
        # Build LLMJudge evaluator for the brief and pitches
        from pydantic_evals.evaluators.llm_as_a_judge import set_default_judge_model
        set_default_judge_model(judge_model)
        
        judge = LLMJudge(
            rubric=(
                "Vérifie si le briefing et les pitchs générés répondent aux critères :\n"
                "1. Le briefing (sous BRIEF:) résume fidèlement le profil du bénéficiaire décrits en entrée "
                "(mécanicien automobile, deux enfants, besoin de santé/soutien psychologique, proximité mer/mosquée).\n"
                "2. Des pitchs (sous CITY PITCHES:) sont bien générés pour les communes du top.\n"
                "Note: L'agent doit pitcher les communes fournies en entrée (comme Saint-Jean-d'Angély), même si elles "
                "ne satisfont pas toutes les préférences (comme la mer). Ne pénalise pas l'agent sur la géographie réelle de la ville.\n"
                "Réponds par True si l'output respecte parfaitement ces consignes."
            ),
            include_input=True,
            model_settings=ModelSettings(temperature=0.0)
        )

        # Define the task function targeting the refiner agent directly
        async def run_refiner_task(inputs: str) -> str:
            state = GraphState(
                search_criteria=criteria,
                search_results=search_results,
                focus_city=target_commune,
                criteria_hash=current_hash,
                execution_mode="full_analysis"
            )
            
            deps = ODISDeps(state=state, client=client)
            
            # Hook the OnlineEvaluation capability to show up in the dedicated Logfire Live Evals panel
            from pydantic_evals.online_capability import OnlineEvaluation
            online_eval = OnlineEvaluation(evaluators=[judge])
            refiner_agent.root_capability.capabilities.append(online_eval)
            
            try:
                # Execute refiner_agent directly (1 single LLM call)
                result_run = await refiner_agent.run(
                    "Génère le briefing du dossier et les explications des résultats.",
                    deps=deps,
                    model=refiner_model,
                    model_settings=get_model_settings("refiner")
                )
            finally:
                # Clean up capability to avoid leak into other processes/runs
                refiner_agent.root_capability.capabilities.remove(online_eval)
            
            res: RefinerResult = result_run.output
            
            # Formulate a clean string output combining all generated narratives
            output_str = f"BRIEF:\n{res.odis_brief}\n\nGLOBAL PITCH:\n{res.global_pitch}\n\nCITY PITCHES:\n"
            for p in res.pitches_per_city:
                output_str += f"- {p.name} ({p.codgeo}): {p.pitch}\n"
                
            return output_str

        # Define the test case using the expected odis_brief as clean input representation
        case = Case(
            name=f"Brief Refinement - {scenario['scenario_name']}",
            inputs=scenario["odis_brief"],
        )
        
        dataset = Dataset(
            name="odis_brief_eval_dataset",
            cases=[case],
            evaluators=[judge]
        )
        
        # Run evaluation (extremely fast and cheap)
        report = await dataset.evaluate(run_refiner_task)
        report.print()
        
        # 3. Verify evaluation results and assertions
        assert len(report.failures) == 0, f"Evaluation encountered runtime failures: {report.failures}"
        assert len(report.cases) == 1
        
        eval_case_result = report.cases[0]
        assertion_result = eval_case_result.assertions.get("LLMJudge")
        assert assertion_result is not None, "LLMJudge assertion result was not recorded"
        assert assertion_result.value is True, f"Refinement evaluation failed: {assertion_result.reason}"
        
        # Output evaluation reasoning for transparency
        print(f"\n[EVAL REASONING] {assertion_result.reason}")
