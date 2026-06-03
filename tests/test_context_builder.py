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




