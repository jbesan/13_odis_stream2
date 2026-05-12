import logging
import asyncio
import logfire
from datetime import datetime
from typing import Literal, Dict, Any, List, Optional
from dataclasses import dataclass

from pydantic_graph import End
from pydantic_graph.beta import GraphBuilder, Graph, StepContext
from pydantic_graph.beta.join import reduce_list_append

from pydantic_ai import ModelSettings
from pydantic_ai.usage import UsageLimits

from agents.state import ODISDeps, GraphState, UsageStats, compute_criteria_hash, CommuneResult, SearchResultsData, ExpertList
from agents.router import router_agent, RoutingResult
from agents.utils import sanitize_llm_markdown
from agents.scout import scout_agent
from agents.web import web_agent
from agents.job_hunter import job_hunter_agent
from agents.synthesizer import synthesizer_agent
from utils.common import normalize_text
from utils.logger import log_agent_trace
import services.bq_logger as bq_logger
from agents.agent_config import get_model, get_p_model, get_model_settings

logger = logging.getLogger("odis_graph")

def capture_usage(result: Any, node_name: str, model_id: str) -> UsageStats:
    """
    Captures usage metrics from an agent run result and returns a UsageStats object.

    Args:
        result: The RunResult from pydantic-ai.
        node_name: The name of the node where the agent was executed.
        model_id: The ID of the model used for the execution.

    Returns:
        A UsageStats object containing tokens, cost, and breakdown.
    """
    try:
        u = result.usage()
        rate_in, rate_out = (0.25, 1.50) if any(x in model_id.lower() for x in ["google-gla:gemini-3.1-flash-lite-preview"]) else (0.10, 0.40)
        cost = (u.input_tokens * rate_in / 1_000_000) + (u.output_tokens * rate_out / 1_000_000)
        
        req_count = getattr(u, 'requests', 1)
        logger.info(f"📊 [USAGE] {node_name}: {u.total_tokens} t (${cost:.4f}) over {req_count} requests")
        
        breakdown_entry = {
            "model": model_id, 
            "input": u.input_tokens, 
            "output": u.output_tokens, 
            "total": u.total_tokens, 
            "cost": float(cost),
            "requests": req_count
        }
        
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

@dataclass
class AgentArtifact:
    domain: str
    result: str # Markdown formatted string with the result
    usage: UsageStats

@dataclass
class DirectSynthesis:
    """DTO for cases where no experts are needed and we go straight to synthesis."""
    pass

# --- Graph Nodes ---
@logfire.instrument("Node: triage")
async def triage_step(ctx: StepContext[GraphState, ODISDeps, None]) -> ExpertList | DirectSynthesis:
    """Decides which experts to trigger based on execution mode or Router LLM."""
    mode = ctx.state.execution_mode
    if mode == "full_analysis":
        return ExpertList(experts=["scout", "web", "job_hunter"])
    
    # specific_ask mode requires routing LLM
    user_msg = ctx.state.messages[-1]["content"] if ctx.state.messages else ""
    mod_id = get_model("router")
    model = get_p_model("router", client=ctx.deps.client)
    
    try:
        from pydantic_ai.result import RunResult
        result: RunResult[RoutingResult] = await router_agent.run(
            user_msg, 
            deps=ctx.deps, 
            model=model,
            model_settings=get_model_settings("router"),
            usage_limits=UsageLimits(request_limit=10)
        )
        decision = result.output
        log_agent_trace("router", mod_id, result)
        
        logger.info(f"🧠 [ROUTER] Direction: {decision.target_agent}")
        if decision.focus_city:
            ctx.state.focus_city = decision.focus_city
        
        if decision.target_agent == 'analysis':
            return ExpertList(experts=["scout", "web", "job_hunter"])
        elif decision.target_agent == 'synthesizer':
            return DirectSynthesis()
        else:
            return ExpertList(experts=[decision.target_agent])
            
    except Exception as e:
        logger.error(f"❌ [ROUTER] Failed: {e}")
        # Graceful fallback: run all 3
        return ExpertList(experts=["scout", "web", "job_hunter"])

