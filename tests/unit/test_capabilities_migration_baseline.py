"""Baseline reference tests for Capabilities and Context migration (Phase 1).

Captures and validates the system prompts and exposed toolsets for all 6 domain experts
on fixed datasets (rich dossier, territory context, empty/minimal case).
This serves as the regression safety net throughout the Phase 1 refactoring.
"""

import pytest
from pydantic_ai import RunContext

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
        expert_skill_instructions={
            "job_hunter": "Skill: Analyse emploi standard.",
            "housing_expert": "Skill: Analyse logement standard.",
            "mobility_expert": "Skill: Analyse mobilité standard.",
            "healthcare_expert": "Skill: Analyse santé standard.",
            "education_expert": "Skill: Analyse éducation standard.",
            "social_integration_expert": "Skill: Analyse sociale standard.",
        },
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
    """Verifies that all 6 experts render stable prompts and expose their expected tools on the rich dossier."""
    state = _make_rich_state()
    ctx = RunContext(deps=ODISDeps(state=state), model=None, usage=None, prompt=None)

    prompt = await prompt_func(ctx)

    # 1. Structural checks
    assert state.odis_brief in prompt
    assert "Bordeaux" in prompt
    assert "33063" in prompt
    assert "Contexte de collaboration" in prompt

    # 2. Tool registration check
    registered_tools = list(agent._function_toolset.tools.keys())
    assert set(registered_tools) == set(expected_tools)


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
