"""Graph-specific data models and DTOs.

This module contains models that are used for communication between nodes
or as structured outputs from agents, and the shared mutable GraphState.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from dataclasses import dataclass, field
from pydantic import BaseModel, Field

from social_agent_core.models.artifacts import AgentArtifact
from social_agent_core.models.state import BeneficiaryState, BeneficiaryStateUpdate
from social_agent_core.knowledge.models import SkillCardConfig

if TYPE_CHECKING:
    from social_agent_core.knowledge.store import KnowledgeStore
    from social_agent_core.services.knowledge_service import BigQueryKnowledgeService
    from social_agent_core.services.employment_service import FranceTravailClient, EmploisInclusionClient
    from social_agent_core.services.map_service import GoogleMapsClient
    from social_agent_core.services.search_service import BraveSearchClient
    from social_agent_core.services.scraper_service import ScraperService


class RoutingDecision(BaseModel):
    """Structured output from the Orchestrator LLM call.

    Attributes:
        experts: List of expert domains to route to.
        is_objective_actionable: True if we should trigger experts now, False if we need user confirmation/clarification first.
        direct_response: The text to show to the user. Use this for rephrasing the J2BD and asking for confirmation.
        is_unmatched: If True, the intent matched no known domain.
    """

    experts: list[str] = Field(default_factory=list)
    is_objective_actionable: bool = False
    direct_response: str | None = None
    state_updates: BeneficiaryStateUpdate | None = Field(default=None)
    problem_to_solve: str | None = None
    is_unmatched: bool = False


class UnmatchedIntent(BaseModel):
    """Signals that no expert domain matched the user intent."""
    latest_message: str


class DirectResponse(BaseModel):
    """Signals a direct response from the orchestrator (e.g. greeting)."""
    text: str


class ExpertList(BaseModel):
    """Signals a list of experts to be executed in parallel."""
    experts: list[str]


@dataclass
class GraphState:
    """Mutable graph state — replaces ODIS's ODISGraphState.

    This is the shared blackboard passed to every node via
    ``GraphRunContext.state``. It wraps the BeneficiaryState
    and collects expert artifacts during a turn.

    Attributes:
        beneficiary: The beneficiary's accompaniment state.
        artifacts: Expert outputs collected during fan-out.
        response: Final synthesized response for the user.
    """

    beneficiary: BeneficiaryState = field(default_factory=BeneficiaryState)
    artifacts: list[AgentArtifact] = field(default_factory=list)
    active_skill_cards: list[SkillCardConfig] = field(default_factory=list)
    resolved_domains: list[str] = field(default_factory=list)
    response: str = ""
    current_turn_trace: list[str] = field(default_factory=list)


@dataclass
class GraphDeps:
    """Read-only dependencies for the graph nodes."""
    store: KnowledgeStore
    bq: BigQueryKnowledgeService | None = None
    ft: FranceTravailClient | None = None
    inclusion: EmploisInclusionClient | None = None
    maps: GoogleMapsClient | None = None
    search: BraveSearchClient | None = None
    scraper: ScraperService | None = None
