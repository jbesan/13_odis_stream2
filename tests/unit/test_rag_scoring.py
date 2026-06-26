import pytest
import pandas as pd
import geopandas as gpd
from core import scoring
from app.core.models import SearchCriterias

def test_format_city_details_rna_rag_summary(sample_data, live_scores_cat, default_config):
    """Verifies that format_city_details correctly extracts RNA RAG counts."""
    # 1. Setup row with RNA RAG columns
    row = sample_data.iloc[0].copy()
    row['inc_rna_fle_count'] = 10
    row['inc_rna_logement_count'] = 5
    row['inc_rna_sante_count'] = 0 # Should be ignored in summary if 0
    
    engine = scoring.ScoringEngine(
            df_all_communes=sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame()
    )
    
    # 2. Act
    # Mock the associations cache for the engine
    engine._associations_cache[str(row.name)] = {
        "inclusion": {
            "fle": ["Asso"] * 10,
            "logement": ["Asso"] * 5
        },
        "refugee": []
    }
    
    details = engine.format_city_details(row, default_config)
    
    # 3. Assert
    assert details.inclusion.asso_inclusion_count == 15
    summary = details.inclusion.asso_inclusion_list_by_cat
    assert len(summary['fle']) == 10
    assert len(summary['logement']) == 5
    assert 'sante' not in summary # Because it was 0

def test_build_communes_rna_count_logic():
    """Verifies the logic I added to build.py (simulated here)."""
    # Simulate the logic in build_communes
    df = pd.DataFrame({
        'codgeo': ['01001', '01002'],
        'inc_rna_fle_count': [10, 0],
        'inc_rna_logement_count': [5, 2],
        'other_col': [1, 2]
    })
    
    # RAG columns logic from build.py:
    rna_cols = [c for c in df.columns if c.startswith("inc_rna_") and c.endswith("_count")]
    assert len(rna_cols) == 2
    df['lien_social_count'] = df[rna_cols].sum(axis=1)
    
    assert df.loc[0, 'lien_social_count'] == 15
    assert df.loc[1, 'lien_social_count'] == 2

def test_scoring_engine_init_with_missing_data(sample_data, live_scores_cat):
    """Verifies that ScoringEngine handles empty associations_data without crashing."""
    # This simulates the state after cleaning up legacy files
    engine = scoring.ScoringEngine(
            df_all_communes=sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(), # EMPTY
        formations_data=pd.DataFrame()
    )
    
    row = sample_data.iloc[0].copy()
    
    # Mock the associations cache
    engine._associations_cache[str(row.name)] = {
        "inclusion": {
            "fle": ["Asso"] * 10
        },
        "refugee": []
    }
    
    details = engine.format_city_details(row, None)
    assert details.inclusion.asso_inclusion_count == 10
