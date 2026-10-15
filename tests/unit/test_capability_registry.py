"""Unit tests for CapabilitySpec and CapabilityRegistry (Phase 1).

Tests strict validation, canonical tool mapping, fail-closed lookups,
and compatibility with existing and new capability definitions.
"""

from pathlib import Path
import pytest
from pydantic import ValidationError

from agents.capability_registry import (
    CANONICAL_TOOL_NAMES,
    CANONICAL_TOOLS,
    DEFAULT_CAPABILITIES_DIR,
    EXPERT_DEFAULT_CAPABILITY,
    VALID_EXPERTS,
    CapabilityNotFoundError,
    CapabilityRegistry,
    CapabilityRegistryError,
    CapabilitySpec,
    CapabilityValidationError,
    ToolNotFoundError,
    get_canonical_tool,
    get_default_capability_registry,
    is_canonical_tool,
    reset_default_capability_registry,
)
from agents.education_expert import education_expert_agent
from agents.healthcare_expert import healthcare_expert_agent
from agents.housing_expert import housing_expert_agent
from agents.job_hunter import job_hunter_agent
from agents.mobility_expert import mobility_expert_agent
from agents.social_integration_expert import social_integration_expert_agent


# ==============================================================================
# 1. Tests for Canonical Tool Registry
# ==============================================================================


def test_canonical_tools_catalog():
    """Validates that all expected tools are present and callable in CANONICAL_TOOLS."""
    expected_tools = {
        "search_places_batch_tool",
        "compute_routes_tool",
        "search_rna_rag_batch_tool",
        "search_job_offers_batch_tool",
        "get_job_details_tool",
        "search_inclusion_jobs_batch_tool",
        "get_inclusion_job_details_tool",
        "search_referentiels_batch_tool",
        "search_web_batch_tool",
    }
    assert expected_tools == CANONICAL_TOOL_NAMES
    for tool_name in expected_tools:
        assert tool_name in CANONICAL_TOOLS
        assert callable(CANONICAL_TOOLS[tool_name])
        assert is_canonical_tool(tool_name)


def test_get_canonical_tool_success_and_failure():
    """Tests resolving canonical tools by name, including fail-closed behavior."""
    tool_fn = get_canonical_tool("search_places_batch_tool")
    assert callable(tool_fn)

    with pytest.raises(ToolNotFoundError) as exc_info:
        get_canonical_tool("non_existent_tool")
    assert "non_existent_tool" in str(exc_info.value)
    assert is_canonical_tool("non_existent_tool") is False


# ==============================================================================
# 2. Tests for CapabilitySpec Validation
# ==============================================================================


def test_capability_spec_valid_with_experts():
    """Tests valid spec creation with explicit experts list."""
    spec = CapabilitySpec(
        id="test_capability",
        name="Test Capability",
        description="A test capability description.",
        experts=["job_hunter", "housing_expert"],
        tools=["search_places_batch_tool"],
        contexts=["housing", "education"],
        loading_policy="eager_only",
        version="1.1.0",
        instructions="These are test instructions.",
    )
    assert spec.id == "test_capability"
    assert spec.name == "Test Capability"
    assert spec.description == "A test capability description."
    assert spec.experts == ["job_hunter", "housing_expert"]
    assert spec.tools == ["search_places_batch_tool"]
    assert spec.contexts == ["housing", "education"]
    assert spec.loading_policy == "eager_only"
    assert spec.version == "1.1.0"
    assert spec.instructions == "These are test instructions."


