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
    def test_filter_communes_distance(self, sample_data):
        """Tests that filter_communes excludes communes outside the radius."""
        start_commune = sample_data.loc[['33063']] # Bordeaux
        max_dist_km = 200

        filtered = scoring.filter_communes(
            df=sample_data,
            start_commune=start_commune,
            loc_type='distance',
            loc_code=None,
            loc_distance_km=max_dist_km
        )

        assert '33063' in filtered.index # Bordeaux (0km)
        assert '64445' in filtered.index # Pau (~170km)
        assert '75056' not in filtered.index # Paris (>500km)

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
        assert len(filtered) == 2
        assert '33063' in filtered.index
        assert '64445' in filtered.index
        assert '75056' not in filtered.index

    def test_filter_communes_france(self, sample_data):
        """Tests filtering for France Metro (excludes DROM)."""
        start_commune = sample_data.loc[['33063']]
        
        # Add a DROM commune to sample data if not present, or mock it
        # sample_data usually comes from conftest. Let's create a local extended DF
        df_extended = sample_data.copy()
        # Add a fake DROM line (Reunion)
        df_extended.loc['97411'] = df_extended.loc['33063'].copy()
        df_extended.loc['97411', 'dep_code'] = '974'
        df_extended.loc['97411', 'reg_code'] = '04'
        
        filtered = scoring.filter_communes(
            df=df_extended,
            start_commune=start_commune,
            loc_type='france',
            loc_code=None,
            loc_distance_km=0
        )
        
        assert '33063' in filtered.index # Bordeaux (Metro)
        assert '97411' not in filtered.index # Saint-Denis (DROM)

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
        # Prerequisite: distance (Engine handles it usually, but here checking criteria scores specifically)
        df_with_dist = scoring.add_distance_to_current_loc(sample_data, default_config.commune_actuelle)
        
        # Update config to ensure met_match columns are generated
        config = default_config
        config.codes_metiers[0] = ['F1'] # Provide at least one code for adult 1 (list nesting fix if needed based on config structure)
        config.nb_enfants = 1 # Enable education scoring
        config.classe_enfants = ['Crèche / Assistante Maternelle', 'Maternelle', 'Elémentaire', 'Collège', 'Lycée'] # Select all for full coverage
        
        # Add mock data for pre-requisites
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
        df_with_dist['inc_asso_core_scaled'] = 0.5 # Mock pre-calculated score

        engine = scoring.ScoringEngine(
            df_all_communes=sample_data,
            df_bv_geo=gpd.GeoDataFrame(),
            df_area_geo=gpd.GeoDataFrame(),
            scores_cat=sample_scores_cat,
            incl_index=sample_incl_index,
            associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']),
            bmo_vertical=pd.DataFrame({'codgeo': ['75056', '33063'], 'fap_code': ['F1', 'F1']}),
            formations_data=pd.DataFrame(columns=['codgeo', 'formation_code']),
            codformations_index=pd.DataFrame(columns=['label']),
            global_stats=global_stats
        )

        scored_df = engine._compute_criteria_scores(
            df=df_with_dist,
            config=config
        )
        
        expected_cols = [
            'met_match_adult1_scaled', 
            # 'log_soc_inoc_scaled', # Depends on logement type
            'log_vac_scaled', # Default is Location
            'inc_population_scaled',
            'inc_services_core_scaled',
            'inc_asso_core_scaled',
            'inc_asso_add_scaled',
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
        df_with_dist['inc_asso_core_scaled'] = 0.5
        
        config = default_config
        config.nb_enfants = 1
        # Only select Maternelle
        config.classe_enfants = ['Maternelle']
        
        engine = scoring.ScoringEngine(
            df_all_communes=sample_data,
            df_bv_geo=gpd.GeoDataFrame(),
            df_area_geo=gpd.GeoDataFrame(),
            scores_cat=sample_scores_cat,
            incl_index=sample_incl_index,
            associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']),
            bmo_vertical=pd.DataFrame(columns=['codgeo', 'fap_code']),
            formations_data=pd.DataFrame(columns=['codgeo', 'formation_code']),
            codformations_index=pd.DataFrame(columns=['label']),
            global_stats=global_stats
        )

        scored_df = engine._compute_criteria_scores(
            df=df_with_dist,
            config=config
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
            inc_services_core_selection=[],
            inc_asso_add_selection=[],
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
            inc_services_core_selection=[],
            inc_asso_add_selection=[],
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
        df_with_dist['inc_asso_core_scaled'] = 0.5
        
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
        
        engine = scoring.ScoringEngine(
            df_all_communes=sample_data,
            df_bv_geo=gpd.GeoDataFrame(),
            df_area_geo=gpd.GeoDataFrame(),
            scores_cat=scores_cat_dynamic,
            incl_index=sample_incl_index,
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
            global_stats=global_stats
        )

        scored_df = engine._compute_criteria_scores(
            df=df_with_dist,
            config=config
        )
        
        # Check met_match_adult1_scaled
        # Row 0: matches A1, B2 (2 matches). Max bound 2. Score should be 2/2 = 1.0
        assert scored_df.loc['75056', 'met_match_adult1_scaled'] == 1.0
        

@pytest.mark.unit
class TestMCPScenario:
    """
    Explicit tests for the MCP (Model Context Protocol) scenario.
    Ensures that the ScoringEngine can be invoked in a purely stateless manner 
    by an external agent (MCP server), passing all necessary configuration 
    and receiving structured results.
    """
    
    def test_mcp_stateless_execution(self, sample_data, sample_scores_cat, sample_incl_index, global_stats):
        """
        Simulates an MCP call where the agent constructs a ScoringConfig 
        and invokes the engine without any Streamlit session state context.
        """
        # 1. MCP Agent prepares the configuration based on user prompt
        # e.g. "I want to move to a place with good jobs and cheap rent near Bordeaux"
        config = ScoringConfig(
             commune_actuelle='33063', # Bordeaux
             loc_distance_km=50,
             poids_emploi=100, # "Good jobs"
             poids_logement=100, # "Cheap rent" implies high weight on housing affordability
             poids_education=0,
             poids_sante=0,
             poids_inclusion=0,
             poids_mobilité=0,
             nb_adultes=1,
             nb_enfants=0,
             hebergement='Location',
             logement='Location',
             codes_metiers=[['F1']], # Mock job code
             codes_formations=[[]],
             classe_enfants=[],
             besoin_sante='Aucun',
             besoins_autres={},
             inc_services_core_selection=[],
             inc_asso_add_selection=[],
             binome_penalty=0.0,
             pop_min=0,
             criteria_weights={}
        )
        
        # 2. MCP Server initializes the Engine (with pre-loaded datasets)
        # In a real scenario, this engine instance might be persistent or created per request with shared data
        engine = scoring.ScoringEngine(
            df_all_communes=sample_data,
            df_bv_geo=gpd.GeoDataFrame(), # Not using BV view here
            df_area_geo=gpd.GeoDataFrame(),
            scores_cat=sample_scores_cat,
            incl_index=sample_incl_index,
            associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']),
            bmo_vertical=pd.DataFrame({'codgeo': ['33063'], 'fap_code': ['F1'], 'count': [10]}),
            formations_data=pd.DataFrame(columns=['codgeo', 'formation_code', 'count']),
            codformations_index=pd.DataFrame(columns=['label']),
            global_stats=global_stats
        )
        
        # 3. Execution
        processed_gdf, unaggregated_gdf = engine.run(config, view_level='Communes')
        
        # 4. Verification of Return Values
        assert not processed_gdf.empty
        assert 'weighted_score' in processed_gdf.columns
        
        # Expect Bordeaux (33063) to be present 
        # (It matches distance 0, and has job 'F1')
        assert '33063' in processed_gdf.index
        
        # Validate that the score reflects the inputs
        # Emploi: has F1 job -> Match score 1.0. Met scaled -> some value.
        # Logement: weights applied.
        score = processed_gdf.loc['33063', 'weighted_score']
        assert score >= 0.0 and score <= 1.0
        
        # Ensure statelessness: No side effects on config or engine
        assert config.commune_actuelle == '33063'

# --- Consolidated Tests from other files ---

@pytest.mark.unit
class TestBVExclusionRepro:
    """Tests related to the specific bug where current BV was not excluded or handled correctly."""
    
    def test_repro_current_bv_exclusion(self):
        """
        Reproduction test from repro_issue_bv_exclusion.py:
        When view_level is 'Bassins de vie', the bassin de vie containing the 
        current commune should be excluded from the results.
        """
        from shapely.geometry import Point, Polygon
        
        # 1. Setup Mock Data
        # Communes: A (Current - Bordeaux), B (Neighbor - Pessac), C (Other - Paris)
        communes_data = {
            'codgeo': ['A', 'B', 'C'],
            'libgeo': ['Bordeaux', 'Pessac', 'Paris'],
            cfg.BV_CODE_COL: ['BV1', 'BV1', 'BV2'], # A and B in BV1, C in BV2
            cfg.BV_NAME_COL: ['Bassin Bordeaux', 'Bassin Bordeaux', 'Bassin Paris'],
            'population': [1000, 1000, 1000],
            'dep_code': ['33', '33', '75'],
            'reg_code': ['75', '75', '11'],
            'epci_code': ['EPCI1', 'EPCI1', 'EPCI2'],
            'epci_nom': ['EPCI 1', 'EPCI 1', 'EPCI 2']
        }
        df_communes = gpd.GeoDataFrame(
            communes_data, 
            geometry=[
                Point(-0.5792, 44.8378), # Bordeaux
                Point(-0.6314, 44.8067), # Pessac (nearby)
                Point(2.3522, 48.8566)   # Paris (far)
            ],
            crs="EPSG:4326"
        ).to_crs(cfg.PROJECTED_CRS).set_index('codgeo') # Project to 2154
        
        # Bassins de vie Geometry
        bv_data = {
            cfg.BV_CODE_COL: ['BV1', 'BV2'],
            cfg.BV_NAME_COL: ['Bassin Bordeaux', 'Bassin Paris']
        }
        df_bv_geo = gpd.GeoDataFrame(
            bv_data,
            geometry=[
                Polygon([(-1, 44), (-1, 45), (0, 45), (0, 44)]), # Covers Bordeaux
                Polygon([(2, 48), (2, 49), (3, 49), (3, 48)])    # Covers Paris
            ],
            crs="EPSG:4326"
        ).to_crs(cfg.PROJECTED_CRS).set_index(cfg.BV_CODE_COL) # Project to 2154
        
        # Area Geometry (Department)
        df_area_geo = gpd.GeoDataFrame(
            {'code': ['01'], 'type': ['departement']},
            geometry=[Polygon([(-1,-1), (-1,12), (12,12), (12,-1)])],
            crs="EPSG:4326"
        ).to_crs(cfg.PROJECTED_CRS).set_index(['type', 'code']) # Project to 2154
        
        # Config
        config = ScoringConfig(
            commune_actuelle='A', # Current commune is A (in BV1)
            loc_distance_km=1000,
            poids_emploi=100,
            poids_logement=0,
            poids_education=0,
            poids_sante=0,
            poids_inclusion=0,
            poids_mobilité=0,
            nb_adultes=1,
            nb_enfants=0,
            hebergement='Location',
            logement='Location',
            codes_metiers=[[]],
            codes_formations=[[]],
            classe_enfants=[],
            besoin_sante='Aucun',
            besoins_autres={},
            inc_services_core_selection=[],
            inc_asso_add_selection=[],
            binome_penalty=0.0,
            pop_min=0,
            criteria_weights={}
        )
        
        # Mock Scores Catalog
        scores_cat = pd.DataFrame({
            'cat': ['emploi'],
            'score': ['met_scaled'],
            'metric': ['met_ratio'],
            'weight': [1.0],
            'min_bound': [0.0],
            'max_bound': [1.0],
            'incl_binome': [False]
        })
        
        # Mock Global Stats
        global_stats = {'met_scaled': {'min': 0.0, 'max': 1.0}}
        
        # Mock Inclusion Index & Associations (Empty)
        incl_index = pd.DataFrame()
        associations_data = pd.DataFrame(columns=['codgeo', 'id_waldec', 'count'])
        
        # Add dummy score column to communes
        df_communes['met_ratio'] = 0.5
        df_communes['log_vac'] = 10
        df_communes['log_total'] = 100
        df_communes['pol_num'] = 1
        df_communes['pop_be'] = 1000
        df_communes['log_soc_total'] = 100
        df_communes['log_soc_inoccupes'] = 10
        df_communes['rp_5+pieces'] = 50
        df_communes['log_rp'] = 100
        
        # Mock new required DataFrames
        bmo_vertical = pd.DataFrame(columns=['codgeo', 'fap_code', 'count'])
        formations_data = pd.DataFrame(columns=['codgeo', 'formation_code', 'count'])
        codformations_index = pd.DataFrame(columns=['label'])

        # Run Pipeline
        engine = scoring.ScoringEngine(
            df_all_communes=df_communes,
            df_bv_geo=df_bv_geo,
            df_area_geo=df_area_geo,
            scores_cat=scores_cat,
            incl_index=incl_index,
            associations_data=associations_data,
            bmo_vertical=bmo_vertical,
            formations_data=formations_data,
            codformations_index=codformations_index,
            global_stats=global_stats
        )

        processed_gdf, _ = engine.run(
            config=config,
            view_level='Bassins de vie'
        )
        
        # Assertions
        # BV1 (containing A) should be EXCLUDED.
        assert 'BV1' not in processed_gdf[cfg.BV_CODE_COL].values, "BV1 (containing current commune) should be excluded from results"
        assert 'BV2' in processed_gdf[cfg.BV_CODE_COL].values, "BV2 should be included"


@pytest.mark.unit
class TestInclusionScoringLogic:
    """Tests from test_inclusion_scoring.py focusing on specific inclusion components."""

    @pytest.fixture
    def mock_associations_data(self):
        return pd.DataFrame({
            'codgeo': ['33063', '33063', '64445'],
            'id_waldec': ['009010', '011000', '009010'], # 009010: Activités manuelles, 011000: Sports
            'count': [5, 10, 2]
        })

    @pytest.fixture
    def mock_incl_index(self):
        data = {
            'codgeo': ['33063', '64445'],
            'key': [{'social_aide', 'admin_mairie'}, {'social_aide'}]
        }
        return pd.DataFrame(data).set_index('codgeo')
    
    @pytest.fixture
    def mock_geo_df(self):
        data = {
            'codgeo': ['33063', '64445'],
            'population': [1000, 500],
            'pop_be': [1000, 500],
            'lien_social_count': [10, 5],
            'lien_social_density': [10.0, 10.0],
            'inc_asso_core_scaled': [0.5, 0.5],
            'inc_services_core_scaled': [1.0, 0.5]
        }
        return gpd.GeoDataFrame(data).set_index('codgeo')

    def test_compute_inclusion_score_socle_admin(self, mock_geo_df, mock_incl_index, mock_associations_data, sample_scores_cat, global_stats):
        """Tests Socle Administratif score calculation."""
        from types import SimpleNamespace
        prefs = SimpleNamespace(
            inc_services_core_selection=['social_aide', 'admin_mairie'],
            inc_asso_add_selection=[],
            besoins_autres=[]
        )
        
        scores = scoring.compute_inclusion_score(mock_geo_df, prefs, mock_incl_index, mock_associations_data, sample_scores_cat, global_stats)
        
        assert scores.loc['33063', 'inc_services_core_scaled'] == 1.0
        assert scores.loc['64445', 'inc_services_core_scaled'] == 0.5

    def test_compute_inclusion_score_affinite(self, mock_geo_df, mock_incl_index, mock_associations_data, sample_scores_cat, global_stats):
        """Tests Affinité score calculation."""
        from types import SimpleNamespace
        prefs = SimpleNamespace(
            inc_services_core_selection=[],
            inc_asso_add_selection=['Bricolage / Création'],
            besoins_autres=[]
        )
        
        scores = scoring.compute_inclusion_score(mock_geo_df, prefs, mock_incl_index, mock_associations_data, sample_scores_cat, global_stats)
        
        assert 'inc_asso_add_scaled' in scores.columns
        assert scores.loc['33063', 'inc_asso_add_scaled'] >= 0
        assert scores.loc['64445', 'inc_asso_add_scaled'] >= 0
        
        prefs_sport = SimpleNamespace(
            inc_services_core_selection=[],
            inc_asso_add_selection=['Sport (Général)'],
            besoins_autres=[]
        )
        scores_sport = scoring.compute_inclusion_score(mock_geo_df, prefs_sport, mock_incl_index, mock_associations_data, sample_scores_cat, global_stats)
        
        assert scores_sport.loc['33063', 'inc_asso_add_scaled'] > scores_sport.loc['64445', 'inc_asso_add_scaled']

    def test_compute_inclusion_score_components(self, mock_geo_df, mock_incl_index, mock_associations_data, sample_scores_cat, global_stats):
        """Tests that all inclusion components are present."""
        from types import SimpleNamespace
        prefs = SimpleNamespace(
            inc_services_core_selection=['social_aide'], 
            inc_asso_add_selection=['Bricolage / Création'],
            besoins_autres=[]
        )
        
        scores = scoring.compute_inclusion_score(mock_geo_df, prefs, mock_incl_index, mock_associations_data, sample_scores_cat, global_stats)
        
        assert 'inc_services_core_scaled' in scores.columns
        assert 'inc_asso_core_scaled' in scores.columns
        assert 'inc_asso_add_scaled' in scores.columns


@pytest.mark.unit
class TestHousingScoresLogic:
    """Tests from test_scoring_logement_v2.py focusing on housing scores exclusion."""

    def test_housing_scores_exclusion_logic(self):
        """
        Verifies that housing scores are actually DROPPED (not just NaNs) and thus excluded from category scoring.
        """
        # Create a small dummy DF with both monome and binome columns
        data = {
            'codgeo': ['A', 'B'],
            'log_vac_scaled': [0.5, 0.6],
            'log_vac_scaled_binome': [0.4, 0.4],
            'log_soc_inoc_scaled': [0.7, 0.8],
            'log_soc_inoc_scaled_binome': [0.1, 0.1],
            'log_occup_scaled': [0.9, 0.2],
            'log_occup_scaled_binome': [0.5, 0.5],
            'met_scaled': [0.5, 0.5], 
            'inc_services_core_scaled': [0.0, 0.0],
            'inc_asso_core_scaled': [0.0, 0.0],
            'inc_population_scaled': [0.0, 0.0],
            'inc_pol_scaled': [0.0, 0.0],
            'dist_current_loc': [1000, 1000],
            'epci_code': ['1', '2']
        }
        df = gpd.GeoDataFrame(data, index=['A', 'B'])

        engine = scoring.ScoringEngine(
            df_all_communes=gpd.GeoDataFrame({'epci_code': ['1']}, index=['A']),
            df_bv_geo=gpd.GeoDataFrame(),
            df_area_geo=gpd.GeoDataFrame(),
            scores_cat=pd.DataFrame(),
            incl_index=pd.DataFrame(),
            associations_data=pd.DataFrame({'codgeo': ['A'], 'id_waldec': ['W1'], 'count': [1]}),
            bmo_vertical=pd.DataFrame({'codgeo': ['A'], 'fap_code': ['F1'], 'count': [1]}),
            formations_data=pd.DataFrame({'codgeo': ['A'], 'formation_code': ['F1'], 'count': [1]}),
            codformations_index=pd.DataFrame(columns=['label']),
            global_stats={}
        )

        def run_scoring(hebergement, logement):
            config = ScoringConfig(
                hebergement=hebergement,
                logement=logement,
                nb_adultes=1,
                nb_enfants=0,
                commune_actuelle='A',
                codes_metiers=[[]],
                codes_formations=[[]],
                classe_enfants=[],
                loc_distance_km=20,
                inc_asso_add_selection=[],
                besoins_autres=[],
                inc_services_core_selection=[],
                poids_emploi=0, poids_logement=100, poids_education=0, poids_inclusion=0, poids_sante=0, poids_mobilité=0,
                criteria_weights={}, binome_penalty=0.0, pop_min=0, besoin_sante="Aucun"
            )
            df_copy = df.copy()
            return engine._compute_criteria_scores(df_copy, config)

        # 1. Test: Location + Location -> Keep log_vac, Drop others
        res1 = run_scoring('Location', 'Location')
        assert 'log_vac_scaled' in res1.columns
        assert 'log_vac_scaled_binome' in res1.columns
        assert 'log_soc_inoc_scaled' not in res1.columns
        assert 'log_soc_inoc_scaled_binome' not in res1.columns
        assert 'log_occup_scaled' not in res1.columns
        assert 'log_occup_scaled_binome' not in res1.columns

        # 2. Test: Chez l'habitant + Logement Social -> Keep occup & soc_inoc. Drop vac.
        res2 = run_scoring("Chez l'habitant", 'Logement Social')
        assert 'log_vac_scaled' not in res2.columns
        assert 'log_vac_scaled_binome' not in res2.columns
        assert 'log_occup_scaled' in res2.columns
        assert 'log_occup_scaled_binome' in res2.columns
        assert 'log_soc_inoc_scaled' in res2.columns
        assert 'log_soc_inoc_scaled_binome' in res2.columns

