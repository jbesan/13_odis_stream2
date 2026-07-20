import pytest
import pandas as pd
import json
import logging
from pathlib import Path
from pipeline.ctai import compute_ctai_scores

def test_compute_ctai_scores_missing_file(caplog):
    # Prepare dummy communes DataFrame
    communes_df = pd.DataFrame({
        "codgeo": ["75056", "38185"],
        "dep_code": ["75", "38"],
        "reg_code": ["11", "84"],
        "commune_name": ["Paris", "Grenoble"]
    })
    
    # Run with a non-existent path to trigger soft fail
    with caplog.at_level(logging.WARNING):
        scores = compute_ctai_scores(
            communes_df=communes_df,
            cache_raw_dir=Path("pipeline/cache/raw"),
            json_path=Path("pipeline/data_private/non_existent_ctai_file.json")
        )
    
    assert "CTAI signatories JSON file not found" in caplog.text
    assert len(scores) == 2
    assert (scores == 0.0).all()

def test_compute_ctai_scores_mocked(tmp_path):
    # Create temporary mock regional, departmental, and EPCI files
    regions_file = tmp_path / "referentiel_regions.json"
    regions_file.write_text(json.dumps([
        {"REG": "11", "LIBELLE": "Île-de-France"},
        {"REG": "24", "LIBELLE": "Centre-Val de Loire"}
    ]))
    
    depts_file = tmp_path / "referentiel_departements.json"
    depts_file.write_text(json.dumps([
        {"DEP": "10", "LIBELLE": "Aube", "REG": "44"},
        {"DEP": "33", "LIBELLE": "Gironde", "REG": "75"}  # dummy
    ]))
    
    epci_file = tmp_path / "ref_epci.json"
    epci_file.write_text(json.dumps([
        {
            "code": "243300315",
            "nom": "Bordeaux Métropole",
            "membres": [
                {"code": "33063", "nom": "Bordeaux"},
                {"code": "33056", "nom": "Blanquefort"}
            ]
        },
        {
            "code": "242100410",
            "nom": "Dijon Métropole",
            "membres": [
                {"code": "21231", "nom": "Dijon"},
                {"code": "21105", "nom": "Chenôve"}
            ]
        }
    ]))
    
    # Create mock JSON signatories file
    ctai_data = {
        "regions": ["Centre-Val de Loire"],
        "departements": ["Aube"],
        "epcis": ["Bordeaux", "Sijon"],  # Sijon should be corrected to dijon
        "communes": ["Brest", "Rennes"]  # dummy communes that won't resolve if not in communes_df, but we put Rennes to check
    }
    
    json_path = tmp_path / "ctai_signataires.json"
    json_path.write_text(json.dumps(ctai_data, ensure_ascii=False, indent=2))
    
    # Create communes DataFrame
    # 75056 is Paris (not a member, gets 0.0)
    # 10081 is Troyes (in Aube dept, gets 0.5)
    # 33063 is Bordeaux (member EPCI, gets 1.0)
    # 33056 is Blanquefort (member EPCI, gets 1.0)
    # 21231 is Dijon (member EPCI via "Sijon" typo correction, gets 1.0)
    # 18033 is Bourges (in Centre-Val de Loire region, gets 0.5)
    communes_df = pd.DataFrame({
        "codgeo": ["75056", "10081", "33063", "33056", "21231", "18033"],
        "dep_code": ["75", "10", "33", "33", "21", "18"],
        "reg_code": ["11", "44", "75", "75", "27", "24"],
        "commune_name": ["Paris", "Troyes", "Bordeaux", "Blanquefort", "Dijon", "Bourges"]
    })
    
    # Run resolution
    scores = compute_ctai_scores(
        communes_df=communes_df,
        cache_raw_dir=tmp_path,
        json_path=json_path
    )
    
    # Check shape and mapping alignment
    assert len(scores) == len(communes_df)
    
    # Assertions
    scores_dict = dict(zip(communes_df["codgeo"], scores))
    assert scores_dict["75056"] == 0.0
    assert scores_dict["10081"] == 0.5
    assert scores_dict["33063"] == 1.0
    assert scores_dict["33056"] == 1.0
    assert scores_dict["21231"] == 1.0
    assert scores_dict["18033"] == 0.5

def test_compute_ctai_scores_plm_remapping(tmp_path):
    # Test that child PLM arrondissement codes are correctly remapped to parent codes.
    regions_file = tmp_path / "referentiel_regions.json"
    regions_file.write_text(json.dumps([]))
    depts_file = tmp_path / "referentiel_departements.json"
    depts_file.write_text(json.dumps([]))
    epci_file = tmp_path / "ref_epci.json"
    epci_file.write_text(json.dumps([]))
    
    # Marseille is a signing commune
    ctai_data = {
        "regions": [],
        "departements": [],
        "epcis": [],
        "communes": ["Marseille"]
    }
    json_path = tmp_path / "ctai_signataires.json"
    json_path.write_text(json.dumps(ctai_data, ensure_ascii=False, indent=2))
        
    # Communes DataFrame has arrondissement codes: 13201 (Marseille 1er) and parent 13055
    communes_df = pd.DataFrame({
        "codgeo": ["13055", "13201", "38185"],
        "dep_code": ["13", "13", "38"],
        "reg_code": ["93", "93", "84"],
        "commune_name": ["Marseille", "Marseille 1er", "Grenoble"]
    })
    
    scores = compute_ctai_scores(
        communes_df=communes_df,
        cache_raw_dir=tmp_path,
        json_path=json_path
    )
    
    scores_dict = dict(zip(communes_df["codgeo"], scores))
    # Arrondissement 13201 should map to Marseille and receive 1.0
    assert scores_dict["13055"] == 1.0
    assert scores_dict["13201"] == 1.0
    assert scores_dict["38185"] == 0.0
