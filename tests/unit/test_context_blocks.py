"""Unit tests for Context Blocks, Context Registry, and Context Renderer.

Validates that:
1. Core blocks, domain blocks, and conditional blocks render correctly.
2. ContextRegistry enforces strict fail-closed lookup and handles canonical IDs and aliases.
3. Stable ordering rules are respected (core blocks in fixed sequence, domain blocks sorted).
4. Assembly from CapabilitySpec declared contexts works seamlessly across all 8 capabilities.
5. Territory conditional block only activates when security or political flags are set.
"""

import json
import pytest

from agents.capability_registry import get_default_capability_registry
from agents.context_blocks import (
    CORE_BLOCK_ORDER,
    ContextBlock,
    ContextBlockNotFoundError,
    ContextBlockValidationError,
    assemble_context_blocks,
    get_default_context_registry,
    render_context_blocks,
    reset_default_context_registry,
)
from agents.context_renderer import (
    format_detail_item,
    format_inclusion_services,
    resolve_focus_city,
)
from agents.state import GraphState, ODISContextBuilder, SearchResultsData
from core.models import (
    AssociationDetail,
    CommuneResult,
    CommuneScoreDetail,
    CriteriaItem,
    EducationMetrics,
    EmploymentMetrics,
    HealthMetrics,
    HousingMetrics,
    InclusionMetrics,
    InclusionServiceDetail,
    JobOfferDetail,
    MobilityMetrics,
    SearchCriterias,
    TerritoryMetrics,
)


@pytest.fixture(autouse=True)
def clean_registry():
    """Ensures each test has a pristine ContextRegistry."""
    reset_default_context_registry()
    yield
    reset_default_context_registry()


def _make_sample_state() -> GraphState:
    criteria = SearchCriterias(
        nb_adultes=2,
        nb_enfants=1,
        odis_brief="Famille avec 1 enfant cherchant école et gare.",
        metiers_souhaites=["Boulanger"],
    )
    commune = CommuneResult(
        codgeo="33063",
        name="Bordeaux",
        population=260000,
        name_bdv="Bordeaux",
        global_score=0.85,
        score_besoins=0.88,
        coeff_population_gauss=0.97,
        education=EducationMetrics(
            cat_score=0.9,
            facility_counts={"ecoles_elementaires": 8},
            facility_details={"ecoles_elementaires": ["École Gambetta"]},
        ),
        housing=HousingMetrics(
            cat_score=0.7,
            price_per_sqm=19.5,
            loyer_moyen_t2=680.0,
            loyer_moyen_t3=890.0,
        ),
        mobility=MobilityMetrics(
            cat_score=0.92,
            has_train_station=True,
            gare_tgv=True,
        ),
        health=HealthMetrics(
            cat_score=0.84,
            apl_medecins=3.6,
        ),
        employment=EmploymentMetrics(
            cat_score=0.78,
            matching_job_offers=[
                [
                    JobOfferDetail(
                        id="OFFER_01",
                        title="Chef Boulanger",
                        company="Maison Levain",
                        contract_type="CDI",
                        rome_code="D1102",
                        location="Bordeaux",
                    )
                ]
            ],
        ),
        inclusion=InclusionMetrics(
            cat_score=0.82,
            associations_refugies=[
                AssociationDetail(
                    id="ASSO_01",
                    name="Entraide Réfugiés",
                    description="Accompagnement administratif",
                )
            ],
        ),
    )
    return GraphState(
        search_criteria=criteria,
        focus_city=commune,
        odis_brief=criteria.odis_brief,
    )


# ==============================================================================
# 1. Core Blocks Tests
# ==============================================================================


def test_core_briefing_block_renders_narrative_and_envelope():
    reg = get_default_context_registry()
    block = reg.get("core.briefing")
    state = _make_sample_state()

    # Raw payload is string narrative
    raw = block.render_raw(state)
    assert raw == "Famille avec 1 enfant cherchant école et gare."

    # Rendered dict is envelope
    rendered = block.render(state)
    assert rendered == {
        "Résumé du dossier (Briefing)": "Famille avec 1 enfant cherchant école et gare."
    }


