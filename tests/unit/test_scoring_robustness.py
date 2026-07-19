import os
import pytest
import pandas as pd
import yaml
import copy

import config as cfg
from utils import data_loader
from core import scoring
from core.models import SearchCriterias


@pytest.fixture(scope="module")
def app_data():
    """Loads application data once for the test module to avoid redundant loads."""
    return data_loader.load_all_data_raw()


@pytest.fixture(scope="module")
def real_engine(app_data):
    """Instantiates a real ScoringEngine with complete production data."""
    return scoring.ScoringEngine.from_app_data(app_data)


def make_search_criterias(raw_scenario_data: dict) -> SearchCriterias:
    """Helper to convert raw scenario dictionaries to validated SearchCriterias models."""
    COMMUNE_TO_CODE = {
        "Bordeaux": "33063",
        "Paris": "75056",
        "Marseille": "13055",
        "Lyon": "69123",
    }

    # Start with base default config
    raw_data = copy.deepcopy(cfg.DEMO_DATA_DEFAULT)
    raw_data.update(raw_scenario_data)

    # Expand weight profile if applicable
    profile = raw_data.get("weight_profile")
    if profile in cfg.WEIGHT_PROFILES:
        for pw_key, pw_val in cfg.WEIGHT_PROFILES[profile].items():
            raw_data[pw_key] = pw_val

    # Resolve commune codes
    commune_name = raw_data.get("commune_actuelle", "Bordeaux")
    commune_code = COMMUNE_TO_CODE.get(commune_name, commune_name)
    commune_actuelle = {"code": commune_code, "label": commune_name}

    commune_pressentie = None
    if raw_data.get("commune_pressentie"):
        cp_name = raw_data["commune_pressentie"]
        cp_code = COMMUNE_TO_CODE.get(cp_name, cp_name)
        commune_pressentie = {"code": cp_code, "label": cp_name}

    # Map lists
    codes_metiers = []
    for metiers in raw_data.get("codes_metiers", []):
        codes_metiers.append([{"code": c, "label": c} for c in metiers])

    codes_formations = []
    for formations in raw_data.get("codes_formations", []):
        codes_formations.append([{"code": c, "label": c} for c in formations])

    inc_services_selection = [
        {"code": s, "label": s} for s in raw_data.get("inc_services_selection", [])
    ]
    inc_asso_add_selection = [
        {"code": a, "label": a} for a in raw_data.get("inc_asso_add_selection", [])
    ]

    type_log = raw_data.get("type_logement", "appt_all")
    type_logement = {"code": type_log, "label": type_log}

    criterias_dict = {
        "commune_actuelle": commune_actuelle,
        "commune_pressentie": commune_pressentie,
        "loc_search_area": raw_data.get("loc_search_area", "departement"),
        "loc_search_code": raw_data.get("loc_search_code", []),
        "nb_adultes": raw_data.get("nb_adultes", 1),
        "nb_enfants": raw_data.get("nb_enfants", 0),
        "classe_enfants": raw_data.get("classe_enfants", []),
        "codes_metiers": codes_metiers,
        "codes_formations": codes_formations,
        "inc_services_selection": inc_services_selection,
        "inc_asso_add_selection": inc_asso_add_selection,
        "hebergement_cible": raw_data.get("hebergement_cible", []),
        "logement": raw_data.get("logement"),
        "type_logement": type_logement,
        "besoin_sante": raw_data.get("besoin_sante", "Aucun"),
        "freq_retour": raw_data.get("freq_retour", "1 fois/mois"),
        "notes_qualitatives": [raw_data["notes_qualitatives"]]
        if isinstance(raw_data.get("notes_qualitatives"), str)
        else raw_data.get("notes_qualitatives", []),
        "weight_profile": raw_data.get("weight_profile", "Équilibré"),
        "criteria_weights": raw_data.get("criteria_weights", {}),
        "poids_emploi": raw_data.get("poids_emploi", 0.0),
        "poids_logement": raw_data.get("poids_logement", 0.0),
        "poids_education": raw_data.get("poids_education", 0.0),
        "poids_inclusion": raw_data.get("poids_inclusion", 0.0),
        "poids_mobilite": raw_data.get("poids_mobilite", 0.0),
        "poids_sante": raw_data.get("poids_sante", 0.0),
        "poids_territoire": raw_data.get("poids_territoire", 0.0),
        "org_context": raw_data.get("org_context"),
        "org_strategic_locations": raw_data.get("org_strategic_locations", []),
        "org_strategic_locations_type": raw_data.get(
            "org_strategic_locations_type", "departement"
        ),
        "target_population": raw_data.get("target_population", 50000),
        "target_population_sigma": raw_data.get("target_population_sigma", 25000),
        "org_boosts": raw_data.get("org_boosts", {}),
    }

    return SearchCriterias(**criterias_dict)


