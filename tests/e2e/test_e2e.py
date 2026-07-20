import pytest
import pandas as pd
import copy
from unittest.mock import patch

# Important: The tests in this file must be run from the 'app/' directory
# for the data paths to resolve correctly.
# Example: pytest tests/test_e2e.py

from config import DEMO_DATA_DEFAULT, DEMO_SCENARIOS
import config as cfg
from utils import data_loader
from core import scoring
from ui import forms as ui_forms
from ui import results as ui_results


def assert_results_logical_invariants(results: pd.DataFrame):
    """
    Asserts logical invariants on the scoring results:
    - Results are not empty.
    - 'weighted_score' column exists.
    - Weighted scores are within the [0.0, 1.0] range.
    - There are no NaN/null values in key score columns.
    - The results are sorted in descending order of 'weighted_score'.
    """
    assert not results.empty, "Results DataFrame should not be empty"
    assert "weighted_score" in results.columns, "'weighted_score' column is missing"

    # Check bounds
    scores = results["weighted_score"]
    assert (scores >= 0.0).all() and (scores <= 1.0).all(), (
        f"Weighted scores must be between 0.0 and 1.0, got min: {scores.min()}, max: {scores.max()}"
    )

    # Check no NaNs in key scoring/results columns
    key_cols = ["weighted_score", "libgeo"]
    for col in key_cols:
        if col in results.columns:
            assert results[col].notna().all(), f"Found NaN values in column '{col}'"

    # Check descending sort order
    is_descending = scores.is_monotonic_decreasing
    assert is_descending, "Results are not sorted in descending order of weighted_score"


@pytest.fixture(scope="module")
def app_data():
    """
    Loads the application data once for the entire test module.
    This is equivalent to st.session_state['app_data'] in the Streamlit app.
    """
    return data_loader.load_all_data_raw()


class MockSessionState(dict):
    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)

    def __setattr__(self, name, value):
        self[name] = value


def run_test_scenario(scenario_id, app_data):
    """
    Helper function to run a search scenario by simulating session state
    and calling the app's own logic.
    """
    # 1. Set up a mock session state dictionary
    mock_session_state = MockSessionState()

    # 2. Add app_data to the mock session state, as the UI functions expect it
    mock_session_state["app_data"] = app_data

    # 3. Load demo data into the session state with 'ui_' prefixes
    scenario_data = DEMO_SCENARIOS[scenario_id]
    default_data = copy.deepcopy(DEMO_DATA_DEFAULT)
    default_data.update(scenario_data)

    for key, value in default_data.items():
        if key == "commune_actuelle":
            mock_session_state["ui_commune"] = value
        elif key == "departement_actuel":
            mock_session_state["ui_departement"] = value
        elif key == "binome_penalty":
            # The UI slider uses percentage values (e.g., 50), but the config expects a float (e.g., 0.5)
            # The create_search_criterias_from_inputs function handles the division by 100.
            mock_session_state["ui_binome_penalty"] = (
                value * 100 if value <= 1 else value
            )
        # Handle list-based inputs for children's classes and professional goals
        elif key == "classe_enfants":
            for i, class_level in enumerate(value):
                mock_session_state[f"ui_classe_enfant_{i}"] = class_level
        elif key == "codes_metiers":
            for i, codes in enumerate(value):
                mock_session_state[f"ui_metiers_adult_{i}"] = codes
        elif key == "codes_formations":
            for i, codes in enumerate(value):
                mock_session_state[f"ui_formations_adult_{i}"] = codes
        elif key == "weight_profile":
            mock_session_state["ui_weight_profile"] = value
            # Expand profile weights into session state as data_loader.apply_search_criteria_to_ui would do
            if value in cfg.WEIGHT_PROFILES:
                for pw_key, pw_val in cfg.WEIGHT_PROFILES[value].items():
                    mock_session_state[f"ui_{pw_key}"] = pw_val
        else:
            mock_session_state[f"ui_{key}"] = value

    # Ensure other necessary defaults are present
    if "ui_inc_services_selection" not in mock_session_state:
        mock_session_state["ui_inc_services_selection"] = {}

    # Ensure dynamic keys for adults and children are present, even if empty,
    # to prevent KeyErrors in the list comprehensions in create_search_criterias_from_inputs.
    for i in range(default_data["nb_adultes"]):
        mock_session_state.setdefault(f"ui_metiers_adult_{i}", [])
        mock_session_state.setdefault(f"ui_formations_adult_{i}", [])

    for i in range(default_data["nb_enfants"]):
        mock_session_state.setdefault(f"ui_classe_enfant_{i}", cfg.CLASSES_SCOLAIRES[0])

    # Map old loc_search_area to new Mobility UI fields for test compatibility
    loc_val = default_data.get("loc_search_area")
    if loc_val == "france":
        mock_session_state["ui_france_search"] = True
        mock_session_state["ui_region_search"] = False
    elif loc_val == "region":
        mock_session_state["ui_france_search"] = False
        mock_session_state["ui_region_search"] = True
    else:
        mock_session_state["ui_france_search"] = False
        mock_session_state["ui_region_search"] = False

    if not mock_session_state.get("ui_france_search"):
        current_dept_code = mock_session_state["ui_departement"]
        # Find region code for the department
        dept_details = app_data.get("dept_details", {})
        current_reg_code = dept_details.get(current_dept_code, {}).get("reg_code")

        mock_session_state["ui_mobility_region"] = (
            [current_reg_code] if current_reg_code else []
        )
        if loc_val == "region":
            mock_session_state["ui_mobility_dept"] = ["Toute la région"]
        else:
            mock_session_state["ui_mobility_dept"] = [current_dept_code]

    # 4. Create the SearchCriterias by calling the app's own UI function.
    # We use unittest.mock.patch to temporarily replace streamlit's session_state
    # with our dictionary for the duration of the call.
    with patch("ui.forms.st.session_state", mock_session_state):
        scoring_config = ui_forms.create_search_criterias_from_inputs()
        print(f"DEBUG Scenario {scenario_id}: {scoring_config}")

    # 5. Instantiate ScoringEngine
    engine = scoring.ScoringEngine.from_app_data(app_data)

    # 6. Run the engine
    processed_gdf = engine.run(scoring_config)

    # 7. Post-processing (Legacy test consistency)
    # The previous test implementation manually dropped the start commune.
    # To match snapshots, we might need to remove it if it's present.
    if scoring_config.commune_actuelle in processed_gdf.index:
        processed_gdf = processed_gdf.drop(scoring_config.commune_actuelle)

    # Engine returns results sorted by score descending
    return processed_gdf


