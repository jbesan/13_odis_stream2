import logging
import asyncio
import logfire
from datetime import datetime
from typing import Literal, Dict, Any, List, Optional
from dataclasses import dataclass

from pydantic_graph import End
from pydantic_graph.beta import GraphBuilder, Graph, StepContext, TypeExpression
from pydantic_graph.beta.join import reduce_list_append

from pydantic_ai import ModelSettings
from pydantic_ai.usage import UsageLimits

from agents.state import ODISDeps, GraphState, UsageStats, compute_criteria_hash, CommuneResult, SearchResultsData, ExpertList
from agents.ts_agent import ts_agent, SwarmPlan
from agents.utils import sanitize_llm_markdown
from agents.job_hunter import job_hunter_agent
from agents.housing_expert import housing_expert_agent
from agents.mobility_expert import mobility_expert_agent
from agents.healthcare_expert import healthcare_expert_agent
from agents.education_expert import education_expert_agent
from agents.social_integration_expert import social_integration_expert_agent
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

# --- Graph Nodes ---
@logfire.instrument("Node: triage")
async def triage_step(ctx: StepContext[GraphState, ODISDeps, None]) -> ExpertList | End[str]:
    """Runs the TS_AGENT (planner) to identify the tasks and active Skill Cards, or outputs direct answers."""
    user_msg = ctx.state.messages[-1]["content"] if ctx.state.messages else ""
    mod_id = get_model("ts_agent")
    model = get_p_model("ts_agent", client=ctx.deps.client)
    
    try:
        from pydantic_ai import AgentRunResult as RunResult
        result: RunResult[SwarmPlan] = await ts_agent.run(
            user_msg, 
            deps=ctx.deps, 
            model=model,
            model_settings=get_model_settings("ts_agent"),
            usage_limits=UsageLimits(request_limit=5)
        )
        plan = result.output
        log_agent_trace("ts_agent", mod_id, result)
        
        # Capture usage and merge it
        usage = capture_usage(result, "ts_agent", mod_id)
        ctx.state.usage.merge(usage)
        
        # 1. DIRECT ANSWER BYPASS (Bypassing Swarm & Synthesizer)
        if plan.swarm_mode == 'direct_answer':
            direct_ans = plan.direct_answer or "Pas de réponse directe générée."
            logger.info(f"🧠 [TS_AGENT] Direct Answer generated: {direct_ans[:100]}...")
            
            # Build odis_synthesis
            new_odis_synthesis = []
            if ctx.state.execution_mode == 'specific_ask' and ctx.state.messages:
                last_user_msg = ctx.state.messages[-1]
                if last_user_msg.get("role") == "user":
                    new_odis_synthesis.append(last_user_msg)
            new_odis_synthesis.append({"role": "assistant", "content": direct_ans})
            
            # Apply back to the city result if possible
            if ctx.state.search_results and ctx.state.focus_city:
                city_res = ctx.state.search_results.get_by_code(ctx.state.focus_city.codgeo)
                if city_res:
                    if not city_res.odis_synthesis: city_res.odis_synthesis = []
                    city_res.odis_synthesis.extend(new_odis_synthesis)
            
            return End(direct_ans)
        
        # 2. POPULATE Swarm Tasks and Skill Cards
        ctx.state.active_skills = []
        ctx.state.expert_tasks = {}
        ctx.state.expert_skill_instructions = {}
        
        from services.knowledge_store import KnowledgeStore
        store = KnowledgeStore()
        
        experts_to_run = []
        for task in plan.tasks:
            experts_to_run.append(task.expert)
            ctx.state.expert_tasks[task.expert] = task.task_description
            ctx.state.active_skills.extend(task.skill_cards)
            
            # Load instructions for this expert's skill cards
            skill_insts = []
            for skill_id in task.skill_cards:
                card = store.get_skill_card(skill_id)
                if card and card.get("instructions"):
                    skill_insts.append(f"--- Skill Card: {skill_id} ---\n{card.get('instructions')}")
            
            if skill_insts:
                ctx.state.expert_skill_instructions[task.expert] = "\n\n".join(skill_insts)
            else:
                ctx.state.expert_skill_instructions[task.expert] = "Aucune consigne spécifique de Skill Card active."
        
        # Deduplicate active skills
        ctx.state.active_skills = list(set(ctx.state.active_skills))
        
        logger.info(f"🧠 [TS_AGENT] Swarm Plan: experts={experts_to_run}, skills={ctx.state.active_skills}")
        return ExpertList(experts=experts_to_run)
            
    except Exception as e:
        logger.error(f"❌ [TS_AGENT] Planning failed: {e}", exc_info=True)
        # Fallback list of experts
        default_experts = ["housing_expert", "mobility_expert", "social_integration_expert"]
        if ctx.state.search_criteria.nb_adultes > 0:
            default_experts.append("job_hunter")
        if ctx.state.search_criteria.nb_enfants > 0 or ctx.state.search_criteria.classe_enfants:
            default_experts.append("education_expert")
        if ctx.state.search_criteria.besoin_sante:
            default_experts.append("healthcare_expert")
            
        fallback_skills = {
            "housing_expert": ["housing_full_analysis"],
            "mobility_expert": ["mobility_full_analysis"],
            "healthcare_expert": ["healthcare_full_analysis"],
            "education_expert": ["education_full_analysis"],
            "social_integration_expert": ["social_full_analysis"],
            "job_hunter": ["job_full_analysis"]
        }
        
        ctx.state.expert_skill_instructions = {}
        from services.knowledge_store import KnowledgeStore
        store = KnowledgeStore()
        
        for exp in default_experts:
            ctx.state.expert_tasks[exp] = "Analyse de terrain standard."
            # Load default skill instructions
            skill_insts = []
            for skill_id in fallback_skills.get(exp, []):
                card = store.get_skill_card(skill_id)
                if card and card.get("instructions"):
                    skill_insts.append(f"--- Skill Card: {skill_id} ---\n{card.get('instructions')}")
            
            if skill_insts:
                ctx.state.expert_skill_instructions[exp] = "\n\n".join(skill_insts)
            else:
                ctx.state.expert_skill_instructions[exp] = "Aucune consigne spécifique de Skill Card active."
            
        return ExpertList(experts=default_experts)

