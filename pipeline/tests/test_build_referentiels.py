import json
from unittest.mock import MagicMock

import pandas as pd

import pipeline.build as build


def test_generate_referentiels_falls_back_to_raw_regions(tmp_path, monkeypatch):
    clean_dir = tmp_path / "clean"
    raw_dir = tmp_path / "raw"
    output_dir = tmp_path / "output"
    clean_dir.mkdir()
    raw_dir.mkdir()
    output_dir.mkdir()

    # Run-scoped clean directories contain these two referentials, but not the
    # legacy regions.parquet file.
    pd.DataFrame({"codgeo": ["75056"], "nom": ["Paris"]}).to_parquet(
        clean_dir / "communes.parquet", engine="fastparquet"
    )
    pd.DataFrame(
        {
            "code": ["75", "69"],
            "label": ["Paris", "Rhône"],
            "reg_code": ["11", "84"],
        }
    ).to_parquet(clean_dir / "departements.parquet", engine="fastparquet")
    pd.DataFrame(
        {
            "geo_level": ["commune", "commune"],
            "region_code": ["11", "84"],
            "departement_code": ["75", "69"],
        }
    ).to_parquet(raw_dir / "odace_dim_geo.parquet", engine="fastparquet")
    (raw_dir / "referentiel_regions.json").write_text(
        json.dumps(
            [
                {"REG": "11", "LIBELLE": "Île-de-France"},
                {"REG": "84", "LIBELLE": "Auvergne-Rhône-Alpes"},
                {"REG": "93", "LIBELLE": "Provence-Alpes-Côte d'Azur"},
            ]
        ),
        encoding="utf-8",
    )
    pd.DataFrame(
        {
            "code": ["M1805", "K1302"],
            "label": ["Développement informatique", "Aide à domicile"],
        }
    ).to_parquet(raw_dir / "rome_referential_api.parquet", engine="fastparquet")
    monkeypatch.setattr(build, "CLEAN_DIR", clean_dir)
    monkeypatch.setattr(build, "CACHE_DIR", raw_dir)
    monkeypatch.setattr(build, "OUTPUT_DIR", output_dir)

    build.generate_referentiels(
        {
            "sources": {
                "regions_ref": {
                    "format": "json",
                    "local_name": "referentiel_regions.json",
                }
            }
        },
        MagicMock(),
    )

    refs = pd.read_parquet(
        output_dir / "odis_referentiels.parquet", engine="fastparquet"
    )
    regions = refs.loc[refs["key"] == "regions"].set_index("code")["label"].to_dict()
    assert regions == {
        "11": "Île-de-France",
        "84": "Auvergne-Rhône-Alpes",
    }
