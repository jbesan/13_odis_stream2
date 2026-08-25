import logging
import asyncio
import os
import logfire
from typing import Any

from pydantic_graph import End
from pydantic_graph.graph_builder import GraphBuilder, Graph
from pydantic_graph.step import StepContext
from pydantic_graph.util import TypeExpression
from pydantic_graph.join import reduce_list_append

from pydantic_ai.usage import UsageLimits, RunUsage

from agents.state import (
    ODISDeps,
    GraphState,
    UsageStats,
    compute_criteria_hash,
    ExpertList,
    AgentArtifact,
)
from agents.ts_agent import ts_agent, SwarmPlan
from agents.utils import sanitize_llm_markdown
from agents.job_hunter import job_hunter_agent
from agents.housing_expert import housing_expert_agent
from agents.mobility_expert import mobility_expert_agent
from agents.healthcare_expert import healthcare_expert_agent
from agents.education_expert import education_expert_agent
from agents.social_integration_expert import social_integration_expert_agent
from agents.synthesizer import synthesizer_agent
from agents.comparator import compute_city_comparison
from agents.ccas_worker import locate_ccas_deterministic
from agents.source_registry import source_keys, source_references_for_result
from utils.logger import log_agent_trace
import services.bq_logger as bq_logger
from agents.agent_config import get_model, get_p_model, get_model_settings

logger = logging.getLogger("odis_graph")


def adaptive_expert_enabled(domain: str) -> bool:
    """Read the dormant pilot flag for compatibility with its offline tests.

    The production graph intentionally no longer consults this value.  Keeping
    the parser avoids breaking the pilot's isolated tests while guaranteeing
    that the legacy expert is the only active social-integration path.
    """
    return domain in {
        item.strip()
        for item in os.getenv("ODIS_ADAPTIVE_EXPERTS", "").split(",")
        if item.strip()
    }


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
        u = getattr(result, "usage", None)
        if isinstance(u, RunUsage):
            pass
        elif callable(u):
            u = u()
        if u is None:
            return UsageStats()

        raw_in = getattr(u, "input_tokens", 0)
        raw_out = getattr(u, "output_tokens", 0)
        raw_tot = getattr(u, "total_tokens", 0)

        input_tokens = int(raw_in) if isinstance(raw_in, (int, float)) else 0
        output_tokens = int(raw_out) if isinstance(raw_out, (int, float)) else 0
        total_tokens = (
            int(raw_tot)
            if isinstance(raw_tot, (int, float))
            else (input_tokens + output_tokens)
        )

        m_lower = model_id.lower()
        if "3.5-flash-lite" in m_lower:
            rate_in, rate_out = (0.30, 2.50)
        elif "3.1-flash-lite" in m_lower:
            rate_in, rate_out = (0.25, 1.50)
        else:
            rate_in, rate_out = (0.10, 0.40)
        cost = (input_tokens * rate_in / 1_000_000) + (
            output_tokens * rate_out / 1_000_000
        )

        req_count = (
            int(getattr(u, "requests", 1))
            if isinstance(getattr(u, "requests", None), (int, float))
            else 1
        )
        tool_calls = (
            int(getattr(u, "tool_calls", 0))
            if isinstance(getattr(u, "tool_calls", None), (int, float))
            else 0
        )
        cache_read = (
            int(getattr(u, "cache_read_tokens", 0))
            if isinstance(getattr(u, "cache_read_tokens", None), (int, float))
            else 0
        )
        cache_write = (
            int(getattr(u, "cache_write_tokens", 0))
            if isinstance(getattr(u, "cache_write_tokens", None), (int, float))
            else 0
        )
        cache_hit = (
            float(getattr(u, "cache_hit_ratio", 0.0))
            if isinstance(getattr(u, "cache_hit_ratio", None), (int, float))
            else 0.0
        )

        logger.info(
            "📊 [USAGE] %s: %s t ($%.4f) over %s requests; "
            "tool_calls=%s cache_read=%s cache_write=%s cache_hit_ratio=%.3f",
            node_name,
            total_tokens,
            cost,
            req_count,
            tool_calls,
            cache_read,
            cache_write,
            cache_hit,
        )

        breakdown_entry = {
            "model": model_id,
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
            "cost": float(cost),
            "requests": req_count,
            "tool_calls": tool_calls,
            "cache_read_tokens": cache_read,
            "cache_write_tokens": cache_write,
            "cache_hit_ratio": cache_hit,
        }

        return UsageStats(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=cost,
            requests=req_count,
            tool_calls=tool_calls,
            cache_read_tokens=cache_read,
            cache_write_tokens=cache_write,
            cache_hit_ratio=cache_hit,
            breakdown={node_name: breakdown_entry},
        )
    except Exception as e:
        logger.warning(f"⚠️ [USAGE] capture_usage failed for {node_name}: {e}")
        return UsageStats()

