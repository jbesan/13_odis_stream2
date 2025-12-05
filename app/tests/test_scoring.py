import pytest
import pandas as pd
import geopandas as gpd
import numpy as np
import copy
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
    def test_compute_criteria_scores_structure(self, sample_data, default_config, sample_incl_index, sample_scores_cat, global_stats):
        """Tests that criteria scores are added as columns."""
        # Prerequisite: distance
        df_with_dist = scoring.add_distance_to_current_loc(sample_data, default_config.commune_actuelle)
        
        # Update config to ensure met_match columns are generated
        config = default_config
        config.codes_metiers = [['F1']] # Provide at least one code for adult 1
        config.nb_enfants = 1 # Enable education scoring
        config.classe_enfants = ['Crêche / Assistante Maternelle', 'Maternelle', 'Elémentaire', 'Collège', 'Lycée'] # Select all for full coverage
        
        # Add mock data for petite enfance and met_scaled
        df_with_dist['taux_couverture'] = 50.0
        df_with_dist['met_scaled'] = 0.5
        df_with_dist['log_vac_scaled'] = 0.5
        df_with_dist['inc_population_scaled'] = 0.5
        df_with_dist['inc_pol_scaled'] = 0.5
        df_with_dist['log_occup_scaled'] = 0.5
        df_with_dist['log_soc_inoc_scaled'] = 0.5
        df_with_dist['edu_classes_ferm_scaled'] = 0.5
        df_with_dist['edu_petite_enfance_scaled'] = 0.5 # Mock pre-calculated score
        df_with_dist['edu_maternelle_scaled'] = 0.5
        df_with_dist['edu_elementaire_scaled'] = 0.5
        df_with_dist['edu_college_scaled'] = 0.5
        df_with_dist['edu_lycee_scaled'] = 0.5
        df_with_dist['sante_hopital_scaled'] = 0.5
        df_with_dist['sante_maternite_scaled'] = 0.5
        df_with_dist['sante_maternite_scaled'] = 0.5
        df_with_dist['sante_psy_scaled'] = 0.5
        df_with_dist['inc_lien_social_score'] = 0.5 # Mock pre-calculated score

        scored_df = scoring.compute_criteria_scores(
            df=df_with_dist,
            prefs=config.__dict__,
            incl_index=sample_incl_index,
            df_all_communes=sample_data,
            associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']), # Mock associations data
            bmo_vertical=pd.DataFrame({'codgeo': ['75056', '33063'], 'fap_code': ['F1', 'F1']}), # Mock BMO data
            formations_data=pd.DataFrame(columns=['codgeo', 'formation_code']), # Mock formations
            codformations_index=pd.DataFrame(columns=['label']), # Mock index
            scores_cat=sample_scores_cat,
            global_stats=global_stats
        )
        
        expected_cols = [
            'met_match_adult1_scaled', 
            # 'log_soc_inoc_scaled', # Depends on logement type
            'log_vac_scaled', # Default is Location
            'inc_population_scaled',
            'inc_socle_admin_score',
            'inc_lien_social_score',
            'inc_affinite_score',
            'edu_petite_enfance_scaled', # New
            'edu_maternelle_scaled', # New
            'edu_elementaire_scaled', # New
            'edu_college_scaled', # New
            'edu_lycee_scaled' # New
        ]
        for col in expected_cols:
            assert col in scored_df.columns

    def test_compute_criteria_scores_partial_selection(self, sample_data, default_config, sample_incl_index, sample_scores_cat, global_stats):
        """Tests that only selected education criteria are added."""
        # Prerequisite: distance
        df_with_dist = scoring.add_distance_to_current_loc(sample_data, default_config.commune_actuelle)
        df_with_dist['met_scaled'] = 0.5
        df_with_dist['log_vac_scaled'] = 0.5
        df_with_dist['edu_maternelle_scaled'] = 0.5 # Needed for partial selection test
        df_with_dist['edu_classes_ferm_scaled'] = 0.5
        df_with_dist['inc_population_scaled'] = 0.5
        df_with_dist['inc_population_scaled'] = 0.5
        df_with_dist['inc_pol_scaled'] = 0.5
        df_with_dist['inc_lien_social_score'] = 0.5
        
        config = default_config
        config.nb_enfants = 1
        # Only select Maternelle
        config.classe_enfants = ['Maternelle']
        
        scored_df = scoring.compute_criteria_scores(
            df=df_with_dist,
            prefs=config.__dict__,
            incl_index=sample_incl_index,
            df_all_communes=sample_data,
            associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']),
            bmo_vertical=pd.DataFrame(columns=['codgeo', 'fap_code']),
            formations_data=pd.DataFrame(columns=['codgeo', 'formation_code']),
            codformations_index=pd.DataFrame(columns=['label']),
            scores_cat=sample_scores_cat,
            global_stats=global_stats
        )
        
        # Maternelle should be there
        assert 'edu_maternelle_scaled' in scored_df.columns
        
        # Others should NOT be there
        assert 'edu_petite_enfance_scaled' not in scored_df.columns
        assert 'edu_elementaire_scaled' not in scored_df.columns
        assert 'edu_college_scaled' not in scored_df.columns
        assert 'edu_lycee_scaled' not in scored_df.columns

    def test_compute_category_scores_aggregation(self, sample_data, sample_scores_cat, default_config):
        """Tests that category scores are correctly aggregated from criteria scores."""
        df = sample_data.copy()
        # Mock criteria scores
        df['met_scaled'] = 1.0
        df['met_match_adult1_scaled'] = 0.5
        
        # Filter scores_cat to only these two for 'emploi'
        relevant_metrics = ['met_ratio', 'met_match_adult1']
        scores_cat_subset = sample_scores_cat[sample_scores_cat['metric'].isin(relevant_metrics)].copy()
        scores_cat_subset['cat'] = 'emploi' # Force category
        
        df_cat = scoring.compute_category_scores(df, scores_cat_subset, 0.5, default_config)
        
        # Mean of 1.0 and 0.5 is 0.75
        assert 'emploi_cat_score' in df_cat.columns
        assert df_cat.iloc[0]['emploi_cat_score'] == 0.75

    def test_compute_weighted_score_nan_handling(self, sample_data, default_config):
        """Tests that NaN scores are excluded from the weighted average."""
        df = sample_data.copy()
        
        # Setup: 3 categories with equal weights (100)
        # Emploi: 1.0
        # Logement: 1.0
        # Education: NaN (Missing data)
        
        df['emploi_cat_score'] = 1.0
        df['logement_cat_score'] = 1.0
        df['education_cat_score'] = float('nan')
        
        # Ensure weights are set
        config = default_config
        config.poids_emploi = 100
        config.poids_logement = 100
        config.poids_education = 100
        config.nb_enfants = 1 # Ensure education is not skipped by exclusion logic
        
        # Act
        weighted_score = scoring.compute_weighted_score(df, config)
        
        # Assert
        # Should be (1.0*100 + 1.0*100) / 200 = 1.0
        # If NaN was treated as 0, it would be (200) / 300 = 0.66
        assert weighted_score.iloc[0] == 1.0
        
        # Case 2: Education is 0.0 (Valid score)
        df['education_cat_score'] = 0.0
        weighted_score_zero = scoring.compute_weighted_score(df, config)
        assert weighted_score_zero.iloc[0] == pytest.approx(0.6666, rel=1e-3)

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
        # Run aggregation
        df_bv = scoring.aggregate_scores_by_bassin_de_vie(df)
        
        assert len(df_bv) == 1
        bv1 = df_bv.iloc[0]
        assert bv1['population'] == 300
        # Weighted average: (10*100 + 20*200) / 300 = (1000 + 4000) / 300 = 5000/300 = 16.66
        assert np.isclose(bv1['weighted_score'], 16.666666)
        assert bv1['url_odis'] == 'urlB' # B is bigger

