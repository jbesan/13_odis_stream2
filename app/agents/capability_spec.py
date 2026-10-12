"""Capability specification module.

Re-exports CapabilitySpec and related types from agents.capability_registry.
"""

from agents.capability_registry import (
    CANONICAL_TOOL_NAMES,
    CANONICAL_TOOLS,
    EXPERT_DEFAULT_CAPABILITY,
    LEGACY_SKILL_TO_CAPABILITY,
    VALID_EXPERTS,
    CapabilityNotFoundError,
    CapabilityRegistry,
    CapabilityRegistryError,
    CapabilitySpec,
    CapabilityValidationError,
    ExpertName,
    ToolNotFoundError,
    get_canonical_tool,
    get_default_capability_registry,
    is_canonical_tool,
    reset_default_capability_registry,
)

__all__ = [
    "CapabilitySpec",
    "CapabilityRegistry",
    "CapabilityRegistryError",
    "CapabilityNotFoundError",
    "CapabilityValidationError",
    "ToolNotFoundError",
    "ExpertName",
    "VALID_EXPERTS",
    "CANONICAL_TOOLS",
    "CANONICAL_TOOL_NAMES",
    "EXPERT_DEFAULT_CAPABILITY",
    "LEGACY_SKILL_TO_CAPABILITY",
    "get_canonical_tool",
    "is_canonical_tool",
    "get_default_capability_registry",
    "reset_default_capability_registry",
]