def test_core_briefing_block_handles_empty_briefing():
    reg = get_default_context_registry()
    block = reg.get("core.briefing")
    state = GraphState(odis_brief="")

    assert block.render_raw(state) == "Aucun briefing particulier renseigné."
    assert block.render(state) == {}


def test_core_criteria_block_renders_readable_labels():
    reg = get_default_context_registry()
    block = reg.get("core.criteria")
    state = _make_sample_state()

    raw = block.render_raw(state)
    assert isinstance(raw, dict)
    assert "Nombre d'adultes" in raw
    assert raw["Nombre d'adultes"] == 2
    assert "odis_brief" not in raw
    assert "poids_emploi" not in raw

    rendered = block.render(state)
    assert "Critères de recherche" in rendered
    assert rendered["Critères de recherche"] == raw


def test_core_criteria_block_handles_none_criteria():
    reg = get_default_context_registry()
    block = reg.get("core.criteria")
    state = GraphState(search_criteria=None)

    assert block.render_raw(state) == {}
    assert block.render(state) == {}


def test_core_commune_identity_block():
    reg = get_default_context_registry()
    block = reg.get("core.commune_identity")
    state = _make_sample_state()

    raw = block.render_raw(state)
    assert raw["Code INSEE"] == "33063"
    assert raw["Nom"] == "Bordeaux"
    assert raw["Population"] == 260000
    assert raw["Score global"] == 85
    assert raw["Adéquation besoins"] == 88

    rendered = block.render(state)
    assert "Commune analysée (Identité)" in rendered
    assert rendered["Commune analysée (Identité)"] == raw


def test_core_commune_identity_block_handles_none_city():
    reg = get_default_context_registry()
    block = reg.get("core.commune_identity")
    state = GraphState(focus_city=None)

    assert block.render_raw(state) == {}
    assert block.render(state) == {}


# ==============================================================================
# 2. Domain Blocks & Aliases Tests
# ==============================================================================


@pytest.mark.parametrize(
    "block_id,alias,expected_key",
    [
        ("commune.housing", "housing", "Données logement"),
        ("commune.mobility", "mobility", "Données mobilité"),
        ("commune.healthcare", "commune.health", "Données santé"),
        ("commune.healthcare", "health", "Données santé"),
        ("commune.healthcare", "healthcare", "Données santé"),
        ("commune.education", "education", "Données éducation"),
        ("commune.social", "commune.inclusion", "Données inclusion"),
        ("commune.social", "social", "Données inclusion"),
        ("commune.social", "inclusion", "Données inclusion"),
        ("commune.job", "commune.employment", "Données emploi et formation"),
        ("commune.job", "job", "Données emploi et formation"),
        ("commune.job", "employment", "Données emploi et formation"),
    ],
)
def test_domain_blocks_canonical_and_alias_resolution(block_id, alias, expected_key):
    reg = get_default_context_registry()
    state = _make_sample_state()

    # Both canonical ID and alias resolve to the same block
    block_canonical = reg.get(block_id)
    block_alias = reg.get(alias)
    assert block_canonical.id == block_alias.id

    rendered = block_alias.render(state)
    assert expected_key in rendered
    assert len(rendered[expected_key]) > 0


# ==============================================================================
# 3. Conditional Territory Block Tests
# ==============================================================================


