import pytest
import os
import sys
from unittest.mock import MagicMock
from google import genai

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))

from agents.refiner import refiner_agent, RefinerResult
from agents.state import GraphState, ODISDeps
from core.models import SearchCriterias, CriteriaItem, SearchResultsData, CommuneResult
from agents.agent_config import get_p_model

@pytest.mark.asyncio
async def test_refiner_agent_live_shortlisted_city():
    """
    Live integration test to prove that refiner_agent generates pitches for both
    the Top 5 recommended results AND the shortlisted commune_pressentie.
    """
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY or GEMINI_API_KEY not set, skipping live integration test")

    client = genai.Client(api_key=api_key)
    
    # 1. Prepare State with a current city, top recommendation, and shortlisted city
    state = GraphState()
    state.search_criteria = SearchCriterias(
        commune_actuelle=CriteriaItem(code="75056", label="Paris"),
        commune_pressentie=CriteriaItem(code="72181", label="Le Mans"),
        nb_adultes=1
    )
    
    # Simulate scored communes (1 top 5 recommendation, 1 shortlisted city)
    rec1 = CommuneResult(codgeo="44055", name="Nantes", population=300000, global_score=0.92)
    rec1.scores = {}
    
    pressentie = CommuneResult(codgeo="72181", name="Le Mans", population=140000, global_score=0.85)
    pressentie.scores = {}
    
    state.search_results = SearchResultsData(
        search_hash="test_hash_val",
        results=[rec1],
        current_geo=CommuneResult(codgeo="75056", name="Paris", population=2100000, global_score=0.4),
        commune_pressentie=pressentie
    )
    
    deps = ODISDeps(state=state, client=client)
    model = get_p_model("refiner", client=client)
    
    result = await refiner_agent.run(
        "Génère le briefing du dossier et les explications des résultats.",
        deps=deps,
        model=model
    )
    
    response_obj = result.output
    assert isinstance(response_obj, RefinerResult)
    assert response_obj.odis_brief is not None
    assert response_obj.global_pitch is not None
    
    # Check that we have a pitch for both Nantes (recommended) and Le Mans (pressentie)
    codgeos_received = {p.codgeo for p in response_obj.pitches_per_city}
    print(f"DEBUG: Codgeos received: {codgeos_received}")
    
    assert "44055" in codgeos_received, "Should contain Nantes (recommended city)"
    assert "72181" in codgeos_received, "Should contain Le Mans (shortlisted city)"
