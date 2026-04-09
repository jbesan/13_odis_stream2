import pytest
import os
import sys
import logging
from dotenv import load_dotenv
from typing import Any


# Ensure 'app' directory is in python path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))

from agents.graph import create_odis_graph
from agents.state import ODISGraphState, ODISDeps
from google import genai

# Path to the .env file in the app directory
env_path = os.path.join(os.path.dirname(__file__), '..', 'app', '.env')
load_dotenv(dotenv_path=env_path)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# @pytest.mark.asyncio
@pytest.mark.skip(reason="This test costs tokens")
async def test_graph_execution_end_to_end():
    """
    Verifies that the ODIS graph can be instantiated and executed end-to-end.
    This replaces the standalone verify_graph.py script.
    """
    logger.info("🚀 Initializing Graph...")
    graph = create_odis_graph()
    
    logger.info("📝 Preparing State...")
    state = ODISGraphState()
    # Simulate a user message prompting for details (Decoration Trigger)
    # We pre-fill specific criteria to avoid Interviewer loop if possible
    state.focus_city = "Bordeaux"
    state.messages.append({"role": "user", "content": "Peux-tu me donner des détails sur Bordeaux ?"})
    
    logger.info("▶️ Running Graph (ainvoke)...")
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY not set")

    client = genai.Client(api_key=api_key)
    deps = ODISDeps(state=state, client=client)
    
    try:
        final_state = await graph.ainvoke(state, config={"configurable": {"deps": deps}})
        logger.info("✅ Graph Execution Complete!")
        
        # assertions
        assert 'messages' in final_state
        assert len(final_state['messages']) > 0
        
        if 'experts_results' in final_state:
            logger.info("🧩 Experts Results (Parallel Execution):")
            for k, v in final_state['experts_results'].items():
                logger.info(f"  - {k}: {v[:50]}...")
        
        last_msg = final_state['messages'][-1]
        logger.info(f"🤖 Last Response: {last_msg['content'][:100]}...")
        assert last_msg['content'] is not None
        
    except Exception as e:
        logger.error(f"❌ Graph Execution Failed: {e}", exc_info=True)
        pytest.fail(f"Graph execution failed: {e}")
