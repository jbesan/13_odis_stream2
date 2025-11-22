import pytest
import pandas as pd
import geopandas as gpd
import numpy as np
from app import scoring
from app import config as cfg
from app.config import ScoringConfig

# --- Unit Tests for Scoring Logic ---

@pytest.mark.unit
class TestFilterCommunes:
    def test_filter_by_distance_excludes_far_communes(self, sample_data):
        """Tests that filter_by_distance excludes communes outside the radius."""
        # Arrange
        # Add distance first (usually done by add_distance_to_current_loc)
        # We simulate it here or call the function if we want to test integration of these two small units
        # Let's use the function to be safe
        current_codgeo = '33063' # Bordeaux
        df_with_dist = scoring.add_distance_to_current_loc(sample_data, current_codgeo)
        max_dist_km = 200 

        # Act
        filtered_df = scoring.filter_by_distance(df_with_dist, max_dist_km)

        # Assert
        assert '33063' in filtered_df.index # Bordeaux (0km)
        assert '64445' in filtered_df.index # Pau (~170km)
        assert '75056' not in filtered_df.index # Paris (>500km)

    def test_filter_communes_departement(self, sample_data):
        """Tests filtering by department."""
        start_commune = sample_data.loc[['33063']] # Bordeaux
        
        filtered = scoring.filter_communes(
            df=sample_data,
            start_commune=start_commune,
            loc_type='departement',
            loc_code='33',
            loc_distance_km=None
        )
        
        assert len(filtered) == 1
        assert '33063' in filtered.index
        assert '75056' not in filtered.index

    def test_filter_communes_region(self, sample_data):
        """Tests filtering by region."""
        start_commune = sample_data.loc[['33063']] # Bordeaux (Reg 75)
        
        filtered = scoring.filter_communes(
            df=sample_data,
            start_commune=start_commune,
            loc_type='region',
            loc_code='75',
            loc_distance_km=None
        )
        
        # Should include Bordeaux (33) and Pau (64) which are both in Reg 75
        assert len(filtered) == 2
        assert '33063' in filtered.index
        assert '64445' in filtered.index
        assert '75056' not in filtered.index

@pytest.mark.unit
class TestDistanceCalculation:
    def test_add_distance_to_current_loc_correctness(self, sample_data):
        """Tests that distance is calculated correctly (0 for self)."""
        current_codgeo = '33063' # Bordeaux
        df_with_dist = scoring.add_distance_to_current_loc(sample_data, current_codgeo)
        
        assert df_with_dist.loc[current_codgeo, 'dist_current_loc'] == 0
        assert df_with_dist.loc['75056', 'dist_current_loc'] > 400 # Paris is far

@pytest.mark.unit
class TestScoringLogic:
    def test_compute_criteria_scores_structure(self, sample_data, default_config, sample_incl_index):
        """Tests that criteria scores are added as columns."""
        # Prerequisite: distance
        df_with_dist = scoring.add_distance_to_current_loc(sample_data, default_config.commune_actuelle)
        
        # Update config to ensure met_match columns are generated
        config = default_config
        config.codes_metiers = [['F1']] # Provide at least one code for adult 1
        
        scored_df = scoring.compute_criteria_scores(
            df=df_with_dist,
            prefs=config.__dict__,
            incl_index=sample_incl_index,
            df_all_communes=sample_data
        )
        
        expected_cols = [
            'met_match_adult1_scaled', 
            # 'log_soc_inoc_scaled', # Depends on logement type
            'log_vac_scaled', # Default is Location
            'population_scaled'
        ]
        for col in expected_cols:
            assert col in scored_df.columns

    def test_compute_category_scores_aggregation(self, sample_data, sample_scores_cat):
        """Tests that category scores are correctly aggregated from criteria scores."""
        df = sample_data.copy()
        # Mock criteria scores
        df['met_scaled'] = 1.0
        df['met_match_adult1_scaled'] = 0.5
        
        # Filter scores_cat to only these two for 'emploi'
        relevant_metrics = ['met_ratio', 'met_match_adult1']
        scores_cat_subset = sample_scores_cat[sample_scores_cat['metric'].isin(relevant_metrics)].copy()
        scores_cat_subset['cat'] = 'emploi' # Force category
        
        df_cat = scoring.compute_category_scores(df, scores_cat_subset, binome_penalty=0.5)
        
        # Mean of 1.0 and 0.5 is 0.75
        assert 'emploi_cat_score' in df_cat.columns
        assert df_cat.iloc[0]['emploi_cat_score'] == 0.75

    def test_compute_weighted_score(self, default_config):
        """Tests the final weighted score calculation."""
        df = pd.DataFrame({
            'emploi_cat_score': [1.0],
            'logement_cat_score': [0.0],
            # Other categories missing or 0
            'education_cat_score': [0.0],
            'inclusion_cat_score': [0.0],
            'mobilité_cat_score': [0.0]
        })
        
        config = default_config
        config.poids_emploi = 100
        config.poids_logement = 100
        # Others are default (100, 25, 100)
        # But let's set them to 0 to simplify test
        config.poids_education = 0
        config.poids_inclusion = 0
        config.poids_mobilité = 0
        
        weighted_score = scoring.compute_weighted_score(df, config)
        
        # (1.0 * 100 + 0.0 * 100) / (100 + 100) = 0.5
        assert weighted_score.iloc[0] == 0.5

@pytest.mark.unit
class TestAggregation:
    def test_aggregate_scores_by_bassin_de_vie(self):
        """Tests aggregation of scores by BV."""
        df = pd.DataFrame({
            'codgeo': ['A', 'B'],
            'population': [100, 200],
            'weighted_score': [10, 20],
            cfg.BV_CODE_COL: ['BV1', 'BV1'],
            cfg.BV_NAME_COL: ['Bassin 1', 'Bassin 1'],
            'epci_nom': ['EPCI', 'EPCI'],
            'url_odis': ['urlA', 'urlB'],
            'url_wikipedia': ['wikiA', 'wikiB'],
            'be_libfap_top': [['JobA'], ['JobB']],
            'noms_formations': [['FormA'], ['FormB']]
        }).set_index('codgeo')
        
        result = scoring.aggregate_scores_by_bassin_de_vie(df)
        
        assert len(result) == 1
        bv1 = result.iloc[0]
        assert bv1['population'] == 300
        # Weighted average: (10*100 + 20*200) / 300 = (1000 + 4000) / 300 = 5000/300 = 16.66
        assert np.isclose(bv1['weighted_score'], 16.666666)
        assert bv1['url_odis'] == 'urlB' # B is bigger
