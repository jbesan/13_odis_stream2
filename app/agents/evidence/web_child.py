"""Isolated Gemini native-search child used only after approved trusted failures."""

import re
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.output import NativeOutput

from agents.agent_config import create_agent
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
    """Best-effort extraction of provider-returned URLs; never trusts model prose."""
    found: dict[str, WebSource] = {}
    for message in messages:
        for part in getattr(message, "parts", []):
            if getattr(part, "part_kind", "") != "builtin-tool-return":
                continue
            raw = repr((getattr(part, "content", None), getattr(part, "provider_details", None)))
            for url in re.findall(r"https?://[^\s'\"<>),]+", raw):
                found[url] = WebSource(url=url)
    return list(found.values())
