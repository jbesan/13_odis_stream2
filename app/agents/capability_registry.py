"""Capability specification and registry for ODIS AI Agent Swarm.

Provides strict models, canonical tool mappings, and fail-closed registry
for loading and managing agent capabilities from markdown definitions.
"""

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, Final, List, Literal, Optional, Sequence, Set, Union

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_ai import Capability

import config as cfg
from agents.context_blocks import get_default_context_registry
from agents.tools import (
    compute_routes_tool,
    get_inclusion_job_details_tool,
    get_job_details_tool,
    search_inclusion_jobs_batch_tool,
    search_job_offers_batch_tool,
    search_places_batch_tool,
    search_referentiels_batch_tool,
    search_rna_rag_batch_tool,
)
from agents.web_search import search_web_batch_tool

logger = logging.getLogger("CapabilityRegistry")

# ==============================================================================
# 1. Expert Types and Constants
# ==============================================================================

ExpertName = Literal[
    "job_hunter",
    "housing_expert",
    "mobility_expert",
    "healthcare_expert",
    "education_expert",
    "social_integration_expert",
]

VALID_EXPERTS: Final[frozenset[str]] = frozenset(
    {
        "job_hunter",
        "housing_expert",
        "mobility_expert",
        "healthcare_expert",
        "education_expert",
        "social_integration_expert",
    }
)

# ==============================================================================
# 2. Canonical Tool Registry
# ==============================================================================

CANONICAL_TOOLS: Final[Dict[str, Callable[..., Any]]] = {
    "search_places_batch_tool": search_places_batch_tool,
    "compute_routes_tool": compute_routes_tool,
    "search_rna_rag_batch_tool": search_rna_rag_batch_tool,
    "search_job_offers_batch_tool": search_job_offers_batch_tool,
    "get_job_details_tool": get_job_details_tool,
    "search_inclusion_jobs_batch_tool": search_inclusion_jobs_batch_tool,
    "get_inclusion_job_details_tool": get_inclusion_job_details_tool,
    "search_referentiels_batch_tool": search_referentiels_batch_tool,
    "search_web_batch_tool": search_web_batch_tool,
}

CANONICAL_TOOL_NAMES: Final[frozenset[str]] = frozenset(CANONICAL_TOOLS.keys())

EXPERT_DEFAULT_CAPABILITY: Final[Dict[str, str]] = {
    "housing_expert": "housing_adequation_territoire",
    "mobility_expert": "mobility_adequation_territoire",
    "healthcare_expert": "healthcare_adequation_territoire",
    "education_expert": "education_adequation_territoire",
    "social_integration_expert": "social_adequation_territoire",
    "job_hunter": "job_adequation_territoire",
}


def is_canonical_tool(tool_name: str) -> bool:
    """Checks whether a tool name is registered in the canonical tools registry.

    Args:
        tool_name: Name of the tool to verify.

    Returns:
        True if tool exists in CANONICAL_TOOLS, False otherwise.
    """
    return tool_name in CANONICAL_TOOL_NAMES


def get_canonical_tool(tool_name: str) -> Callable[..., Any]:
    """Retrieves the callable for a canonical tool name.

    Args:
        tool_name: The name of the canonical tool.

    Returns:
        The callable corresponding to the tool name.

    Raises:
        ToolNotFoundError: If the tool name is not registered.
    """
    if tool_name not in CANONICAL_TOOLS:
        raise ToolNotFoundError(
            f"Tool '{tool_name}' is not registered in the canonical tools catalog. "
            f"Available canonical tools: {sorted(CANONICAL_TOOL_NAMES)}"
        )
    return CANONICAL_TOOLS[tool_name]


# ==============================================================================
# 3. Exceptions
# ==============================================================================


class CapabilityRegistryError(Exception):
    """Base exception for all capability registry and specification errors."""


class CapabilityNotFoundError(CapabilityRegistryError, KeyError):
    """Raised when a requested capability ID is not found in the registry (fail-closed)."""


