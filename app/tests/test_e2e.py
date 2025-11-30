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
import data_loader
import scoring
import ui

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
        # For Bassins de vie, the identifier is BV2022, not codgeo
        actual_id = actual.get('codgeo') or actual.get('BV2022')
        expected_id = expected.get('codgeo') or expected.get('BV2022')
        assert actual_id == expected_id, f"Row {i}: ID mismatch (codgeo or BV2022)"
        assert actual.get('codgeo_binome') == expected.get('codgeo_binome'), f"Row {i}: codgeo_binome mismatch"
        assert pytest.approx(actual['weighted_score'], rel=1e-4) == expected['weighted_score'], f"Row {i}: weighted_score mismatch"


@pytest.fixture(scope="module")
def app_data():
    """
    Loads the application data once for the entire test module.
    This is equivalent to st.session_state['app_data'] in the Streamlit app.
    """
    return data_loader.init_datasets()

def run_test_scenario(scenario_id, view_level, app_data):
    """
    Helper function to run a search scenario by simulating session state
    and calling the app's own logic.
    """
    # 1. Set up a mock session state dictionary
    mock_session_state = {}

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
            # The create_scoring_config_from_inputs function handles the division by 100.
            mock_session_state['ui_penalite_binome'] = value * 100 if value <= 1 else value
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
    if 'ui_besoins_autres' not in mock_session_state:
        mock_session_state['ui_besoins_autres'] = {}
    if 'ui_pop_min' not in mock_session_state:
        mock_session_state['ui_pop_min'] = 1000

    # Ensure dynamic keys for adults and children are present, even if empty,
    # to prevent KeyErrors in the list comprehensions in create_scoring_config_from_inputs.
    for i in range(default_data['nb_adultes']):
        mock_session_state.setdefault(f"ui_metiers_adult_{i}", [])
        mock_session_state.setdefault(f"ui_formations_adult_{i}", [])

    for i in range(default_data['nb_enfants']):
        mock_session_state.setdefault(f"ui_classe_enfant_{i}", cfg.CLASSES_SCOLAIRES[0])


    # 4. Create the ScoringConfig by calling the app's own UI function.
    # We use unittest.mock.patch to temporarily replace streamlit's session_state
    # with our dictionary for the duration of the call.
    with patch('ui.st.session_state', mock_session_state):
        scoring_config = ui.create_scoring_config_from_inputs()

    # 5. Get required dataframes from the loaded app_data
    df_all_communes = app_data['odis']
    df_bv_geo = app_data['bv_geo']
    df_area_geo = app_data['area_geo']
    start_commune = df_all_communes.loc[[scoring_config.commune_actuelle]]

    # 6. Filter communes or bassins de vie based on the view level
    loc_type = 'distance' if isinstance(scoring_config.loc_distance_km, int) else scoring_config.loc_distance_km
    
    if view_level == 'Communes':
        loc_col = 'dep_code' if loc_type == 'departement' else 'reg_code'
        communes_to_score = scoring.filter_communes(
            df=df_all_communes,
            start_commune=start_commune,
            loc_type=loc_type,
            loc_code=start_commune.iloc[0][loc_col] if loc_type != 'distance' else None,
            loc_distance_km=scoring_config.loc_distance_km if loc_type == 'distance' else None
        )
    else:  # Bassins de vie
        loc_col = 'dep_code' if loc_type == 'departement' else 'reg_code'
        filtered_bvs = scoring.filter_bassins_de_vie(
            bv_gdf=df_bv_geo,
            start_commune=start_commune,
            loc_type=loc_type,
            loc_code=start_commune.iloc[0][loc_col] if loc_type != 'distance' else None,
            loc_distance_km=scoring_config.loc_distance_km if loc_type == 'distance' else None,
            area_gdf=df_area_geo
        )
        bv_ids_to_keep = filtered_bvs.index.tolist()
        communes_to_score = df_all_communes[df_all_communes[cfg.BV_CODE_COL].isin(bv_ids_to_keep)]

    # 7. Compute the scores
    odis_scored = scoring.compute_odis_score(
        df_search=communes_to_score,
        df_all_communes=df_all_communes,
        scores_cat=app_data['scores_cat'],
        config=scoring_config,
        incl_index=app_data['incl_index'],
        associations_data=app_data['associations_data'], # Pass association data
        global_stats=app_data['global_score_stats'], # Added
    )
    
    odis_scored = odis_scored.drop(scoring_config.commune_actuelle, errors='ignore')

    # 8. Process the final results (aggregate if necessary)
    if odis_scored.empty:
        return gpd.GeoDataFrame()
    
    if view_level == 'Bassins de vie':
        df_bv_scores = scoring.aggregate_scores_by_bassin_de_vie(odis_scored)
        gdf_bv_geo_filtered = df_bv_geo[df_bv_geo.index.isin(df_bv_scores[cfg.BV_CODE_COL])]
        processed_gdf = gdf_bv_geo_filtered.merge(df_bv_scores, left_index=True, right_on=cfg.BV_CODE_COL)
    else:  # Commune level
        processed_gdf = odis_scored
        
    return processed_gdf.sort_values('weighted_score', ascending=False)


