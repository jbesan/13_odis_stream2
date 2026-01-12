import pytest
import pandas as pd
import geopandas as gpd
from core import scoring
from app.core.models import ScoringConfig

@pytest.fixture
def scoring_engine(sample_data, sample_scores_cat, sample_incl_index, global_stats):
    return scoring.ScoringEngine(
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

@pytest.fixture
def base_df(sample_data):
    df = sample_data.copy()
    # Add all potential education columns to the base DF to test pruning
    df['edu_petite_enfance_scaled'] = 0.5
    df['edu_maternelle_scaled'] = 0.5
    df['edu_elementaire_scaled'] = 0.5
    df['edu_college_scaled'] = 0.5
    df['edu_lycee_scaled'] = 0.5
    df['edu_classes_ferm_scaled'] = 0.5
    df['youth_decline_scaled'] = 0.5
    # Add optional inclusion columns to test pruning
    df['inc_asso_add_scaled'] = 0.5
    df['inc_services_add_scaled'] = 0.5
    return df

@pytest.fixture
def base_config():
    return ScoringConfig(
        poids_emploi=100,
        poids_logement=100,
        poids_education=100,
        poids_inclusion=100,
        poids_mobilité=100,
        poids_sante=100,
        criteria_weights={},
        commune_actuelle='33063',
        loc_search_area='departement',
        nb_adultes=1,
        nb_enfants=0,
        hebergement='Location',
        logement='Location',
        codes_metiers=[[]],
        codes_formations=[[]],
        classe_enfants=[],
        besoin_sante='Aucun',
        inc_services_add_selection=[],
        inc_services_core_selection=[],
        inc_asso_add_selection=[]
    )

def test_no_children_pruning(scoring_engine, base_df, base_config):
    """Scenario 1: nb_enfants = 0 -> Verify all edu columns and youth decline are removed."""
    config = base_config
    config.nb_enfants = 0
    
    scored_df = scoring_engine._compute_criteria_scores(base_df, config)
    
    edu_cols = [
        'edu_petite_enfance_scaled', 'edu_maternelle_scaled', 'edu_elementaire_scaled', 
        'edu_college_scaled', 'edu_lycee_scaled', 'edu_classes_ferm_scaled', 
        'youth_decline_scaled'
    ]
    for col in edu_cols:
        assert col not in scored_df.columns

def test_children_no_specific_levels_pruning(scoring_engine, base_df, base_config):
    """Scenario 2: nb_enfants = 1, classe_enfants = [] -> Verify specific school levels are removed."""
    config = base_config
    config.nb_enfants = 1
    config.classe_enfants = []
    
    scored_df = scoring_engine._compute_criteria_scores(base_df, config)
    
    school_levels = [
        'edu_petite_enfance_scaled', 'edu_maternelle_scaled', 'edu_elementaire_scaled', 
        'edu_college_scaled', 'edu_lycee_scaled'
    ]
    for col in school_levels:
        assert col not in scored_df.columns
    
    # General indicators should still be there (if children exist)
    assert 'edu_classes_ferm_scaled' in scored_df.columns
    # Note: youth_decline_scaled might not be in base_df if not pre-calculated, 
    # but here we ensured it's in base_df fixture.
    assert 'youth_decline_scaled' in scored_df.columns

def test_specific_level_retention(scoring_engine, base_df, base_config):
    """Scenario 3: nb_enfants = 1, classe_enfants = ['Maternelle'] -> Verify ONLY edu_maternelle_scaled remains among school levels."""
    config = base_config
    config.nb_enfants = 1
    config.classe_enfants = ['Maternelle']
    
    scored_df = scoring_engine._compute_criteria_scores(base_df, config)
    
    assert 'edu_maternelle_scaled' in scored_df.columns
    
    other_levels = [
        'edu_petite_enfance_scaled', 'edu_elementaire_scaled', 
        'edu_college_scaled', 'edu_lycee_scaled'
    ]
    for col in other_levels:
        assert col not in scored_df.columns

def test_alias_petite_enfance(scoring_engine, base_df, base_config):
    """Tests the 'Petite Enfance/Crêche' alias from the interviewer agent."""
    config = base_config
    config.nb_enfants = 1
    config.classe_enfants = ['Petite Enfance/Crêche']
    
    scored_df = scoring_engine._compute_criteria_scores(base_df, config)
    
    assert 'edu_petite_enfance_scaled' in scored_df.columns

def test_association_pruning(scoring_engine, base_df, base_config):
    """Scenario 4: empty inc_asso_add_selection -> Verify inc_asso_add_scaled is removed."""
    config = base_config
    config.inc_asso_add_selection = []
    
    scored_df = scoring_engine._compute_criteria_scores(base_df, config)
    
    assert 'inc_asso_add_scaled' not in scored_df.columns

def test_service_pruning(scoring_engine, base_df, base_config):
    """Scenario 5: empty inc_services_add_selection -> Verify inc_services_add_scaled is removed."""
    config = base_config
    config.inc_services_add_selection = []
    
    scored_df = scoring_engine._compute_criteria_scores(base_df, config)
    
    assert 'inc_services_add_scaled' not in scored_df.columns