def test_capability_spec_rejects_unknown_tool():
    """Validates that declaring an unknown tool raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec(
            id="bad_tool_spec",
            name="Bad Tool Spec",
            description="Spec with bad tool",
            experts=["housing_expert"],
            tools=["unauthorized_scraper_tool"],
            instructions="Instructions.",
        )
    assert "Unknown tool 'unauthorized_scraper_tool'" in str(exc_info.value)


def test_capability_spec_rejects_unknown_expert():
    """Validates that declaring an unapproved expert raises ValidationError."""
    with pytest.raises(ValidationError):
        CapabilitySpec(
            id="bad_expert_spec",
            name="Bad Expert Spec",
            description="Spec with bad expert",
            experts=["unapproved_ai_agent"],  # type: ignore
            instructions="Instructions.",
        )


def test_capability_spec_rejects_empty_experts():
    """Validates that empty experts list is rejected."""
    with pytest.raises(ValidationError):
        CapabilitySpec(
            id="empty_experts_spec",
            name="Empty Experts Spec",
            description="Spec with empty experts",
            experts=[],
            instructions="Instructions.",
        )


@pytest.mark.parametrize(
    "invalid_id",
    [
        "",
        "   ",
        "invalid-id",  # Hyphens disallowed (snake_case required)
        "InvalidId",  # Uppercase disallowed
        "invalid id",  # Spaces disallowed
        "invalid/id",  # Slashes disallowed
    ],
)
def test_capability_spec_rejects_invalid_id(invalid_id: str):
    """Validates strict rejection of invalid capability IDs."""
    with pytest.raises(ValidationError):
        CapabilitySpec(
            id=invalid_id,
            name="Valid Name",
            description="Valid Description",
            experts=["job_hunter"],
            instructions="Valid instructions.",
        )


@pytest.mark.parametrize("empty_field", ["name", "description", "instructions"])
def test_capability_spec_rejects_empty_required_strings(empty_field: str):
    """Validates that name, description, and instructions cannot be empty strings."""
    data = {
        "id": "valid_id",
        "name": "Valid Name",
        "description": "Valid Description",
        "experts": ["job_hunter"],
        "instructions": "Valid instructions.",
    }
    data[empty_field] = "   "
    with pytest.raises(ValidationError):
        CapabilitySpec(**data)


def test_capability_spec_deduplicates_tools_and_contexts():
    """Validates that duplicate tool and context declarations are deduplicated preserving order."""
    spec = CapabilitySpec(
        id="dedup_spec",
        name="Dedup Spec",
        description="Tests deduplication",
        experts=["job_hunter"],
        tools=[
            "search_places_batch_tool",
            "compute_routes_tool",
            "search_places_batch_tool",
        ],
        contexts=["education", "housing", "education"],
        instructions="Instructions.",
    )
    assert spec.tools == ["search_places_batch_tool", "compute_routes_tool"]
    assert spec.contexts == ["education", "housing"]


# ==============================================================================
# 3. Tests for Markdown Parsing (from_markdown and from_file)
# ==============================================================================


def test_from_markdown_valid():
    """Tests parsing a complete markdown string with frontmatter."""
    md_content = """---
id: custom_spec
name: Custom Capability
description: Description of custom capability
experts: mobility_expert
tools:
  - compute_routes_tool
version: 2.0.0
---

# Instructions
1. First step.
2. Second step.
"""
    spec = CapabilitySpec.from_markdown(md_content)
    assert spec.id == "custom_spec"
    assert spec.name == "Custom Capability"
    assert spec.experts == ["mobility_expert"]
    assert spec.tools == ["compute_routes_tool"]
    assert spec.version == "2.0.0"
    assert "First step." in spec.instructions


def test_from_markdown_corrupted_yaml():
    """Tests fail-closed error when frontmatter has invalid YAML syntax."""
    corrupted_md = """---
