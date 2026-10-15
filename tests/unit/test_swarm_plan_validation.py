"""Unit tests for TS Agent SwarmPlan and ExpertTask validation (Phase 1).

Tests validation of execution contracts, capabilities authorization via CapabilityRegistry,
and rejection of empty/unknown/unauthorized capabilities.
"""

import pytest
from pydantic import ValidationError

from agents.capability_registry import get_default_capability_registry
from agents.ts_agent import ExpertTask, SwarmPlan, format_capabilities_catalog


def test_valid_expert_plan_with_capabilities_is_accepted():
    """Validates that a SwarmPlan using canonical capabilities is successfully validated."""
    plan = SwarmPlan(
        swarm_mode="specific_ask",
        tasks=[
            ExpertTask(
                expert="housing_expert",
                task_description="Vérifie le logement.",
                capabilities=["housing_adequation_territoire"],
            )
        ],
    )

    assert plan.swarm_mode == "specific_ask"
    assert len(plan.tasks) == 1
    assert plan.tasks[0].capabilities == ["housing_adequation_territoire"]


def test_multi_capabilities_for_expert_accepted():
    """Validates that multiple authorized capabilities for a single expert are accepted and deduplicated."""
    task = ExpertTask(
        expert="job_hunter",
        task_description="Recherche d'emploi approfondie.",
        capabilities=[
            "job_adequation_territoire",
            "job_extend_job_search",
            "job_adequation_territoire",  # duplicate to test deduplication
        ],
    )

    assert task.capabilities == [
        "job_adequation_territoire",
        "job_extend_job_search",
    ]


def test_expert_task_rejects_empty_capabilities():
    """Validates that an ExpertTask rejects empty capabilities (min_length=1 rule)."""
    with pytest.raises(ValidationError):
        ExpertTask(
            expert="housing_expert",
            task_description="Tâche sans capability.",
            capabilities=[],
        )


def test_expert_task_rejects_unknown_capability():
    """Validates that an unknown capability ID fails closed with ValidationError."""
    with pytest.raises(ValidationError):
        ExpertTask(
            expert="housing_expert",
            task_description="Tâche avec capability inconnue.",
            capabilities=["unknown_capability_id"],
        )


def test_expert_task_rejects_unauthorized_expert():
    """Validates that assigning a capability to an unauthorized expert fails with ValidationError."""
    with pytest.raises(ValidationError):
        ExpertTask(
            expert="housing_expert",
            task_description="Tâche logement avec capability éducation.",
            capabilities=["education_adequation_territoire"],
        )


@pytest.mark.parametrize(
    "payload",
    [
        # Empty tasks in analysis modes
        {"swarm_mode": "full_analysis", "tasks": []},
        {"swarm_mode": "specific_ask", "direct_answer": "Réponse", "tasks": []},
        # Direct answer missing text
        {"swarm_mode": "direct_answer", "tasks": []},
        # Direct answer with expert tasks
        {
            "swarm_mode": "direct_answer",
            "direct_answer": "Réponse",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Ne doit pas être exécuté.",
                    "capabilities": ["housing_adequation_territoire"],
                }
            ],
        },
        # Duplicate expert in tasks
        {
            "swarm_mode": "full_analysis",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Tâche A",
                    "capabilities": ["housing_adequation_territoire"],
                },
                {
                    "expert": "housing_expert",
                    "task_description": "Tâche B",
                    "capabilities": ["housing_adequation_territoire"],
                },
            ],
        },
        # Unauthorized capability for expert (cross-domain)
        {
            "swarm_mode": "full_analysis",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Mauvais domaine.",
                    "capabilities": ["healthcare_adequation_territoire"],
                }
            ],
        },
        # Unknown capability ID
        {
            "swarm_mode": "full_analysis",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Capability inconnue.",
                    "capabilities": ["does_not_exist"],
                }
            ],
        },
        # Empty capabilities list
        {
            "swarm_mode": "full_analysis",
            "tasks": [
                {
                    "expert": "housing_expert",
                    "task_description": "Sans capability.",
                    "capabilities": [],
                }
            ],
        },
    ],
)
def test_invalid_execution_contracts_are_rejected(payload):
    """Validates that all malformed or non-compliant SwarmPlans fail validation."""
    with pytest.raises(ValidationError):
        SwarmPlan.model_validate(payload)


def test_format_capabilities_catalog():
    """Validates that format_capabilities_catalog generates a deterministic markdown table with all capabilities."""
    registry = get_default_capability_registry()
    catalog_table = format_capabilities_catalog(registry)

    assert "| ID | Nom | Expert(s) | Description |" in catalog_table
    assert "| :--- | :--- | :--- | :--- |" in catalog_table

    for spec in registry.list_all():
        assert f"`{spec.id}`" in catalog_table
        assert spec.name in catalog_table
        assert spec.experts[0] in catalog_table


def test_expert_task_coerces_single_string_inputs():
    """Validates that single string capability is coerced into a list."""
    task_cap = ExpertTask.model_validate(
        {
            "expert": "housing_expert",
            "task_description": "Logement",
            "capabilities": "housing_adequation_territoire",
        }
    )
    assert task_cap.capabilities == ["housing_adequation_territoire"]


def test_expert_task_from_object_attributes():
    """Validates that an attribute-bearing object (non-dict) is supported via from_attributes."""
    from types import SimpleNamespace

    obj = SimpleNamespace(
        expert="housing_expert",
        task_description="Test object",
        capabilities=["housing_adequation_territoire"],
    )
    task = ExpertTask.model_validate(obj, from_attributes=True)
    assert task.expert == "housing_expert"
    assert task.capabilities == ["housing_adequation_territoire"]


@pytest.mark.asyncio
async def test_ts_agent_instructions_render():
    """Validates that ts_agent_instructions dynamic prompt renders with catalog and boilerplate."""
    from unittest.mock import MagicMock
    from agents.state import GraphState, ODISDeps
    from agents.ts_agent import ts_agent_instructions

    mock_state = GraphState(interaction_id="test-interaction-123")
    mock_deps = ODISDeps(state=mock_state)
    mock_ctx = MagicMock()
    mock_ctx.deps = mock_deps

    prompt = await ts_agent_instructions(mock_ctx)
    assert "COORDINATEUR du swarm d'agents IA thématiques" in prompt
    assert "# Répertoire des Capabilities disponibles :" in prompt
    assert "| `housing_adequation_territoire` |" in prompt
    assert "| `job_adequation_territoire` |" in prompt
    assert "# Contexte du dossier :" in prompt