def test_territory_block_emits_only_when_flags_are_active():
    reg = get_default_context_registry()
    block = reg.get("commune.territory")

    # State without territory alerts
    state_clean = _make_sample_state()
    assert block.render(state_clean) == {}
    assert block.render_raw(state_clean) == {}

    # State with maire_extreme_droite = True
    state_far_right = _make_sample_state()
    state_far_right.focus_city.territoire = TerritoryMetrics(maire_extreme_droite=True)
    rendered_far_right = block.render(state_far_right)
    assert "Données territoire (Contexte local)" in rendered_far_right
    assert rendered_far_right["Données territoire (Contexte local)"]["Maire d'extrême droite"] is True

    # State with ter_insecurite set
    state_insecure = _make_sample_state()
    state_insecure.focus_city.territoire = TerritoryMetrics(ter_insecurite=0.85)
    rendered_insecure = block.render(state_insecure)
    assert "Données territoire (Contexte local)" in rendered_insecure
    assert "Taux d'insécurité" in str(rendered_insecure) or "ter_insecurite" in str(rendered_insecure) or 0.85 in rendered_insecure["Données territoire (Contexte local)"].values()


def test_territory_block_alias_resolution():
    reg = get_default_context_registry()
    block = reg.get("territory")
    assert block.id == "commune.territory"


# ==============================================================================
# 4. ContextRegistry Error Handling & Invariants
# ==============================================================================


def test_registry_raises_on_unknown_block():
    reg = get_default_context_registry()
    with pytest.raises(ContextBlockNotFoundError) as exc_info:
        reg.get("unknown.nonexistent")
    assert "unknown.nonexistent" in str(exc_info.value)
    assert "Available canonical blocks" in str(exc_info.value)


def test_registry_raises_on_empty_block_id():
    reg = get_default_context_registry()
    with pytest.raises(ContextBlockNotFoundError):
        reg.get("   ")


def test_registry_has_method():
    reg = get_default_context_registry()
    assert reg.has("commune.housing") is True
    assert reg.has("housing") is True
    assert reg.has("commune.health") is True
    assert reg.has("unknown") is False


def test_registry_rejects_duplicate_registration_without_overwrite():
    reg = get_default_context_registry()
    dup = ContextBlock(
        id="commune.housing",
        title="Duplicate",
        description="Duplicate",
        raw_renderer=lambda s, c: {},
        dict_renderer=lambda s, c: {},
    )
    with pytest.raises(ContextBlockValidationError):
        reg.register(dup, overwrite=False)


def test_registry_allows_overwrite():
    reg = get_default_context_registry()
    custom = ContextBlock(
        id="commune.housing",
        title="Logement personnalisé",
        description="Custom",
        raw_renderer=lambda s, c: "custom_raw",
        dict_renderer=lambda s, c: {"Logement personnalisé": "ok"},
    )
    reg.register(custom, overwrite=True)
    assert reg.get("commune.housing").title == "Logement personnalisé"


def test_registry_filter_by_family():
    reg = get_default_context_registry()
    core_blocks = reg.list_blocks(family="core")
    domain_blocks = reg.list_blocks(family="domain")
    cond_blocks = reg.list_blocks(family="conditional")

    assert {b.id for b in core_blocks} == {"core.briefing", "core.criteria", "core.commune_identity"}
    assert "commune.housing" in {b.id for b in domain_blocks}
    assert {b.id for b in cond_blocks} == {"commune.territory"}


# ==============================================================================
# 5. Ordering & Assembly Invariants
# ==============================================================================


def test_resolve_contexts_orders_core_first_and_sorts_domains():
    reg = get_default_context_registry()

    # Pass in scrambled order with aliases and duplicates
    input_ids = [
        "commune.social",
        "core.commune_identity",
        "housing",
        "core.briefing",
        "commune.education",
        "core.criteria",
        "commune.housing",  # duplicate
    ]

    resolved = reg.resolve_contexts(input_ids)
    resolved_ids = [b.id for b in resolved]

    # Core blocks must appear first in stable CORE_BLOCK_ORDER
    assert resolved_ids[:3] == list(CORE_BLOCK_ORDER)

    # Domain blocks must be deduplicated and sorted by canonical ID
    assert resolved_ids[3:] == [
        "commune.education",
        "commune.housing",
        "commune.social",
    ]


def test_assemble_context_blocks_function():
    state = _make_sample_state()
    assembled = assemble_context_blocks(["commune.housing", "commune.mobility"], state)

    assert "Données logement" in assembled
    assert "Données mobilité" in assembled
    assert len(assembled) == 2


