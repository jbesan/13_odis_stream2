
import logging
import os
from typing import Literal, Dict, Any
from langgraph.graph import StateGraph, END, START
from langchain_core.runnables import RunnableConfig
from google import genai
from dotenv import load_dotenv

# Ensure environment is loaded BEFORE importing agents
# The .env is in the parent directory of 'agents' (app/)
base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
env_path = os.path.join(base_dir, ".env")
load_dotenv(env_path)

# Agents & Logic
from agents.state import ODISDeps, ODISGraphState, UsageStats
# from agents.refiner import ContextRefiner
from agents.router import router_agent, RoutingResult
from agents.interviewer import interviewer_agent
from agents.scorer import scorer_agent
from agents.scout import scout_agent
from agents.web import web_agent
from agents.job_hunter import job_hunter_agent
from agents.synthesizer import synthesizer_agent
from agents.agent_config import get_model
from pydantic_ai.messages import ToolReturnPart


logger = logging.getLogger("odis_graph")

# --- Helpers ---

def get_deps(config: RunnableConfig) -> ODISDeps:
    """Helper to extract ODISDeps from LangGraph config."""
    deps = config.get("configurable", {}).get("deps")
    if not deps:
        raise ValueError("ODISDeps missing in graph config['configurable']['deps']")
    return deps

def capture_usage(result, node_name: str, model_id: str) -> UsageStats:
    """Extracts usage from PydanticAI result and calculates cost based on model type."""
    u = result.usage()
    
    # Pricing per 1M tokens
    # Gemini 3 Flash: $0.05 / $3.00
    # Gemini 2.5 Flash Lite: $0.01 / $0.40
    
    if "gemini-3" in model_id.lower():
        rate_in = 0.05
        rate_out = 3.00
    else:  # Assume gemini-2.5-flash-lite or similar low-cost model
        rate_in = 0.01
        rate_out = 0.40
        
    cost = (u.input_tokens * rate_in / 1_000_000) + (u.output_tokens * rate_out / 1_000_000)
    
    breakdown = {
        node_name: {
            "model": model_id,
            "input": u.input_tokens,
            "output": u.output_tokens,
            "total": u.total_tokens,
            "cost": cost
        }
    }
    
    return UsageStats(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        total_tokens=u.total_tokens,
        cost_usd=cost,
        breakdown=breakdown
    )

from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

def _get_p_model(agent_name: str, client: genai.Client) -> GoogleModel:
    mod_id = get_model(agent_name)
    if ":" in mod_id:
        _, model_name = mod_id.split(":", 1)
    else:
        model_name = mod_id
    
    # Explicitly inject the fresh client for this thread/loop
    provider = GoogleProvider(client=client)
    return GoogleModel(model_name, provider=provider)

# --- Routing Logic (Pure Python) ---

def route_from_start(state: ODISGraphState):
    """
    SOTA Pattern: Router Bypass.
    If we are in the middle of an interview, skip the Router entirely.
    """
    # If the last active agent was Interviewer and it's NOT done -> Go back to Interviewer
    if state.active_agent == "interviewer" and not state.is_interview_complete:
        logger.info("⏩ [ROUTER BYPASS] Continuing active interview session.")
        return "interviewer"
    
    # Default entry point
    return "router"

def route_from_interviewer(state: ODISGraphState):
    """
    SOTA Pattern: Autonomous Loop.
    Decides whether to loop back or release control to Router.
    """
    if state.is_interview_complete:
        # Release control: Go to Router (or END if you want to stop)
        logger.info("🚩 [INTERVIEWER] Session complete. Returning control to Router.")
        # Usually, after interview, we might want the Router to check if we go to Scorer
        return "router" 
    
    # Keep control: Loop back to handle user answer
    return "interviewer"

# --- Nodes ---

async def router_node(state: ODISGraphState, config: RunnableConfig):
    """Decides the next step based on user input and state."""
    deps = get_deps(config)
    deps.state = state
    
    from datetime import datetime
    start_time = datetime.now()
    logger.debug(f"🚀 [RELAY] Entering router_node at {start_time.strftime('%H:%M:%S.%f')[:-3]}")

    # Input is the last message
    user_msg = state.messages[-1]["content"] if state.messages else ""
    
    # Run Router Agent with bound model
    try:
        # Inject client!
        mod_id = get_model("router")
        model = _get_p_model("router", client=deps.client)
        result = await router_agent.run(user_msg, deps=deps, model=model)
        decision = result.output
        
        end_time = datetime.now()
        logger.info(f"🧠 [ROUTER] Direction: {decision.target_agent} ({decision.reasoning}) - Duration: {(end_time - start_time).total_seconds():.3f}s")
        
        # Decide if we need Refiner (only for experts)
        experts = ['scorer', 'decoration', 'scout', 'web', 'job_hunter']
        next_step = decision.target_agent
        if decision.target_agent in experts:
            # We want to go to Refiner first, but store the FINAL target in next_node
            pass

        return {
            "next_node": decision.target_agent,
            "focus_city": decision.focus_city or state.focus_city,
            "active_agent": decision.target_agent, # Set the active agent
            "usage": capture_usage(result, "router", mod_id)
        }
    except Exception as e:
        logger.error(f"❌ [ROUTER] Failed: {e}", exc_info=True)
        raise e

