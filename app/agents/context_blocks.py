"""Context blocks and registry for ODIS AI Agent Swarm.

Defines modular, typed context blocks (core, domain, and conditional)
and a fail-closed registry to assemble LLM context projections declared
by capability specifications.
"""

import logging
from typing import Any, Callable, Dict, Final, List, Literal, Optional, Sequence
from pydantic import BaseModel, ConfigDict, Field, field_validator

from agents.context_renderer import (
    dump_context,
    format_commune_identity,
    format_criteria,
    format_model,
    resolve_focus_city,
)
from core.models import CommuneResult

logger = logging.getLogger(__name__)

# ==============================================================================
# 1. Exceptions
# ==============================================================================


class ContextRegistryError(Exception):
    """Base exception for all context block and registry errors."""


class ContextBlockNotFoundError(ContextRegistryError, KeyError):
    """Raised when a requested context block ID is not registered (fail-closed)."""


class ContextBlockValidationError(ContextRegistryError, ValueError):
    """Raised when a context block specification is invalid."""


# ==============================================================================
# 2. ContextBlock Model
# ==============================================================================

ContextBlockFamily = Literal["core", "domain", "conditional"]


class ContextBlock(BaseModel):
    """Specification and renderer for a discrete context block."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    id: str = Field(
        ...,
        description="Unique canonical identifier for the context block (e.g., 'commune.housing').",
    )
    title: str = Field(
        ...,
        description="Section title or root key used when rendering this block into a context dictionary.",
    )
    description: str = Field(
        ...,
        description="Description of the context block purpose and contents.",
    )
    family: ContextBlockFamily = Field(
        default="domain",
        description="Category family of the context block ('core', 'domain', or 'conditional').",
    )
    aliases: List[str] = Field(
        default_factory=list,
        description="Alternative identifiers that map to this block.",
    )
    raw_renderer: Callable[[Any, Optional[CommuneResult]], Any] = Field(
        ...,
        description="Callable returning the raw payload (e.g. str narrative, dict) for prefix caching or isolated consumption.",
    )
    dict_renderer: Callable[[Any, Optional[CommuneResult]], Dict[str, Any]] = Field(
        ...,
        description="Callable returning dictionary chunk with section key for context assembly.",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        cleaned = v.strip() if isinstance(v, str) else ""
        if not cleaned:
            raise ValueError("Context block ID cannot be empty.")
        return cleaned

    @field_validator("aliases")
    @classmethod
    def _validate_aliases(cls, v: List[str]) -> List[str]:
        cleaned_list: List[str] = []
        seen: set[str] = set()
        for a in v:
            cleaned = a.strip() if isinstance(a, str) else ""
            if not cleaned:
                raise ValueError("Context block alias cannot be empty.")
            if cleaned not in seen:
                seen.add(cleaned)
                cleaned_list.append(cleaned)
        return cleaned_list

    def render_raw(
        self, state: Any, focus_city: Optional[CommuneResult] = None
    ) -> Any:
        """Renders raw block payload.

        Args:
            state: GraphState or state-like container.
            focus_city: Pre-resolved CommuneResult, or None.

        Returns:
            Raw payload (string, dict, etc.).
        """
        return self.raw_renderer(state, focus_city)

    def render(
        self, state: Any, focus_city: Optional[CommuneResult] = None
    ) -> Dict[str, Any]:
        """Renders dictionary chunk with section title/key for context assembly.

        Args:
            state: GraphState or state-like container.
            focus_city: Pre-resolved CommuneResult, or None.

        Returns:
            Dictionary ready to be merged into LLM context.
        """
        return self.dict_renderer(state, focus_city)


# ==============================================================================
# 3. Built-in Block Renderers
# ==============================================================================


def _render_raw_briefing(state: Any, _city: Optional[CommuneResult]) -> str:
    if state and getattr(state, "odis_brief", None):
        brief = state.odis_brief.strip()
        if brief:
            return brief
    return "Aucun briefing particulier renseigné."


def _render_dict_briefing(
    state: Any, _city: Optional[CommuneResult]
) -> Dict[str, Any]:
    if state and getattr(state, "odis_brief", None):
        brief = state.odis_brief.strip()
        if brief:
            return {"Résumé du dossier (Briefing)": brief}
    return {}


def _render_raw_criteria(
    state: Any, _city: Optional[CommuneResult]
) -> Dict[str, Any]:
    if state and getattr(state, "search_criteria", None):
        return format_criteria(state.search_criteria)
    return {}


def _render_dict_criteria(
    state: Any, _city: Optional[CommuneResult]
) -> Dict[str, Any]:
    raw = _render_raw_criteria(state, _city)
    return {"Critères de recherche": raw} if raw else {}


def _render_raw_commune_identity(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    return format_commune_identity(resolved) if resolved else {}


def _render_dict_commune_identity(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    raw = _render_raw_commune_identity(state, city)
    return {"Commune analysée (Identité)": raw} if raw else {}


def _render_raw_housing(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "housing", None):
        return {}
    return format_model(resolved.housing)


def _render_dict_housing(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "housing", None):
        return {}
    return {"Données logement": format_model(resolved.housing)}


def _render_raw_mobility(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "mobility", None):
        return {}
    return format_model(resolved.mobility)


def _render_dict_mobility(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "mobility", None):
        return {}
    return {"Données mobilité": format_model(resolved.mobility)}


def _render_raw_healthcare(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "health", None):
        return {}
    return format_model(resolved.health)


def _render_dict_healthcare(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "health", None):
        return {}
    return {"Données santé": format_model(resolved.health)}


def _render_raw_education(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "education", None):
        return {}
    return format_model(resolved.education)


def _render_dict_education(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "education", None):
        return {}
    return {"Données éducation": format_model(resolved.education)}


def _render_raw_social(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "inclusion", None):
        return {}
    return format_model(resolved.inclusion)


def _render_dict_social(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "inclusion", None):
        return {}
    return {"Données inclusion": format_model(resolved.inclusion)}


def _render_raw_job(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "employment", None):
        return {}
    return format_model(resolved.employment)


def _render_dict_job(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if not resolved or not getattr(resolved, "employment", None):
        return {}
    return {"Données emploi et formation": format_model(resolved.employment)}


def _render_raw_territory(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    resolved = city or resolve_focus_city(state)
    if (
        resolved
        and getattr(resolved, "territoire", None)
        and (
            resolved.territoire.ter_insecurite is not None
            or resolved.territoire.maire_extreme_droite
        )
    ):
        return format_model(resolved.territoire)
    return {}


def _render_dict_territory(
    state: Any, city: Optional[CommuneResult]
) -> Dict[str, Any]:
    raw = _render_raw_territory(state, city)
    return {"Données territoire (Contexte local)": raw} if raw else {}


# ==============================================================================
# 4. Standard Context Blocks Catalog
# ==============================================================================

CORE_BLOCK_ORDER: Final[tuple[str, ...]] = (
    "core.briefing",
    "core.criteria",
    "core.commune_identity",
)

BUILTIN_BLOCKS: Final[List[ContextBlock]] = [
    # --- Core Blocks ---
    ContextBlock(
        id="core.briefing",
        title="Résumé du dossier (Briefing)",
        description="Root-level user dossier briefing narrative.",
        family="core",
        aliases=[],
        raw_renderer=_render_raw_briefing,
        dict_renderer=_render_dict_briefing,
    ),
    ContextBlock(
        id="core.criteria",
        title="Critères de recherche",
        description="Search criteria with human-readable labels excluding internal scoring flags.",
        family="core",
        aliases=[],
        raw_renderer=_render_raw_criteria,
        dict_renderer=_render_dict_criteria,
    ),
    ContextBlock(
        id="core.commune_identity",
        title="Commune analysée (Identité)",
        description="Base identification and scores for the target commune.",
        family="core",
        aliases=[],
        raw_renderer=_render_raw_commune_identity,
        dict_renderer=_render_dict_commune_identity,
    ),
    # --- Domain Blocks ---
    ContextBlock(
        id="commune.housing",
        title="Données logement",
        description="Housing metrics (rents, social housing delay, prices) for the target commune.",
        family="domain",
        aliases=["housing"],
        raw_renderer=_render_raw_housing,
        dict_renderer=_render_dict_housing,
    ),
    ContextBlock(
        id="commune.mobility",
        title="Données mobilité",
        description="Mobility metrics (train station, public transit, TGV access) for the target commune.",
        family="domain",
        aliases=["mobility"],
        raw_renderer=_render_raw_mobility,
        dict_renderer=_render_dict_mobility,
    ),
    ContextBlock(
        id="commune.healthcare",
        title="Données santé",
        description="Healthcare metrics (APL doctor access, maternity, emergencies) for the target commune.",
        family="domain",
        aliases=["commune.health", "healthcare", "health"],
        raw_renderer=_render_raw_healthcare,
        dict_renderer=_render_dict_healthcare,
    ),
    ContextBlock(
        id="commune.education",
        title="Données éducation",
        description="Education metrics (school counts, school names by grade) for the target commune.",
        family="domain",
        aliases=["education"],
        raw_renderer=_render_raw_education,
        dict_renderer=_render_dict_education,
    ),
    ContextBlock(
        id="commune.social",
        title="Données inclusion",
        description="Social inclusion metrics (refugee associations, inclusion services) for the target commune.",
        family="domain",
        aliases=["commune.inclusion", "social", "inclusion"],
        raw_renderer=_render_raw_social,
        dict_renderer=_render_dict_social,
    ),
    ContextBlock(
        id="commune.job",
        title="Données emploi et formation",
        description="Employment metrics and matching job offers for the target commune.",
        family="domain",
        aliases=["commune.employment", "job", "employment"],
        raw_renderer=_render_raw_job,
        dict_renderer=_render_dict_job,
    ),
    # --- Conditional Block ---
    ContextBlock(
        id="commune.territory",
        title="Données territoire (Contexte local)",
        description="Territory context alerts (security rate, extreme-right leadership) active for social integration.",
        family="conditional",
        aliases=["territory"],
        raw_renderer=_render_raw_territory,
        dict_renderer=_render_dict_territory,
    ),
]


# ==============================================================================
# 5. Context Registry
# ==============================================================================


class ContextRegistry:
    """Registry managing and resolving context blocks for agent runs."""

    def __init__(self) -> None:
        self._blocks: Dict[str, ContextBlock] = {}
        self._alias_map: Dict[str, str] = {}
        for block in BUILTIN_BLOCKS:
            self.register(block)

    def register(self, block: ContextBlock, overwrite: bool = False) -> None:
        """Registers a ContextBlock and its aliases.

        Args:
            block: The ContextBlock instance to register.
            overwrite: Whether to overwrite existing block if already registered.

        Raises:
            ContextBlockValidationError: If block ID or alias collides and overwrite is False.
        """
        if not overwrite and block.id in self._blocks:
            raise ContextBlockValidationError(
                f"Context block '{block.id}' is already registered."
            )
        if (
            not overwrite
            and block.id in self._alias_map
            and self._alias_map[block.id] != block.id
        ):
            raise ContextBlockValidationError(
                f"Context block ID '{block.id}' conflicts with existing alias mapping to '{self._alias_map[block.id]}'."
            )

        for alias in block.aliases:
            if alias == block.id:
                continue
            if not overwrite and alias in self._blocks and alias != block.id:
                raise ContextBlockValidationError(
                    f"Alias '{alias}' for block '{block.id}' conflicts with existing canonical block '{alias}'."
                )
            if (
                not overwrite
                and alias in self._alias_map
                and self._alias_map[alias] != block.id
            ):
                raise ContextBlockValidationError(
                    f"Alias '{alias}' for block '{block.id}' conflicts with existing mapping to '{self._alias_map[alias]}'."
                )

        if overwrite:
            old_aliases = [k for k, v in self._alias_map.items() if v == block.id]
            for old_a in old_aliases:
                del self._alias_map[old_a]
            self._alias_map.pop(block.id, None)

        self._blocks[block.id] = block

        for alias in block.aliases:
            if alias != block.id:
                self._alias_map[alias] = block.id

    def get_canonical_id(self, identifier: str) -> str:
        """Resolves any identifier or alias to its canonical block ID.

        Args:
            identifier: Context block identifier or alias.

        Returns:
            The canonical block ID.

        Raises:
            ContextBlockNotFoundError: If not registered.
        """
        block = self.get(identifier)
        return block.id

    def list_aliases(self) -> Dict[str, str]:
        """Returns mapping of all registered aliases to their canonical block ID."""
        return dict(self._alias_map)

    def get(self, block_id: str) -> ContextBlock:
        """Retrieves a ContextBlock by canonical ID or alias.

        Args:
            block_id: Block identifier or alias.

        Returns:
            The resolved ContextBlock.

        Raises:
            ContextBlockNotFoundError: If the block is not registered (fail-closed).
        """
        cleaned = block_id.strip() if isinstance(block_id, str) else ""
        if not cleaned:
            raise ContextBlockNotFoundError("Context block identifier cannot be empty.")

        if cleaned in self._blocks:
            return self._blocks[cleaned]

        if cleaned in self._alias_map:
            canonical_id = self._alias_map[cleaned]
            return self._blocks[canonical_id]

        raise ContextBlockNotFoundError(
            f"Context block '{block_id}' is not registered in ContextRegistry. "
            f"Available canonical blocks: {sorted(self._blocks.keys())}"
        )

    def has(self, block_id: str) -> bool:
        """Checks if a block ID or alias is registered.

        Args:
            block_id: Block identifier or alias.

        Returns:
            True if registered, False otherwise.
        """
        cleaned = block_id.strip() if isinstance(block_id, str) else ""
        return cleaned in self._blocks or cleaned in self._alias_map

    def list_ids(self) -> List[str]:
        """Returns sorted list of canonical block identifiers."""
        return sorted(self._blocks.keys())

    def list_blocks(
        self, family: Optional[ContextBlockFamily] = None
    ) -> List[ContextBlock]:
        """Lists registered canonical ContextBlock instances, optionally filtered by family.

        Args:
            family: Optional filter for block family ('core', 'domain', 'conditional').

        Returns:
            List of ContextBlock instances.
        """
        if family is not None:
            return [b for b in self._blocks.values() if b.family == family]
        return list(self._blocks.values())

    def resolve_contexts(self, context_ids: Sequence[str]) -> List[ContextBlock]:
        """Resolves, canonicalizes, deduplicates and stably orders context blocks.

        Ordering rules:
        1. Core blocks appear first in stable canonical order (CORE_BLOCK_ORDER).
        2. Domain and conditional blocks follow, sorted by canonical identifier.

        Args:
            context_ids: Sequence of context block identifiers or aliases.

        Returns:
            List of unique, stably ordered ContextBlock instances.

        Raises:
            ContextBlockNotFoundError: If any context identifier is not registered.
        """
        canonical_blocks: Dict[str, ContextBlock] = {}
        for raw_id in context_ids:
            block = self.get(raw_id)
            canonical_blocks[block.id] = block

        # Order: core blocks first in fixed sequence
        ordered: List[ContextBlock] = []
        for core_id in CORE_BLOCK_ORDER:
            if core_id in canonical_blocks:
                ordered.append(canonical_blocks.pop(core_id))

        # Remaining domain / conditional blocks sorted by canonical id
        for dom_id in sorted(canonical_blocks.keys()):
            ordered.append(canonical_blocks[dom_id])

        return ordered

    def assemble(
        self,
        context_ids: Sequence[str],
        state: Any,
        focus_city: Optional[CommuneResult] = None,
    ) -> Dict[str, Any]:
        """Resolves blocks and merges their rendered dictionaries in stable order.

        Args:
            context_ids: Sequence of context block identifiers or aliases.
            state: GraphState or state-like container.
            focus_city: Optional pre-resolved CommuneResult.

        Returns:
            Dictionary with combined context sections.
        """
        resolved_blocks = self.resolve_contexts(context_ids)
        merged: Dict[str, Any] = {}
        for block in resolved_blocks:
            chunk = block.render(state, focus_city=focus_city)
            if chunk:
                merged.update(chunk)
        return merged

    def render(
        self,
        context_ids: Sequence[str],
        state: Any,
        focus_city: Optional[CommuneResult] = None,
        *,
        compact: bool = True,
    ) -> str:
        """Assembles context blocks and serializes into JSON.

        Args:
            context_ids: Sequence of context block identifiers or aliases.
            state: GraphState or state-like container.
            focus_city: Optional pre-resolved CommuneResult.
            compact: Whether to emit compact JSON.

        Returns:
            Serialized JSON string.
        """
        merged = self.assemble(context_ids, state, focus_city=focus_city)
        return dump_context(merged, compact=compact)


# ==============================================================================
# 6. Global Registry & Convenience Functions
# ==============================================================================

_GLOBAL_CONTEXT_REGISTRY: Optional[ContextRegistry] = None


def get_default_context_registry() -> ContextRegistry:
    """Retrieves or initializes the global default ContextRegistry singleton.

    Returns:
        The singleton ContextRegistry instance.
    """
    global _GLOBAL_CONTEXT_REGISTRY
    if _GLOBAL_CONTEXT_REGISTRY is None:
        _GLOBAL_CONTEXT_REGISTRY = ContextRegistry()
    return _GLOBAL_CONTEXT_REGISTRY


def reset_default_context_registry() -> ContextRegistry:
    """Resets the global default ContextRegistry singleton (for hermetic test fixtures).

    Returns:
        A fresh ContextRegistry instance.
    """
    global _GLOBAL_CONTEXT_REGISTRY
    _GLOBAL_CONTEXT_REGISTRY = ContextRegistry()
    return _GLOBAL_CONTEXT_REGISTRY


def assemble_context_blocks(
    contexts: Sequence[str],
    state: Any,
    focus_city: Optional[CommuneResult] = None,
    *,
    registry: Optional[ContextRegistry] = None,
) -> Dict[str, Any]:
    """Assembles declared context blocks into a dictionary.

    Args:
        contexts: Sequence of context block identifiers or aliases.
        state: The GraphState instance.
        focus_city: Optional pre-resolved CommuneResult.
        registry: Optional ContextRegistry instance (defaults to global singleton).

    Returns:
        Dictionary of combined rendered context blocks.
    """
    reg = registry or get_default_context_registry()
    return reg.assemble(contexts, state, focus_city=focus_city)


def render_context_blocks(
    contexts: Sequence[str],
    state: Any,
    focus_city: Optional[CommuneResult] = None,
    *,
    compact: bool = True,
    registry: Optional[ContextRegistry] = None,
) -> str:
    """Renders assembled context blocks into a JSON string.

    Args:
        contexts: Sequence of context block identifiers or aliases.
        state: The GraphState instance.
        focus_city: Optional pre-resolved CommuneResult.
        compact: Whether to emit compact JSON.
        registry: Optional ContextRegistry instance (defaults to global singleton).

    Returns:
        Serialized JSON string.
    """
    reg = registry or get_default_context_registry()
    return reg.render(contexts, state, focus_city=focus_city, compact=compact)

