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
    
    assert isinstance(history_17, dict)
    assert "municipales" in history_17
    assert "presidentielles" in history_17

    muni_17 = history_17["municipales"]
    pres_17 = history_17["presidentielles"]

    # Municipales 2026 T1 and Municipales 2020 T1
    assert len(muni_17) == 2
    assert muni_17[0]["election"] == "Municipales 2026"
    assert muni_17[0]["tour"] == "1er tour"
    assert muni_17[0]["nuance"] == "Parti Socialiste"
    assert round(muni_17[0]["percentage"], 1) == 71.4

    assert muni_17[1]["election"] == "Municipales 2020"
    assert muni_17[1]["tour"] == "1er tour"
    assert muni_17[1]["nuance"] == "Union de la Gauche"

    # Présidentielle 2022 T2 and Présidentielle 2017 T1
    assert len(pres_17) == 2
    assert pres_17[0]["election"] == "Présidentielle 2022"
    assert pres_17[0]["tour"] == "2nd tour"
    assert pres_17[0]["nuance"] == "MACRON"

    assert pres_17[1]["election"] == "Présidentielle 2017"
    assert pres_17[1]["tour"] == "1er tour"
    assert pres_17[1]["nuance"] == "La France Insoumise"

    # Validate Bordeaux (33063) fallback labels and tour
    row_33 = df_out[df_out["codgeo"] == "33063"].iloc[0]
    history_33 = json.loads(row_33["electoral_history"])
    assert isinstance(history_33, dict)

    muni_33 = history_33["municipales"]
    pres_33 = history_33["presidentielles"]

    assert len(muni_33) == 1
    assert muni_33[0]["election"] == "Municipales 2026"
    assert muni_33[0]["tour"] == "1er tour"
    assert muni_33[0]["nuance"] == "L-GAUCHE"
    assert round(muni_33[0]["percentage"], 1) == 66.7

    assert len(pres_33) == 1
    assert pres_33[0]["election"] == "Présidentielle 2022"
    assert pres_33[0]["tour"] == "1er tour"
    assert pres_33[0]["nuance"] == "MÉLENCHON"
    assert pres_33[0]["percentage"] == 100.0

