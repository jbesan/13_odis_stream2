import os
import pandas as pd
import pytest
from pathlib import Path
import app.config as cfg

OUTPUT_DIR = Path("pipeline/cache/output")

@pytest.fixture
def plm_mapping():
    return {
        '75056': [str(x) for x in range(75101, 75121)], # Paris
        '13055': [str(x) for x in range(13201, 13217)], # Marseille
        '69123': [str(x) for x in range(69381, 69390)]  # Lyon
    }

def test_communes_plm_consolidation(plm_mapping):
    """Verify that odis_communes.parquet is free from arrondissements and has consolidated parent metrics."""
    communes_path = OUTPUT_DIR / "odis_communes.parquet"
    if not communes_path.exists():
        pytest.skip("odis_communes.parquet does not exist yet. Run the pipeline first.")
        
    df = pd.read_parquet(communes_path, engine='fastparquet')
    
    # Assert 'codgeo' exists
    assert 'codgeo' in df.columns
    
    # Verify no child arrondissements exist in communes
    all_children = []
    for children in plm_mapping.values():
        all_children.extend(children)
        
    arrondissements_found = df[df['codgeo'].isin(all_children)]
    assert arrondissements_found.empty, f"Found individual PLM arrondissements in communes dataset: {arrondissements_found['codgeo'].unique()}"
    
    # Verify parents exist
    for parent in plm_mapping.keys():
        parent_rows = df[df['codgeo'] == parent]
        assert not parent_rows.empty, f"Parent code {parent} is missing from communes dataset"
        
        row = parent_rows.iloc[0]
        
        # Verify key metrics are non-zero/non-null (ensuring consolidation took place)
        assert row['population'] > 100000, f"Parent {parent} has suspiciously low population: {row['population']}"
        assert row['sante_apl'] > 0, f"Parent {parent} has sante_apl = {row['sante_apl']}"
        assert row['edu_pe_tx_couverture'] > 0, f"Parent {parent} has edu_pe_tx_couverture = {row['edu_pe_tx_couverture']}"
        
        # Verify that scaled outputs derived from consolidated counts are positive
        assert 'inc_siae_density_scaled' in df.columns
        assert row['inc_siae_density_scaled'] > 0, f"Parent {parent} has 0 or missing inc_siae_density_scaled"
        
        assert 'mob_gare_scaled' in df.columns
        assert row['mob_gare_scaled'] > 0, f"Parent {parent} has 0 or missing mob_gare_scaled"
        
        # Sante RDV delay / medical metrics should be populated
        assert pd.notnull(row['sante_apl']), f"Parent {parent} has NaN sante_apl"
        assert pd.notnull(row['edu_pe_tx_couverture']), f"Parent {parent} has NaN edu_pe_tx_couverture"

def test_vertical_tables_plm_consolidation(plm_mapping):
    """Verify that vertical tables contain parent data and no arrondissement data."""
    all_children = []
    for children in plm_mapping.values():
        all_children.extend(children)
        
    vertical_files = [
        ("odis_associations_agg.parquet", "codgeo"),
        ("odis_formations_agg.parquet", "codgeo"),
        ("odis_ccas.parquet", "codgeo"),
        ("odis_refugee_associations.parquet", "codgeo"),
        ("odis_pois.parquet", "codgeo")
    ]
    
    for filename, codgeo_col in vertical_files:
        filepath = OUTPUT_DIR / filename
        if not filepath.exists():
            continue
            
        df = pd.read_parquet(filepath, engine='fastparquet')
        df[codgeo_col] = df[codgeo_col].astype(str)
        
        # Assert no child arrondissements exist in vertical table
        arr_rows = df[df[codgeo_col].isin(all_children)]
        assert arr_rows.empty, f"Found child arrondissements in {filename}: {arr_rows[codgeo_col].unique()}"
        
        # Assert parent codes exist in vertical table
        for parent in plm_mapping.keys():
            parent_rows = df[df[codgeo_col] == parent]
            assert not parent_rows.empty, f"Parent code {parent} is missing from {filename}"
            
            # Additional validation: count or records should be positive
            if filename == "odis_associations_agg.parquet":
                assert parent_rows['count'].sum() > 0, f"Parent {parent} has 0 associations in {filename}"
            elif filename == "odis_formations_agg.parquet":
                assert parent_rows['count'].sum() > 0, f"Parent {parent} has 0 formations in {filename}"
            elif filename == "odis_pois.parquet":
                assert len(parent_rows) > 0, f"Parent {parent} has 0 POIs in {filename}"
