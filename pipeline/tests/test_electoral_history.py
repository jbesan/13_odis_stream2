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
    # 1. Filtering to keep ONLY Municipales (muni) and Présidentielles (pres)
    # 2. Filtering out legi, euro, dpmt, etc.
    # 3. Aggregating votes for same commune + election
    # 4. Formatting winner labels using NUANCE_LABELS map and fallback to lists/candidates
    fake_data = pd.DataFrame([
        # Commune 1: Saint-Jean-d'Angély (17347)
        {"id_election": "2026_muni_t1", "code_commune": "17347", "nuance": "LSOC", "libelle_abrege_liste": "", "nom": "", "voix": 500},
        {"id_election": "2026_muni_t1", "code_commune": "17347", "nuance": "RN", "libelle_abrege_liste": "", "nom": "", "voix": 200},
        
        # Elections to filter out (dpmt, legi, euro)
        {"id_election": "2021_dpmt_t1", "code_commune": "17347", "nuance": "DVD", "libelle_abrege_liste": "", "nom": "", "voix": 1000},
        {"id_election": "2024_legi_t2", "code_commune": "17347", "nuance": "UG", "libelle_abrege_liste": "", "nom": "", "voix": 600},
        {"id_election": "2024_euro_t1", "code_commune": "17347", "nuance": "LRN", "libelle_abrege_liste": "", "nom": "", "voix": 450},
        
        # Kept elections (pres and muni)
        {"id_election": "2022_pres_t2", "code_commune": "17347", "nuance": "", "libelle_abrege_liste": "", "nom": "MACRON", "voix": 800},
        {"id_election": "2020_muni_t1", "code_commune": "17347", "nuance": "UG", "libelle_abrege_liste": "", "nom": "", "voix": 400},
        {"id_election": "2017_pres_t1", "code_commune": "17347", "nuance": "FI", "libelle_abrege_liste": "", "nom": "", "voix": 350},
        
        # Commune 2: Bordeaux (33063) - test fallback labels
        {"id_election": "2026_muni_t1", "code_commune": "33063", "nuance": None, "libelle_abrege_liste": "L-GAUCHE", "nom": None, "voix": 100},
        {"id_election": "2026_muni_t1", "code_commune": "33063", "nuance": None, "libelle_abrege_liste": "L-DROITE", "nom": None, "voix": 50},
        {"id_election": "2022_pres_t1", "code_commune": "33063", "nuance": None, "libelle_abrege_liste": None, "nom": "MÉLENCHON", "voix": 200},
    ])
    
    logger = MagicMock(spec=PipelineLogger)
    
    def side_effect_read_parquet(path, columns=None, filters=None, engine=None):
        if columns == ["id_election"]:
            return fake_data[["id_election"]]
        if filters:
            allowed = filters[0][2]
            return fake_data[fake_data["id_election"].isin(allowed)]
        return fake_data

    with (
        patch("pandas.read_parquet", side_effect=side_effect_read_parquet),
        patch("pipeline.ingest.CLEAN_DIR", clean_dir)
    ):
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
    
    # Should have 4 kept elections (muni 2026, pres 2022, muni 2020, pres 2017)
    assert len(history_17) == 4
    
    # 1. 2026 Muni T1: LSOC -> Parti Socialiste (500 / 700 = 71.4%)
    assert history_17[0]["election"] == "Municipales 2026"
    assert history_17[0]["nuance"] == "Parti Socialiste"
    assert round(history_17[0]["percentage"], 1) == 71.4
    
    # 2. 2022 Pres T2: MACRON
    assert history_17[1]["election"] == "Présidentielle 2022"
    assert history_17[1]["nuance"] == "MACRON"
    
    # 3. 2020 Muni T1: UG -> Union de la Gauche
    assert history_17[2]["election"] == "Municipales 2020"
    assert history_17[2]["nuance"] == "Union de la Gauche"
    
    # 4. 2017 Pres T1: FI -> La France Insoumise
    assert history_17[3]["election"] == "Présidentielle 2017"
    assert history_17[3]["nuance"] == "La France Insoumise"
    
    # Ensure legi and euro are NOT present
    elections_17 = [item["election"] for item in history_17]
    assert "Législatives 2024" not in elections_17
    assert "Européennes 2024" not in elections_17
    
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