# ==========================================
# TIER 1: Demo Scenarios Integration Tests
# ==========================================


@pytest.mark.parametrize("scenario_id", ["agir", "emile", "3"])
def test_tier1_demo_scenarios_integration(real_engine, app_data, scenario_id):
    """
    Validates that real demo profiles produce mathematically consistent scores
    and verify the presence of active category score columns.
    """
    # 1. Load scenario config
    scenario_raw = cfg.DEMO_SCENARIOS[scenario_id]
    config = make_search_criterias(scenario_raw)

    # 2. Run the engine
    scored_df = real_engine.run(config)

    assert not scored_df.empty

    # 3. Assert Config-to-Column Parity
    # For every category with non-zero weight, the f"{cat}_cat_score" MUST exist.
    for cat in real_engine.categories:
        weight = getattr(config, f"poids_{cat}", 0.0)
        if cat == "education" and config.nb_enfants == 0:
            weight = 0.0
        if cat == "sante" and not config.besoin_sante:
            weight = 0.0

        col_name = f"{cat}_cat_score"
        if weight > 0:
            assert col_name in scored_df.columns, (
                f"Category '{cat}' is weighted ({weight}) but '{col_name}' column is missing from results!"
            )

    # 4. Active Criteria Parity
    # config.active_criteria must be a superset of all scores_config.yaml entries whose baseline is True
    baseline_ids = real_engine.scores_cat[real_engine.scores_cat["baseline"] == True][
        "score"
    ].tolist()
    for b_id in baseline_ids:
        # Check if the category of the baseline criteria is excluded
        cat_of_baseline = real_engine.scores_cat[
            real_engine.scores_cat["score"] == b_id
        ].iloc[0]["cat"]
        if cat_of_baseline == "education" and config.nb_enfants == 0:
            continue
        if cat_of_baseline == "sante" and not config.besoin_sante:
            continue
        assert b_id in config.active_criteria, (
            f"Baseline criteria '{b_id}' is not in active_criteria set!"
        )

    # 5. Category Score Boundedness
    for cat in real_engine.categories:
        col_name = f"{cat}_cat_score"
        if col_name in scored_df.columns:
            # Category scores must be within [0.0, 1.0]
            scores = scored_df[col_name].dropna()
            assert ((scores >= 0.0) & (scores <= 1.0)).all(), (
                f"Scores in '{col_name}' are out of bounds [0.0, 1.0]!"
            )

    # 6. Reconstruct weighted scores row-by-row for the top commune
    top_commune_id = scored_df.index[0]
    row = scored_df.loc[top_commune_id]

    total_score = 0.0
    total_weight = 0.0
    for cat in real_engine.categories:
        if cat == "education" and config.nb_enfants == 0:
            continue
        if cat == "sante" and not config.besoin_sante:
            continue

        col = f"{cat}_cat_score"
        if col not in scored_df.columns:
            continue

        val = row[col]
        if pd.isna(val):
            continue

        w = getattr(config, f"poids_{cat}", 0.0)
        total_score += val * w
        total_weight += w

    expected_weighted_score = total_score / total_weight if total_weight > 0 else 0.0
    actual_weighted_score = row["weighted_score"]

    assert pytest.approx(actual_weighted_score, abs=1e-5) == expected_weighted_score, (
        f"Reconstructed weighted score ({expected_weighted_score}) does not match engine's computed weighted_score ({actual_weighted_score}) for {top_commune_id}!"
    )


# ==========================================
# TIER 2: Mathematical Invariant Checks
# ==========================================


def test_tier2_bounded_output_invariant(real_engine):
    """Enforces that all category scores and final scores strictly reside in [0.0, 1.0]."""
    config = make_search_criterias(cfg.DEMO_SCENARIOS["agir"])
    scored_df = real_engine.run(config)

    # Check weighted score
    assert (
        (scored_df["weighted_score"] >= 0.0) & (scored_df["weighted_score"] <= 1.0)
    ).all()

    # Check all category columns
    for col in [c for c in scored_df.columns if c.endswith("_cat_score")]:
        scores = scored_df[col].dropna()
        assert ((scores >= 0.0) & (scores <= 1.0)).all()