@pytest.mark.e2e
def test_scenario_1_communes(app_data):
    """E2E test for demo scenario 1."""
    results = run_test_scenario("1", app_data)
    assert_results_logical_invariants(results)
    assert results.shape[0] > 5


@pytest.mark.e2e
def test_scenario_2_communes(app_data):
    """E2E test for demo scenario 2."""
    results = run_test_scenario("2", app_data)
    assert_results_logical_invariants(results)
    assert results.shape[0] > 5


@pytest.mark.e2e
def test_scenario_3_communes(app_data):
    """E2E test for demo scenario 3."""
    results = run_test_scenario("3", app_data)
    assert_results_logical_invariants(results)
    assert results.shape[0] > 5


@pytest.mark.e2e
def test_differential_sensitivity_e2e(app_data, default_config):
    """
    E2E test verifying that changing configuration weights (e.g. maximizing employment vs
    maximizing housing) yields directionally distinct top recommendations.
    """
    engine = scoring.ScoringEngine.from_app_data(app_data)

    # 1. Employment-focused configuration
    config_employment = default_config.model_copy(deep=True)
    config_employment.poids_emploi = 1.0
    config_employment.poids_logement = 0.0
    config_employment.poids_education = 0.0
    config_employment.poids_inclusion = 0.0
    config_employment.poids_sante = 0.0
    config_employment.poids_mobilite = 0.0
    config_employment.loc_search_area = "departement"
    config_employment.loc_search_code = ["33"]

    # Add a targeted job to adult 0 to ensure employment scoring has dynamic data
    from core.models import CriteriaItem

    config_employment.codes_metiers = [
        [CriteriaItem(code="H2206", label="Soudage manual")]
    ]

    # 2. Housing-focused configuration
    config_housing = default_config.model_copy(deep=True)
    config_housing.poids_emploi = 0.0
    config_housing.poids_logement = 1.0
    config_housing.poids_education = 0.0
    config_housing.poids_inclusion = 0.0
    config_housing.poids_sante = 0.0
    config_housing.poids_mobilite = 0.0
    config_housing.loc_search_area = "departement"
    config_housing.loc_search_code = ["33"]

    res_employment = engine.run(config_employment)
    res_housing = engine.run(config_housing)

    # Assertions on invariants for both
    assert_results_logical_invariants(res_employment)
    assert_results_logical_invariants(res_housing)

    # Verify that the two runs produce different top communes
    top_employment = res_employment.head(10).index.tolist()
    top_housing = res_housing.head(10).index.tolist()

    assert top_employment != top_housing, (
        "Top 10 results should differ when prioritizing Employment vs Housing"
    )


@pytest.mark.e2e
def test_result_details_display(app_data):
    """
    Tests that the detail view for each of the top 5 results can be rendered without error.
    """
    # 1. Run a scenario to get some results
    results_communes = run_test_scenario("1", app_data)

    # 2. Get the scoring config and app_data that the UI functions will need
    mock_config_state = MockSessionState(
        {
            "app_data": app_data,
            "ui_departement": "33",
            "ui_commune": "Bordeaux",
            "ui_mobility_france": False,
            "ui_mobility_region": ["75"],  # IDF
            "ui_mobility_dept": "75",
            "ui_nb_adultes": 1,
            "ui_nb_enfants": 0,
            "ui_hebergement": "Location",
            "ui_logement": "Location",
            "ui_besoin_sante": [],
            "ui_inc_services_selection": {},
            "ui_poids_emploi": 1.0,
            "ui_poids_logement": 1.0,
            "ui_poids_education": 1.0,
            "ui_poids_inclusion": 0.25,
            "ui_poids_sante": 1.0,
            "ui_poids_mobilite": 1.0,
            "ui_metiers_adult_0": [],
            "ui_formations_adult_0": [],
        }
    )
    with patch("ui.forms.st.session_state", mock_config_state):
        scoring_config = ui_forms.create_search_criterias_from_inputs()

    # 3. Set up a mock session state for the UI functions

    mock_session_state_details = MockSessionState(
        {
            "app_data": app_data,
            "config": scoring_config,
            "processed_gdf": results_communes,
        }
    )

    # 4. Test the commune details display
    engine = scoring.ScoringEngine.from_app_data(app_data)
    with patch("ui.results.st.session_state", mock_session_state_details):
        for index, row in results_communes.head(5).iterrows():
            try:
                # Convert row to CommuneResult as expected by the new UI logic
                commune = engine.format_city_details(row, scoring_config)
                ui_results._display_result_details(commune)
            except Exception as e:
                pytest.fail(
                    f"Failed to display details for commune result {row.get('libgeo', 'UNKNOWN')} ({index}): {e}"
                )
