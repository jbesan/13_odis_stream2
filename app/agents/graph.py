import logging
import os
import operator
from datetime import datetime
from typing import Literal, Dict, Any, List, Optional, Annotated
from agents.agent_config import get_model, get_p_model

# Loading LangGraph + Pydantic AI components
from langgraph.graph import StateGraph, END, START
from langchain_core.runnables import RunnableConfig
from pydantic_ai import Agent, RunContext, ModelSettings
from pydantic_ai.messages import ToolReturnPart

# Loading each ODIS agent
from agents.state import ODISDeps, ODISGraphState, UsageStats, FocusCity, compute_criteria_hash
from agents.router import router_agent, RoutingResult
from agents.utils import sanitize_llm_markdown
from agents.interviewer import interviewer_agent
from agents.refiner import refiner_agent
from agents.scorer import scorer_agent
from agents.scout import scout_agent
from agents.web import web_agent
from agents.job_hunter import job_hunter_agent
from agents.synthesizer import synthesizer_agent


logger = logging.getLogger("odis_graph")

def get_deps(config: RunnableConfig) -> ODISDeps:
    deps = config.get("configurable", {}).get("deps")
    if not deps: raise ValueError("ODISDeps missing")
    return deps

def capture_usage(result, node_name: str, model_id: str) -> UsageStats:
    try:
        u = result.usage()
        # Pricing for Gemini 3 line (Flash Lite Preview approx)
        rate_in, rate_out = (0.05, 3.00) if any(x in model_id.lower() for x in ["gemini-3", "gemini-4"]) else (0.10, 0.40)
        cost = (u.input_tokens * rate_in / 1_000_000) + (u.output_tokens * rate_out / 1_000_000)
        
        breakdown_entry = {
            "model": model_id, 
            "input": u.input_tokens, 
            "output": u.output_tokens, 
            "total": u.total_tokens, 
            "cost": float(cost)
        }
        
        logger.info(f"📊 [USAGE] {node_name}: {u.total_tokens} t (${cost:.4f})")
        
        return UsageStats(
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            total_tokens=u.total_tokens,
            cost_usd=cost,
            breakdown={node_name: breakdown_entry}
        )
    except Exception as e:
        logger.warning(f"⚠️ [USAGE] capture_usage failed for {node_name}: {e}")
        return UsageStats()



# --- Routing Logic (Pure Python) ---