id: [unclosed_bracket_syntax
name: Bad YAML
---
Body
"""
    with pytest.raises(CapabilityValidationError) as exc_info:
        CapabilitySpec.from_markdown(corrupted_md)
    assert "Malformed YAML" in str(exc_info.value)


def test_from_markdown_missing_frontmatter():
    """Tests fail-closed error when frontmatter is entirely missing."""
    raw_md = "# Just Markdown\nNo frontmatter here."
    with pytest.raises(CapabilityValidationError) as exc_info:
        CapabilitySpec.from_markdown(raw_md)
    assert "Missing YAML frontmatter" in str(exc_info.value)


def test_from_markdown_empty_content():
    """Tests fail-closed error when markdown string is empty."""
    with pytest.raises(CapabilityValidationError) as exc_info:
        CapabilitySpec.from_markdown("   ")
    assert "Empty capability content" in str(exc_info.value)


def test_from_file_id_mismatch(tmp_path: Path):
    """Tests that a filename stem not matching the declared ID is rejected."""
    bad_file = tmp_path / "wrong_name.md"
    bad_file.write_text(
        """---
id: declared_id
name: Declared Spec
description: Spec with mismatched filename
experts: job_hunter
---
Instructions
""",
        encoding="utf-8",
    )
    with pytest.raises(CapabilityValidationError) as exc_info:
        CapabilitySpec.from_file(bad_file)
    assert "Capability ID mismatch" in str(exc_info.value)


# ==============================================================================
# 4. Tests for CapabilityRegistry on Production Capabilities
# ==============================================================================


def test_default_capabilities_directories():
    """Validates that DEFAULT_CAPABILITIES_DIR points to app/agents/capabilities and is a directory."""
    assert DEFAULT_CAPABILITIES_DIR.is_dir()
    assert DEFAULT_CAPABILITIES_DIR.name == "capabilities"


def test_registry_loads_all_production_capabilities():
    """Tests that CapabilityRegistry successfully loads all 8 capabilities from app/agents/capabilities."""
    registry = CapabilityRegistry()
    assert len(registry) == 8

    all_specs = registry.list_all()
    assert len(all_specs) == 8

    # IDs should be sorted alphabetically
    all_ids = [spec.id for spec in all_specs]
    expected_ids = sorted(
        [
            "education_adequation_territoire",
            "healthcare_adequation_territoire",
            "housing_adequation_territoire",
            "job_adequation_territoire",
            "job_extend_job_search",
            "job_fetch_specific_offer",
            "mobility_adequation_territoire",
            "social_adequation_territoire",
        ]
    )
    assert all_ids == expected_ids


def test_registry_get_existing_capabilities():
    """Tests retrieving specific capabilities and verifying enriched frontmatter attributes."""
    registry = CapabilityRegistry()

    # Housing
    housing = registry.get("housing_adequation_territoire")
    assert housing.id == "housing_adequation_territoire"
    assert housing.name == "Adéquation Territoriale - Logement"
    assert housing.experts == ["housing_expert"]
    assert set(housing.tools) == {
        "search_places_batch_tool",
        "compute_routes_tool",
        "search_web_batch_tool",
    }
    assert housing.contexts == ["commune.housing"]
    assert housing.loading_policy == "eager_only"
    assert housing.version == "1.0.0"
    assert "search_places_batch_tool" in housing.instructions

    # Healthcare (strict iso check: places + rna, no compute_routes)
    healthcare = registry.get("healthcare_adequation_territoire")
    assert healthcare.id == "healthcare_adequation_territoire"
    assert healthcare.name == "Adéquation Territoriale - Santé"
    assert healthcare.experts == ["healthcare_expert"]
    assert healthcare.tools == ["search_places_batch_tool", "search_rna_rag_batch_tool"]
    assert "compute_routes_tool" not in healthcare.tools
    assert "compute_routes_tool" not in healthcare.instructions
    assert "search_rna_rag_batch_tool" in healthcare.instructions
    assert healthcare.contexts == ["commune.health"]
    assert healthcare.loading_policy == "eager_only"

    # Job Hunter adequation territoire (all 5 tools declared)
    job = registry.get("job_adequation_territoire")
    assert job.id == "job_adequation_territoire"
    assert job.name == "Adéquation Territoriale - Emploi"
    assert job.experts == ["job_hunter"]
    assert job.tools == [
        "search_job_offers_batch_tool",
        "get_job_details_tool",
        "search_inclusion_jobs_batch_tool",
        "get_inclusion_job_details_tool",
        "search_referentiels_batch_tool",
    ]
    assert job.contexts == ["commune.employment"]
    assert job.loading_policy == "eager_only"

    # Mobility
    mobility = registry.get("mobility_adequation_territoire")
    assert mobility.id == "mobility_adequation_territoire"
    assert mobility.name == "Adéquation Territoriale - Mobilité"
    assert mobility.experts == ["mobility_expert"]
    assert mobility.tools == [
        "search_places_batch_tool",
        "compute_routes_tool",
        "search_web_batch_tool",
    ]
    assert mobility.contexts == ["commune.mobility"]

    # Education
    education = registry.get("education_adequation_territoire")
    assert education.id == "education_adequation_territoire"
    assert education.name == "Adéquation Territoriale - Éducation"
    assert education.experts == ["education_expert"]
    assert education.tools == ["search_places_batch_tool"]
    assert education.contexts == ["commune.education"]

    # Social Integration
    social = registry.get("social_adequation_territoire")
    assert social.id == "social_adequation_territoire"
    assert social.name == "Adéquation Territoriale - Insertion Sociale"
    assert social.experts == ["social_integration_expert"]
    assert social.tools == [
        "search_places_batch_tool",
        "search_rna_rag_batch_tool",
        "search_web_batch_tool",
    ]
    assert social.contexts == ["commune.inclusion", "commune.territory"]

    # Containment check
    assert "housing_adequation_territoire" in registry
    assert "non_existent_cap" not in registry


def test_registry_get_for_expert_production():
    """Tests get_for_expert returns the expected capabilities for each of the 6 experts."""
    registry = CapabilityRegistry()

    # Verify all valid experts are recognized
    for exp in VALID_EXPERTS:
        specs = registry.get_for_expert(exp)
        assert len(specs) >= 1
        for s in specs:
            assert exp in s.experts

    # Job hunter has 3 capabilities
    job_specs = registry.get_for_expert("job_hunter")
    assert [s.id for s in job_specs] == [
        "job_adequation_territoire",
        "job_extend_job_search",
        "job_fetch_specific_offer",
    ]

    # Housing has 1 capability
    housing_specs = registry.get_for_expert("housing_expert")
    assert [s.id for s in housing_specs] == ["housing_adequation_territoire"]

    # Mobility has 1 capability
    mobility_specs = registry.get_for_expert("mobility_expert")
    assert [s.id for s in mobility_specs] == ["mobility_adequation_territoire"]

    # Healthcare has 1 capability
    health_specs = registry.get_for_expert("healthcare_expert")
    assert [s.id for s in health_specs] == ["healthcare_adequation_territoire"]

    # Education has 1 capability
    edu_specs = registry.get_for_expert("education_expert")
    assert [s.id for s in edu_specs] == ["education_adequation_territoire"]

    # Social integration has 1 capability
    social_specs = registry.get_for_expert("social_integration_expert")
    assert [s.id for s in social_specs] == ["social_adequation_territoire"]


def test_registry_get_for_expert_invalid():
    """Tests that calling get_for_expert with an unrecognized expert raises ValueError."""
    registry = CapabilityRegistry()
    with pytest.raises(ValueError) as exc_info:
        registry.get_for_expert("unknown_specialist")
    assert "unknown_specialist" in str(exc_info.value)


def test_registry_fail_closed_on_unknown_id():
    """Tests that registry.get raises CapabilityNotFoundError on missing ID (fail-closed)."""
    registry = CapabilityRegistry()
    with pytest.raises(CapabilityNotFoundError) as exc_info:
        registry.get("non_existent_capability")
    assert "non_existent_capability" in str(exc_info.value)
    # Check inheritance: should also be a KeyError and CapabilityRegistryError
    assert isinstance(exc_info.value, KeyError)
    assert isinstance(exc_info.value, CapabilityRegistryError)

    with pytest.raises(CapabilityNotFoundError):
        registry.get("")


def test_registry_resolve_tools_production():
    """Tests resolving tools for a capability against canonical tool functions via build_capabilities_for_expert."""
    registry = CapabilityRegistry()
    caps = registry.build_capabilities_for_expert(
        ["housing_adequation_territoire"], expert="housing_expert"
    )
    assert len(caps) == 1
    tool_names = {t.__name__ for t in caps[0].tools}
    assert tool_names == {
        "search_places_batch_tool",
        "compute_routes_tool",
        "search_web_batch_tool",
    }
    for tool_fn in caps[0].tools:
        assert callable(tool_fn)
        assert tool_fn == CANONICAL_TOOLS[tool_fn.__name__]


def test_registry_expert_tools_alignment_with_agents():
    """Verifies that each expert's tools declared in *_adequation_territoire match expected tools."""
    registry = CapabilityRegistry()

    expected_tools_by_expert = {
        "job_hunter": {
            "search_job_offers_batch_tool",
            "get_job_details_tool",
            "search_inclusion_jobs_batch_tool",
            "get_inclusion_job_details_tool",
            "search_referentiels_batch_tool",
        },
        "healthcare_expert": {
            "search_places_batch_tool",
            "search_rna_rag_batch_tool",
        },
        "education_expert": {
            "search_places_batch_tool",
        },
        "housing_expert": {
            "search_places_batch_tool",
            "compute_routes_tool",
            "search_web_batch_tool",
        },
        "mobility_expert": {
            "search_places_batch_tool",
            "compute_routes_tool",
            "search_web_batch_tool",
        },
        "social_integration_expert": {
            "search_places_batch_tool",
            "search_rna_rag_batch_tool",
            "search_web_batch_tool",
        },
    }

    prefix_map = {
        "job_hunter": "job_adequation_territoire",
        "healthcare_expert": "healthcare_adequation_territoire",
        "education_expert": "education_adequation_territoire",
        "housing_expert": "housing_adequation_territoire",
        "mobility_expert": "mobility_adequation_territoire",
        "social_integration_expert": "social_adequation_territoire",
    }

    for expert, cap_id in prefix_map.items():
        spec = registry.get(cap_id)
        assert set(spec.tools) == expected_tools_by_expert[expert]


# ==============================================================================
# 5. Tests for CapabilityRegistry Isolation and Error Handling
# ==============================================================================


def test_registry_missing_directory():
    """Tests that specifying a non-existent directory raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        CapabilityRegistry(capabilities_dir="/non/existent/path/for/caps")


def test_registry_empty_directory(tmp_path: Path):
    """Tests that an empty directory initializes an empty registry."""
    empty_dir = tmp_path / "empty_caps"
    empty_dir.mkdir()
    registry = CapabilityRegistry(capabilities_dir=empty_dir)
    assert len(registry) == 0
    assert registry.list_all() == []
    with pytest.raises(CapabilityNotFoundError):
        registry.get("any_id")


def test_registry_corrupted_file_fails_closed(tmp_path: Path):
    """Tests that any corrupted capability file causes the registry load to fail immediately."""
    caps_dir = tmp_path / "caps_corrupt"
    caps_dir.mkdir()

    # Valid file
    valid_file = caps_dir / "valid_cap.md"
    valid_file.write_text(
        """---
id: valid_cap
name: Valid Cap
description: Valid description
domain: job_hunter
---
Body
""",
        encoding="utf-8",
    )

    # Corrupted file
    corrupt_file = caps_dir / "corrupted_cap.md"
    corrupt_file.write_text("Not valid frontmatter at all", encoding="utf-8")

    with pytest.raises(CapabilityValidationError):
        CapabilityRegistry(capabilities_dir=caps_dir)


def test_registry_duplicate_id_fails_closed(tmp_path: Path):
    """Tests that duplicate capability IDs across files raise CapabilityValidationError."""
    caps_dir = tmp_path / "caps_dup"
    sub1 = caps_dir / "sub1"
    sub2 = caps_dir / "sub2"
    sub1.mkdir(parents=True)
    sub2.mkdir(parents=True)

    file1 = sub1 / "file1.md"
    file1.write_text(
        """---
id: file1
name: Cap One
description: Desc 1
experts: job_hunter
---
Body 1
""",
        encoding="utf-8",
    )

    # File with identical stem and declared id in another subfolder
    file2 = sub2 / "file1.md"
    file2.write_text(
        """---
id: file1
name: Cap Copy
description: Desc Copy
experts: job_hunter
---
Body 2
""",
        encoding="utf-8",
    )

    with pytest.raises(CapabilityValidationError) as exc_info:
        CapabilityRegistry(capabilities_dir=caps_dir)
    assert "Duplicate capability ID 'file1'" in str(exc_info.value)


def test_get_default_capability_registry_singleton():
    """Tests singleton behavior of get_default_capability_registry."""
    reset_default_capability_registry()
    reg1 = get_default_capability_registry()
    reg2 = get_default_capability_registry()
    assert reg1 is reg2
    assert len(reg1) == 8

    # Custom directory does not pollute the default singleton
    custom_dir = Path("/tmp")
    custom_reg = get_default_capability_registry(capabilities_dir=custom_dir)
    assert custom_reg is not reg1
    assert get_default_capability_registry() is reg1

    # Force reload
    reg3 = get_default_capability_registry(force_reload=True)
    assert reg3 is not reg1
    assert len(reg3) == 8


# ==============================================================================
# 6. Deep Verification Tests for Rigor & Edge Cases
# ==============================================================================


def test_capability_spec_rejects_extra_fields():
    """Validates that extra/unrecognized frontmatter fields are rejected (fail-closed)."""
    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec(
            id="extra_fields_spec",
            name="Extra Spec",
            description="Spec with unknown fields",
            experts=["job_hunter"],
            instructions="Instructions.",
            unexpected_field="disallowed",  # type: ignore
        )
    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_from_markdown_rejects_typo_in_field_names():
    """Tests that frontmatter with field typos (e.g., 'tool:' instead of 'tools:') is rejected."""
    md_with_typo = """---
id: typo_spec
name: Typo Spec
description: Spec with typo in tools key
domain: housing_expert
tool:
  - search_places_batch_tool
---
Body
"""
    with pytest.raises(CapabilityValidationError) as exc_info:
        CapabilitySpec.from_markdown(md_with_typo)
    assert "Extra inputs are not permitted" in str(exc_info.value)


def test_capability_spec_strips_whitespace_tools_and_contexts():
    """Validates that whitespace around tools and contexts is cleaned and resolve_tools works."""
    spec = CapabilitySpec(
        id="whitespace_spec",
        name="Whitespace Spec",
        description="Spec with whitespace in lists",
        experts=["housing_expert"],
        tools=["  search_places_batch_tool  ", "compute_routes_tool  "],
        contexts=["  housing  ", "  education  "],
        tags=["  tag1  ", "  tag2  ", "  tag1  "],
        instructions="Instructions.",
    )
    assert spec.tools == ["search_places_batch_tool", "compute_routes_tool"]
    assert spec.contexts == ["housing", "education"]
    assert spec.tags == ["tag1", "tag2"]


def test_capability_spec_rejects_empty_tag():
    """Validates that empty tags are rejected with ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        CapabilitySpec(
            id="empty_tag_spec",
            name="Test",
            description="Test",
            experts=["job_hunter"],
            instructions="Inst",
            tags=["valid_tag", "   "],
        )
    assert "Tag must be a non-empty string" in str(exc_info.value)


def test_capability_spec_normalizes_scalar_experts_in_yaml():
    """Validates that a single string in experts (YAML scalar) is normalized to a list."""
    md = """---
id: scalar_expert_spec
name: Scalar Expert Spec
description: Spec with scalar expert
experts: job_hunter
---
Instructions.
"""
    spec = CapabilitySpec.from_markdown(md)
    assert spec.experts == ["job_hunter"]


def test_capability_spec_deduplicates_experts():
    """Validates that duplicate experts are deduplicated preserving order."""
    spec = CapabilitySpec(
        id="dup_experts_spec",
        name="Dup Experts Spec",
        description="Spec with duplicate experts",
        experts=["job_hunter", "housing_expert", "job_hunter"],
        instructions="Instructions.",
    )
    assert spec.experts == ["job_hunter", "housing_expert"]


@pytest.mark.parametrize(
    "invalid_id",
    [
        "_starts_with_underscore",
        "___",
        "123_starts_with_number",
        "123",
        "has whitespace",
        "has-hyphens",
        "CamelCase",
    ],
)
def test_capability_spec_rejects_invalid_id_patterns(invalid_id: str):
    """Validates strict rejection of identifiers violating snake_case rules."""
    with pytest.raises(ValidationError):
        CapabilitySpec(
            id=invalid_id,
            name="Test",
            description="Test",
            experts=["job_hunter"],
            instructions="Instructions.",
        )


@pytest.mark.parametrize("empty_version", ["", "   "])
def test_capability_spec_rejects_empty_version(empty_version: str):
    """Validates that version cannot be an empty string."""
    with pytest.raises(ValidationError):
        CapabilitySpec(
            id="test_version",
            name="Test",
            description="Test",
            experts=["job_hunter"],
            version=empty_version,
            instructions="Instructions.",
        )


def test_from_file_rejects_non_md_extension(tmp_path: Path):
    """Validates that from_file rejects non-.md files."""
    txt_file = tmp_path / "valid_cap.txt"
    txt_file.write_text("dummy content", encoding="utf-8")
    with pytest.raises(CapabilityValidationError) as exc_info:
        CapabilitySpec.from_file(txt_file)
    assert "must have a '.md' extension" in str(exc_info.value)


def test_from_file_rejects_directory(tmp_path: Path):
    """Validates that from_file raises IsADirectoryError when passed a directory."""
    with pytest.raises(IsADirectoryError):
        CapabilitySpec.from_file(tmp_path)


def test_registry_container_methods():
    """Validates len, in, and __repr__ on CapabilityRegistry."""
    registry = CapabilityRegistry()
    assert len(registry) == 8
    assert "housing_adequation_territoire" in registry
    assert "unknown_cap" not in registry
    repr_str = repr(registry)
    assert "CapabilityRegistry(8 capabilities" in repr_str


def test_capability_spec_rejects_invalid_loading_policy():
    """Validates that an invalid loading_policy raises ValidationError."""
    with pytest.raises(ValidationError):
        CapabilitySpec(
            id="test_loading_policy",
            name="Test",
            description="Test",
            experts=["job_hunter"],
            loading_policy="invalid_policy",  # type: ignore
            instructions="Instructions.",
        )


def test_capability_spec_normalizes_scalar_tools_contexts_tags():
    """Validates that scalar strings for tools, contexts, and tags are normalized to lists."""
    md = """---
id: scalar_fields_spec
name: Scalar Fields Spec
description: Spec with scalar tools contexts and tags
experts: housing_expert
tools: search_places_batch_tool
contexts: commune.housing
tags: logement
---
Instructions.
"""
    spec = CapabilitySpec.from_markdown(md)
    assert spec.tools == ["search_places_batch_tool"]
    assert spec.contexts == ["commune.housing"]
    assert spec.tags == ["logement"]


def test_registry_raises_when_default_dir_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    """Validates that when DEFAULT_CAPABILITIES_DIR does not exist, registry raises FileNotFoundError."""
    non_existent = tmp_path / "non_existent_capabilities"
    monkeypatch.setattr(
        "agents.capability_registry.DEFAULT_CAPABILITIES_DIR", non_existent
    )
    with pytest.raises(FileNotFoundError):
        CapabilityRegistry(capabilities_dir=None)


def test_build_capabilities_defer_loading(tmp_path: Path):
    """Validates S5: hot start capabilities are always eager, while additional deferrable capabilities are deferred."""
    # 1. With standard capabilities (all currently eager_only)
    registry = get_default_capability_registry()
    caps_eager = registry.build_capabilities_for_expert(
        ["housing_adequation_territoire"], defer_loading=False, expert="housing_expert"
    )
    assert len(caps_eager) == 1
    assert caps_eager[0].defer_loading is False

    caps_hot_start = registry.build_capabilities_for_expert(
        ["housing_adequation_territoire"], defer_loading=True, expert="housing_expert"
    )
    assert len(caps_hot_start) == 1
    # Hot start capabilities must remain eager even when defer_loading=True
    assert caps_hot_start[0].defer_loading is False

    # 2. With a dedicated directory having an eager hot start and a deferrable extra capability
    caps_dir = tmp_path / "caps"
    caps_dir.mkdir()
    (caps_dir / "housing_adequation_territoire.md").write_text(
        """---
id: housing_adequation_territoire
name: Logement Adéquation
description: Base logement
experts:
  - housing_expert
tools:
  - search_places_batch_tool
contexts:
  - commune.housing
loading_policy: eager_only
---
Instructions
""",
        encoding="utf-8",
    )
    (caps_dir / "housing_extra_deferrable.md").write_text(
        """---
id: housing_extra_deferrable
name: Logement Extra
description: Extra deferrable
experts:
  - housing_expert
tools:
  - search_places_batch_tool
  - compute_routes_tool
contexts:
  - commune.housing
loading_policy: deferrable
---
Instructions
""",
        encoding="utf-8",
    )
    custom_reg = CapabilityRegistry(capabilities_dir=caps_dir)

    # When defer_loading is False: only requested hot-start capability is returned, eager
    caps_off = custom_reg.build_capabilities_for_expert(
        ["housing_adequation_territoire"], defer_loading=False, expert="housing_expert"
    )
    assert len(caps_off) == 1
    assert caps_off[0].id == "housing_adequation_territoire"
    assert caps_off[0].defer_loading is False
    assert [t.__name__ for t in caps_off[0].tools] == ["search_places_batch_tool"]

    # When defer_loading is True: hot-start is eager, extra deferrable capability is added as deferred
    caps_on = custom_reg.build_capabilities_for_expert(
        ["housing_adequation_territoire"], defer_loading=True, expert="housing_expert"
    )
    assert len(caps_on) == 2
    # Hot-start capability
    assert caps_on[0].id == "housing_adequation_territoire"
    assert caps_on[0].defer_loading is False
    assert [t.__name__ for t in caps_on[0].tools] == ["search_places_batch_tool"]
    # Deferrable capability (tools deduplicated: search_places_batch_tool already registered on eager cap)
    assert caps_on[1].id == "housing_extra_deferrable"
    assert caps_on[1].defer_loading is True
    assert [t.__name__ for t in caps_on[1].tools] == ["compute_routes_tool"]


def test_registry_live_agent_toolsets_subset_of_capabilities():
    """Verifies that all tools statically configured on actual agent instances are in their adequation capability."""
    registry = CapabilityRegistry()
    expert_agents = {
        "job_hunter": (job_hunter_agent, "job_adequation_territoire"),
        "healthcare_expert": (healthcare_expert_agent, "healthcare_adequation_territoire"),
        "education_expert": (education_expert_agent, "education_adequation_territoire"),
        "housing_expert": (housing_expert_agent, "housing_adequation_territoire"),
        "mobility_expert": (mobility_expert_agent, "mobility_adequation_territoire"),
        "social_integration_expert": (
            social_integration_expert_agent,
            "social_adequation_territoire",
        ),
    }
    for _exp_name, (agent, cap_id) in expert_agents.items():
        spec = registry.get(cap_id)
        agent_static_tools = set(agent._function_toolset.tools.keys())
        # Every static tool exposed by the live agent must be declared in the capability
        assert agent_static_tools.issubset(set(spec.tools)), (
            f"Agent static tools {agent_static_tools} not subset of {spec.id} tools {spec.tools}"
        )


def test_registry_get_base_capability_id_all_experts():
    """Validates that get_base_capability_id maps all 6 expert domains to existing adéquation capabilities."""
    registry = get_default_capability_registry()
    for expert in VALID_EXPERTS:
        base_cap_id = registry.get_base_capability_id(expert)
        assert EXPERT_DEFAULT_CAPABILITY[expert] == base_cap_id
        assert base_cap_id in registry
        spec = registry.get(base_cap_id)
        assert expert in spec.experts
        assert base_cap_id.endswith("_adequation_territoire")

    assert registry.get_base_capability_id("job_hunter") == "job_adequation_territoire"
    assert (
        registry.get_base_capability_id("social_integration_expert")
        == "social_adequation_territoire"
    )

    with pytest.raises(CapabilityValidationError):
        registry.get_base_capability_id("unknown_expert")


def test_registry_build_capabilities_expert_validation():
    """Validates that build_capabilities_for_expert enforces expert authorization boundaries."""
    registry = get_default_capability_registry()

    # Valid expert assignment succeeds
    caps = registry.build_capabilities_for_expert(
        ["housing_adequation_territoire"], expert="housing_expert"
    )
    assert len(caps) == 1
    assert caps[0].id == "housing_adequation_territoire"

    # Cross-expert assignment must fail closed
    with pytest.raises(CapabilityValidationError) as exc_info:
        registry.build_capabilities_for_expert(
            ["housing_adequation_territoire"], expert="job_hunter"
        )
    assert "not permitted for expert 'job_hunter'" in str(exc_info.value)

    # Invalid expert name must fail closed
    with pytest.raises(CapabilityValidationError):
        registry.build_capabilities_for_expert(
            ["housing_adequation_territoire"], expert="invalid_expert"
        )


def test_registry_build_capabilities_multi_capability_tool_deduplication():
    """Validates that overlapping tools across multiple capabilities are registered only once."""
    registry = get_default_capability_registry()

    # job_adequation_territoire and job_extend_job_search share search_referentiels_batch_tool,
    # search_job_offers_batch_tool, and search_inclusion_jobs_batch_tool
    caps = registry.build_capabilities_for_expert(
        ["job_adequation_territoire", "job_extend_job_search"],
        expert="job_hunter",
    )
    assert len(caps) == 2

    # First capability gets its declared tools
    cap1_tools = [t.__name__ for t in caps[0].tools]
    assert len(cap1_tools) == 5

    # Second capability has duplicate tools omitted so no tool name collision occurs in PydanticAI
    cap2_tools = [t.__name__ for t in caps[1].tools]
    assert len(cap2_tools) == 0

    # Total unique tools across capabilities equals union of declared tools
    all_tools = cap1_tools + cap2_tools
    assert len(all_tools) == len(set(all_tools))


def test_registry_rejects_unknown_context_block_on_load(tmp_path: Path):
    """Validates that loading a capability with an unknown context block raises CapabilityValidationError."""
    bad_cap_file = tmp_path / "bad_context_cap.md"
    bad_cap_file.write_text(
        """---
id: bad_context_cap
name: Bad Context
description: Testing unknown context
experts:
  - housing_expert
contexts:
  - unknown.context.block
loading_policy: eager_only
---
Instructions
""",
        encoding="utf-8",
    )
    with pytest.raises(CapabilityValidationError) as exc_info:
        CapabilityRegistry(capabilities_dir=tmp_path)
    assert "Unknown context block 'unknown.context.block'" in str(exc_info.value)


def test_registry_accepts_valid_context_blocks_on_load(tmp_path: Path):
    """Validates that loading a capability with valid context blocks succeeds."""
    good_cap_file = tmp_path / "good_context_cap.md"
    good_cap_file.write_text(
        """---
id: good_context_cap
name: Good Context
description: Testing valid context
experts:
  - housing_expert
contexts:
  - commune.housing
loading_policy: eager_only
---
Instructions
""",
        encoding="utf-8",
    )
    registry = CapabilityRegistry(capabilities_dir=tmp_path)
    assert "good_context_cap" in registry




