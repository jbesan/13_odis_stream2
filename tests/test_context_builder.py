import pytest
import os
import sys
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

# Ensure 'app' directory is in python path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))

from agents.state import ODISContextBuilder
from core.models import CriteriaItem

class SubModel(BaseModel):
    sub_field: str = Field("sub", description="Sub field", json_schema_extra={"odis_visibility": ["agent_test"]})
    hidden_field: str = Field("hidden", description="Hidden", json_schema_extra={"odis_visibility": ["other"]})

class MockModel(BaseModel):
    public_field: str = Field("public", description="Public field", json_schema_extra={"odis_visibility": ["all"]})
    scout_field: str = Field("scout", description="Scout field", json_schema_extra={"odis_visibility": ["agent_scout"]})
    ui_field: str = Field("ui", description="UI field", json_schema_extra={"odis_visibility": ["ui_details"]})
    nested: SubModel = Field(default_factory=SubModel, description="Nested model", json_schema_extra={"odis_visibility": ["agent_test"]})
    items: List[CriteriaItem] = Field(
        default_factory=lambda: [CriteriaItem(code="C1", label="Label 1")],
        description="Items",
        json_schema_extra={"odis_visibility": ["agent_scout"]}
    )
    empty_list: List[str] = Field(default_factory=list, description="Empty List", json_schema_extra={"odis_visibility": ["all"]})

def test_auto_build_context_filtering():
    model = MockModel()
    
    # Test Scout visibility
    scout_ctx = ODISContextBuilder._auto_build_context(model, "agent_scout")
    assert "Public field" in scout_ctx
    assert "Scout field" in scout_ctx
    assert "UI field" not in scout_ctx
    assert scout_ctx["Public field"] == "public"
    assert scout_ctx["Scout field"] == "scout"
    
    # Test UI visibility
    ui_ctx = ODISContextBuilder._auto_build_context(model, "ui_details")
    assert "Public field" in ui_ctx
    assert "UI field" in ui_ctx
    assert "Scout field" not in ui_ctx

def test_auto_build_context_recursion():
    model = MockModel()
    
    # Test recursion for agent_test
    test_ctx = ODISContextBuilder._auto_build_context(model, "agent_test")
    assert "Nested model" in test_ctx
    assert test_ctx["Nested model"]["Sub field"] == "sub"
    assert "Hidden" not in test_ctx["Nested model"]

def test_auto_build_context_simplification():
    model = MockModel()
    
    # CriteriaItem should be simplified to its label
    scout_ctx = ODISContextBuilder._auto_build_context(model, "agent_scout")
    assert scout_ctx["Items"] == ["Label 1"]
    assert isinstance(scout_ctx["Items"][0], str)

def test_auto_build_context_none_handling():
    class OptionalModel(BaseModel):
        opt: Optional[str] = Field(None, description="Optional", json_schema_extra={"odis_visibility": ["all"]})
    
    model = OptionalModel(opt=None)
    ctx = ODISContextBuilder._auto_build_context(model, "all")
    assert "Optional" not in ctx

def test_auto_build_context_empty_list_handling():
    model = MockModel(empty_list=[])
    ctx = ODISContextBuilder._auto_build_context(model, "all")
    # Empty lists should be included if they are not None
    assert "Empty List" in ctx
    assert ctx["Empty List"] == []

def test_auto_build_context_commune_score_detail_formatting():
    from core.models import CommuneScoreDetail
    
    # Test case 1: Unit with no leading space
    detail1 = CommuneScoreDetail(
        label="Test metric 1",
        score_id="m1",
        score_normalise=0.85,
        relative_weight=1.5,
        valeur_kpi=2,
        unit="associations"
    )
    val1 = ODISContextBuilder._process_value(detail1, "agent_scout")
    assert val1 == "Test metric 1: 2 associations, score: 0.85, poids relatif: 1.5%"

    # Test case 2: Unit with leading space (should not result in double spaces)
    detail2 = CommuneScoreDetail(
        label="Test metric 2",
        score_id="m2",
        score_normalise=0.5,
        relative_weight=2.0,
        valeur_kpi=3,
        unit=" offres"
    )
    val2 = ODISContextBuilder._process_value(detail2, "agent_scout")
    assert val2 == "Test metric 2: 3 offres, score: 0.5, poids relatif: 2.0%"

    # Test case 3: Empty unit
    detail3 = CommuneScoreDetail(
        label="Test metric 3",
        score_id="m3",
        score_normalise=0.99,
        relative_weight=1.0,
        valeur_kpi=4.5,
        unit=""
    )
    val3 = ODISContextBuilder._process_value(detail3, "agent_scout")
    assert val3 == "Test metric 3: 4.5, score: 0.99, poids relatif: 1.0%"