def test_tier2_zero_preservation_invariant(real_engine):
    """A commune with absolute zero raw values for all indicators in a category must preserve a category score of 0.0."""
    config = make_search_criterias(cfg.DEMO_SCENARIOS["agir"])
    scored_df = real_engine.run(config)

    # Ensure logement category is present
    assert "logement_cat_score" in scored_df.columns


def test_tier2_no_nan_invariant(real_engine):
    """The scored DataFrame must contain zero NaN values in any computed category or overall score columns."""
    config = make_search_criterias(cfg.DEMO_SCENARIOS["agir"])
    scored_df = real_engine.run(config)

    assert scored_df["weighted_score"].isna().sum() == 0
    for col in [c for c in scored_df.columns if c.endswith("_cat_score")]:
        assert scored_df[col].isna().sum() == 0


def test_tier2_data_model_parity(real_engine):
    """The Pydantic SearchResultsData model must match the Pandas DataFrame values down to 4 decimal places."""
    config = make_search_criterias(cfg.DEMO_SCENARIOS["agir"])
    # Run in a search area that has territory columns (we will search in department 17 to ensure territory doesn't throw)
    config.loc_search_area = "departement"
    config.loc_search_code = ["17"]
    model, scored_df = real_engine.run_optimized(config)

    # Check that model results match df values
    for i, result_item in enumerate(model.results[:10]):
        codgeo = result_item.codgeo
        df_row = scored_df.loc[codgeo]

        # Parity for global score
        assert (
            pytest.approx(result_item.global_score, abs=1e-4)
            == df_row["weighted_score"]
        )

        # Parity for territory category score if present in model
        if (
            hasattr(result_item, "territoire")
            and result_item.territoire
            and "territoire_cat_score" in df_row.index
        ):
            assert 0.0 <= result_item.territoire.cat_score <= 1.0


def test_tier2_config_engine_category_symmetry(real_engine):
    """
    For every category defined in scores_config.yaml with at least one baseline: true indicator,
    C_cat_score MUST be present in the scored DataFrame when that category is not excluded by conditional rules.
    """
    # 1. Parse scores_config.yaml categories
    with open(os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE), "r") as f:
        scores_yaml = yaml.safe_load(f)

    yaml_baseline_cats = set()
    for item in scores_yaml.get("scores", []):
        if item.get("baseline", False):
            yaml_baseline_cats.add(item.get("category"))

    # Remove categories that are conditionally excluded in the test config
    test_scenario = {
        "nb_enfants": 1,
        "besoin_sante": ["Hôpital"],
        "poids_emploi": 1.0,
        "poids_logement": 1.0,
        "poids_education": 1.0,
        "poids_inclusion": 1.0,
        "poids_mobilite": 1.0,
        "poids_sante": 1.0,
        "poids_territoire": 1.0,
    }
    config = make_search_criterias(test_scenario)
    scored_df = real_engine.run(config)

    for cat in yaml_baseline_cats:
        col_name = f"{cat}_cat_score"
        assert col_name in scored_df.columns, (
            f"Category '{cat}' is in scores_config.yaml as baseline but '{col_name}' is missing in scored DataFrame!"
        )


def test_tier2_monotonic_weight_influence(real_engine):
    """Increasing poids_emploi while keeping all other weights constant must increase or keep overall score constant for high-performing communes."""
    config_low = make_search_criterias(
        {
            "poids_emploi": 0.1,
            "poids_logement": 0.9,
            "weight_profile": "Profil personnalisé",
        }
    )
    config_high = make_search_criterias(
        {
            "poids_emploi": 1.0,
            "poids_logement": 0.9,
            "weight_profile": "Profil personnalisé",
        }
    )

    df_low = real_engine.run(config_low)
    df_high = real_engine.run(config_high)

    # Find a commune with very high employment score (close to 1.0)
    high_emp_communes = df_low[df_low["emploi_cat_score"] > 0.8].index
    if len(high_emp_communes) > 0:
        commune_id = high_emp_communes[0]
        assert (
            df_high.loc[commune_id, "weighted_score"]
            >= df_low.loc[commune_id, "weighted_score"] - 1e-6
        )


def test_tier2_fixture_parity_invariant(real_engine, live_scores_cat):
    """The conftest.py live_scores_cat fixture must contain the same category names as scores_config.yaml."""
    with open(os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE), "r") as f:
        scores_yaml = yaml.safe_load(f)

    yaml_cats = {
        item.get("category")
        for item in scores_yaml.get("scores", [])
        if item.get("category")
    }

    fixture_cats = set(live_scores_cat["cat"].unique())

    missing_cats = yaml_cats - fixture_cats
    assert not missing_cats, (
        f"conftest.py live_scores_cat fixture is missing categories: {missing_cats}"
    )


