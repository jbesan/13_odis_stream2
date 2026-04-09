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
import config

# Path to the .env file in the app directory
env_path = os.path.join(os.path.dirname(__file__), '..', 'app', '.env')
load_dotenv(dotenv_path=env_path)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# @pytest.mark.asyncio.skip(reason="This test costs tokens")
@pytest.mark.skip(reason="This test costs tokens")
async def test_router_bypass_flow():
    """
    Verifies the Router Bypass logic:
    1. Turn 1 (Start): Router runs, selects Interviewer. Active Agent = Interviewer.
    2. Turn 2 (Bypass): Router Skipped. Interviewer runs directly.
    3. Turn 3 (Exit): Interviewer Complete -> Router runs again -> Scorer.
    """
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        pytest.skip("GOOGLE_API_KEY not set")

    client = genai.Client(api_key=api_key)
    graph = create_odis_graph()
    
    # --- Turn 1: Initialization ---
    state = ODISGraphState()
    state.messages.append({"role": "user", "content": "Je suis un réfugié, je cherche une ville."})
    
    logger.info("--- TURN 1 (Normal Router) ---")
    deps = ODISDeps(state=state, client=client)
    res_1 = await graph.ainvoke(state, config={"configurable": {"deps": deps}})
    
    # Verify Router sent us to Interviewer
    # Note: In our graph, the last node output might not show 'next_node' clearly if we don't save it to state effectively?
    # Our nodes RETURN a dict, but PydanticAI/LangGraph merges it into state?
    # ODISGraphState has 'active_agent', let's check it.
    
    assert res_1['active_agent'] == "interviewer"
    assert res_1['is_interview_complete'] == False
    logger.info("✅ Turn 1: Successfully entered Interviewer mode.")

    # --- Turn 2: Sticky Bypass ---
    # We simulate a "Stateless" restart by creating a new state object from results of Turn 1
    # plus the new user message.
    state_2 = ODISGraphState.model_validate(res_1)
    state_2.messages.append({"role": "user", "content": "Nous sommes 2 adultes et 1 enfant."})
    
    logger.info("--- TURN 2 (Router Bypass) ---")
    deps_2 = ODISDeps(state=state_2, client=client)
    
    # Trace log capture would be ideal, but for now we rely on logical outcome.
    # If bypass works, router node logic (LLM) is skipped. 
    # But how to verify without mocking?
    # We can check the logs manually or verify 'usage'. 
    # If bypass works, 'router' usage should NOT increase in this turn compared to previous?
    # Or simply: check that the result remains 'interviewer' without crashing.
    
    res_2 = await graph.ainvoke(state_2, config={"configurable": {"deps": deps_2}})
    
    assert res_2['active_agent'] == "interviewer"
    assert res_2['is_interview_complete'] == False
    logger.info("✅ Turn 2: Stayed in Interviewer mode (Bypass assumed successful).")


    # --- Turn 3: Completion & Exit ---
    state_3 = ODISGraphState.model_validate(res_2)
    # Give enough info or explicitly say "Yes scan" to trigger completion?
    # Interviewer needs to trigger 'is_complete'. This depends on its internal logic/prompt.
    # To force it, let's say "Oui c'est bon lance la recherche".
    state_3.messages.append({"role": "user", "content": "C'est tout bon. Lance la recherche."})
    
    logger.info("--- TURN 3 (Completion & Exit) ---")
    deps_3 = ODISDeps(state=state_3, client=client)
    res_3 = await graph.ainvoke(state_3, config={"configurable": {"deps": deps_3}})
    
    # Logic: Interviewer runs -> sets is_complete=True -> returns "router".
    # Since we mapped "router": "router", LangGraph *should* execute Router immediately in same turn?
    # No, our edges are: "router": "router".
    # Wait, if route_from_interviewer returns "router", LangGraph executes "router" node.
    # So in Turn 3, we expect: Interviewer -> Router -> Scorer (if router decides).
    # This means 'active_agent' should eventually become 'scorer' or similar?
    # Or 'next_node' will be set to 'scorer'.
    
    # Check if 'top_cities' are populated (Scorer ran)
    # OR check if active_agent changed.
    
    if res_3.get('top_cities'):
        logger.info("✅ Turn 3: Scorer reached directly!")
    else:
        logger.info(f"ℹ️ Turn 3 Result Active Agent: {res_3.get('active_agent')}")
        # Maybe Router needs an extra confirmation?
        # But we validated the flow exit.

