import pytest
import pandas as pd
import geopandas as gpd
import copy
from unittest.mock import patch, MagicMock
import json
from pathlib import Path
import numpy as np

# Important: The tests in this file must be run from the 'app/' directory
# for the data paths to resolve correctly.
# Example: pytest tests/test_e2e.py

from config import DEMO_DATA_DEFAULT, DEMO_SCENARIOS
import config as cfg
from utils import data_loader
from core import scoring
from core.models import SearchCriterias
from ui import components as ui
from ui import forms as ui_forms
from ui import results as ui_results

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"

def assert_results_match_snapshot(test_name: str, results: pd.DataFrame, request):
    """
    Asserts that the top 5 results of a test match a stored snapshot.
    If --update-snapshots is passed, it generates/updates the snapshot.
    """
    snapshot_file = SNAPSHOT_DIR / f"{test_name}.json"
    
    # Prepare the snapshot data from the current results
    # We need to handle potential differences in dtypes for JSON serialization
    results_for_snapshot = results.copy()
    if 'geometry' in results_for_snapshot.columns:
        results_for_snapshot = results_for_snapshot.drop(columns=['geometry'])
    if 'polygon' in results_for_snapshot.columns:
        results_for_snapshot = results_for_snapshot.drop(columns=['polygon'])

    snapshot_data = results_for_snapshot.head(5).reset_index().to_dict(orient='records')
    for record in snapshot_data:
        for key, value in record.items():
            if isinstance(value, (pd.Timestamp, pd.Timedelta)):
                record[key] = str(value)
            elif isinstance(value, (np.int64, np.int32)):
                record[key] = int(value)
            elif isinstance(value, (np.float64, np.float32)):
                record[key] = float(value)
            elif isinstance(value, (np.bool_)):
                record[key] = bool(value)
            elif isinstance(value, np.ndarray):
                record[key] = value.tolist()
            elif hasattr(value, 'wkt'): # Handles shapely geometries (Point, Polygon, etc.)
                record[key] = value.wkt
            elif isinstance(value, list) and len(value) > 0 and isinstance(value[0], (np.int64, np.int32)):
                record[key] = [int(v) for v in value]
            elif isinstance(value, set):
                record[key] = list(value)


    if request.config.getoption("--update-snapshots"):
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        with open(snapshot_file, 'w', encoding='utf-8') as f:
            json.dump(snapshot_data, f, indent=4, ensure_ascii=False)
        pytest.skip(f"Snapshot updated for {test_name}")

    if not snapshot_file.exists():
        pytest.fail(f"Snapshot file not found for {test_name}. Run with --update-snapshots to create it.")

    with open(snapshot_file, 'r', encoding='utf-8') as f:
        expected_data = json.load(f)
    
    # Compare the data, focusing on key fields
    for i, (actual, expected) in enumerate(zip(snapshot_data, expected_data)):
        # Handle different identifier types (communes vs BV)
        actual_id = actual.get('codgeo') or actual.get('bassin_de_vie')
        expected_id = expected.get('codgeo') or expected.get('bassin_de_vie')
        
        assert actual_id == expected_id, f"Row {i}: ID mismatch (codgeo or bassin_de_vie). Expected {expected_id}, got {actual_id}"
        
        # Weighted score comparison (pinned to the logic current at snapshot time)
        assert pytest.approx(actual['weighted_score'], rel=1e-4) == expected['weighted_score'], f"Row {i}: weighted_score mismatch"


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
    mock_session_state['app_data'] = app_data

    # 3. Load demo data into the session state with 'ui_' prefixes
    scenario_data = DEMO_SCENARIOS[scenario_id]
    default_data = copy.deepcopy(DEMO_DATA_DEFAULT)
    default_data.update(scenario_data)

    for key, value in default_data.items():
        if key == 'sante':
            mock_session_state['ui_besoin_sante'] = value
        elif key == 'commune_actuelle':
            mock_session_state['ui_commune'] = value
        elif key == 'departement_actuel':
            mock_session_state['ui_departement'] = value
        elif key == 'binome_penalty':
            # The UI slider uses percentage values (e.g., 50), but the config expects a float (e.g., 0.5)
            # The create_search_criterias_from_inputs function handles the division by 100.
            mock_session_state['ui_binome_penalty'] = value * 100 if value <= 1 else value
        # Handle list-based inputs for children's classes and professional goals
        elif key == 'classe_enfants':
            for i, class_level in enumerate(value):
                mock_session_state[f'ui_classe_enfant_{i}'] = class_level
        elif key == 'codes_metiers':
            for i, codes in enumerate(value):
                mock_session_state[f'ui_metiers_adult_{i}'] = codes
        elif key == 'codes_formations':
            for i, codes in enumerate(value):
                mock_session_state[f'ui_formations_adult_{i}'] = codes
        else:
            mock_session_state[f'ui_{key}'] = value
    
    # Ensure other necessary defaults are present
    if 'ui_inc_services_add_selection' not in mock_session_state:
        mock_session_state['ui_inc_services_add_selection'] = {}

    # Ensure dynamic keys for adults and children are present, even if empty,
    # to prevent KeyErrors in the list comprehensions in create_search_criterias_from_inputs.
    for i in range(default_data['nb_adultes']):
        mock_session_state.setdefault(f"ui_metiers_adult_{i}", [])
        mock_session_state.setdefault(f"ui_formations_adult_{i}", [])

    for i in range(default_data['nb_enfants']):
        mock_session_state.setdefault(f"ui_classe_enfant_{i}", cfg.CLASSES_SCOLAIRES[0])

    # Map old loc_search_area to new Mobility UI fields for test compatibility
    loc_val = default_data.get('loc_search_area')
    if loc_val == 'france':
        mock_session_state['ui_france_search'] = True
        mock_session_state['ui_region_search'] = False
    elif loc_val == 'region':
        mock_session_state['ui_france_search'] = False
        mock_session_state['ui_region_search'] = True
    else:
        mock_session_state['ui_france_search'] = False
        mock_session_state['ui_region_search'] = False

    if not mock_session_state.get('ui_france_search'):
        current_dept_code = mock_session_state['ui_departement']
        # Find region code for the department
        dept_details = app_data.get('dept_details', {})
        current_reg_code = dept_details.get(current_dept_code, {}).get('reg_code')
        
        mock_session_state['ui_mobility_region'] = current_reg_code
        if loc_val == 'region':
            mock_session_state['ui_mobility_dept'] = ["Toute la région"]
        else:
            mock_session_state['ui_mobility_dept'] = [current_dept_code]

    # 4. Create the SearchCriterias by calling the app's own UI function.
    # We use unittest.mock.patch to temporarily replace streamlit's session_state
    # with our dictionary for the duration of the call.
    with patch('ui.forms.st.session_state', mock_session_state):
        scoring_config = ui_forms.create_search_criterias_from_inputs()
        print(f"DEBUG Scenario {scenario_id}: {scoring_config}")

    # 5. Instantiate ScoringEngine
    # We pass empty global_stats as in the app page
    engine = scoring.ScoringEngine(
            df_all_communes=app_data['odis'],
        df_bv_geo=app_data['bv_geo'],
        scores_cat=app_data['scores_cat'],
        incl_index=app_data['incl_index'],
        associations_data=app_data['associations_data'],
        formations_data=app_data['formations_data'],
        codformations_index=app_data['codformations_index'],
        global_stats={} 
    )

    # 6. Run the engine
    processed_gdf = engine.run(scoring_config)

    # 7. Post-processing (Legacy test consistency)
    # The previous test implementation manually dropped the start commune.
    # To match snapshots, we might need to remove it if it's present.
    if scoring_config.commune_actuelle in processed_gdf.index:
        processed_gdf = processed_gdf.drop(scoring_config.commune_actuelle)
    
    # Engine returns results sorted by score descending
    return processed_gdf