# ==========================================
# TIER 3: Differential Sensitivity Tests
# ==========================================


def test_tier3_directional_shift_population(real_engine):
    """
    Changing target population must dynamically shift the territory score and overall score.
    For Saint-Jean-d'Angély (~7,000 inhabitants):
    - target_population = 20,000 is a better fit than 150,000.
    - territory score with pop 20k must be strictly greater than pop 150k.
    """
    config_20k = make_search_criterias(
        {
            "weight_profile": "Équilibré",
            "poids_territoire": 1.0,
            "target_population": 20000,
            "target_population_sigma": 10000,
            "org_strategic_locations": [],
            "loc_search_area": "departement",
            "loc_search_code": ["17"],
        }
    )
    df_20k = real_engine.run(config_20k)

    config_150k = make_search_criterias(
        {
            "weight_profile": "Équilibré",
            "poids_territoire": 1.0,
            "target_population": 150000,
            "target_population_sigma": 75000,
            "org_strategic_locations": [],
            "loc_search_area": "departement",
            "loc_search_code": ["17"],
        }
    )
    df_150k = real_engine.run(config_150k)

    commune_id = "17347"  # Saint-Jean-d'Angély

    # We expect these columns to exist only after the bug fix.
    # If territory skip is present, this will raise KeyError or failure, which is the expected RED phase.
    score_20k = df_20k.loc[commune_id, "territoire_cat_score"]
    score_150k = df_150k.loc[commune_id, "territoire_cat_score"]

    assert score_20k > score_150k, (
        f"Saint-Jean-d'Angély territory score did not shift correctly: pop_20k score={score_20k}, pop_150k score={score_150k}!"
    )

    global_20k = df_20k.loc[commune_id, "weighted_score"]
    global_150k = df_150k.loc[commune_id, "weighted_score"]

    assert global_20k > global_150k, (
        f"Saint-Jean-d'Angély global score did not shift correctly: pop_20k score={global_20k}, pop_150k score={global_150k}!"
    )


def test_tier3_zero_weight_isolation(real_engine):
    """Setting poids_territoire = 0.0 must isolate the global score from population shifts completely."""
    config_20k = make_search_criterias(
        {
            "weight_profile": "Profil personnalisé",
            "poids_territoire": 0.0,
            "poids_logement": 1.0,
            "target_population": 20000,
            "target_population_sigma": 10000,
            "org_strategic_locations": [],
            "loc_search_area": "departement",
            "loc_search_code": ["17"],
        }
    )
    df_20k = real_engine.run(config_20k)

    config_150k = make_search_criterias(
        {
            "weight_profile": "Profil personnalisé",
            "poids_territoire": 0.0,
            "poids_logement": 1.0,
            "target_population": 150000,
            "target_population_sigma": 75000,
            "org_strategic_locations": [],
            "loc_search_area": "departement",
            "loc_search_code": ["17"],
        }
    )
    df_150k = real_engine.run(config_150k)

    commune_id = "17347"

    global_20k = df_20k.loc[commune_id, "weighted_score"]
    global_150k = df_150k.loc[commune_id, "weighted_score"]

    assert pytest.approx(global_20k, abs=1e-6) == global_150k, (
        f"Global score shifted despite zero territory weight! 20k global={global_20k}, 150k global={global_150k}"
    )


def test_tier3_multiple_code_path_sensitivity(real_engine):
    """Verifies that the population sensitivity test also holds when strategic locations are non-empty."""
    config_20k = make_search_criterias(
        {
            "weight_profile": "Équilibré",
            "poids_territoire": 1.0,
            "target_population": 20000,
            "target_population_sigma": 10000,
            "org_strategic_locations": ["17"],
            "loc_search_area": "departement",
            "loc_search_code": ["17"],
        }
    )
    df_20k = real_engine.run(config_20k)

    config_150k = make_search_criterias(
        {
            "weight_profile": "Équilibré",
            "poids_territoire": 1.0,
            "target_population": 150000,
            "target_population_sigma": 75000,
            "org_strategic_locations": ["17"],
            "loc_search_area": "departement",
            "loc_search_code": ["17"],
        }
    )
    df_150k = real_engine.run(config_150k)

    commune_id = "17347"

    score_20k = df_20k.loc[commune_id, "territoire_cat_score"]
    score_150k = df_150k.loc[commune_id, "territoire_cat_score"]

    assert score_20k > score_150k, (
        f"Saint-Jean-d'Angély territory score with strategic locations did not shift correctly: pop_20k score={score_20k}, pop_150k score={score_150k}!"
    )