def test_job_hunter_context_includes_matching_jobs():
    """Verifies that CommuneResult's matching_job_offers are correctly extracted in the context built for agent_job_hunter."""
    from core.models import CommuneResult, JobOfferDetail, EmploymentMetrics
    
    offer = JobOfferDetail(
        id="OFFER_MOCK_123",
        title="Conseiller Clientèle",
        company="Banque Populaire",
        contract_type="CDI",
        contract_label="Contrat à durée indéterminée",
        description="Gestion d'un portefeuille de clients.",
        location="Paris",
        location_insee="75056",
        salary="30K-35K",
        url="https://example.com/apply-conseiller",
        rome_code="C1201",
        rome_label="Conseil clientèle en assurances"
    )
    
    commune = CommuneResult(
        codgeo="75056",
        name="Paris",
        population=2148271,
        global_score=0.88,
        employment=EmploymentMetrics(
            cat_score=0.9,
            matching_job_offers=[[offer]]
        )
    )
    
    ctx = ODISContextBuilder._auto_build_context(commune, "agent_job_hunter")
    
    # Assert employment metrics are present
    assert "Données emploi et formation" in ctx
    emp_ctx = ctx["Données emploi et formation"]
    
    # Assert matching job offers are present
    assert "Liste des offres d'emploi correspondantes séparées par adulte du ménage" in emp_ctx
    offers_list = emp_ctx["Liste des offres d'emploi correspondantes séparées par adulte du ménage"]
    
    assert len(offers_list) == 1
    assert len(offers_list[0]) == 1
    
    job_ctx = offers_list[0][0]
    assert job_ctx["Identifiant unique de l'offre"] == "OFFER_MOCK_123"
    assert job_ctx["Intitulé du poste"] == "Conseiller Clientèle"
    assert job_ctx["Code INSEE du lieu de travail"] == "75056"
    assert job_ctx["Code ROME de l'offre"] == "C1201"
    assert job_ctx["Libellé ROME de l'offre"] == "Conseil clientèle en assurances"


def test_ts_agent_context_explicit_visibility():
    """Verifies that ts_agent's context only contains fields explicitly tagged with 'agent_ts_agent' or 'all'."""
    class ExpertData(BaseModel):
        all_field: str = Field("all", description="All field", json_schema_extra={"odis_visibility": ["all"]})
        ts_field: str = Field("ts", description="TS field", json_schema_extra={"odis_visibility": ["agent_ts_agent"]})
        synth_field: str = Field("synth", description="Synthesizer field", json_schema_extra={"odis_visibility": ["agent_synthesizer"]})
        
    model = ExpertData()
    ctx = ODISContextBuilder._auto_build_context(model, "agent_ts_agent")
    
    assert "All field" in ctx
    assert "TS field" in ctx
    assert "Synthesizer field" not in ctx
    assert ctx["All field"] == "all"
    assert ctx["TS field"] == "ts"



def test_ts_agent_context_excludes_unwanted_sections():
    """Verifies that the generated context for ts_agent excludes 'Ville actuelle (référence)' and 'Top 5 communes identifiées (Détails métriques)'."""
    from agents.state import GraphState, ODISContextBuilder, SearchResultsData, CommuneResult
    from core.models import SearchCriterias
    import json
    
    criteria = SearchCriterias()
    current = CommuneResult(codgeo="75056", name="Paris", population=2148271)
    rec1 = CommuneResult(codgeo="13055", name="Marseille", population=870018)
    
    results_data = SearchResultsData(
        search_hash="abc",
        current_geo=current,
        results=[rec1]
    )
    
    state = GraphState(
        search_criteria=criteria,
        search_results=results_data
    )
    
    # Context for ts_agent
    ts_ctx_str = ODISContextBuilder.agent_context(state, "ts_agent")
    ts_ctx = json.loads(ts_ctx_str)
    
    assert "Ville actuelle (référence)" not in ts_ctx
    assert "Top 5 communes identifiées (Détails métriques)" not in ts_ctx
    
    # Context for refiner should include them
    refiner_ctx_str = ODISContextBuilder.agent_context(state, "refiner")
    refiner_ctx = json.loads(refiner_ctx_str)
    assert "Ville actuelle (référence)" in refiner_ctx
    assert "Top 5 communes identifiées (Détails métriques)" in refiner_ctx


def test_association_detail_context_formatting():
    """Verifies that AssociationDetail objects are formatted as pipe-separated strings for agents, but normal dicts for UI."""
    from core.models import AssociationDetail

    asso = AssociationDetail(
        id="W123456789",
        name="Test Association",
        description="Providing test assistance and social support.",
        waldec_label="Action Sociale",
        refugee_focused=True
    )

    # For an agent, it should be simplified to a pipe-separated string
    agent_ctx = ODISContextBuilder._auto_build_context(asso, "agent_social_integration_expert")
    assert agent_ctx == "W123456789 | Test Association | Providing test assistance and social support."

    # For UI, it should remain a dictionary with all visible fields
    ui_ctx = ODISContextBuilder._auto_build_context(asso, "ui_details")
    assert isinstance(ui_ctx, dict)
    assert ui_ctx["Identifiant unique (WALDEC)"] == "W123456789"
    assert ui_ctx["Nom de l'association"] == "Test Association"
    assert ui_ctx["Description de l'activité"] == "Providing test assistance and social support."
    assert ui_ctx["Libellé de la catégorie WALDEC"] == "Action Sociale"
    assert ui_ctx["Si l'association est dédiée aux réfugiés"] is True