def test_render_context_blocks_function_compact_and_indented():
    state = _make_sample_state()
    compact = render_context_blocks(["commune.housing"], state, compact=True)
    indented = render_context_blocks(["commune.housing"], state, compact=False)

    assert "\n" not in compact
    assert "\n" in indented
    assert '":{' in compact or '":{"' in compact
    assert json.loads(compact) == json.loads(indented)


# ==============================================================================
# 6. Compatibility with All 8 Capabilities from Registry
# ==============================================================================


def test_assemble_all_8_capabilities_contexts_without_error():
    """Verifies that every capability in the CapabilityRegistry declares contexts

    that are valid and assemble smoothly with a realistic state.
    """
    cap_reg = get_default_capability_registry()
    state = _make_sample_state()

    for spec in cap_reg.list_all():
        assert len(spec.contexts) >= 1, f"Capability {spec.id} has no contexts declared"
        assembled = assemble_context_blocks(spec.contexts, state)
        assert isinstance(assembled, dict)
        assert len(assembled) >= 1, f"Capability {spec.id} assembled empty context: {spec.contexts}"


# ==============================================================================
# 7. Iso-fidelity with ODISContextBuilder and Non-Expert Paths
# ==============================================================================


def test_expert_prompt_contexts_uses_blocks_seamlessly():
    state = _make_sample_state()
    for expert_name in ODISContextBuilder.DOMAIN_EXPERTS:
        ctxs = ODISContextBuilder.expert_prompt_contexts(state, expert_name)
        assert ctxs.briefing == state.odis_brief
        assert "Code INSEE" in ctxs.commune
        assert "33063" in ctxs.commune
        assert len(ctxs.specific) > 2  # Not empty "{}"


def test_non_expert_agent_context_paths_intact():
    state = _make_sample_state()
    refiner_ctx = json.loads(ODISContextBuilder.agent_context(state, "refiner"))
    assert "Résumé du dossier (Briefing)" in refiner_ctx
    assert "Critères de recherche" in refiner_ctx

    interviewer_ctx = json.loads(ODISContextBuilder.agent_context(state, "interviewer"))
    assert "Critères identifiés" in interviewer_ctx
    assert "Résumé du dossier (Briefing)" not in interviewer_ctx

    ts_ctx = json.loads(ODISContextBuilder.agent_context(state, "ts_agent"))
    assert "Ville cible" in ts_ctx
    assert "Bordeaux (33063)" in ts_ctx["Ville cible"]


# ==============================================================================
# 8. Context Renderer Unit Isolations
# ==============================================================================


def test_format_detail_item_variants():
    # CriteriaItem
    ci = CriteriaItem(code="D1102", label="Boulanger")
    assert format_detail_item(ci) == "Boulanger"

    # AssociationDetail
    asso = AssociationDetail(id="W123", name="Asso Test", description="Aide")
    assert format_detail_item(asso) == "W123 | Asso Test | Aide"

    # JobOfferDetail
    offer = JobOfferDetail(
        id="JOB_99",
        title="Cuisinier",
        company="Resto",
        contract_type="CDI",
        rome_code="G1602",
        description="Poste chaud",
    )
    formatted_job = format_detail_item(offer)
    assert "JOB_99" in formatted_job
    assert "Cuisinier" in formatted_job
    assert "Resto" in formatted_job
    assert "CDI" in formatted_job
    assert "[ROME: G1602]" in formatted_job

    # InclusionServiceDetail
    srv = InclusionServiceDetail(
        id="SRV_1",
        name="Accompagnement",
        nom_structure="Structure Alpha",
        distance_km=2.5,
        commune_nom="Bordeaux",
    )
    formatted_srv = format_detail_item(srv)
    assert "Accompagnement" in formatted_srv
    assert "Structure Alpha" in formatted_srv
    assert "2.5 km" in formatted_srv

    # CommuneScoreDetail discrete
    disc = CommuneScoreDetail(
        score_id="gare",
        label="Gare SNCF",
        metric_type="discrete",
        status_label="Présence",
        score_normalise=1.0,
        relative_weight=15.0,
    )
    formatted_disc = format_detail_item(disc)
    assert "Gare SNCF: Présence (score: 1.0, poids relatif: 15.0%)" == formatted_disc

    # CommuneScoreDetail continuous
    cont = CommuneScoreDetail(
        score_id="loyer",
        label="Loyer moyen",
        metric_type="continuous",
        valeur_kpi=15.5,
        unit="€/m²",
        score_normalise=0.75,
        relative_weight=20.0,
    )
    formatted_cont = format_detail_item(cont)
    assert "Loyer moyen: 15.5 €/m², score: 0.75, poids relatif: 20.0%" == formatted_cont