@pytest.mark.e2e
def test_scenario_1_communes(app_data, request):
    """E2E test for demo scenario 1 at the Communes level."""
    results = run_test_scenario('1', 'Communes', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert results.shape[0] > 5
    assert_results_match_snapshot('test_scenario_1_communes', results, request)

@pytest.mark.e2e
def test_scenario_1_bv(app_data, request):
    """E2E test for demo scenario 1 at the Bassins de vie level."""
    results = run_test_scenario('1', 'Bassins de vie', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert results.shape[0] > 5
    assert_results_match_snapshot('test_scenario_1_bv', results, request)

@pytest.mark.e2e
def test_scenario_2_communes(app_data, request):
    """E2E test for demo scenario 2 at the Communes level."""
    results = run_test_scenario('2', 'Communes', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert results.shape[0] > 5
    assert_results_match_snapshot('test_scenario_2_communes', results, request)

@pytest.mark.e2e
def test_scenario_2_bv(app_data, request):
    """E2E test for demo scenario 2 at the Bassins de vie level."""
    results = run_test_scenario('2', 'Bassins de vie', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert results.shape[0] > 5
    assert_results_match_snapshot('test_scenario_2_bv', results, request)

@pytest.mark.e2e
def test_scenario_3_communes(app_data, request):
    """E2E test for demo scenario 3 at the Communes level."""
    results = run_test_scenario('3', 'Communes', app_data)
    assert not results.empty
    assert 'weighted_score' in results.columns
    assert results.shape[0] > 5
    assert_results_match_snapshot('test_scenario_3_communes', results, request)

@pytest.mark.e2e

def test_scenario_3_bv(app_data, request):

    """E2E test for demo scenario 3 at the Bassins de vie level."""

    results = run_test_scenario('3', 'Bassins de vie', app_data)

    assert not results.empty

    assert 'weighted_score' in results.columns

    assert results.shape[0] > 5

    assert_results_match_snapshot('test_scenario_3_bv', results, request)



@pytest.mark.e2e
def test_result_details_display(app_data):
    """
    Tests that the detail view for each of the top 5 results can be rendered without error.
    """
    # 1. Run a scenario to get some results
    results_communes = run_test_scenario('1', 'Communes', app_data)
    results_bv = run_test_scenario('1', 'Bassins de vie', app_data)

    # 2. Get the scoring config and app_data that the UI functions will need
    mock_config_state = {
        'app_data': app_data,
        'ui_departement': '33',
        'ui_commune': 'Bordeaux',
        'ui_loc_distance_km': 50,
        'ui_nb_adultes': 1,
        'ui_nb_enfants': 0,
        'ui_hebergement': 'Location',
        'ui_logement': 'Location',
        'ui_besoin_sante': 'Aucun',
        'ui_besoins_autres': {},
        'ui_penalite_binome': 50,
        'ui_pop_min': 1000,
        'ui_poids_emploi': 100,
        'ui_poids_logement': 100,
        'ui_poids_education': 100,
        'ui_poids_inclusion': 25,
        'ui_poids_sante': 100,
        'ui_poids_mobilité': 100,
        'ui_metiers_adult_0': [],
        'ui_formations_adult_0': []
    }
    with patch('ui.st.session_state', mock_config_state):
        scoring_config = ui.create_scoring_config_from_inputs()

    # 3. Set up a mock session state for the UI functions
    mock_session_state = MagicMock()
    mock_session_state.app_data = app_data
    mock_session_state.config = scoring_config
    mock_session_state.processed_gdf = results_communes
    mock_session_state.view_level = 'Communes'

    # 4. Test the commune details display
    with patch('ui.st.session_state', mock_session_state):
        for index, row in results_communes.head(5).iterrows():
            try:
                ui._display_result_details(row)
            except Exception as e:
                pytest.fail(f"Failed to display details for commune result {row.libgeo} ({index}): {e}")

    # 5. Test the "Bassin de vie" details display
    mock_session_state.processed_gdf = results_bv
    mock_session_state.view_level = 'Bassins de vie'
    
    with patch('ui.st.session_state', mock_session_state):
        for index, row in results_bv.head(5).iterrows():
            try:
                ui._display_bv_result_details(row)
            except Exception as e:
                pytest.fail(f"Failed to display details for BV result {row.libgeo} ({index}): {e}")