def run_test_scenario_bv(scenario_id, app_data):
    """
    Helper function to run a search scenario and aggregate results by Bassin de Vie.
    """
    results_communes = run_test_scenario(scenario_id, app_data)
    
    # Aggregate by Bassin de Vie
    # We take the best commune in each BV as the representative for some fields, 
    # but aggregate others.
    
    # 🧪 Pattern matching snapshots:
    # Snapshots have: population_bv, libgeo (representative), bassin_de_vie, communes (list)
    
    bv_results = results_communes.copy()
    
    # Grouping logic
    agg_funcs = {
        'weighted_score': 'max',
        'population': 'sum',
        'libgeo': 'first', # Usually the first/best commune
        'codgeo': lambda x: sorted(list(x)),
    }
    
    # Add other columns if they exist in snapshots
    for col in ['inclusion_cat_score', 'mobilité_cat_score', 'mob_dist_scaled', 'mob_epci_scaled', 'population_bv']:
        if col in bv_results.columns:
            agg_funcs[col] = 'first'
            
    # Snapshots seem to have 'index' as a column sometimes
    bv_results = bv_results.reset_index()
    bv_grouped = bv_results.groupby('bassin_de_vie').agg(agg_funcs)
    
    # Rename columns to match snapshot expectations
    bv_grouped = bv_grouped.rename(columns={
        'codgeo': 'communes',
        'population': 'population_bv'
    })
    
    # Sort by weighted_score
    bv_grouped = bv_grouped.sort_values('weighted_score', ascending=False)
    
    return bv_grouped