async def extract_domains(ctx: StepContext[GraphState, ODISDeps, ExpertList]) -> list[str]:
    """Helper to unwrap the DTO into a list of strings for mapping."""
    return ctx.inputs.experts

@logfire.instrument("Expert Node: {ctx.inputs}")
async def expert_worker_step(ctx: StepContext[GraphState, ODISDeps, str]) -> AgentArtifact:
    """Parallel worker that delegates to the appropriate domain expert agent."""
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
        "job_hunter": job_hunter_agent,
        "housing_expert": housing_expert_agent,
        "mobility_expert": mobility_expert_agent,
        "healthcare_expert": healthcare_expert_agent,
        "education_expert": education_expert_agent,
        "social_integration_expert": social_integration_expert_agent
    }
    agent = agent_map.get(domain)
    
    if not agent:
        return AgentArtifact(domain=domain, result="Agent not found.", usage=UsageStats())

    try:
        # Use the specific task description generated by the ts_agent coordinator, fallback to raw user_msg
        expert_query = ctx.state.expert_tasks.get(domain) or user_msg
        logger.info(f"🚀 [{domain.upper()}] Running with prompt: {expert_query}")
        
        result = await agent.run(
            expert_query, 
            deps=ctx.deps, 
            model=model,
            model_settings=get_model_settings(domain),
            usage_limits=UsageLimits(request_limit=5)
        )
        log_agent_trace(domain, mod_id, result)
        logger.info(f"✅ [{domain.upper()}] Node finished for {focus}.")
        
        artifact_str = f"### Recherches effectuées\n{result.output.searched}\n\n### Resultats\n{result.output.result}"
        usage = capture_usage(result, domain, mod_id)
        return AgentArtifact(domain=domain, result=artifact_str, usage=usage)
    except Exception as e:
        logger.error(f"❌ [{domain.upper()}] Error: {e}")
        return AgentArtifact(domain=domain, result=f"Erreur d'analyse: {e}", usage=UsageStats())

@logfire.instrument("Node: synthesizer")
async def synthesizer_step(ctx: StepContext[GraphState, ODISDeps, list[AgentArtifact]]) -> End[str]:
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
                # Accumulate experts token usage
                if artifact.usage:
                    ctx.state.usage.merge(artifact.usage)
    
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
        ctx.state.usage.merge(usage)
        
    except Exception as e:
        logger.error(f"❌ [SYNTHESIZER-FAILURE] Agent run failed: {e}", exc_info=True)
        final_content = "⚠️ _Désolé, une erreur technique est survenue lors de la synthèse finale. Les experts ont cependant fini leur travail._"
    
    # BQ Logging
    try:
        user_input = ctx.state.messages[-1].get("content", "Analyse IA") if ctx.state.messages else "Analyse IA"
        from dataclasses import asdict
        state_dict = asdict(ctx.state)
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
    
    # MapReduce Pipeline (Start -> Triage -> Map -> Parallel Experts -> Join -> Synthesizer -> End)
    g.add(g.edge_from(g.start_node).to(triage_node))
    g.add(
        g.edge_from(triage_node).to(
            g.decision()
            .branch(g.match(TypeExpression[ExpertList]).to(extract_domains_node))
            .branch(g.match(TypeExpression[End]).to(g.end_node))
        )
    )
    g.add_mapping_edge(extract_domains_node, expert_worker_node)
    g.add(g.edge_from(expert_worker_node).to(collect_experts))
    g.add(g.edge_from(collect_experts).to(synthesizer_node))
    g.add(g.edge_from(synthesizer_node).to(g.end_node))
    
    return g.build()
