import pytest
from pydantic import ValidationError

from agents.ts_agent import ExpertTask, SwarmPlan


def test_valid_expert_plan_is_accepted():
    plan = SwarmPlan(
        swarm_mode="specific_ask",
        tasks=[
            ExpertTask(
                expert="housing_expert",
                task_description="Vérifie le logement.",
                skill_cards=["housing_full_analysis"],
            )
        ],
    )

    assert plan.swarm_mode == "specific_ask"


@pytest.mark.parametrize(
    "payload",
    [
        {"swarm_mode": "full_analysis", "tasks": []},
        {"swarm_mode": "specific_ask", "direct_answer": "Réponse", "tasks": []},
        {"swarm_mode": "direct_answer", "tasks": []},
        {
            "swarm_mode": "direct_answer",
            "direct_answer": "Réponse",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Ne doit pas être exécuté.",
                    "skill_cards": [],
                }
            ],
        },
        {
            "swarm_mode": "full_analysis",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Tâche A",
                    "skill_cards": [],
                },
                {
                    "expert": "housing_expert",
                    "task_description": "Tâche B",
                    "skill_cards": [],
                },
            ],
        },
        {
            "swarm_mode": "full_analysis",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Mauvais domaine.",
                    "skill_cards": ["healthcare_full_analysis"],
                }
            ],
        },
        {
            "swarm_mode": "full_analysis",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Carte inconnue.",
                    "skill_cards": ["does_not_exist"],
                }
            ],
        },
    ],
)
def test_invalid_execution_contracts_are_rejected(payload):
    with pytest.raises(ValidationError):
        SwarmPlan.model_validate(payload)