@pytest.mark.e2e
def test_scenario_1_communes(app_data, request):
    """E2E test for demo scenario 1."""
    results = run_test_scenario('1', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert results.shape[0] > 5
    assert_results_match_snapshot('test_scenario_1_communes', results, request)

@pytest.mark.e2e
def test_scenario_2_communes(app_data, request):
    """E2E test for demo scenario 2."""
    results = run_test_scenario('2', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert results.shape[0] > 5
    assert_results_match_snapshot('test_scenario_2_communes', results, request)

@pytest.mark.e2e
def test_scenario_3_communes(app_data, request):
    """E2E test for demo scenario 3."""
    results = run_test_scenario('3', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert results.shape[0] > 5
    assert_results_match_snapshot('test_scenario_3_communes', results, request)

@pytest.mark.e2e
def test_scenario_1_bv(app_data, request):
    """E2E test for demo scenario 1 (BV level)."""
    results = run_test_scenario_bv('1', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert_results_match_snapshot('test_scenario_1_bv', results, request)

@pytest.mark.e2e
def test_scenario_2_bv(app_data, request):
    """E2E test for demo scenario 2 (BV level)."""
    results = run_test_scenario_bv('2', app_data)
    assert not results.empty
    assert_results_match_snapshot('test_scenario_2_bv', results, request)

@pytest.mark.e2e
def test_scenario_3_bv(app_data, request):
    """E2E test for demo scenario 3 (BV level)."""
    results = run_test_scenario_bv('3', app_data)
    assert not results.empty
    assert_results_match_snapshot('test_scenario_3_bv', results, request)


@pytest.mark.e2e
def test_result_details_display(app_data):
    """
    Tests that the detail view for each of the top 5 results can be rendered without error.
    """
    # 1. Run a scenario to get some results
    results_communes = run_test_scenario('1', app_data)

    # 2. Get the scoring config and app_data that the UI functions will need
    mock_config_state = MockSessionState({
        'app_data': app_data,
        'ui_departement': '33',
        'ui_commune': 'Bordeaux',
        'ui_mobility_france': False,
        'ui_mobility_region': '75', # IDF
        'ui_mobility_dept': '75',
        'ui_nb_adultes': 1,
        'ui_nb_enfants': 0,
        'ui_hebergement': 'Location',
        'ui_logement': 'Location',
        'ui_besoin_sante': 'Aucun',
        'ui_inc_services_add_selection': {},
        'ui_poids_emploi': 100,
        'ui_poids_logement': 100,
        'ui_poids_education': 100,
        'ui_poids_inclusion': 25,
        'ui_poids_sante': 100,
        'ui_poids_mobilite': 100,
        'ui_metiers_adult_0': [],
        'ui_formations_adult_0': []
    })
    with patch('ui.forms.st.session_state', mock_config_state):
        scoring_config = ui_forms.create_search_criterias_from_inputs()

    # 3. Set up a mock session state for the UI functions

    mock_session_state_details = MockSessionState({
        'app_data': app_data,
        'config': scoring_config,
        'processed_gdf': results_communes,
    })

    # 4. Test the commune details display
    engine = scoring.ScoringEngine.from_app_data(app_data)
    with patch('ui.results.st.session_state', mock_session_state_details):
        for index, row in results_communes.head(5).iterrows():
            try:
                # Convert row to CommuneResult as expected by the new UI logic
                commune = engine.format_city_details(row, scoring_config)
                ui_results._display_result_details(commune)
            except Exception as e:
                pytest.fail(f"Failed to display details for commune result {row.get('libgeo', 'UNKNOWN')} ({index}): {e}")