async def refiner_node(state: ODISGraphState, config: RunnableConfig):
    """Updates the briefing in the state."""
    deps = get_deps(config)
    deps.state = state
    from agents.refiner import refiner_agent
    
    try:
        # 1. Skip if no new info to summarize
        new_msgs_count = len(state.messages) - state.last_summarized_idx
        if new_msgs_count <= 0 and not state.experts_results and state.briefing:
            logger.info("⏩ [REFINER] No new info, skipping synthesis.")
            return {}

        mod_id = get_model("refiner")
        model = _get_p_model("refiner", client=deps.client)
        
        result = await refiner_agent.run("Mise à jour du briefing", deps=deps, model=model)
        
        briefing = result.output.briefing.strip()
        logger.debug(f"📝 [REFINER] Briefing updated.")
        logger.debug(briefing)
        
        return {
            "briefing": briefing,
            "last_summarized_idx": len(state.messages),
            "usage": capture_usage(result, "refiner", mod_id)
        }
    except Exception as e:
        logger.error(f"❌ [REFINER] Node failed: {e}", exc_info=True)
        raise e

async def interviewer_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"] if state.messages else ""
    
    from datetime import datetime
    start_time = datetime.now()
    logger.debug(f"🚀 [RELAY] Entering interviewer_node at {start_time.strftime('%H:%M:%S.%f')[:-3]}")
    
    try:
        mod_id = get_model("interviewer")
        model = _get_p_model("interviewer", client=deps.client)
        result = await interviewer_agent.run(user_msg, deps=deps, model=model)
        
        end_time = datetime.now()
        logger.debug(f"📊 [RELAY] Exiting interviewer_node at {end_time.strftime('%H:%M:%S.%f')[:-3]} - Duration: {(end_time - start_time).total_seconds():.3f}s")
        
        return {
            "messages": [{"role": "assistant", "content": result.output.response}],
            "search_criteria": result.output.search_criteria or deps.state.search_criteria,
            # CRITICAL: Lock the active agent
            "active_agent": "interviewer",
            # CRITICAL: Update completion status from Agent output
            "is_interview_complete": result.output.is_complete,
            "usage": capture_usage(result, "interviewer", mod_id)
        }
    except Exception as e:
        logger.error(f"❌ [INTERVIEWER] Failed: {e}", exc_info=True)
        raise e

async def scorer_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    
    from datetime import datetime
    start_time = datetime.now()
    logger.debug(f"🚀 [RELAY] Entering scorer_node at {start_time.strftime('%H:%M:%S.%f')[:-3]}")

    mod_id = get_model("scorer")
    model = _get_p_model("scorer", client=deps.client)
    result = await scorer_agent.run("Start Scoring", deps=deps, model=model) 

    # We extract top_cities EXCLUSIVELY from the tool history to avoid LLM parroting lag
    top_cities = []
    
    for msg in reversed(result.all_messages()):
        if hasattr(msg, 'parts'):
            for part in msg.parts:
                # In pydantic-ai, tool results are specifically in ToolReturnPart
                if isinstance(part, ToolReturnPart) and part.tool_name == 'compute_top_cities_tool':
                    if isinstance(part.content, dict) and "cities" in part.content:
                        logger.info("✅ [GRAPH] Recovered full top_cities from tool output history")
                        top_cities = part.content["cities"]
                        break

    end_time = datetime.now()
    logger.info(f"📊 [RELAY] Exiting scorer_node at {end_time.strftime('%H:%M:%S.%f')[:-3]} - Duration: {(end_time - start_time).total_seconds():.3f}s")

    # We update top_cities from recovered data
    return {
        "messages": [{"role": "assistant", "content": result.output.response}],
        "top_cities": top_cities,
        "experts_results": {"scorer": result.output.response}, # For synthesizer/refiner to see the text
        "next_node": END,
        "usage": capture_usage(result, "scorer", mod_id)
    }

# -- Decoration Cascade Nodes --

async def scout_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"] 
    
    mod_id = get_model("scout")
    model = _get_p_model("scout", client=deps.client)
    result = await scout_agent.run(user_msg, deps=deps, model=model)
    
    return {
        "experts_results": {"scout": str(result.output)},
        "usage": capture_usage(result, "scout", mod_id)
    }