async def extract_domains(ctx: StepContext[GraphState, ODISDeps, ExpertList]) -> list[str]:
    """Helper to unwrap the DTO into a list of strings for mapping."""
    return ctx.inputs.experts

@logfire.instrument("Expert Node: {ctx.inputs}")
async def expert_worker_step(ctx: StepContext[GraphState, ODISDeps, str]) -> AgentArtifact:
    """Parallel worker that handles cache bypass and delegates to the appropriate agent."""
    domain = ctx.inputs
    user_msg = ctx.state.messages[-1]["content"] if ctx.state.messages else ""
    focus = ctx.state.focus_city.name.lower().strip() if ctx.state.focus_city else "unknown"
    h = compute_criteria_hash(ctx.state.search_criteria)
    ctx.state.criteria_hash = h
    logfire.info("Processing Expert Node for {search_hash}", search_hash=h)
    
    # 1. CACHE BYPASS
    if ctx.state.execution_mode == 'full_analysis' and ctx.state.search_results and ctx.state.search_results.search_hash == h:
        city_res = ctx.state.search_results.get_by_code(ctx.state.focus_city.codgeo if ctx.state.focus_city else "")
        if city_res and city_res.expert_analysis.get(domain):
            logger.info(f"⏭️ [{domain.upper()}] Artifact already exists for {focus}. Skipping LLM call.")
            return AgentArtifact(domain=domain, result=city_res.expert_analysis.get(domain), usage=UsageStats())
            
    # 2. RUN EXPERT
    logger.info(f"🚀 [{domain.upper()}] Node started for {focus}.")
    mod_id = get_model(domain)
    model = get_p_model(domain, client=ctx.deps.client)
    
    agent_map = {
        "scout": scout_agent,
        "web": web_agent,
        "job_hunter": job_hunter_agent
    }
    agent = agent_map.get(domain)
    
    if not agent:
        return AgentArtifact(domain=domain, result="Agent not found.", usage=UsageStats())

    try:
        result = await agent.run(
            user_msg, 
            deps=ctx.deps, 
            model=model,
            model_settings=get_model_settings(domain),
            usage_limits=UsageLimits(request_limit=15)
        )
        log_agent_trace(domain, mod_id, result)
        logger.info(f"✅ [{domain.upper()}] Node finished for {focus}.")
        
        artifact_str = f"### Recherches effectuees\n{result.output.searched}\n\n### Resultats\n{result.output.result}"
        usage = capture_usage(result, domain, mod_id)
        return AgentArtifact(domain=domain, result=artifact_str, usage=usage)
    except Exception as e:
        logger.error(f"❌ [{domain.upper()}] Error: {e}")
        return AgentArtifact(domain=domain, result=f"Erreur d'analyse: {e}", usage=UsageStats())

