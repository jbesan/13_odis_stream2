"""Graph construction and state for the Social-Agent-Core supervisor."""

from __future__ import annotations

from pydantic_graph.beta import GraphBuilder, Graph
from pydantic_graph.beta.join import reduce_list_append

from social_agent_core.graph.models import (
    GraphState, GraphDeps, ExpertList, DirectResponse, UnmatchedIntent
)
from social_agent_core.graph.nodes.orchestrator import (
    orchestrator_step, triage_step, extract_domains, direct_response_step
)
from social_agent_core.graph.nodes.expert_worker import expert_worker_step
from social_agent_core.graph.nodes.synthesizer import synthesizer_step
from social_agent_core.graph.nodes.pm_discovery import pm_discovery_step


def build_supervisor_graph() -> Graph[GraphState, GraphDeps, None, str]:
    """Build the pydantic-graph supervisor (V2)."""
    g = GraphBuilder(state_type=GraphState, deps_type=GraphDeps, output_type=str)
    
    # Register steps
    orchestrator_node = g.step(orchestrator_step)
    triage_node = g.step(triage_step)
    expert_worker_node = g.step(expert_worker_step)
    synthesizer_node = g.step(synthesizer_step)
    pm_discovery_node = g.step(pm_discovery_step)
    extract_domains_node = g.step(extract_domains)
    direct_response_node = g.step(direct_response_step)
    
    # Define Join node to collect artifacts from parallel workers
    collect_experts = g.join(reduce_list_append, initial_factory=list)
    
    # Root: Start -> Orchestrator Step -> Triage Step
    g.add(g.edge_from(g.start_node).to(orchestrator_node))
    g.add(g.edge_from(orchestrator_node).to(triage_node))
    
    # Triage decision logic
    decision = g.decision() \
        .branch(g.match(UnmatchedIntent).to(pm_discovery_node)) \
        .branch(g.match(DirectResponse).to(direct_response_node)) \
        .branch(g.match(ExpertList).to(extract_domains_node))
    
    g.add(g.edge_from(triage_node).to(decision))
    
    # Parallel Fan-out / Join path
    # 1. Map each expert domain to a parallel worker
    g.add_mapping_edge(extract_domains_node, expert_worker_node)
    # 2. Collect results into the join node
    g.add(g.edge_from(expert_worker_node).to(collect_experts))
    # 3. Pass the collected list to the synthesizer
    g.add(g.edge_from(collect_experts).to(synthesizer_node))
    
    # Finalize to End Node
    g.add(g.edge_from(pm_discovery_node).to(g.end_node))
    g.add(g.edge_from(direct_response_node).to(g.end_node))
    g.add(g.edge_from(synthesizer_node).to(g.end_node))
    
    return g.build()
