
import logging
import os
from typing import Literal, Dict, Any
from langgraph.graph import StateGraph, END
from google import genai
from dotenv import load_dotenv

# Agents & Logic
from agents.state import ODISDeps, ODISGraphState, UsageStats
from agents.refiner import ContextRefiner
from agents.router import router_agent, RoutingResult
from agents.interviewer import interviewer_agent
from agents.scorer import scorer_agent
from agents.scout import scout_agent
from agents.web import web_agent
from agents.job_hunter import job_hunter_agent
from agents.synthesizer import synthesizer_agent
from agents.agent_config import get_model

load_dotenv()

logger = logging.getLogger("odis_graph")


load_dotenv()

logger = logging.getLogger("odis_graph")



from langchain_core.runnables import RunnableConfig

# --- Helpers ---

def get_deps(config: RunnableConfig) -> ODISDeps:
    """Helper to extract ODISDeps from LangGraph config."""
    deps = config.get("configurable", {}).get("deps")
    if not deps:
        raise ValueError("ODISDeps missing in graph config['configurable']['deps']")
    return deps

def capture_usage(result) -> UsageStats:
    """Extracts usage from PydanticAI result and calculates cost (Gemini 2.5 Flash Lite and Gemini 3 Flash)."""
    u = result.usage()
    # Rates for Gemini 2.5 Flash Lite : $0.010/1M in, $0.40/1M out
    # Rates for Gemini 3 Flash : $0.050/1M in, $3.00/1M out
    cost = (u.input_tokens * 0.050 / 1_000_000) + (u.output_tokens * 3.00 / 1_000_000)
    return UsageStats(
        input_tokens=u.input_tokens,
        output_tokens=u.output_tokens,
        total_tokens=u.total_tokens,
        cost_usd=cost
    )

from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

def _get_p_model(agent_name: str, client: genai.Client) -> GoogleModel:
    model_name = get_model(agent_name)
    
    # Explicitly inject the fresh client for this thread/loop
    provider = GoogleProvider(client=client)
    return GoogleModel(model_name, provider=provider)

# --- Nodes ---

async def router_node(state: ODISGraphState, config: RunnableConfig):
    """Decides the next step based on user input and state."""
    deps = get_deps(config)
    deps.state = state
    
    # Input is the last message
    user_msg = state.messages[-1]["content"] if state.messages else ""
    
    # Run Router Agent with bound model
    try:
        # Inject client!
        model = _get_p_model("router", client=deps.client)
        result = await router_agent.run(user_msg, deps=deps, model=model)
        decision = result.output
        logger.info(f"🧠 [ROUTER] Direction: {decision.target_agent} ({decision.reasoning})")
        
        return {
            "next_node": decision.target_agent,
            "usage": capture_usage(result)
        }
    except Exception as e:
        logger.error(f"❌ [ROUTER] Failed: {e}", exc_info=True)
        raise e

async def refiner_node(state: ODISGraphState, config: RunnableConfig):
    """Updates the briefing in the state."""
    deps = get_deps(config)
    from agents.refiner import generate_briefing
    
    try:
        briefing = await generate_briefing(state, deps)
        logger.info(f"🧠 [REFINER] Briefing updated.")
        return {"briefing": briefing}
    except Exception as e:
        logger.error(f"❌ [REFINER] Failed: {e}", exc_info=True)
        raise e

async def interviewer_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"] if state.messages else ""
    
    try:
        model = _get_p_model("interviewer", client=deps.client)
        result = await interviewer_agent.run(user_msg, deps=deps, model=model)
        
        # We return the NEW messages and the updated search_criteria
        return {
            "messages": [{"role": "assistant", "content": result.output.response}],
            "search_criteria": deps.state.search_criteria, # Return the mutated Pydantic model
            "next_node": END,
            "usage": capture_usage(result)
        }
    except Exception as e:
        logger.error(f"❌ [INTERVIEWER] Failed: {e}", exc_info=True)
        raise e