def test_format_inclusion_services_deduplicates_by_structure():
    srv1 = InclusionServiceDetail(
        id="S1",
        name="Aide 1",
        nom_structure="Centre Social",
        distance_km=1.0,
        commune_nom="Bordeaux",
    )
    srv2 = InclusionServiceDetail(
        id="S2",
        name="Aide 2",
        nom_structure="Centre Social",
        distance_km=1.0,
        commune_nom="Bordeaux",
    )
    grouped = {"logement": [srv1, srv2]}
    res = format_inclusion_services(grouped)
    assert "logement" in res
    assert len(res["logement"]) == 1  # Deduplicated by structure
    assert res["logement"][0] == "Centre Social (1.0 km - Bordeaux)"


def test_resolve_focus_city_with_search_results():
    city1 = CommuneResult(codgeo="33063", name="Bordeaux")
    city2 = CommuneResult(codgeo="31555", name="Toulouse")
    results = SearchResultsData(results=[city1, city2])
    state = GraphState(focus_city=CommuneResult(codgeo="31555", name="Toulouse Simple"), search_results=results)

    resolved = resolve_focus_city(state)
    assert resolved.codgeo == "31555"
    assert resolved.name == "Toulouse"


# ==============================================================================
# 9. Robustness & Edge Cases
# ==============================================================================


def test_commune_score_detail_with_none_unit_does_not_crash():
    """Verifies that format_detail_item handles CommuneScoreDetail with unit=None safely."""
    dummy = type(
        "CommuneScoreDetail",
        (),
        {
            "label": "Score Test",
            "metric_type": "continuous",
            "valeur_kpi": 42,
            "unit": None,
            "score_normalise": 0.8,
            "relative_weight": 10.0,
        },
    )()
    formatted = format_detail_item(dummy)
    assert "Score Test: 42" in formatted
    assert "score: 0.8" in formatted


def test_format_inclusion_services_none_and_empty():
    """Verifies that format_inclusion_services handles None and empty dict safely."""
    assert format_inclusion_services(None) == {}
    assert format_inclusion_services({}) == {}


def test_resolve_focus_city_with_string_focus_city():
    """Verifies that resolve_focus_city safely converts a string focus_city to CommuneResult."""
    class SimpleState:
        focus_city = "Bordeaux"
        search_results = None

    resolved = resolve_focus_city(SimpleState())
    assert isinstance(resolved, CommuneResult)
    assert resolved.name == "Bordeaux"
    assert resolved.codgeo == ""


def test_context_block_trims_and_validates_id_and_aliases():
    """Verifies that ContextBlock trims whitespace, rejects empty, and deduplicates aliases."""
    block = ContextBlock(
        id="  commune.custom  ",
        title="Custom Title",
        description="Custom Desc",
        aliases=["  alias1  ", "alias2", "alias1"],
        raw_renderer=lambda s, c: {},
        dict_renderer=lambda s, c: {},
    )
    assert block.id == "commune.custom"
    assert block.aliases == ["alias1", "alias2"]

    with pytest.raises(ValueError, match="Context block ID cannot be empty"):
        ContextBlock(
            id="   ",
            title="T",
            description="D",
            raw_renderer=lambda s, c: {},
            dict_renderer=lambda s, c: {},
        )

    with pytest.raises(ValueError, match="Context block alias cannot be empty"):
        ContextBlock(
            id="commune.custom2",
            title="T",
            description="D",
            aliases=["   "],
            raw_renderer=lambda s, c: {},
            dict_renderer=lambda s, c: {},
        )


