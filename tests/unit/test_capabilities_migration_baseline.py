"""Baseline reference tests for Capabilities and Context migration (Phase 1).

Captures and validates the system prompts and exposed toolsets for all 6 domain experts
on fixed datasets (rich dossier, territory context, empty/minimal case).
This serves as the regression safety net throughout the Phase 1 refactoring.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pydantic_ai import RunContext

from agents.capability_registry import get_default_capability_registry
from agents.state import GraphState, ODISDeps
from agents.education_expert import education_expert_agent, education_expert_instructions
from agents.healthcare_expert import healthcare_expert_agent, healthcare_expert_instructions
from agents.housing_expert import housing_expert_agent, housing_expert_instructions
from agents.job_hunter import job_hunter_agent, job_hunter_instructions
from agents.mobility_expert import mobility_expert_agent, mobility_expert_instructions
from agents.social_integration_expert import (
    social_integration_expert_agent,
    social_integration_expert_instructions,
)
from core.models import (
    CommuneResult,
    SearchCriterias,
    EducationMetrics,
    HousingMetrics,
    EmploymentMetrics,
    HealthMetrics,
    InclusionMetrics,
    MobilityMetrics,
    TerritoryMetrics,
    JobOfferDetail,
    AssociationDetail,
)


def _make_rich_state() -> GraphState:
    """Builds a comprehensive realistic dossier with data across all domains."""
    criteria = SearchCriterias(
        nb_adultes=2,
        nb_enfants=1,
        classe_enfants=["primaire"],
        metiers_souhaites=["Boulanger", "Cuisinier"],
        besoin_sante="Pédiatrie",
        odis_brief="Famille monoparentale cherchant un logement proche écoles et gare.",
    )
    commune = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        population=260000,
        name_bdv="Bordeaux",
        global_score=0.82,
        education=EducationMetrics(
            cat_score=0.85,
            facility_counts={"ecoles_elementaires": 12},
            facility_details={"ecoles_elementaires": ["École Gambetta", "École Jean Moulin"]},
        ),
        housing=HousingMetrics(
            cat_score=0.65,
            price_per_sqm=18.5,
            loyer_moyen_t2=650.0,
            loyer_moyen_t3=850.0,
            log_soc_delay_months=18.0,
        ),
        mobility=MobilityMetrics(
            cat_score=0.90,
            gare_tgv=True,
            dist_gare_proche_km=1.2,
            has_train_station=True,
        ),
        health=HealthMetrics(
            cat_score=0.88,
            apl_medecins=3.8,
            urgences_proches_km=2.5,
            maternite_proche_km=3.0,
        ),
        employment=EmploymentMetrics(
            cat_score=0.75,
            matching_job_offers=[
                [
                    JobOfferDetail(
                        id="OFFER_BORD_1",
                        title="Second de cuisine",
                        company="Bistrot Bordelais",
                        contract_type="CDI",
                        rome_code="G1602",
                        location="Bordeaux",
                    )
                ]
            ],
        ),
        inclusion=InclusionMetrics(
            cat_score=0.80,
            associations_refugies=[
                AssociationDetail(
                    id="W33200001",
                    name="Accueil Réfugiés Gironde",
                    description="Accompagnement social et cours de français",
                    commune="Bordeaux",
                )
            ],
        ),
    )
    return GraphState(
        search_criteria=criteria,
        focus_city=commune,
        odis_brief=criteria.odis_brief,
    )


def _make_territory_state() -> GraphState:
    """Builds a state specifically with local political / security territory alerts."""
    state = _make_rich_state()
    state.focus_city.territoire = TerritoryMetrics(
        cat_score=0.40,
        maire_extreme_droite=True,
        ter_insecurite=0.75,
    )
    return state


def _make_empty_state() -> GraphState:
    """Builds a minimal state with no criteria, no brief, and no domain metrics."""
    return GraphState(
        search_criteria=None,
        focus_city=CommuneResult(codgeo="00000", name="Commune Inconnue"),
        odis_brief=None,
    )


EXPERT_CONFIGS = [
    ("education_expert", education_expert_agent, education_expert_instructions, ["search_places_batch_tool"]),
    ("healthcare_expert", healthcare_expert_agent, healthcare_expert_instructions, ["search_places_batch_tool", "search_rna_rag_batch_tool"]),
    ("housing_expert", housing_expert_agent, housing_expert_instructions, ["search_places_batch_tool", "compute_routes_tool"]),
    ("job_hunter", job_hunter_agent, job_hunter_instructions, [
        "search_job_offers_batch_tool",
        "get_job_details_tool",
        "search_inclusion_jobs_batch_tool",
        "get_inclusion_job_details_tool",
        "search_referentiels_batch_tool",
    ]),
    ("mobility_expert", mobility_expert_agent, mobility_expert_instructions, ["search_places_batch_tool", "compute_routes_tool"]),
    ("social_integration_expert", social_integration_expert_agent, social_integration_expert_instructions, ["search_rna_rag_batch_tool", "search_places_batch_tool"]),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("domain,agent,prompt_func,expected_tools", EXPERT_CONFIGS)
async def test_baseline_expert_rendered_prompt_and_tools(domain, agent, prompt_func, expected_tools):
    """Verifies that all 6 experts render stable prompts without duplicate SKILL_INSTRUCTIONS and expose expected capabilities."""
    state = _make_rich_state()
    ctx = RunContext(deps=ODISDeps(state=state), model=None, usage=None, prompt=None)

    prompt = await prompt_func(ctx)

    # 1. Structural checks on base prompt
    assert state.odis_brief in prompt
    assert "Bordeaux" in prompt
    assert "33063" in prompt
    assert "Contexte de collaboration" in prompt
    assert "Projet de vie du bénéficiaire" in prompt
    assert "Critères de recherche du foyer" in prompt
    assert "Commune à analyser" in prompt

    # 2. Pruning verification: NO duplicate SKILL_INSTRUCTIONS in base prompt
    assert "{SKILL_INSTRUCTIONS}" not in prompt
    assert "Consignes additionnelles issues des Skill Cards actives" not in prompt
    assert "Aucune consigne spécifique de Skill Card active" not in prompt

    # 3. Tool registration check
    # Singletons have 0 static tools by default; domain tools are supplied via capabilities.
    assert list(agent._function_toolset.tools.keys()) == []

    # 4. Capability injection check: instructions & tools are carried by Capability
    registry = get_default_capability_registry()
    base_cap_id = registry.get_base_capability_id(domain)
    caps = registry.build_capabilities_for_expert([base_cap_id], expert=domain)
    assert len(caps) >= 1
    cap = caps[0]
    cap_instructions = cap.get_instructions()
    assert len(cap_instructions) >= 1
    assert len(cap_instructions[0].strip()) > 0
    assert cap.tools is not None

    cap_tool_names = [getattr(t, "__name__", str(t)) for t in cap.tools]
    assert set(expected_tools).issubset(set(cap_tool_names))


@pytest.mark.asyncio
async def test_baseline_social_expert_includes_territory_when_present():
    """Verifies that social_integration_expert includes Données territoire only when alert flags are active."""
    state_ter = _make_territory_state()
    ctx_ter = RunContext(deps=ODISDeps(state=state_ter), model=None, usage=None, prompt=None)
    prompt_ter = await social_integration_expert_instructions(ctx_ter)
    assert "Données territoire (Contexte local)" in prompt_ter
    assert "maire_extreme_droite" in prompt_ter or "Maire d'extrême droite" in prompt_ter or "true" in prompt_ter.lower()

    state_rich = _make_rich_state()
    ctx_rich = RunContext(deps=ODISDeps(state=state_rich), model=None, usage=None, prompt=None)
    prompt_rich = await social_integration_expert_instructions(ctx_rich)
    assert "Données territoire (Contexte local)" not in prompt_rich


@pytest.mark.asyncio
@pytest.mark.parametrize("domain,agent,prompt_func,expected_tools", EXPERT_CONFIGS)
async def test_baseline_empty_state_robustness(domain, agent, prompt_func, expected_tools):
    """Verifies that all experts cleanly handle an empty/minimal dossier without crashing."""
    state_empty = _make_empty_state()
    ctx = RunContext(deps=ODISDeps(state=state_empty), model=None, usage=None, prompt=None)

    prompt = await prompt_func(ctx)
    assert "Aucun briefing particulier renseigné" in prompt or "Commune Inconnue" in prompt


def test_baseline_deferred_loading_flag_and_capability_propagation(monkeypatch: pytest.MonkeyPatch):
    """Verifies that the deferred loading flag defaults to False and propagates to Capability."""
    from config import is_capability_deferred_loading_enabled

    registry = get_default_capability_registry()

    # Default should be False
    monkeypatch.delenv("ODIS_CAPABILITY_DEFERRED_LOADING", raising=False)
    assert is_capability_deferred_loading_enabled() is False

    caps = registry.build_capabilities_for_expert(
        ["housing_adequation_territoire"],
        defer_loading=is_capability_deferred_loading_enabled(),
        expert="housing_expert",
    )
    assert caps[0].defer_loading is False

    # Enabled via flag
    monkeypatch.setenv("ODIS_CAPABILITY_DEFERRED_LOADING", "true")
    assert is_capability_deferred_loading_enabled() is True

    caps_def = registry.build_capabilities_for_expert(
        ["housing_adequation_territoire"],
        defer_loading=is_capability_deferred_loading_enabled(),
        expert="housing_expert",
    )
    # S5: Hot-start capabilities requested by the TS are always eager (defer_loading=False)
    assert caps_def[0].defer_loading is False


@pytest.mark.asyncio
async def test_triage_step_deferred_loading_flag(monkeypatch: pytest.MonkeyPatch):
    """Verifies that triage_step populates capability IDs and expert_worker_step compiles capabilities with deferred loading flag."""
    from agents.graph import triage_step, expert_worker_step
    from agents.ts_agent import SwarmPlan, ExpertTask

    class MockStepContext:
        def __init__(self, state: GraphState, deps: ODISDeps, inputs=None):
            self.state = state
            self.deps = deps
            self.inputs = inputs

    plan = SwarmPlan(
        swarm_mode="specific_ask",
        tasks=[
            ExpertTask(
                expert="housing_expert",
                task_description="Analyse logement",
                capabilities=["housing_adequation_territoire"],
            )
        ],
    )

    # 1. When flag is False
    monkeypatch.setenv("ODIS_CAPABILITY_DEFERRED_LOADING", "false")
    with patch("agents.graph.ts_agent.run", new_callable=AsyncMock) as mock_run:
        mock_res = MagicMock()
        mock_res.output = plan
        mock_res.usage = MagicMock(return_value=MagicMock(input_tokens=1, output_tokens=1, total_tokens=2, requests=1))
        mock_run.return_value = mock_res

        state_false = GraphState()
        deps_false = ODISDeps(state=state_false)
        ctx_false = MockStepContext(state_false, deps_false)
        await triage_step(ctx_false)  # type: ignore
        assert state_false.expert_capabilities["housing_expert"] == [
            "housing_adequation_territoire"
        ]

    # Verify worker step compiles capabilities with defer_loading False
    worker_ctx_false = MockStepContext(state_false, deps_false, inputs="housing_expert")
    with patch("agents.graph.housing_expert_agent.run", new_callable=AsyncMock) as mock_agent_run:
        mock_res = MagicMock()
        mock_res.output = MagicMock(result="Logement")
        mock_res.usage = MagicMock(return_value=MagicMock(input_tokens=1, output_tokens=1, total_tokens=2, requests=1, grounding_queries=0))
        mock_res.all_messages = MagicMock(return_value=[])
        mock_agent_run.return_value = mock_res
        await expert_worker_step(worker_ctx_false)  # type: ignore
        caps = mock_agent_run.call_args.kwargs["capabilities"]
        assert caps[0].defer_loading is False

    # 2. When flag is True
    monkeypatch.setenv("ODIS_CAPABILITY_DEFERRED_LOADING", "true")
    state_true = GraphState(
        expert_tasks={"housing_expert": "Analyse logement"},
        expert_capabilities={"housing_expert": ["housing_adequation_territoire"]},
    )
    deps_true = ODISDeps(state=state_true)
    worker_ctx_true = MockStepContext(state_true, deps_true, inputs="housing_expert")
    with patch("agents.graph.housing_expert_agent.run", new_callable=AsyncMock) as mock_agent_run:
        mock_res = MagicMock()
        mock_res.output = MagicMock(result="Logement")
        mock_res.usage = MagicMock(return_value=MagicMock(input_tokens=1, output_tokens=1, total_tokens=2, requests=1, grounding_queries=0))
        mock_res.all_messages = MagicMock(return_value=[])
        mock_agent_run.return_value = mock_res
        await expert_worker_step(worker_ctx_true)  # type: ignore
        caps = mock_agent_run.call_args.kwargs["capabilities"]
        # S5: Hot-start capabilities requested by the TS are always eager (defer_loading=False)
        assert caps[0].defer_loading is False


