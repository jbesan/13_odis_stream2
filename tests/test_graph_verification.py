import pytest
import os
import sys
import logging
from dotenv import load_dotenv
from typing import Any


# Ensure 'app' directory is in python path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))

from agents.graph import create_odis_graph
from agents.state import GraphState, ODISDeps
from google import genai

# Path to the .env file in the app directory
env_path = os.path.join(os.path.dirname(__file__), '..', 'app', '.env')
load_dotenv(dotenv_path=env_path)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@pytest.mark.asyncio
# @pytest.mark.skip(reason="This test costs tokens")
async def test_graph_execution_end_to_end():
    """
    Verifies that the ODIS graph can be instantiated and executed end-to-end.
    This replaces the standalone verify_graph.py script.
    """
    logger.info("🚀 Initializing Graph...")
    graph = create_odis_graph()
    
    logger.info("📝 Preparing State...")
    state = GraphState()
    
    # Simulate a user message prompting for details
    from core.models import SearchCriterias, CommuneResult, SearchResultsData, CriteriaItem
    
    # 1. Setup criteria
    state.search_criteria = SearchCriterias(
        commune_actuelle=CriteriaItem(code="13001", label="Marseille"),
        nb_adultes=1
    )
    
    # 2. Compute hash (using the helper)
    from agents.state import compute_criteria_hash
    current_hash = compute_criteria_hash(state.search_criteria)
    state.criteria_hash = current_hash # This is the state field for the criteria hash
    
    # 3. Setup mock results that match the hash
    marseille = CommuneResult(codgeo="13001", name="Marseille", population=800000, global_score=0.9)
    state.search_results = SearchResultsData(
        search_hash=current_hash,
        results=[marseille],
        current_geo=marseille
    )
    
    state.focus_city = CommuneResult(name="Marseille", codgeo="13001")
    state.messages.append({"role": "user", "content": "Peux-tu me donner des détails sur Marseille ?"})
    
    logger.info("▶️ Running Graph (run)...")
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY not set")

    client = genai.Client(api_key=api_key)
    deps = ODISDeps(state=state, client=client)
    
    try:
        final_response = await graph.run(state=state, deps=deps)
        logger.info("✅ Graph Execution Complete!")
        
        # assertions
        assert final_response is not None
        # Handle pydantic-graph End object
        res_str = final_response.data if hasattr(final_response, "data") else str(final_response)
        logger.info(f"🤖 Last Response: {res_str[:100]}...")
        
        # Check if results were merged into state
        if state.search_results:
            city = state.search_results.get_by_code("13001")
            if city and city.expert_analysis:
                logger.info("🧩 Experts Results (Parallel Execution):")
                for k, v in city.expert_analysis.items():
                    logger.info(f"  - {k}: {v[:50]}...")
        
    except Exception as e:
        logger.error(f"❌ Graph Execution Failed: {e}", exc_info=True)
        pytest.fail(f"Graph execution failed: {e}")