def route_from_start(state: ODISGraphState):
    """
    SOTA Pattern: Router Bypass.
    Force Discovery phase (Interviewer) until completion flag is set.
    """
    if not state.is_interview_complete:
        logger.info("🎤 [DISCOVERY] Forcing Interviewer phase.")
        return "interviewer"
    
    # Default entry point for post-discovery
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
    logger.debug(f"🚀 [RELAY] Entering router_node")

    # Input is the last message
    user_msg = state.messages[-1]["content"] if state.messages else ""
    
    # Run Router Agent with bound model
    try:
        # Inject client!
        mod_id = get_model("router")
        model =  get_p_model("router", client=deps.client)
        from pydantic_ai.usage import UsageLimits
        result = await router_agent.run(
            user_msg, 
            deps=deps, 
            model=model,
            model_settings=ModelSettings(max_output_tokens=4096),
            usage_limits=UsageLimits(request_limit=10)
        )
        decision = result.output
        
        end_time = datetime.now()
        logger.info(f"🧠 [ROUTER] Direction: {decision.target_agent}")
        
        # Decide if we need Refiner (only for experts)
        # SOTA: Always launch the full trio to ensure LangGraph fan-in condition is met.
        # The cache-bypass logic in each node will prevent redundant LLM calls.
        experts_mapping = {
            'scorer': {'pending': [], 'mode': 'full_analysis'},
            'analysis': {'pending': ['scout', 'web', 'job_hunter'], 'mode': 'full_analysis'},
            'scout': {'pending': ['scout_solo'], 'mode': 'specific_ask'},
            'web': {'pending': ['web_solo'], 'mode': 'specific_ask'},
            'job_hunter': {'pending': ['job_hunter_solo'], 'mode': 'specific_ask'},
            'synthesizer': {'pending': ['synthesizer'], 'mode': 'specific_ask'},
        }

        pending = []
        mode = 'full_analysis'
        next_step = decision.target_agent

        if decision.target_agent in experts_mapping:
            pending = experts_mapping[decision.target_agent]['pending']
            mode = experts_mapping[decision.target_agent]['mode']

        focus = state.focus_city
        # if decision.focus_city:
        #     # If router found a city name, we wrap it. Refiner will refine it later.
        #     focus = FocusCity(name=decision.focus_city, codgeo="")
        
        h = compute_criteria_hash(state.search_criteria)
        return {
            "next_node": decision.target_agent,
            "focus_city": (decision.focus_city if decision.focus_city and decision.focus_city.name else state.focus_city),
            "criteria_hash": h,
            "active_agent": decision.target_agent,
            "pending_experts": pending,
            "execution_mode": mode,
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
        if new_msgs_count <= 0 and not state.scoring_results and state.briefing:
            logger.info("⏩ [REFINER] No new info, skipping synthesis.")
            return {}

        mod_id = get_model("refiner")
        model =  get_p_model("refiner", client=deps.client)
        
        from pydantic_ai.usage import UsageLimits
        result = await refiner_agent.run(
            "Mise à jour du briefing", 
            deps=deps, 
            model=model,
            model_settings=ModelSettings(max_output_tokens=4096),
            usage_limits=UsageLimits(request_limit=10)
        )
        
        briefing = result.output.briefing.strip()
        logger.info(f"📝 [REFINER] Briefing updated.")
        logger.debug(briefing)
        
        # Robust update: only override if not empty
        updates = {"last_summarized_idx": len(state.messages)}
        if briefing:
            updates["briefing"] = briefing
        
        updates["usage"] = capture_usage(result, "refiner", mod_id)
        return updates
    except Exception as e:
        logger.error(f"❌ [REFINER] Node failed: {e}", exc_info=True)
        raise e

async def interviewer_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"] if state.messages else ""
    start_time = datetime.now()
    
    try:
        mod_id = get_model("interviewer")
        model =  get_p_model("interviewer", client=deps.client)
        from pydantic_ai.usage import UsageLimits
        result = await interviewer_agent.run(
            user_msg, deps=deps, model=model,
            model_settings=ModelSettings(max_output_tokens=4096),
            usage_limits=UsageLimits(request_limit=10)
        )
        
        end_time = datetime.now()
        logger.info(f"📊 [INTERVIEWER] Done in {(end_time - start_time).total_seconds():.3f}s")
        
        return {
            "messages": [{"role": "assistant", "content": sanitize_llm_markdown(result.output.response)}],
            "search_criteria": result.output.search_criteria,
            "criteria_hash": compute_criteria_hash(result.output.search_criteria),
            "active_agent": "interviewer",
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
    logger.info(f"🚀 [SCORER] Entering scorer_node at {start_time.strftime('%H:%M:%S.%f')[:-3]}")

    mod_id = get_model("scorer")
    model =  get_p_model("scorer", client=deps.client)
    from pydantic_ai.usage import UsageLimits
    from pydantic_ai.exceptions import UsageLimitExceeded
    try:
        result = await scorer_agent.run(
            "Start Scoring", 
            deps=deps, 
            model=model,
            model_settings=ModelSettings(max_output_tokens=4096),
            usage_limits=UsageLimits(request_limit=10)
        ) 
    except UsageLimitExceeded as e:
        logger.error(f"🚨 [SCORER] Usage limit exceeded: {e}. Too many turns or tool calls.")
        # Attempt to return a graceful failure message 
        return {
            "messages": [{"role": "assistant", "content": "Désolé, j'ai rencontré une erreur interne suite à une boucle de réflexion. Peux-tu reformuler ta demande ?"}],
            "active_agent": "scorer",
            "next_node": END
        }

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
    logger.debug(f"📊 [SCORER] Exiting scorer_node at {end_time.strftime('%H:%M:%S.%f')[:-3]} - Duration: {(end_time - start_time).total_seconds():.3f}s")

    # --- Unified Telemetry Logging (BigQuery) ---
    try:
        from services import telemetry
        criteria_model = state.search_criteria
        full_config = criteria_model.model_dump()
        
        criteria_keys = ['commune_actuelle', 'loc_search_area', 'situation_famille', 'nb_enfants', 'besoin_emploi', 'besoin_sante', 'inc_services_add_selection']
        search_criteria = {k: full_config.get(k) for k in criteria_keys if k in full_config}
        weights = {k: v for k, v in full_config.items() if k.startswith('poids_')}
        
        top_5_results = []
        top_5_breakdown = {}
        if top_cities:
            top_5 = top_cities[:5]
            top_5_results = [
                {"codgeo": str(c.get('codgeo')), "libgeo": str(c.get('libgeo', '')), "score": float(c.get('weighted_score', 0))} 
                for c in top_5
            ]
            
            # Granular Breakdown (extracted from tool results)
            for city in top_5:
                codgeo = str(city.get('codgeo'))
                top_5_breakdown[codgeo] = {
                    "libgeo": city.get('libgeo', city.get('name', '')),
                    "scores": city.get('details', {}).get('scores', {})
                }
        
        telemetry.log_search_complete(
            criteria=search_criteria,
            weights=weights,
            results=top_5_results,
            breakdown=top_5_breakdown,
            source_flow='ia'
        )
    except Exception as tel_e:
        logger.warning(f"⚠️ [SCORER] Telemetry failed: {tel_e}")

    # --- Message Construction ---
    # We combine the global response and individual pitches into a clean markdown message for the chat.
    final_content = result.output.response or ""
    logging.info(f"💎 [DEBUG-RAW-SCORER] response={repr(final_content)}")
    
    if result.output.pitches_per_city:
        final_content += "\n\n### 📍 Top des communes recommandées\n"
        for city in result.output.pitches_per_city:
            logging.info(f"💎 [DEBUG-RAW-PITCH] city={city.codgeo} pitch={repr(city.pitch)}")
            final_content += f"\n- **{city.name}** ({city.codgeo})\n  {sanitize_llm_markdown(city.pitch)}\n"
    
    final_content = sanitize_llm_markdown(final_content)
    logging.info(f"✨ [DEBUG-SANITY-CHECK-FINAL] content={repr(final_content)}")
    
    logger.info(f"📤 [SCORER] Final message constructed (length: {len(final_content)})")

    return {
        "messages": [{"role": "assistant", "content": final_content}],
        "top_cities": top_cities,
        "criteria_hash": compute_criteria_hash(state.search_criteria),
        "scoring_results": {"scorer": result.output.model_dump()}, # Now it contains response and pitches_per_city 

        "next_node": END,
        "usage": capture_usage(result, "scorer", mod_id)
    }

# -- Decoration Cascade Nodes --

async def scout_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"] 
    
    h = compute_criteria_hash(state.search_criteria)
    focus = state.focus_city.name.lower().strip() if state.focus_city else "unknown"
    
    # --- CACHE BYPASS ---
    # Bypass cache ONLY if in full_analysis mode. 
    # specific_ask ALWAYS triggers the LLM to answer the user's question.
    if state.execution_mode == 'full_analysis':
        existing = state.commune_artifacts.get(focus, {}).get(h, {}).get("scout")
        if existing:
            logger.info(f"⏭️ [SCOUT] Artifact already exists for {focus}. Skipping LLM call.")
            return {"criteria_hash": h} 

    logger.info("🚀 [SCOUT] Node started.")
    mod_id = get_model("scout")
    model =  get_p_model("scout", client=deps.client)
    from pydantic_ai.usage import UsageLimits
    result = await scout_agent.run(
        user_msg, 
        deps=deps, 
        model=model,
        model_settings=ModelSettings(max_output_tokens=4096),
        usage_limits=UsageLimits(request_limit=10)
    )
    
    logger.info(f"✅ [SCOUT] Node finished for {focus}.")
    
    return {
        "commune_artifacts": {focus: {h: {"scout": f"### Recherches effectuees\n{result.output.searched}\n\n### Resultats\n{result.output.result}"}}},
        "criteria_hash": h,
        "usage": capture_usage(result, "scout", mod_id)
    }

async def web_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    h = compute_criteria_hash(state.search_criteria)
    focus = state.focus_city.name.lower().strip() if state.focus_city else "unknown"
    
    # --- CACHE BYPASS ---
    if state.execution_mode == 'full_analysis':
        existing = state.commune_artifacts.get(focus, {}).get(h, {}).get("web")
        if existing:
            logger.info(f"⏭️ [WEB] Artifact already exists for {focus}. Skipping LLM call.")
            return {"criteria_hash": h}

    logger.info("🚀 [WEB] Node started.")
    mod_id = get_model("web")
    model =  get_p_model("web", client=deps.client)
    from pydantic_ai.usage import UsageLimits
    result = await web_agent.run(
        user_msg, 
        deps=deps, 
        model=model,
        model_settings=ModelSettings(max_output_tokens=4096),
        usage_limits=UsageLimits(request_limit=15) # Web needs more for research
    )
    
    logger.info(f"✅ [WEB] Node finished for {focus}.")
    
    return {
        "commune_artifacts": {focus: {h: {"web": f"### Recherches effectuees\n{result.output.searched}\n\n### Resultats\n{result.output.result}"}}},
        "criteria_hash": h,
        "usage": capture_usage(result, "web", mod_id)
    }

async def job_hunter_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    user_msg = state.messages[-1]["content"]
    
    h = compute_criteria_hash(state.search_criteria)
    focus = state.focus_city.name.lower().strip() if state.focus_city else "unknown"
    
    # --- CACHE BYPASS ---
    if state.execution_mode == 'full_analysis':
        existing = state.commune_artifacts.get(focus, {}).get(h, {}).get("job_hunter")
        if existing:
            logger.info(f"⏭️ [JOB_HUNTER] Artifact already exists for {focus}. Skipping LLM call.")
            return {"criteria_hash": h}

    logger.info("🚀 [JOB_HUNTER] Node started.")
    mod_id = get_model("job_hunter")
    model =  get_p_model("job_hunter", client=deps.client)
    from pydantic_ai.usage import UsageLimits
    result = await job_hunter_agent.run(
        user_msg, 
        deps=deps, 
        model=model,
        model_settings=ModelSettings(max_output_tokens=4096),
        usage_limits=UsageLimits(request_limit=15)
    )
    
    logger.info(f"✅ [JOB_HUNTER] Node finished for {focus}.")
    
    return {
        "commune_artifacts": {focus: {h: {"job_hunter": f"### Recherches effectuees\n{result.output.searched}\n\n### Resultats\n{result.output.result}"}}},
        "criteria_hash": h,
        "usage": capture_usage(result, "job_hunter", mod_id)
    }

async def synthesizer_node(state: ODISGraphState, config: RunnableConfig):
    deps = get_deps(config)
    deps.state = state
    
    city_name = state.focus_city.name if state.focus_city else "Unknown"
    logger.info(f"🎤 [SYNTHESIZER] Starting synthesis for {city_name}...")
    input_msg = f"Synthèse demandée pour {city_name}."
    
    mod_id = get_model("synthesizer")
    model =  get_p_model("synthesizer", client=deps.client)
    from pydantic_ai.usage import UsageLimits
    result = await synthesizer_agent.run(
        input_msg, 
        deps=deps, 
        model=model,
        model_settings=ModelSettings(max_output_tokens=4096),
        usage_limits=UsageLimits(request_limit=10)
    )
    logger.info(f"✅ [SYNTHESIZER] Synthesis complete for {city_name}.")
    
    return {
        "messages": [{"role": "assistant", "content": sanitize_llm_markdown(result.output.response)}],
        "next_node": END,
        "pending_experts": [], # Clear the pending list here now
        "usage": capture_usage(result, "synthesizer", mod_id)
    }

# --- Graph Definition ---

def create_odis_graph():
    builder = StateGraph(ODISGraphState)
    
    # 1. Add Nodes
    builder.add_node("router", router_node)
    builder.add_node("refiner", refiner_node)
    builder.add_node("interviewer", interviewer_node)
    builder.add_node("scorer", scorer_node)
    
    # Expert Nodes (Parallel)
    builder.add_node("scout", scout_node)
    builder.add_node("web", web_node)
    builder.add_node("job_hunter", job_hunter_node)
    
    # Expert Nodes (Solo)
    builder.add_node("scout_solo", scout_node)
    builder.add_node("web_solo", web_node)
    builder.add_node("job_hunter_solo", job_hunter_node)
    
    builder.add_node("synthesizer", synthesizer_node)
    
    # Logic nodes (No-ops for wiring)
    # builder.add_node("joiner", joiner_node)
    
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
        # Analysis mode, solo experts or synthesizer go through Refiner
        experts = ['scorer', 'analysis', 'scout', 'web', 'job_hunter', 'synthesizer']
        if decision in experts:
            return "refiner"
        elif decision == "interviewer":
            return "interviewer"
        else:
            return END
            
    builder.add_conditional_edges(
        "router", 
        router_branch,
        {
            "refiner": "refiner",
            "interviewer": "interviewer",
            END: END
        }
    )
    
    # After Refiner: the Experts or Scorer takes over (Fan-out)
    def refiner_branch(state: ODISGraphState):
        decision = state.next_node
        if decision == "scorer":
            return "scorer"
        
        # Everything else (analysis or solo) triggers parallel experts
        logger.info(f"🔀 [REFINER] Launching experts: {state.pending_experts}")
        return state.pending_experts or END
            
    builder.add_conditional_edges(
        "refiner",
        refiner_branch,
        {
            "scorer": "scorer",
            "scout": "scout",
            "web": "web",
            "job_hunter": "job_hunter",
            "scout_solo": "scout_solo",
            "web_solo": "web_solo",
            "job_hunter_solo": "job_hunter_solo",
            "synthesizer": "synthesizer",
            END: END
        }
    )
    
    # Fan-in: Parallel Expert nodes converge to synthesizer
    builder.add_edge(["scout", "web", "job_hunter"], "synthesizer")
    
    # Direct edges: Solo experts go straight to synthesizer
    builder.add_edge("scout_solo", "synthesizer")
    builder.add_edge("web_solo", "synthesizer")
    builder.add_edge("job_hunter_solo", "synthesizer")
    
    # End Edges
    builder.add_edge("synthesizer", END)
    builder.add_edge("scorer", END)
    
    return builder.compile()
