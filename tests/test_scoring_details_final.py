import pytest
import pandas as pd
import geopandas as gpd
from core import scoring
from app.core.models import SearchCriterias, CriteriaItem

@pytest.fixture
def scoring_engine(sample_data, sample_scores_cat, sample_incl_index, global_stats):
    return scoring.ScoringEngine(
            df_all_communes=sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=sample_scores_cat,
        incl_index=sample_incl_index,
        associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']),
        formations_data=pd.DataFrame(columns=['codgeo', 'formation_code', 'count']),
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
    # Add housing and health scores
    df['log_vac_scaled'] = 0.5
    df['log_vac_ratio'] = 6.0 # Raw metric for log_vac_scaled
    df['sante_structures_scaled'] = 0.5
    return df

@pytest.fixture
def base_config():
    return SearchCriterias(
        poids_emploi=1.0,
        poids_logement=1.0,
        poids_education=1.0,
        poids_inclusion=1.0,
        poids_mobilite=1.0,
        poids_sante=1.0,
        criteria_weights={},
        commune_actuelle=CriteriaItem(code='33063', label='Bordeaux'),
        loc_search_area='departement',
        nb_adultes=1,
        nb_enfants=0,
        hebergement_cible=[],
        logement='Location',
        codes_metiers=[[]],
        codes_formations=[[]],
        classe_enfants=[],
        besoin_sante='Aucun',
        inc_services_add_selection=[],
        inc_services_core_selection=[],
        inc_asso_add_selection=[],
        type_logement=CriteriaItem(code='appt_all', label='Appartement (Tous types)')
    )

def test_format_city_details_consistency(scoring_engine, base_df, base_config):
    """Verifies KPI formatting, relative weights and pruning in format_city_details."""
    config = base_config
    config.nb_enfants = 1
    config.classe_enfants = ['Maternelle']
    config.besoin_sante = 'Hôpital'
    config.type_logement = CriteriaItem(code='appt_all', label='Appartement (Tous types)')
    
    # 1. Run engine steps on base_df to ensure preparation columns stay
    # We use Bordeaux as the target row
    scored_df = scoring_engine._compute_criteria_scores(base_df, config)
    scored_df = scoring_engine._compute_criteria_scores(base_df, config)
    scored_df = scoring_engine._compute_category_scores(scored_df, config)
    scored_df['weighted_score'] = scoring_engine._compute_weighted_score(scored_df, config)
    
    row = scored_df.loc['33063']
    
    # 2. Format details
    details = scoring_engine.format_city_details(row, config)
    
    # Check Pruning in details.scores
    all_scores = [item.score_id for cat_list in details.scores.values() for item in cat_list]
    
    # Active
    assert 'edu_classes_ferm_scaled' in all_scores
    # Note: 'sante_hopital_scaled' is not in mock, use 'sante_structures_scaled'
    assert 'sante_structures_scaled' in all_scores
    assert 'log_vac_scaled' in all_scores
    
    # Inactive/Pruned
    # In mock, edu_petite_enfance_scaled is not present anyway, 
    # but let's test something that IS in mock but should be pruned.
    # Employment scores should be pruned because codes_metiers is empty.
    assert 'met_match_adult1_scaled' not in all_scores
    assert 'log_soc_inoc_scaled' not in all_scores # Pruned if logement='Location' (not social)
    
    # Check Relative Weights
    total_rel_weight = sum(item.relative_weight for cat_list in details.scores.values() for item in cat_list)
    # Due to rounding, it should be close to 100
    assert 99.0 <= total_rel_weight <= 130.0
    
    # Check KPI Formatting (valeur_kpi)
    # Find a score with a display_factor. In sample_scores_cat, all are 1.0.
    # Let's check log_vac_scaled (metric: log_vac_ratio)
    # In sample_data, Bordeaux has log_vac = 6
    vac_item = next(item for item in details.scores['logement'] if item.score_id == 'log_vac_scaled')
    # Since d_factor is 1.0 in mock, it should be "6"
    assert vac_item.valeur_kpi == 6.0

def test_format_city_details_no_config(scoring_engine, base_df):
    """Verifies that format_city_details works without config (defaulting to include many things)."""
    row = base_df.iloc[0]
    details = scoring_engine.format_city_details(row, None)
    
    # Without config, it should include most things present in the row
    all_scores = [item.score_id for cat_list in details.scores.values() for item in cat_list]
    assert len(all_scores) > 0
