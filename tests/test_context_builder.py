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