# --- Graph Nodes ---
@logfire.instrument("Node: triage")
async def triage_step(
    ctx: StepContext[GraphState, ODISDeps, None],
) -> ExpertList | End[str]:
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
            usage_limits=UsageLimits(request_limit=5),
        )
        plan = result.output
        log_agent_trace("ts_agent", mod_id, result)

        # Capture usage and merge it
        usage = capture_usage(result, "ts_agent", mod_id)
        ctx.state.usage.merge(usage)

        # The validated coordinator plan is the only authority for the route.
        # The launcher has no semantic knowledge of whether a follow-up needs a
        # targeted answer or a fresh full analysis.
        ctx.state.execution_mode = plan.swarm_mode

        # 1. DIRECT ANSWER BYPASS (Bypassing Swarm & Synthesizer)
        if plan.swarm_mode == "direct_answer":
            # SwarmPlan's execution-contract validator guarantees this value.
            assert plan.direct_answer is not None
            direct_ans = plan.direct_answer
            logger.info(f"🧠 [TS_AGENT] Direct Answer generated: {direct_ans[:100]}...")

            # Build odis_synthesis
            new_odis_synthesis = []
            if ctx.state.messages:
                last_user_msg = ctx.state.messages[-1]
                if last_user_msg.get("role") == "user":
                    new_odis_synthesis.append(last_user_msg)
            new_odis_synthesis.append({"role": "assistant", "content": direct_ans})

            # Apply back to the city result if possible
            if ctx.state.search_results and ctx.state.focus_city:
                city_res = ctx.state.search_results.get_by_code(
                    ctx.state.focus_city.codgeo
                )
                if city_res:
                    if not city_res.odis_synthesis:
                        city_res.odis_synthesis = []
                    city_res.odis_synthesis.extend(new_odis_synthesis)

            # BQ Logging for direct_answer
            try:
                user_input = (
                    ctx.state.messages[-1].get("content", "Question directe")
                    if ctx.state.messages
                    else "Question directe"
                )
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
                    ctx.state.username,
                )
            except Exception as e:
                logger.warning(f"⚠️ [BQ-LOG] Direct answer logging failed: {e}")

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
                    skill_insts.append(
                        f"--- Skill Card: {skill_id} ---\n{card.get('instructions')}"
                    )

            if skill_insts:
                ctx.state.expert_skill_instructions[task.expert] = "\n\n".join(
                    skill_insts
                )
            else:
                ctx.state.expert_skill_instructions[task.expert] = (
                    "Aucune consigne spécifique de Skill Card active."
                )

        # Deduplicate active skills
        ctx.state.active_skills = list(set(ctx.state.active_skills))

        # 3. APPEND LOCAL DETERMINISTIC WORKERS IN FULL ANALYSIS MODE
        if plan.swarm_mode == "full_analysis":
            for local_worker in ("city_comparator", "ccas_locator"):
                if local_worker not in experts_to_run:
                    experts_to_run.append(local_worker)
                    ctx.state.expert_tasks[local_worker] = "Analyse déterministe locale"
                    ctx.state.expert_skill_instructions[local_worker] = "Traitement déterministe Python"

        logger.info(
            f"🧠 [TS_AGENT] Swarm Plan: experts={experts_to_run}, skills={ctx.state.active_skills}"
        )
        return ExpertList(experts=experts_to_run)

    except Exception as e:
        logger.error(f"❌ [TS_AGENT] Planning failed: {e}", exc_info=True)
        failure_message = (
            "⚠️ L'analyse IA n'a pas pu être planifiée. "
            "Les résultats de classement restent disponibles ; réessayez l'analyse."
        )
        if ctx.state.search_results and ctx.state.focus_city:
            city_res = ctx.state.search_results.get_by_code(ctx.state.focus_city.codgeo)
            if city_res:
                city_res.odis_synthesis.append(
                    {"role": "assistant", "content": failure_message}
                )
        return End(failure_message)


