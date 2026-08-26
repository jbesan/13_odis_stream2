"""Isolated Gemini native-search child used only after approved trusted failures."""

from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.output import NativeOutput

from agents.agent_config import create_agent
from agents.grounding import extract_web_grounding
from core.evidence import WebEvidenceBundle, WebSource


@dataclass(frozen=True)
class WebChildDeps:
    query: str
    reason: str


web_child_agent: Agent[WebChildDeps, WebEvidenceBundle] = create_agent(
    "social_integration_expert",
    name="social_integration_web_child",
    deps_type=WebChildDeps,
    capabilities=[WebSearch(max_uses=1)],
    # Native JSON output avoids mixing Gemini native search with a custom
    # `final_result` function tool, the combination that caused the legacy
    # provider/tool incompatibility.
    output_type=NativeOutput(WebEvidenceBundle),
)


def provider_sources(messages: list[object]) -> list[WebSource]:
    """Extract provider-grounded URLs; never trusts model prose."""

    grounding = extract_web_grounding(messages)
    queries = grounding.get("queries", [])
    query = queries[0] if queries else None
    return [
        WebSource(
            title=source.get("title"),
            url=source["url"],
            domain=source.get("domain"),
            query=query,
        )
        for source in grounding.get("sources", [])
        if isinstance(source.get("url"), str) and source["url"]
    ]