def test_registry_rejects_block_id_colliding_with_existing_alias():
    """Verifies that registering a block whose ID collides with an existing alias is rejected."""
    reg = get_default_context_registry()
    # 'housing' is an alias of 'commune.housing'
    colliding = ContextBlock(
        id="housing",
        title="Collision",
        description="Desc",
        raw_renderer=lambda s, c: {},
        dict_renderer=lambda s, c: {},
    )
    with pytest.raises(ContextBlockValidationError, match="conflicts with existing alias mapping"):
        reg.register(colliding, overwrite=False)


def test_registry_rejects_alias_colliding_with_existing_canonical_id():
    """Verifies that registering an alias that matches an existing canonical block ID is rejected."""
    reg = get_default_context_registry()
    # 'commune.housing' is an existing canonical block ID
    colliding = ContextBlock(
        id="commune.custom_housing",
        title="Custom",
        description="Desc",
        aliases=["commune.housing"],
        raw_renderer=lambda s, c: {},
        dict_renderer=lambda s, c: {},
    )
    with pytest.raises(ContextBlockValidationError, match="conflicts with existing canonical block"):
        reg.register(colliding, overwrite=False)


def test_registry_overwriting_cleans_up_stale_aliases():
    """Verifies that overwriting a block prunes previous aliases that are no longer declared."""
    reg = get_default_context_registry()
    b1 = ContextBlock(
        id="commune.test_prune",
        title="Title",
        description="Desc",
        aliases=["old_alias_1", "old_alias_2"],
        raw_renderer=lambda s, c: {},
        dict_renderer=lambda s, c: {},
    )
    reg.register(b1)
    assert reg.has("old_alias_1") is True
    assert reg.has("old_alias_2") is True

    # Overwrite with only old_alias_1
    b2 = ContextBlock(
        id="commune.test_prune",
        title="Title Updated",
        description="Desc",
        aliases=["old_alias_1", "new_alias_3"],
        raw_renderer=lambda s, c: {},
        dict_renderer=lambda s, c: {},
    )
    reg.register(b2, overwrite=True)
    assert reg.has("old_alias_1") is True
    assert reg.has("new_alias_3") is True
    assert reg.has("old_alias_2") is False  # Pruned!


def test_registry_canonical_id_and_list_aliases():
    """Verifies get_canonical_id and list_aliases."""
    reg = get_default_context_registry()
    assert reg.get_canonical_id("housing") == "commune.housing"
    assert reg.get_canonical_id("commune.housing") == "commune.housing"
    assert reg.get_canonical_id("commune.health") == "commune.healthcare"

    alias_map = reg.list_aliases()
    assert "housing" in alias_map
    assert alias_map["housing"] == "commune.housing"


def test_multi_domain_capability_with_partially_hydrated_commune():
    """Verifies assembling multiple domain blocks when commune has partial data."""
    partially_hydrated = CommuneResult(
        codgeo="44109",
        name="Nantes",
        population=320000,
        housing=HousingMetrics(cat_score=0.8, price_per_sqm=16.0),
        inclusion=InclusionMetrics(cat_score=0.75, associations_refugies=[]),
    )
    # Simulate un-hydrated health metric (e.g. None)
    partially_hydrated.health = None  # type: ignore[assignment]
    state = GraphState(focus_city=partially_hydrated)

    assembled = assemble_context_blocks(
        ["commune.housing", "commune.healthcare", "commune.social"],
        state,
    )

    # Housing is present
    assert "Données logement" in assembled
    # Healthcare was None -> omitted from assembled dict
    assert "Données santé" not in assembled
    # Social is present
    assert "Données inclusion" in assembled