async def extract_domains(
    ctx: StepContext[GraphState, ODISDeps, ExpertList],
) -> list[str]:
    """Helper to unwrap the DTO into a list of strings for mapping."""
    return ctx.inputs.experts


@logfire.instrument("Expert Node: {ctx.inputs}")
async def expert_worker_step(
    ctx: StepContext[GraphState, ODISDeps, str],
) -> AgentArtifact:
    """Parallel worker that delegates to domain expert agents or local deterministic workers."""
    domain = ctx.inputs
    user_msg = ctx.state.messages[-1]["content"] if ctx.state.messages else ""
    focus = (
        ctx.state.focus_city.name.lower().strip() if ctx.state.focus_city else "unknown"
    )
    h = compute_criteria_hash(ctx.state.search_criteria)
    ctx.state.criteria_hash = h
    trace_attrs = {
        "interaction_id": ctx.state.interaction_id,
        "run_id": ctx.state.run_id,
        "run_attempt": ctx.state.run_attempt,
        "organization_id": ctx.state.organization_id,
        "domain": domain,
        "criteria_hash": h,
        "focus_city_code": (
            ctx.state.focus_city.codgeo if ctx.state.focus_city else "unknown"
        ),
    }
    logfire.info("Expert run started", **trace_attrs)

    # 1. RESOLVE FULL COMMUNE RESULT FROM SEARCH RESULTS
    city_res = None
    if ctx.state.search_results and ctx.state.focus_city and ctx.state.focus_city.codgeo:
        city_res = ctx.state.search_results.get_by_code(ctx.state.focus_city.codgeo)
    target_city = city_res or ctx.state.focus_city

    # 2. LOCAL DETERMINISTIC WORKERS (Phase 1: 0 tokens, <15ms)
    if domain == "city_comparator":
        logger.info(f"⚡ [CITY_COMPARATOR] Running local deterministic comparison for {focus}.")
        ref_geo = ctx.state.search_results.current_geo if ctx.state.search_results else None
        press_geo = ctx.state.search_results.commune_pressentie if ctx.state.search_results else None
        artifact = compute_city_comparison(target_city, ref_geo, press_geo)
        logfire.info(
            "Expert run finished",
            **trace_attrs,
            execution_kind="deterministic",
            source_keys=[],
        )
        return artifact

    if domain == "ccas_locator":
        logger.info(f"⚡ [CCAS_LOCATOR] Running local deterministic CCAS search for {focus}.")
        artifact = locate_ccas_deterministic(target_city)
        logfire.info(
            "Expert run finished",
            **trace_attrs,
            execution_kind="deterministic",
            source_keys=[],
        )
        return artifact

    # 3. CACHE BYPASS
    if (
        ctx.state.execution_mode == "full_analysis"
        and ctx.state.search_results
        and ctx.state.search_results.search_hash == h
    ):
        city_res = ctx.state.search_results.get_by_code(
            ctx.state.focus_city.codgeo if ctx.state.focus_city else ""
        )
        if city_res and city_res.expert_analysis.get(domain):
            logger.info(
                f"⏭️ [{domain.upper()}] Artifact already exists for {focus}. Skipping LLM call."
            )
            typed = None
            if city_res.expert_artifacts.get(domain):
                from core.evidence import DomainArtifact as EvidenceDomainArtifact

                typed = EvidenceDomainArtifact.model_validate(
                    city_res.expert_artifacts[domain]
                )
            sources = (
                city_res.expert_sources.get(domain, [])
                if hasattr(city_res, "expert_sources")
                else source_references_for_result(domain, None)
            )
            logfire.info(
                "Expert run finished",
                **trace_attrs,
                execution_kind="cache_bypass",
                source_keys=source_keys(sources),
            )
            return AgentArtifact(
                domain=domain,
                result=city_res.expert_analysis.get(domain),
                usage=UsageStats(),
                evidence_artifact=typed,
                sources=sources,
            )

    # 3. RUN EXPERT LLM
    logger.info(f"🚀 [{domain.upper()}] Node started for {focus}.")
    mod_id = get_model(domain)
    model = get_p_model(domain, client=ctx.deps.client)

    agent_map = {
        "job_hunter": job_hunter_agent,
        "housing_expert": housing_expert_agent,
        "mobility_expert": mobility_expert_agent,
        "healthcare_expert": healthcare_expert_agent,
        "education_expert": education_expert_agent,
        "social_integration_expert": social_integration_expert_agent,
    }
    agent = agent_map.get(domain)

    if not agent:
        return AgentArtifact(
            domain=domain, result="Agent not found.", usage=UsageStats()
        )

    try:
        # Use the specific task description generated by the ts_agent coordinator, fallback to raw user_msg
        expert_query = ctx.state.expert_tasks.get(domain) or user_msg
        logger.info("🚀 [%s] Running legacy expert task", domain.upper())

        with logfire.span("Expert LLM run", **trace_attrs):
            result = await agent.run(
                expert_query,
                deps=ctx.deps,
                model=model,
                model_settings=get_model_settings(domain),
                usage_limits=UsageLimits(request_limit=5),
            )
        log_agent_trace(domain, mod_id, result)
        logger.info(f"✅ [{domain.upper()}] Node finished for {focus}.")

        artifact_str = result.output.result.strip()
        usage = capture_usage(result, domain, mod_id)
        sources = source_references_for_result(domain, result)
        logfire.info(
            "Expert run finished",
            **trace_attrs,
            model_id=mod_id,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            requests=usage.requests,
            tool_calls=usage.tool_calls,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            cache_hit_ratio=usage.cache_hit_ratio,
            source_keys=source_keys(sources),
        )
        return AgentArtifact(
            domain=domain,
            result=artifact_str,
            usage=usage,
            sources=sources,
        )
    except Exception as e:
        logger.error(f"❌ [{domain.upper()}] Error: {e}")
        logfire.info(
            "Expert run failed",
            **trace_attrs,
            error_type=type(e).__name__,
        )
        return AgentArtifact(
            domain=domain,
            result=f"Erreur d'analyse: {e}",
            usage=UsageStats(),
            sources=source_references_for_result(domain, None),
        )