class CapabilityValidationError(CapabilityRegistryError, ValueError):
    """Raised when a capability specification is invalid or corrupted."""


class ToolNotFoundError(CapabilityRegistryError, KeyError):
    """Raised when a tool requested by a capability is not found in canonical tools."""


# ==============================================================================
# 4. CapabilitySpec Model
# ==============================================================================


class CapabilitySpec(BaseModel):
    """Specification model for an agent capability.

    Defines metadata, allowed experts, required tools and contexts,
    loading policy, and instruction markdown body.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        ...,
        description="Unique identifier for the capability (lowercase snake_case starting with a letter).",
    )
    name: str = Field(
        ...,
        description="Human-readable title for the capability.",
    )
    description: str = Field(
        ...,
        description="Concise description of the capability purpose.",
    )
    experts: List[ExpertName] = Field(
        ...,
        min_length=1,
        description="List of domain experts allowed to execute this capability.",
    )
    tools: List[str] = Field(
        default_factory=list,
        description="List of canonical tool names required by this capability.",
    )
    contexts: List[str] = Field(
        default_factory=list,
        description="List of context block identifiers required by this capability.",
    )
    loading_policy: Literal["eager_only", "deferrable"] = Field(
        default="eager_only",
        description="Loading policy for Pydantic AI capability compilation.",
    )
    version: str = Field(
        default="1.0.0",
        description="Semantic version of the capability.",
    )
    instructions: str = Field(
        ...,
        description="Markdown instruction body injected into the agent prompt.",
    )
    tags: List[str] = Field(
        default_factory=list,
        description="Optional categorization tags.",
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_yaml_fields(cls, data: Any) -> Any:
        """Normalizes and deduplicates YAML fields.

        Args:
            data: Raw dictionary or input object before validation.

        Returns:
            Normalized dictionary.
        """
        if isinstance(data, dict):
            experts = data.get("experts")

            # Allow experts to be given as a single string (YAML scalar)
            if isinstance(experts, str):
                experts = [experts.strip()] if experts.strip() else []
                data["experts"] = experts

            # Deduplicate experts if provided as a list
            if isinstance(experts, list):
                cleaned_exp = [
                    e.strip() if isinstance(e, str) else e
                    for e in experts
                    if e
                ]
                data["experts"] = list(dict.fromkeys(cleaned_exp))

            # Allow tools, contexts, and tags to be given as single strings (YAML scalars)
            for list_field in ("tools", "contexts", "tags"):
                raw_val = data.get(list_field)
                if isinstance(raw_val, str):
                    data[list_field] = [raw_val.strip()] if raw_val.strip() else []
        return data

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        """Validates that id is a non-empty lowercase alphanumeric snake_case string starting with a letter.

        Args:
            v: The ID string to validate.

        Returns:
            The cleaned ID string.

        Raises:
            ValueError: If the ID is empty or invalid snake_case.
        """
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Capability 'id' must be a non-empty string.")
        cleaned = v.strip()
        if not re.match(r"^[a-z][a-z0-9_]*$", cleaned):
            raise ValueError(
                f"Capability 'id' '{v}' must be lowercase alphanumeric with underscores "
                "starting with a letter (valid snake_case)."
            )
        return cleaned

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """Validates that name is non-empty.

        Args:
            v: The name string.

        Returns:
            The cleaned name string.

        Raises:
            ValueError: If name is empty or only whitespace.
        """
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Capability 'name' must be a non-empty string.")
        return v.strip()

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        """Validates that description is non-empty.

        Args:
            v: The description string.

        Returns:
            The cleaned description string.

        Raises:
            ValueError: If description is empty or only whitespace.
        """
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Capability 'description' must be a non-empty string.")
        return v.strip()

    @field_validator("version")
    @classmethod
    def _validate_version(cls, v: str) -> str:
        """Validates that version is a non-empty string.

        Args:
            v: The version string.

        Returns:
            The cleaned version string.

        Raises:
            ValueError: If version is empty or only whitespace.
        """
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Capability 'version' must be a non-empty string.")
        return v.strip()

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, v: str) -> str:
        """Validates that instructions markdown content is non-empty.

        Args:
            v: The instructions string.

        Returns:
            The cleaned instructions string.

        Raises:
            ValueError: If instructions is empty or only whitespace.
        """
        if not isinstance(v, str) or not v.strip():
            raise ValueError("Capability 'instructions' must be a non-empty string.")
        return v.strip()

    @field_validator("tools")
    @classmethod
    def _validate_tools(cls, tools: List[str]) -> List[str]:
        """Validates that all declared tools exist in CANONICAL_TOOLS.

        Strips whitespace and deduplicates while preserving order.

        Args:
            tools: List of tool names.

        Returns:
            Deduplicated list of stripped tool names.

        Raises:
            ValueError: If tools is not a list, contains empty items, or unknown tools.
        """
        if not isinstance(tools, list):
            raise ValueError("Capability 'tools' must be a list of strings.")
        cleaned_tools: List[str] = []
        for tool_name in tools:
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError("Tool name must be a non-empty string.")
            cleaned_tool = tool_name.strip()
            if cleaned_tool not in CANONICAL_TOOLS:
                raise ValueError(
                    f"Unknown tool '{cleaned_tool}' declared in capability spec. "
                    f"Allowed canonical tools: {sorted(CANONICAL_TOOLS.keys())}"
                )
            cleaned_tools.append(cleaned_tool)
        return list(dict.fromkeys(cleaned_tools))

    @field_validator("contexts")
    @classmethod
    def _validate_contexts(cls, contexts: List[str]) -> List[str]:
        """Validates and deduplicates declared contexts.

        Strips whitespace and deduplicates while preserving order.

        Args:
            contexts: List of context block names.

        Returns:
            Deduplicated list of stripped context names.

        Raises:
            ValueError: If contexts is not a list or contains empty items.
        """
        if not isinstance(contexts, list):
            raise ValueError("Capability 'contexts' must be a list of strings.")
        cleaned_contexts: List[str] = []
        for ctx in contexts:
            if not isinstance(ctx, str) or not ctx.strip():
                raise ValueError("Context name must be a non-empty string.")
            cleaned_contexts.append(ctx.strip())
        return list(dict.fromkeys(cleaned_contexts))

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, tags: List[str]) -> List[str]:
        """Validates, strips and deduplicates declared categorization tags.

        Args:
            tags: List of tag strings.

        Returns:
            Deduplicated list of stripped non-empty tag strings.

        Raises:
            ValueError: If tags is not a list or contains non-string items.
        """
        if not isinstance(tags, list):
            raise ValueError("Capability 'tags' must be a list of strings.")
        cleaned_tags: List[str] = []
        for tag in tags:
            if not isinstance(tag, str) or not tag.strip():
                raise ValueError("Tag must be a non-empty string.")
            cleaned_tags.append(tag.strip())
        return list(dict.fromkeys(cleaned_tags))

    @classmethod
    def from_markdown(
        cls, content: str, source_path: Optional[str] = None
    ) -> "CapabilitySpec":
        """Parses and validates a CapabilitySpec from a Markdown string with YAML frontmatter.

        Args:
            content: Raw markdown text with YAML frontmatter.
            source_path: Optional file path for descriptive error messages.

        Returns:
            Validated CapabilitySpec instance.

        Raises:
            CapabilityValidationError: If frontmatter is missing, malformed, or fails validation.
        """
        if not content or not content.strip():
            loc = f" in '{source_path}'" if source_path else ""
            raise CapabilityValidationError(f"Empty capability content{loc}.")

        try:
            post = frontmatter.loads(content)
        except Exception as exc:
            loc = f" in '{source_path}'" if source_path else ""
            raise CapabilityValidationError(
                f"Malformed YAML frontmatter{loc}: {exc}"
            ) from exc

        metadata = dict(post.metadata)
        body = post.content.strip()

        if not metadata:
            loc = f" in '{source_path}'" if source_path else ""
            raise CapabilityValidationError(f"Missing YAML frontmatter{loc}.")

        metadata["instructions"] = body

        try:
            return cls.model_validate(metadata)
        except ValidationError as exc:
            loc = f" in '{source_path}'" if source_path else ""
            raise CapabilityValidationError(
                f"Validation failed for capability{loc}: {exc}"
            ) from exc

    @classmethod
    def from_file(cls, file_path: Union[str, Path]) -> "CapabilitySpec":
        """Loads and validates a CapabilitySpec from a Markdown file.

        Args:
            file_path: Absolute or relative path to the .md capability file.

        Returns:
            Validated CapabilitySpec instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            IsADirectoryError: If file_path points to a directory.
            CapabilityValidationError: If file extension is not .md or spec validation fails.
        """
        path = Path(file_path)
        if path.is_dir():
            raise IsADirectoryError(
                f"Expected capability file, but found a directory: '{path}'"
            )
        if not path.is_file():
            raise FileNotFoundError(f"Capability file not found: '{file_path}'")
        if path.suffix.lower() != ".md":
            raise CapabilityValidationError(
                f"Capability file '{path}' must have a '.md' extension, got '{path.suffix}'."
            )

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            raise CapabilityValidationError(
                f"Could not read capability file '{path}': {exc}"
            ) from exc

        spec = cls.from_markdown(content, source_path=str(path))

        if path.stem != spec.id:
            raise CapabilityValidationError(
                f"Capability ID mismatch in '{path}': filename stem '{path.stem}' "
                f"does not match declared id '{spec.id}'."
            )

        return spec


# ==============================================================================
# 5. CapabilityRegistry
# ==============================================================================


DEFAULT_CAPABILITIES_DIR: Final[Path] = Path(cfg.APP_DIR) / "agents" / "capabilities"


class CapabilityRegistry:
    """Registry and cache for agent capabilities loaded from markdown definitions.

    Enforces strict validation (fail-closed) on load and lookup.
    """

    def __init__(
        self,
        capabilities_dir: Optional[Union[str, Path]] = None,
        auto_load: bool = True,
    ) -> None:
        """Initializes the CapabilityRegistry.

        Args:
            capabilities_dir: Path to directory containing .md capability specs.
                Defaults to 'app/agents/capabilities' under project root.
            auto_load: Whether to eagerly load and validate all capabilities on init.

        Raises:
            FileNotFoundError: If capabilities_dir does not exist.
            CapabilityValidationError: If any capability file fails validation.
        """
        if capabilities_dir is None:
            self.capabilities_dir = DEFAULT_CAPABILITIES_DIR
        else:
            self.capabilities_dir = Path(capabilities_dir)

        self._specs: Dict[str, CapabilitySpec] = {}

        if auto_load:
            self.load_all()

    def load_all(self) -> None:
        """Loads, validates, and caches all capabilities from the directory.

        Recursively discovers all .md files under capabilities_dir, ignoring hidden paths.

        Raises:
            FileNotFoundError: If capabilities_dir does not exist.
            CapabilityValidationError: If any file fails validation or duplicates exist.
        """
        if not self.capabilities_dir.is_dir():
            raise FileNotFoundError(
                f"Capabilities directory not found: {self.capabilities_dir}"
            )

        new_specs: Dict[str, CapabilitySpec] = {}
        for file_path in sorted(self.capabilities_dir.rglob("*.md")):
            # Skip hidden files or directories (e.g. .git, .tmp)
            if any(part.startswith(".") for part in file_path.parts):
                continue
            spec = CapabilitySpec.from_file(file_path)
            if spec.id in new_specs:
                raise CapabilityValidationError(
                    f"Duplicate capability ID '{spec.id}' found in '{file_path}' "
                    f"(already registered from another file)."
                )
            self._validate_spec_contexts(spec, source=str(file_path))
            new_specs[spec.id] = spec

        self._specs = new_specs
        logger.info(
            f"Successfully loaded and cached {len(self._specs)} capabilities from {self.capabilities_dir}"
        )

    def _validate_spec_contexts(
        self, spec: CapabilitySpec, source: Optional[str] = None
    ) -> None:
        """Validates that all context blocks declared by a capability exist in ContextRegistry.

        Args:
            spec: The CapabilitySpec to validate.
            source: Optional file path or description for error messages.

        Raises:
            CapabilityValidationError: If any declared context block is unknown.
        """
        ctx_registry = get_default_context_registry()
        for ctx_id in spec.contexts:
            if not ctx_registry.has(ctx_id):
                loc = f" in '{source}'" if source else f" in capability '{spec.id}'"
                raise CapabilityValidationError(
                    f"Unknown context block '{ctx_id}' declared{loc}. "
                    f"Available context blocks: {ctx_registry.list_ids()}"
                )

    def get(self, capability_id: str) -> CapabilitySpec:
        """Retrieves a capability by its ID.

        Args:
            capability_id: Unique capability identifier.

        Returns:
            The loaded CapabilitySpec.

        Raises:
            CapabilityNotFoundError: If the capability ID does not exist in the registry.
        """
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise CapabilityNotFoundError(f"Invalid capability ID: {capability_id!r}")

        cleaned = capability_id.strip()
        if cleaned in self._specs:
            return self._specs[cleaned]

        raise CapabilityNotFoundError(
            f"Capability '{cleaned}' not found in registry. "
            f"Available capabilities: {sorted(self._specs.keys())}"
        )

    def list_all(self) -> List[CapabilitySpec]:
        """Lists all registered capabilities sorted alphabetically by ID.

        Returns:
            List of all CapabilitySpec instances.
        """
        return [self._specs[cid] for cid in sorted(self._specs.keys())]

    def get_for_expert(self, expert: str) -> List[CapabilitySpec]:
        """Retrieves all capabilities declared for a specific expert.

        Args:
            expert: The identifier of the expert (e.g. 'housing_expert').

        Returns:
            List of CapabilitySpec instances allowed for this expert, sorted by ID.

        Raises:
            CapabilityValidationError: If expert is not a recognized expert name.
        """
        if expert not in VALID_EXPERTS:
            raise CapabilityValidationError(
                f"Invalid expert '{expert}'. Allowed experts: {sorted(VALID_EXPERTS)}"
            )

        return [
            spec
            for cid, spec in sorted(self._specs.items())
            if expert in spec.experts
        ]

    def resolve_capability_id(self, identifier: str) -> str:
        """Resolves a capability identifier to its canonical ID.

        Args:
            identifier: The capability ID.

        Returns:
            The canonical capability ID.

        Raises:
            CapabilityNotFoundError: If the identifier cannot be resolved.
        """
        return self.get(identifier).id

    def get_base_capability_id(self, expert: str) -> str:
        """Returns the canonical base capability ID for an expert domain.

        Args:
            expert: Expert identifier (e.g. 'housing_expert', 'job_hunter').

        Returns:
            The canonical capability ID for adéquation territoire.

        Raises:
            CapabilityValidationError: If expert is unknown.
        """
        if expert not in VALID_EXPERTS:
            raise CapabilityValidationError(
                f"Invalid expert '{expert}'. Allowed experts: {sorted(VALID_EXPERTS)}"
            )
        return EXPERT_DEFAULT_CAPABILITY[expert]

    def build_capabilities_for_expert(
        self,
        capability_ids: Sequence[str],
        defer_loading: bool = False,
        expert: Optional[str] = None,
    ) -> List[Capability]:
        """Instantiates Pydantic AI Capability objects for an expert run with tool deduplication.

        Hot start: Capabilities explicitly requested in capability_ids (e.g. planned by ts_agent)
        are ALWAYS eagerly loaded (defer_loading=False).
        If defer_loading is True and an expert is specified, additional capabilities belonging to
        the expert marked as 'deferrable' in their loading_policy are appended with defer_loading=True.
        Ensures that if multiple capabilities declare the same tool, it is only registered once
        (on the first capability encountered) to prevent tool name collision in Pydantic AI.

        Args:
            capability_ids: Sequence of capability identifiers.
            defer_loading: Whether to inject additional deferrable capabilities (default False in Phase 1).
            expert: Optional expert name to validate that declared capabilities belong to it.

        Returns:
            List of instantiated pydantic_ai.Capability objects.

        Raises:
            CapabilityNotFoundError: If any capability ID cannot be resolved.
            CapabilityValidationError: If any capability is not allowed for the given expert.
            ToolNotFoundError: If any declared tool cannot be resolved.
        """
        if expert is not None and expert not in VALID_EXPERTS:
            raise CapabilityValidationError(
                f"Invalid expert '{expert}'. Allowed experts: {sorted(VALID_EXPERTS)}"
            )

        capabilities: List[Capability] = []
        registered_tools: Set[str] = set()
        canonical_eager_ids: List[str] = []

        # 1. Hot start: Explicitly planned capabilities are ALWAYS eager (defer_loading=False)
        for cid in capability_ids:
            canonical_id = self.resolve_capability_id(cid)
            canonical_eager_ids.append(canonical_id)
            spec = self.get(canonical_id)

            if expert is not None and expert not in spec.experts:
                raise CapabilityValidationError(
                    f"Capability '{spec.id}' is not permitted for expert '{expert}'. "
                    f"Allowed experts: {sorted(spec.experts)}"
                )

            tools_for_cap: List[Callable[..., Any]] = []
            for tool_name in spec.tools:
                if tool_name not in registered_tools:
                    registered_tools.add(tool_name)
                    tools_for_cap.append(get_canonical_tool(tool_name))

            cap = Capability(
                id=spec.id,
                description=spec.description,
                instructions=spec.instructions,
                tools=tools_for_cap,
                defer_loading=False,
            )
            capabilities.append(cap)

        # 2. Deferred capabilities: If enabled and expert given, append remaining deferrable capabilities
        if defer_loading and expert is not None:
            expert_specs = self.get_for_expert(expert)
            for spec in expert_specs:
                if (
                    spec.id not in canonical_eager_ids
                    and spec.loading_policy == "deferrable"
                ):
                    tools_for_cap = []
                    for tool_name in spec.tools:
                        if tool_name not in registered_tools:
                            registered_tools.add(tool_name)
                            tools_for_cap.append(get_canonical_tool(tool_name))

                    cap = Capability(
                        id=spec.id,
                        description=spec.description,
                        instructions=spec.instructions,
                        tools=tools_for_cap,
                        defer_loading=True,
                    )
                    capabilities.append(cap)

        return capabilities

    def __contains__(self, capability_id: str) -> bool:
        """Checks if a capability ID exists in the registry."""
        return capability_id in self._specs

    def __len__(self) -> int:
        """Returns the number of registered capabilities."""
        return len(self._specs)

    def __repr__(self) -> str:
        """String representation of the registry."""
        return f"CapabilityRegistry({len(self._specs)} capabilities loaded from {self.capabilities_dir})"


# ==============================================================================
# 6. Default Registry Singleton
# ==============================================================================

_default_registry: Optional[CapabilityRegistry] = None


def reset_default_capability_registry() -> None:
    """Resets the singleton default capability registry."""
    global _default_registry
    _default_registry = None


def get_default_capability_registry(
    capabilities_dir: Optional[Union[str, Path]] = None,
    force_reload: bool = False,
) -> CapabilityRegistry:
    """Returns the singleton CapabilityRegistry instance or a custom registry.

    When capabilities_dir is None, returns the cached global singleton.
    When a custom capabilities_dir is provided, returns a dedicated instance without
    mutating the global default singleton.

    Args:
        capabilities_dir: Optional custom capabilities directory path.
        force_reload: Whether to force re-instantiation and re-parsing of default.

    Returns:
        The CapabilityRegistry instance.
    """
    global _default_registry
    if capabilities_dir is not None:
        return CapabilityRegistry(capabilities_dir=capabilities_dir, auto_load=True)

    if _default_registry is None or force_reload:
        _default_registry = CapabilityRegistry(auto_load=True)
    return _default_registry