async def scorer_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    
    model = _get_p_model("scorer", client=deps.client)
    result = await scorer_agent.run("Start Scoring", deps=deps, model=model) 
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result)
    }

# -- Decoration Cascade Nodes --

async def scout_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"] 
    
    model = _get_p_model("scout", client=deps.client)
    result = await scout_agent.run(user_msg, deps=deps, model=model)
    
    return {
        "experts_results": {"scout": str(result.output)},
        "usage": capture_usage(result)
    }

async def web_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    model = _get_p_model("web", client=deps.client)
    result = await web_agent.run(user_msg, deps=deps, model=model)
    return {
        "experts_results": {"web": str(result.output)},
        "usage": capture_usage(result)
    }

async def job_hunter_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    model = _get_p_model("job_hunter", client=deps.client)
    result = await job_hunter_agent.run(user_msg, deps=deps, model=model)
    return {
        "experts_results": {"job_hunter": str(result.output)},
        "usage": capture_usage(result)
    }

async def synthesizer_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    scout_res = state.experts_results.get("scout", "N/A")
    web_res = state.experts_results.get("web", "N/A")
    job_res = state.experts_results.get("job_hunter", "N/A")
    
    input_msg = f"""
    Synthèse demandée pour {state.focus_city}.
    
    [SCOUT]: {scout_res}
    [WEB]: {web_res}
    [JOB]: {job_res}
    """
    
    model = _get_p_model("synthesizer", client=deps.client)
    result = await synthesizer_agent.run(input_msg, deps=deps, model=model)
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result)
    }

# -- Standalone Tool Nodes --

async def scout_standalone_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    model = _get_p_model("scout", client=deps.client)
    result = await scout_agent.run(user_msg, deps=deps, model=model)
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result)
    }

async def web_standalone_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    model = _get_p_model("web", client=deps.client)
    result = await web_agent.run(user_msg, deps=deps, model=model)
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result)
    }

async def job_standalone_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    model = _get_p_model("job_hunter", client=deps.client)
    result = await job_hunter_agent.run(user_msg, deps=deps, model=model)
    return {
        "messages": [{"role": "assistant", "content": str(result.output)}],
        "next_node": END,
        "usage": capture_usage(result)
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
    builder.add_node("job_solo", job_standalone_node)
    
    # Expert Parallel Nodes
    builder.add_node("scout", scout_node)
    builder.add_node("web", web_node)
    builder.add_node("job_hunter", job_hunter_node)
    builder.add_node("synthesizer", synthesizer_node)
    
    # 2. Edges
    builder.set_entry_point("refiner")
    
    # After Refiner, always Router
    builder.add_edge("refiner", "router")
    
    # After Router, Branching
    def route_decision(state: ODISGraphState):
        decision = state.next_node # set by router
        if decision == "decoration":
            return ["scout", "web", "job_hunter"] # Return List for Parallel Fan-Out
        elif decision == "scout":
            return "scout_solo"
        elif decision == "web":
            return "web_solo"
        elif decision == "job_hunter":
            return "job_solo"
        else:
            return decision # interviewer, scorer
            
    builder.add_conditional_edges(
        "router",
        route_decision,
        {
            "interviewer": "interviewer",
            "scorer": "scorer",
            "scout_solo": "scout_solo",
            "web_solo": "web_solo",
            "job_solo": "job_solo",
            "scout": "scout",
            "web": "web",
            "job_hunter": "job_hunter"
        }
    )
    
    # Parallel Fan-In -> Synthesizer
    builder.add_edge("scout", "synthesizer")
    builder.add_edge("web", "synthesizer")
    builder.add_edge("job_hunter", "synthesizer")
    
    # Synthesizer End
    builder.add_edge("synthesizer", END)
    
    # Standalone End Edges
    builder.add_edge("interviewer", END)
    builder.add_edge("scorer", END)
    builder.add_edge("scout_solo", END)
    builder.add_edge("web_solo", END)
    builder.add_edge("job_solo", END)
    
    return builder.compile()
