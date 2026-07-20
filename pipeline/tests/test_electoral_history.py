import os
import json
import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import patch, MagicMock

from pipeline.ingest import clean_electoral_history, PipelineLogger

def test_clean_electoral_history(tmp_path):
    """
    Unit test for clean_electoral_history using a mocked dataset.
    """
    clean_dir = tmp_path / "clean"
    clean_dir.mkdir()
    
    # Configure mock directories
    config = {
        "sources": {
            "electoral_history": {
                "local_name": "candidats_results.parquet"
            }
        }
    }
    
    # Create fake electoral data
    # We want to test:
    # 1. Filtering out non-major elections (like 2021_dpmt_t1)
    # 2. Aggregating votes for same commune + election
    # 3. Formatting winner labels using NUANCE_LABELS map and fallback to lists/candidates
    # 4. Limit to 5 latest elections sorted chronologically (newest first)
    fake_data = pd.DataFrame([
        # Commune 1: Saint-Jean-d'Angély (17347)
        {"id_election": "2026_muni_t1", "code_commune": "17347", "nuance": "LSOC", "libelle_abrege_liste": "", "nom": "", "voix": 500},
        {"id_election": "2026_muni_t1", "code_commune": "17347", "nuance": "RN", "libelle_abrege_liste": "", "nom": "", "voix": 200},
        
        # Non-major election to filter out
        {"id_election": "2021_dpmt_t1", "code_commune": "17347", "nuance": "DVD", "libelle_abrege_liste": "", "nom": "", "voix": 1000},
        
        # Major elections for 17347
        {"id_election": "2024_legi_t2", "code_commune": "17347", "nuance": "UG", "libelle_abrege_liste": "", "nom": "", "voix": 600},
        {"id_election": "2024_legi_t2", "code_commune": "17347", "nuance": "ENS", "libelle_abrege_liste": "", "nom": "", "voix": 400},
        {"id_election": "2024_legi_t1", "code_commune": "17347", "nuance": "UG", "libelle_abrege_liste": "", "nom": "", "voix": 300},
        {"id_election": "2024_euro_t1", "code_commune": "17347", "nuance": "LRN", "libelle_abrege_liste": "", "nom": "", "voix": 450},
        {"id_election": "2022_legi_t2", "code_commune": "17347", "nuance": "LENS", "libelle_abrege_liste": "", "nom": "", "voix": 550},
        {"id_election": "2022_legi_t2", "code_commune": "17347", "nuance": "RN", "libelle_abrege_liste": "", "nom": "", "voix": 450},
        
        # An older major election that should be cut off (only 5 latest kept)
        {"id_election": "2022_pres_t2", "code_commune": "17347", "nuance": "", "libelle_abrege_liste": "", "nom": "MACRON", "voix": 800},
        
        # Commune 2: Bordeaux (33063) - test fallback labels
        {"id_election": "2026_muni_t1", "code_commune": "33063", "nuance": None, "libelle_abrege_liste": "L-GAUCHE", "nom": None, "voix": 100},
        {"id_election": "2026_muni_t1", "code_commune": "33063", "nuance": None, "libelle_abrege_liste": "L-DROITE", "nom": None, "voix": 50},
        {"id_election": "2022_pres_t1", "code_commune": "33063", "nuance": None, "libelle_abrege_liste": None, "nom": "MÉLENCHON", "voix": 200},
    ])
    
    logger = MagicMock(spec=PipelineLogger)
    
    with (
        patch("pandas.read_parquet") as mock_read_parquet,
        patch("pipeline.ingest.CLEAN_DIR", clean_dir)
    ):
        mock_read_parquet.return_value = fake_data
        clean_electoral_history(config, logger)
        
    output_path = clean_dir / "electoral_history.parquet"
    assert output_path.exists()
    
    df_out = pd.read_parquet(output_path)
    assert len(df_out) == 2
    assert "codgeo" in df_out.columns
    assert "electoral_history" in df_out.columns
    
    # Validate Saint-Jean-d'Angély (17347)
    row_17 = df_out[df_out["codgeo"] == "17347"].iloc[0]
    history_17 = json.loads(row_17["electoral_history"])
    
    # Should have exactly 5 elements
    assert len(history_17) == 5
    
    # 1. 2026 Muni T1: LSOC -> Parti Socialiste (500 / 700 = 71.4%)
    assert history_17[0]["election"] == "Municipales 2026"
    assert history_17[0]["nuance"] == "Parti Socialiste"
    assert round(history_17[0]["percentage"], 1) == 71.4
    
    # 2. 2024 Legi T2: UG -> Union de la Gauche (600 / 1000 = 60.0%)
    assert history_17[1]["election"] == "Législatives 2024"
    assert history_17[1]["nuance"] == "Union de la Gauche"
    assert history_17[1]["percentage"] == 60.0
    
    # 3. 2024 Legi T1: UG -> Union de la Gauche
    assert history_17[2]["election"] == "Législatives 2024"
    
    # 4. 2024 Euro T1: LRN -> Rassemblement National
    assert history_17[3]["election"] == "Européennes 2024"
    assert history_17[3]["nuance"] == "Rassemblement National"
    
    # 5. 2022 Legi T2: LENS -> Ensemble
    assert history_17[4]["election"] == "Législatives 2022"
    assert history_17[4]["nuance"] == "Ensemble"
    
    # Older one (2022 Pres T2) should have been truncated
    elections_17 = [item["election"] for item in history_17]
    assert "Présidentielle 2022" not in elections_17
    
    # Validate Bordeaux (33063) fallback labels
    row_33 = df_out[df_out["codgeo"] == "33063"].iloc[0]
    history_33 = json.loads(row_33["electoral_history"])
    assert len(history_33) == 2
    
    # 1. 2026 Muni T1: L-GAUCHE (libelle_abrege_liste fallback)
    assert history_33[0]["election"] == "Municipales 2026"
    assert history_33[0]["nuance"] == "L-GAUCHE"
    assert round(history_33[0]["percentage"], 1) == 66.7
    
    # 2. 2022 Pres T1: MÉLENCHON (nom fallback)
    assert history_33[1]["election"] == "Présidentielle 2022"
    assert history_33[1]["nuance"] == "MÉLENCHON"
    assert history_33[1]["percentage"] == 100.0