@logfire.instrument("Node: synthesizer")
async def synthesizer_step(
    ctx: StepContext[GraphState, ODISDeps, list[AgentArtifact]],
) -> End[str]:
    """Merges artifacts into state and produces the final synthesis."""
    city_name = ctx.state.focus_city.name if ctx.state.focus_city else "Unknown"
    logger.info(f"🚀 [SYNTHESIZER] starting for {city_name}...")

    input_data = ctx.inputs

    # 1. MERGE ARTIFACTS INTO STATE (if any)
    if isinstance(input_data, list) and input_data:
        for artifact in input_data:
            if getattr(artifact, "usage", None):
                ctx.state.usage.merge(artifact.usage)

        if ctx.state.search_results and ctx.state.focus_city:
            city_res = ctx.state.search_results.get_by_code(
                ctx.state.focus_city.codgeo
            )
            if city_res:
                for artifact in input_data:
                    city_res.expert_analysis[artifact.domain] = artifact.result
                    if artifact.evidence_artifact is not None:
                        city_res.expert_artifacts[artifact.domain] = (
                            artifact.evidence_artifact.model_dump(mode="json")
                        )
                    if artifact.sources:
                        city_res.expert_sources[artifact.domain] = artifact.sources

    # 2. RUN SYNTHESIZER LLM
    input_msg = f"Synthèse demandée pour {city_name}."
    mod_id = get_model("synthesizer")
    model = get_p_model("synthesizer", client=ctx.deps.client)

    synth_output = None
    try:
        result = await synthesizer_agent.run(
            input_msg,
            deps=ctx.deps,
            model=model,
            model_settings=get_model_settings("synthesizer"),
            usage_limits=UsageLimits(request_limit=10),
        )
        log_agent_trace("synthesizer", mod_id, result)
        synth_output = result.output

        # Merge Usage
        usage = capture_usage(result, "synthesizer", mod_id)
        ctx.state.usage.merge(usage)

    except Exception as e:
        logger.error(f"❌ [SYNTHESIZER-FAILURE] Agent run failed: {e}", exc_info=True)
        synth_output = "⚠️ _Désolé, une erreur technique est survenue lors de la synthèse finale. Les experts ont cependant fini leur travail._"

    # BQ Logging
    try:
        user_input = (
            ctx.state.messages[-1].get("content", "Analyse IA")
            if ctx.state.messages
            else "Analyse IA"
        )
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
            ctx.state.username,
        )
    except Exception as e:
        logger.warning(f"⚠️ [BQ-LOG] Synthesis logging failed: {e}")

    # 3. BUILD COMPLETE COMPOSITE REPORT (Decoupled Synthesis + As-is Domain Artifacts)
    city_res = None
    if ctx.state.search_results and ctx.state.focus_city:
        city_res = ctx.state.search_results.get_by_code(ctx.state.focus_city.codgeo)

    if ctx.state.execution_mode == "full_analysis":
        # Extract structured synthesizer fields
        if hasattr(synth_output, "avis_global"):
            avis_global = synth_output.avis_global.strip()
            analyse_comp = (
                getattr(synth_output, "analyse_comparative", "") or ""
            ).strip()
            elements_non_verifies = (
                synth_output.elements_non_verifies.strip()
                if synth_output.elements_non_verifies
                else ""
            )
            et_ensuite = synth_output.et_ensuite.strip()
        else:
            avis_global = sanitize_llm_markdown(str(synth_output))
            analyse_comp = ""
            elements_non_verifies = ""
            et_ensuite = ""

        report_sections = []

        # 1. Executive overview (Top)
        report_sections.append(f"## 🧭 Avis Global d'Orientation pour {city_name}\n\n{avis_global}")

        # 2. Digested territorial comparison
        if analyse_comp:
            report_sections.append(f"## ⚖️ Analyse Comparative Territoriale\n\n{analyse_comp}")

        # 3. Domain Expert Artifacts (displayed as-is without LLM rephrasing)
        domain_display_order = [
            ("housing_expert", "🏠 Logement & Hébergement"),
            ("mobility_expert", "🚆 Mobilité & Transports"),
            ("healthcare_expert", "🏥 Santé & Accompagnement Médical"),
            ("education_expert", "🎓 Éducation & Petite Enfance"),
            ("social_integration_expert", "🤝 Insertion Sociale & Solidarité"),
            ("job_hunter", "💼 Emploi & Insertion Professionnelle"),
        ]
        expert_sections = []
        if city_res:
            for domain_key, domain_label in domain_display_order:
                content = city_res.expert_analysis.get(domain_key)
                if content and content.strip():
                    expert_sections.append(f"### {domain_label}\n\n{content.strip()}")

        if expert_sections:
            report_sections.append("## 🧭 Fiches Détaillées des Experts\n\n" + "\n\n---\n\n".join(expert_sections))

        # 4. Unverified elements / gaps as a proper section (Requirement 4)
        if elements_non_verifies:
            report_sections.append(f"## ⚠️ Éléments Non Vérifiés & Vigilances\n\n{elements_non_verifies}")

        # 5. Call to Action: CCAS Contact at the end (Requirement 1)
        if city_res and "ccas_locator" in city_res.expert_analysis:
            ccas_content = city_res.expert_analysis["ccas_locator"].strip()
            if ccas_content:
                report_sections.append(ccas_content)

        # 6. Call to Action: Et ensuite ? at the end (Requirement 1)
        if et_ensuite:
            report_sections.append(f"## ❓ Et ensuite ? (Pistes d'action)\n\n{et_ensuite}")

        full_report = "\n\n---\n\n".join(report_sections)
    else:
        full_report = sanitize_llm_markdown(str(synth_output))

    # Build odis_synthesis
    new_odis_synthesis = []
    if ctx.state.execution_mode == "specific_ask" and ctx.state.messages:
        last_user_msg = ctx.state.messages[-1]
        if last_user_msg.get("role") == "user":
            new_odis_synthesis.append(last_user_msg)

    new_odis_synthesis.append({"role": "assistant", "content": full_report})

    # Apply back to the city result if possible
    if city_res:
        if not city_res.odis_synthesis:
            city_res.odis_synthesis = []
        city_res.odis_synthesis.extend(new_odis_synthesis)

    return End(full_report)


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
