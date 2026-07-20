import pytest
import pandas as pd
import json
import logging
from pathlib import Path
from pipeline.anvita import compute_anvita_scores, clean_and_normalize

def test_clean_and_normalize():
    assert clean_and_normalize("Percy (Le)") == "le percy"
    assert clean_and_normalize("Percy (Communauté de Communes)") == "percy"
    assert clean_and_normalize("Saint-Jean-d'Angély") == "saint jean d angely"
    assert clean_and_normalize("Saint-Jean-d’Angély") == "saint jean d angely"
    assert clean_and_normalize("Cd Gironde") == "gironde"
    assert clean_and_normalize("Vile de Grenoble") == "vile de grenoble"  # checking only exact prefix replacements

def test_compute_anvita_scores_missing_file(caplog):
    # Prepare dummy communes DataFrame
    communes_df = pd.DataFrame({
        "codgeo": ["75056", "38185"],
        "dep_code": ["75", "38"],
        "reg_code": ["11", "84"],
        "commune_name": ["Paris", "Grenoble"]
    })
    
    # Run with a non-existent path to trigger soft fail
    with caplog.at_level(logging.WARNING):
        scores = compute_anvita_scores(
            communes_df=communes_df,
            cache_raw_dir=Path("pipeline/cache/raw"),
            excel_path=Path("pipeline/data_private/non_existent_anvita_file.xlsx")
        )
    
    assert "ANVITA Excel file not found" in caplog.text
    assert len(scores) == 2
    assert (scores == 0.0).all()

def test_compute_anvita_scores_mocked(tmp_path):
    # Create temporary mock regional, departmental, and EPCI files
    regions_file = tmp_path / "referentiel_regions.json"
    regions_file.write_text(json.dumps([
        {"REG": "11", "LIBELLE": "Île-de-France"},
        {"REG": "84", "LIBELLE": "Auvergne-Rhône-Alpes"}
    ]))
    
    depts_file = tmp_path / "referentiel_departements.json"
    depts_file.write_text(json.dumps([
        {"DEP": "38", "LIBELLE": "Isère", "REG": "84"},
        {"DEP": "33", "LIBELLE": "Gironde", "REG": "75"}  # dummy
    ]))
    
    epci_file = tmp_path / "ref_epci.json"
    epci_file.write_text(json.dumps([
        {
            "code": "243800604",
            "nom": "Grenoble-Alpes Métropole",
            "membres": [
                {"code": "38185", "nom": "Grenoble"},
                {"code": "38169", "nom": "Eybens"}
            ]
        }
    ]))
    
    # Create mock Excel members file
    excel_data = {
        "Collectivité": ["GRENOBLE", "CD GIRONDE", "Région Île-de-France", "Grenoble-Alpes Métropole"],
        "Département": ["38", "33", "", ""]
    }
    excel_df = pd.DataFrame(excel_data)
    excel_path = tmp_path / "membres_anvita.xlsx"
    
    # Save mock Excel to tmp path (requires openpyxl)
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        excel_df.to_excel(writer, sheet_name="CT Membres ANVITA V2", index=False)
    
    # Create communes DataFrame
    # 75056 is Paris (in Île-de-France region, gets 0.5)
    # 38185 is Grenoble (member commune directly and in member EPCI, gets 1.0)
    # 38169 is Eybens (in member EPCI, gets 1.0)
    # 33063 is Bordeaux (in Gironde department, gets 0.5)
    # 69123 is Lyon (not a member, gets 0.0)
    communes_df = pd.DataFrame({
        "codgeo": ["75056", "38185", "38169", "33063", "69123"],
        "dep_code": ["75", "38", "38", "33", "69"],
        "reg_code": ["11", "84", "84", "75", "84"],
        "commune_name": ["Paris", "Grenoble", "Eybens", "Bordeaux", "Lyon"]
    })
    
    # Run resolution
    scores = compute_anvita_scores(
        communes_df=communes_df,
        cache_raw_dir=tmp_path,
        excel_path=excel_path
    )
    
    # Check shape and mapping alignment
    assert len(scores) == len(communes_df)
    
    # Assertions
    # Paris (75056) -> Region member -> 0.5
    # Grenoble (38185) -> Commune/EPCI member -> 1.0 (max score precedence over 0.5 of region)
    # Eybens (38169) -> EPCI member -> 1.0
    # Bordeaux (33063) -> Department member -> 0.5
    # Lyon (69123) -> No membership -> 0.0
    scores_dict = dict(zip(communes_df["codgeo"], scores))
    assert scores_dict["75056"] == 0.5
    assert scores_dict["38185"] == 1.0
    assert scores_dict["38169"] == 1.0
    assert scores_dict["33063"] == 0.5
    assert scores_dict["69123"] == 0.0

def test_compute_anvita_scores_plm_remapping(tmp_path):
    # Test that child PLM arrondissement codes are correctly remapped to parent codes.
    regions_file = tmp_path / "referentiel_regions.json"
    regions_file.write_text(json.dumps([]))
    depts_file = tmp_path / "referentiel_departements.json"
    depts_file.write_text(json.dumps([]))
    epci_file = tmp_path / "ref_epci.json"
    epci_file.write_text(json.dumps([]))
    
    # Paris is a member commune
    excel_data = {
        "Collectivité": ["PARIS"],
        "Département": ["75"]
    }
    excel_df = pd.DataFrame(excel_data)
    excel_path = tmp_path / "membres_anvita.xlsx"
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        excel_df.to_excel(writer, sheet_name="CT Membres ANVITA V2", index=False)
        
    # Communes DataFrame has arrondissement codes: 75101 (Paris 1er) and parent 75056
    communes_df = pd.DataFrame({
        "codgeo": ["75056", "75101", "38185"],
        "dep_code": ["75", "75", "38"],
        "reg_code": ["11", "11", "84"],
        "commune_name": ["Paris", "Paris 1er", "Grenoble"]
    })
    
    scores = compute_anvita_scores(
        communes_df=communes_df,
        cache_raw_dir=tmp_path,
        excel_path=excel_path
    )
    
    scores_dict = dict(zip(communes_df["codgeo"], scores))
    # Arrondissement 75101 should map to Paris and receive 1.0
    assert scores_dict["75056"] == 1.0
    assert scores_dict["75101"] == 1.0
    assert scores_dict["38185"] == 0.0
