import json
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelResponse, ToolCallPart

from agents.agent_config import get_swarm_boilerplate
from agents.education_expert import EducationResult
from agents.graph import capture_usage
from agents.healthcare_expert import HealthcareResult
from agents.housing_expert import HousingResult
from agents.job_hunter import JobHunterResult
from agents.mobility_expert import MobilityResult
from agents.social_integration_expert import (
    SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT,
    SocialIntegrationResult,
)
from agents.source_registry import source_references_for_result
from agents.state import GraphState, ODISContextBuilder
from core.models import CommuneResult, SearchCriterias


def _state() -> GraphState:
    return GraphState(
        search_criteria=SearchCriterias(
            nb_adultes=1,
            odis_brief="Une famille cherche un accueil local.",
        ),
        focus_city=CommuneResult(codgeo="33063", name="Bordeaux"),
        messages=[{"role": "user", "content": "Mission dynamique à ne pas dupliquer."}],
    )


def test_expert_context_has_stable_common_prefix_and_no_duplicate_question():
    state = _state()
    common_social, specific_social = ODISContextBuilder.expert_prompt_contexts(
        state, "social_integration_expert"
    )
    common_housing, specific_housing = ODISContextBuilder.expert_prompt_contexts(
        state, "housing_expert"
    )

    assert common_social == common_housing
    assert json.loads(common_social)["Résumé du dossier (Briefing)"] == (
        "Une famille cherche un accueil local."
    )
    assert "Dernière question" not in common_social
    assert "Mission dynamique" not in common_social
    assert "Données inclusion" in specific_social
    assert "Données logement" in specific_housing
    assert "{MISSION}" not in SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT
    assert "{COMMON_CONTEXT}" in SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT
    assert "{SPECIFIC_CONTEXT}" in SOCIAL_INTEGRATION_EXPERT_SYSTEM_PROMPT
    expert_boilerplate = get_swarm_boilerplate("expert")
    assert "regroupe toutes les recherches indépendantes dans un seul appel batch" in (
        expert_boilerplate
    )
    assert "appelle-les dans la même réponse" in expert_boilerplate
    assert "au plus 5 requêtes au modèle" in expert_boilerplate
    assert "appels de suivi compris" in expert_boilerplate


def test_source_ledger_uses_recorded_tool_calls_not_model_searched_text():
    result = SimpleNamespace(
        all_messages=lambda: [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_rna_rag_batch_tool",
                        {"queries": ["FLE"], "codgeo": "33063"},
                    )
                ]
            )
        ]
    )

    references = source_references_for_result("social_integration_expert", result)
    by_key = {reference["source_key"]: reference for reference in references}

    assert by_key["dossier"]["status"] == "contexte"
    assert by_key["rna"]["status"] == "consultée"
    assert "web" not in by_key


@pytest.mark.parametrize(
    "result_type",
    [
        EducationResult,
        HealthcareResult,
        HousingResult,
        MobilityResult,
        JobHunterResult,
        SocialIntegrationResult,
    ],
)
def test_legacy_expert_output_reserves_searched_for_future_judge(result_type):
    """The audit field stays documented but is absent from Gemini's schema."""
    schema = result_type.model_json_schema()

    assert set(schema["properties"]) == {"result"}
    assert schema["required"] == ["result"]
    assert not hasattr(result_type(result="Analyse"), "searched")


def test_capture_usage_exposes_prompt_cache_metrics():
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        requests=2,
        tool_calls=1,
        cache_read_tokens=60,
        cache_write_tokens=40,
        cache_hit_ratio=0.6,
    )
    captured = capture_usage(
        SimpleNamespace(usage=usage),
        "social_integration_expert",
        "google:gemini-3.1-flash-lite",
    )

    assert captured.requests == 2
    assert captured.tool_calls == 1
    assert captured.cache_read_tokens == 60
    assert captured.cache_write_tokens == 40
    assert captured.cache_hit_ratio == 0.6
    assert captured.breakdown["social_integration_expert"]["cache_hit_ratio"] == 0.6