@pytest.mark.unit
class TestConditionalScoring:
    def test_compute_weighted_score_conditional_exclusion(self):
        """
        Tests that 'education' and 'sante' categories are excluded from the weighted score
        calculation when specific conditions are met (no kids, no health needs).
        """
        # Arrange
        df = pd.DataFrame({
            'emploi_cat_score': [1.0],
            'education_cat_score': [0.5], # Should be ignored
            'sante_cat_score': [0.5],     # Should be ignored
            'logement_cat_score': [1.0]
        })

        # Config with 0 kids and no health needs
        config = ScoringConfig(
            poids_emploi=100,
            poids_logement=100,
            poids_education=100, # Weight is present, but should be ignored
            poids_sante=100,     # Weight is present, but should be ignored
            poids_inclusion=0,
            poids_mobilité=0,
            commune_actuelle='33063',
            loc_distance_km=50,
            nb_adultes=1,
            nb_enfants=0,        # Condition to ignore education
            hebergement='Location',
            logement='Location',
            codes_metiers=[],
            codes_formations=[],
            classe_enfants=[],
            besoin_sante='Aucun', # Condition to ignore sante
            besoins_autres={},
            socle_admin_selection=[],
            affinite_selection=[],
            binome_penalty=0.5,
            pop_min=1000,
            criteria_weights={}
        )

        # Act
        # Expected behavior (after fix): (1.0*100 + 1.0*100) / 200 = 1.0
        weighted_score = scoring.compute_weighted_score(df, config)

        # Assert
        assert weighted_score.iloc[0] == 1.0, f"Expected 1.0, got {weighted_score.iloc[0]}"

    def test_compute_weighted_score_inclusion_when_relevant(self):
        """
        Tests that 'education' and 'sante' categories ARE included when conditions are met.
        """
        # Arrange
        df = pd.DataFrame({
            'emploi_cat_score': [1.0],
            'education_cat_score': [0.5], # Should be included
            'sante_cat_score': [0.5],     # Should be included
        })

        # Config with kids and health needs
        config = ScoringConfig(
            poids_emploi=100,
            poids_logement=0,
            poids_education=100,
            poids_sante=100,
            poids_inclusion=0,
            poids_mobilité=0,
            commune_actuelle='33063',
            loc_distance_km=50,
            nb_adultes=1,
            nb_enfants=1,        # Condition to include education
            hebergement='Location',
            logement='Location',
            codes_metiers=[],
            codes_formations=[],
            classe_enfants=['Maternelle'],
            besoin_sante='Hopital', # Condition to include sante
            besoins_autres={},
            socle_admin_selection=[],
            affinite_selection=[],
            binome_penalty=0.5,
            pop_min=1000,
            criteria_weights={}
        )

        # Act
        # (1.0*100 + 0.5*100 + 0.5*100) / 300 = 200 / 300 = 0.666...
        weighted_score = scoring.compute_weighted_score(df, config)

        # Assert
        assert abs(weighted_score.iloc[0] - 0.666666) < 0.0001

    def test_compute_criteria_scores_dynamic_bounds(self, sample_data, default_config, sample_incl_index, sample_scores_cat, global_stats):
        """Tests that match scores use dynamic max bounds based on preference length."""
        # Prerequisite: distance
        df_with_dist = scoring.add_distance_to_current_loc(sample_data, default_config.commune_actuelle)
        df_with_dist['met_scaled'] = 0.5
        df_with_dist['log_vac_scaled'] = 0.5
        df_with_dist['inc_population_scaled'] = 0.5
        df_with_dist['inc_population_scaled'] = 0.5
        df_with_dist['inc_pol_scaled'] = 0.5
        df_with_dist['inc_lien_social_score'] = 0.5
        
        # Mock data for matches
        df_with_dist['be_codfap_top'] = [['A1', 'B2'], ['A1'], [], [], []]
        df_with_dist['codes_formations'] = [['F1', 'F2'], ['F1'], [], [], []]
        
        # Config with 2 metiers selected and 3 formations selected
        config = copy.deepcopy(default_config)
        config.codes_metiers[0] = ['A1', 'B2'] # 2 items -> max bound 2
        config.codes_formations[0] = ['F1', 'F2', 'F3'] # 3 items -> max bound 3
        
        # Ensure max_bound is null in scores_cat for these scores (as per new config)
        scores_cat_dynamic = sample_scores_cat.copy()
        scores_cat_dynamic.loc[scores_cat_dynamic['score'] == 'met_match_adult1_scaled', 'max_bound'] = None
        scores_cat_dynamic.loc[scores_cat_dynamic['score'] == 'form_match_adult1_scaled', 'max_bound'] = None
        
        scored_df = scoring.compute_criteria_scores(
            df=df_with_dist,
            prefs=config.__dict__,
            incl_index=sample_incl_index,
            df_all_communes=sample_data,
            associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']),
            bmo_vertical=pd.DataFrame({
                'codgeo': ['75056', '75056', '33063'], 
                'fap_code': ['A1', 'B2', 'F1'] # 75056 has A1, B2. 33063 has F1.
            }),
            formations_data=pd.DataFrame({
                'codgeo': ['75056', '75056', '33063'],
                'formation_code': ['F1', 'F2', 'F1'] # 75056 has F1, F2. 33063 has F1.
            }),
            codformations_index=pd.DataFrame(columns=['label']),
            scores_cat=scores_cat_dynamic,
            global_stats=global_stats
        )
        
        # Check met_match_adult1_scaled
        # Row 0: matches A1, B2 (2 matches). Max bound 2. Score should be 2/2 = 1.0
        assert scored_df.loc['75056', 'met_match_adult1_scaled'] == 1.0
        
        # Check form_match_adult1_scaled
        # Row 0: matches F1, F2 (2 matches). Max bound 3. Score should be 2/3 = 0.666...
        assert scored_df.loc['75056', 'form_match_adult1_scaled'] == pytest.approx(2/3, rel=1e-3)
