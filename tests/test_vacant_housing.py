import pytest
import pandas as pd
import numpy as np
from app.data_loader import load_all_datasets
from app.scoring import compute_criteria_scores
import app.config as cfg

@pytest.mark.unit
def test_vacant_housing_criterion():
    """
    Verifies that the vacant housing criterion uses the new structural vacancy data.
    """
    # 1. Load Data (Mocking file paths if necessary, but here we might want to test actual loading if fast enough, 
    # or mock the file reading. Given the instructions, we'll try to load actual data to verify the CSV integration)
    
    # We need to mock the file paths to point to the real files if we are running from root
    # But load_all_datasets uses cfg constants. 
    # Let's assume the environment is set up correctly for the test to find the files.
    
    # However, loading ALL datasets might be slow. 
    # Let's try to mock the return of load_all_datasets or just test the logic if we can isolate it.
    # But the goal is to verify the CSV loading too.
    
    # Let's try to load just the necessary parts if possible, or just call load_all_datasets and check the columns.
    
    try:
        odis, scores_cat, _, _, _, _, _, incl_index, associations_data, global_stats = load_all_datasets(
            cfg.ODIS_FILE,
            cfg.BV_FILENAME,
            cfg.SCORES_CAT_FILE,
            cfg.METIERS_FILE,
            cfg.FORMATIONS_FILE,
            cfg.ECOLES_FILE,
            cfg.MATERNITE_FILE,
            cfg.SANTE_FILE,
            cfg.INCLUSION_FILE,
            cfg.CAF_FILE,
            cfg.LOVAC_FILE
        )
    except Exception as e:
        pytest.fail(f"Data loading failed: {e}")

    # 2. Verify Columns
    assert 'pp_vacant_plus_2ans_25' in odis.columns, "LOVAC column 'pp_vacant_plus_2ans_25' missing in ODIS dataframe"
    assert 'log_vac_struct_ratio' in odis.columns, "Calculated ratio 'log_vac_struct_ratio' missing in ODIS dataframe"

    # 3. Verify Data Integrity (Check a known sample if possible, or general properties)
    # Ambérieu-en-Bugey (01004) from head: pp_vacant_plus_2ans_25 = 207
    # We need to find its index. ODIS is indexed by codgeo.
    if '01004' in odis.index:
        sample = odis.loc['01004']
        # Check raw value
        # Note: We cast to float32 in data_loader
        assert sample['pp_vacant_plus_2ans_25'] == 207.0, f"Expected 207.0 for 01004, got {sample['pp_vacant_plus_2ans_25']}"
        
        # Check ratio calculation
        # log_total is from ODIS. We don't know it exactly without looking at ODIS file, 
        # but we can check consistency: ratio = val / log_total
        expected_ratio = sample['pp_vacant_plus_2ans_25'] / sample['log_total']
        # Handle potential float precision issues
        np.testing.assert_almost_equal(sample['log_vac_struct_ratio'], expected_ratio, decimal=5)

    # 4. Verify Scoring Logic
    # Create a dummy prefs dict
    prefs = cfg.DEMO_DATA_DEFAULT.copy()
    prefs['commune_actuelle'] = '01004' # Ambérieu-en-Bugey (valid codgeo)
    prefs['logement'] = "Location"
    # Fix potential IndexError if default has mismatch
    prefs['nb_adultes'] = 1
    prefs['codes_metiers'] = [[]]
    prefs['codes_formations'] = [[]]
    
    # Run scoring (just criteria)
    # We need a dummy df_all_communes (can be same as odis for this test)
    # Add dummy dist_current_loc to avoid KeyError
    odis['dist_current_loc'] = 0.0

    odis_scored = compute_criteria_scores(
        odis,
        prefs,
        incl_index,
        odis, # df_all_communes
        associations_data,
        scores_cat,
        global_stats
    )
    
    assert 'log_vac_scaled' in odis_scored.columns
    assert odis_scored['log_vac_scaled'].notna().all(), "Scores should not be NaN (unless input was NaN)"
    
    # Check that the score follows the ratio (higher ratio -> higher score, as it is 'vacant' housing availability?)
    # Wait, the config says:
    # strong_point_text: Nombre élevé de logements vacants depuis plus de 2 ans
    # high_value_adjective: élevé
    # This implies higher is "better" or at least "higher score".
    # Let's check correlation.
    
    # Filter out NaNs for correlation check
    valid_data = odis_scored[['log_vac_struct_ratio', 'log_vac_scaled']].dropna()
    if not valid_data.empty:
        correlation = valid_data['log_vac_struct_ratio'].corr(valid_data['log_vac_scaled'])
        assert correlation > 0.9, f"Score should be highly correlated with the ratio, got {correlation}"

if __name__ == "__main__":
    # Manually run if executed as script
    test_vacant_housing_criterion()