@logfire.instrument("Node: synthesizer")
async def synthesizer_step(ctx: StepContext[GraphState, ODISDeps, list[AgentArtifact] | DirectSynthesis]) -> End[str]:
    """Merges artifacts into state and produces the final synthesis."""
    city_name = ctx.state.focus_city.name if ctx.state.focus_city else "Unknown"
    logger.info(f"🚀 [SYNTHESIZER] starting for {city_name}...")
    
    input_data = ctx.inputs
    
    # 1. MERGE ARTIFACTS INTO STATE (if any)
    if isinstance(input_data, list) and input_data and ctx.state.search_results and ctx.state.focus_city:
        city_res = ctx.state.search_results.get_by_code(ctx.state.focus_city.codgeo)
        if city_res:
            for artifact in input_data:
                city_res.expert_analysis[artifact.domain] = artifact.result
    
    # 2. RUN SYNTHESIZER LLM
    input_msg = f"Synthèse demandée pour {city_name}."
    mod_id = get_model("synthesizer")
    model = get_p_model("synthesizer", client=ctx.deps.client)

    final_content = ""
    try:
        result = await synthesizer_agent.run(
            input_msg, 
            deps=ctx.deps, 
            model=model,
            model_settings=get_model_settings("synthesizer"),
            usage_limits=UsageLimits(request_limit=10)
        )
        log_agent_trace("synthesizer", mod_id, result)
        final_content = sanitize_llm_markdown(result.output)
        
        # Merge Usage
        usage = capture_usage(result, "synthesizer", mod_id)
        # TODO: Add global usage merging logic here if necessary
        
    except Exception as e:
        logger.error(f"❌ [SYNTHESIZER-FAILURE] Agent run failed: {e}", exc_info=True)
        final_content = "⚠️ _Désolé, une erreur technique est survenue lors de la synthèse finale. Les experts ont cependant fini leur travail._"
    
    # BQ Logging
    try:
        user_input = ctx.state.messages[-1].get("content", "Analyse IA") if ctx.state.messages else "Analyse IA"
        # We need state to be dict
        from dataclasses import asdict
        state_dict = asdict(ctx.state)
        # ensure focus_city is dict for BQ serialization
        if ctx.state.focus_city:
             state_dict["focus_city"] = ctx.state.focus_city.model_dump()
        if ctx.state.search_criteria:
             state_dict["search_criteria"] = ctx.state.search_criteria.model_dump()
        if ctx.state.search_results:
             state_dict["search_results"] = ctx.state.search_results.model_dump()

        await asyncio.to_thread(
            bq_logger.log_agent_state_to_bq,
            user_input, 
            state_dict,
            ctx.state.interaction_id,
            ctx.state.username
        )
    except Exception as e:
        logger.warning(f"⚠️ [BQ-LOG] Synthesis logging failed: {e}")

    # Build odis_synthesis
    new_odis_synthesis = []
    if ctx.state.execution_mode == 'specific_ask' and ctx.state.messages:
        last_user_msg = ctx.state.messages[-1]
        if last_user_msg.get("role") == "user":
            new_odis_synthesis.append(last_user_msg)
            
    new_odis_synthesis.append({"role": "assistant", "content": final_content})
    
    # Apply back to the city result if possible
    if ctx.state.search_results and ctx.state.focus_city:
        city_res = ctx.state.search_results.get_by_code(ctx.state.focus_city.codgeo)
        if city_res:
            if not city_res.odis_synthesis: city_res.odis_synthesis = []
            city_res.odis_synthesis.extend(new_odis_synthesis)

    return End(final_content)


def create_odis_graph() -> Graph[GraphState, ODISDeps, None, str]:
    """Builds the pydantic-graph state machine."""
    g = GraphBuilder(state_type=GraphState, deps_type=ODISDeps, output_type=str)
    
    triage_node = g.step(triage_step)
    extract_domains_node = g.step(extract_domains)
    expert_worker_node = g.step(expert_worker_step)
    synthesizer_node = g.step(synthesizer_step)
    
    collect_experts = g.join(reduce_list_append, initial_factory=list)
    
    # MapReduce Pipeline
    g.add(g.edge_from(g.start_node).to(triage_node))
    
    # Conditional branching from triage
    decision = g.decision() \
        .branch(g.match(ExpertList).to(extract_domains_node)) \
        .branch(g.match(DirectSynthesis).to(synthesizer_node))
    
    g.add(g.edge_from(triage_node).to(decision))
    
    g.add_mapping_edge(extract_domains_node, expert_worker_node)
    g.add(g.edge_from(expert_worker_node).to(collect_experts))
    g.add(g.edge_from(collect_experts).to(synthesizer_node))
    g.add(g.edge_from(synthesizer_node).to(g.end_node))
    
    return g.build()
