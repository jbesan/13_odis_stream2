"""Context registry module for ODIS AI Agent Swarm.

Re-exports ContextRegistry, ContextBlock, and related assembly utilities
from agents.context_blocks.
"""

from agents.context_blocks import (
    BUILTIN_BLOCKS,
    CORE_BLOCK_ORDER,
    ContextBlock,
    ContextBlockFamily,
    ContextBlockNotFoundError,
    ContextBlockValidationError,
    ContextRegistry,
    ContextRegistryError,
    assemble_context_blocks,
    get_default_context_registry,
    render_context_blocks,
    reset_default_context_registry,
)

__all__ = [
    "ContextBlock",
    "ContextBlockFamily",
    "ContextRegistry",
    "ContextRegistryError",
    "ContextBlockNotFoundError",
    "ContextBlockValidationError",
    "BUILTIN_BLOCKS",
    "CORE_BLOCK_ORDER",
    "get_default_context_registry",
    "reset_default_context_registry",
    "assemble_context_blocks",
    "render_context_blocks",
]