async def web_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    mod_id = get_model("web")
    model = _get_p_model("web", client=deps.client)
    result = await web_agent.run(user_msg, deps=deps, model=model)
    return {
        "experts_results": {"web": str(result.output)},
        "usage": capture_usage(result, "web", mod_id)
    }

async def job_hunter_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    mod_id = get_model("job_hunter")
    model = _get_p_model("job_hunter", client=deps.client)
    result = await job_hunter_agent.run(user_msg, deps=deps, model=model)
    return {
        "experts_results": {"job_hunter": str(result.output)},
        "usage": capture_usage(result, "job_hunter", mod_id)
    }

async def synthesizer_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    scout_res = state.experts_results.get("scout", "N/A")
    web_res = state.experts_results.get("web", "N/A")
    job_res = state.experts_results.get("job_hunter", "N/A")
    
    input_msg = f"Synthèse demandée pour {state.focus_city}."
    
    mod_id = get_model("synthesizer")
    model = _get_p_model("synthesizer", client=deps.client)
    result = await synthesizer_agent.run(input_msg, deps=deps, model=model)
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result, "synthesizer", mod_id)
    }

# -- Standalone Tool Nodes --

async def scout_standalone_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    mod_id = get_model("scout")
    model = _get_p_model("scout", client=deps.client)
    result = await scout_agent.run(user_msg, deps=deps, model=model)
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result, "scout_solo", mod_id)
    }

async def web_standalone_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    mod_id = get_model("web")
    model = _get_p_model("web", client=deps.client)
    result = await web_agent.run(user_msg, deps=deps, model=model)
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result, "web_solo", mod_id)
    }

async def job_standalone_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    mod_id = get_model("job_hunter")
    model = _get_p_model("job_hunter", client=deps.client)
    result = await job_hunter_agent.run(user_msg, deps=deps, model=model)
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result, "job_hunter_solo", mod_id)
    }

# --- Graph Definition ---

def create_odis_graph():
    builder = StateGraph(ODISGraphState)
    
    # 1. Add Nodes
    builder.add_node("router", router_node)
    builder.add_node("refiner", refiner_node)
    
    builder.add_node("interviewer", interviewer_node)
    builder.add_node("scorer", scorer_node)
    
    # Standalone
    builder.add_node("scout_solo", scout_standalone_node)
    builder.add_node("web_solo", web_standalone_node)
    builder.add_node("job_hunter_solo", job_standalone_node)
    
    # Expert Parallel Nodes
    builder.add_node("scout", scout_node)
    builder.add_node("web", web_node)
    builder.add_node("job_hunter", job_hunter_node)
    builder.add_node("synthesizer", synthesizer_node)
    
    # 2. Edges
    # --- 1. OPTIMIZED ENTRY POINT (Router Bypass) ---
    builder.add_conditional_edges(
        START,
        route_from_start,
        {
            "interviewer": "interviewer",
            "router": "router"
        }
    )
    
    # --- 2. INTERVIEWER LOOP ---
    builder.add_conditional_edges(
        "interviewer",
        route_from_interviewer,
        {
            "interviewer": END,           # The Loop (Stop and wait for user)
            "router": "router"            # The Exit Strategy
        }
    )
    
    # After Router: decide whether to go to Refiner (experts) or direct (interviewer)
    def router_branch(state: ODISGraphState):
        decision = state.next_node
        experts = ['scorer', 'decoration', 'scout', 'web', 'job_hunter']
        if decision in experts:
            return "refiner"
        elif decision == "interviewer":
            return "interviewer"
        else:
            return END
            
    builder.add_conditional_edges("router", router_branch)
    
    # After Refiner: go to the expert agent
    def refiner_branch(state: ODISGraphState):
        decision = state.next_node
        if decision == "decoration":
            return ["scout", "web", "job_hunter"]
        elif decision == "scout":
            return "scout_solo"
        elif decision == "web":
            return "web_solo"
        elif decision == "job_hunter":
            return "job_hunter_solo"
        else:
            return decision # e.g. scorer
            
    builder.add_conditional_edges(
        "refiner",
        refiner_branch,
        {
            "scorer": "scorer",
            "scout_solo": "scout_solo",
            "web_solo": "web_solo",
            "job_hunter_solo": "job_hunter_solo",
            "scout": "scout",
            "web": "web",
            "job_hunter": "job_hunter"
        }
    )
    
    # Parallel Fan-In -> Synthesizer
    builder.add_edge("scout", "synthesizer")
    builder.add_edge("web", "synthesizer")
    builder.add_edge("job_hunter", "synthesizer")
    
    # End Edges
    builder.add_edge("synthesizer", END)
    builder.add_edge("scorer", END)
    builder.add_edge("scout_solo", END)
    builder.add_edge("web_solo", END)
    builder.add_edge("job_hunter_solo", END)
    
    return builder.compile()
