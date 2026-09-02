"""Unit tests and out-of-pipeline verification for metropolitan regions and departments."""

import glob
import pandas as pd
import pytest
from app import config as cfg
from app.utils.data_loader import load_referentiels_raw
from app.core.scoring import ScoringEngine


def test_metropolitan_constants_integrity():
    """Verify that the metropolitan codes constants are well-formed and complete."""
    # 13 Metropolitan Regions
    assert len(cfg.METROPOLITAN_REGION_CODES) == 13
    assert len(cfg.METROPOLITAN_REGION_CODES_SET) == 13
    assert cfg.METROPOLITAN_REGION_CODES_SET == set(cfg.METROPOLITAN_REGION_CODES)

    # 96 Metropolitan Departments
    assert len(cfg.METROPOLITAN_DEPT_CODES) == 96
    assert len(cfg.METROPOLITAN_DEPT_CODES_SET) == 96
    assert cfg.METROPOLITAN_DEPT_CODES_SET == set(cfg.METROPOLITAN_DEPT_CODES)

    # Corse departments present
    assert "2A" in cfg.METROPOLITAN_DEPT_CODES_SET
    assert "2B" in cfg.METROPOLITAN_DEPT_CODES_SET

    # DROM regions strictly excluded
    drom_regions = {"01", "02", "03", "04", "06"}
    assert drom_regions.isdisjoint(cfg.METROPOLITAN_REGION_CODES_SET)

    # DROM departments strictly excluded
    drom_depts = {"971", "972", "973", "974", "976"}
    assert drom_depts.isdisjoint(cfg.METROPOLITAN_DEPT_CODES_SET)


def test_data_loader_load_referentiels_filters_overseas(monkeypatch):
    """Verify that load_referentiels_raw filters out DROM regions, departments, and communes."""
    mock_refs = pd.DataFrame(
        [
            # Regions: 1 metro (11 Île-de-France) + 2 DROM (01 Guadeloupe, 04 Réunion)
            {"key": "regions", "code": "11", "label": "Île-de-France"},
            {"key": "regions", "code": "01", "label": "Guadeloupe"},
            {"key": "regions", "code": "04", "label": "La Réunion"},
            # Departements: 2 metro (75 Paris, 33 Gironde) + 2 DROM (971, 974)
            {"key": "departements", "code": "75", "label": "Paris", "reg_code": "11"},
            {"key": "departements", "code": "33", "label": "Gironde", "reg_code": "75"},
            {"key": "departements", "code": "971", "label": "Guadeloupe", "reg_code": "01"},
            {"key": "departements", "code": "974", "label": "La Réunion", "reg_code": "04"},
            # Communes
            {"key": "communes", "code": "75056", "label": "Paris"},
            {"key": "communes", "code": "33063", "label": "Bordeaux"},
            {"key": "communes", "code": "97105", "label": "Basse-Terre"},
            {"key": "communes", "code": "97411", "label": "Saint-Denis"},
        ]
    )

    monkeypatch.setattr(
        "app.utils.data_loader._load_parquet",
        lambda *args, **kwargs: mock_refs,
    )

    bundle = load_referentiels_raw()

    # Regions check
    assert "11" in bundle["regions_names"]
    assert "01" not in bundle["regions_names"]
    assert "04" not in bundle["regions_names"]

    # Departements check
    assert set(bundle["departements_names"].keys()) == {"75", "33"}
    assert "971" not in bundle["departements_names"]
    assert "974" not in bundle["departements_names"]

    assert set(bundle["dept_details"].keys()) == {"75", "33"}
    assert "971" not in bundle["dept_details"]

    assert set(bundle["coddep_set"]) == {"33", "75"}

    # Communes (depcom_df) check
    depcom_df = bundle["depcom_df"]
    assert "75056" in depcom_df.index
    assert "33063" in depcom_df.index
    assert "97105" not in depcom_df.index
    assert "97411" not in depcom_df.index
    assert set(depcom_df["dep_code"].unique()) == {"33", "75"}


def test_scoring_engine_france_filter_uses_metropolitan_depts():
    """Verify that ScoringEngine._filter_communes with 'france' restricts to metropolitan depts."""
    df = pd.DataFrame(
        {
            "dep_code": ["75", "33", "971", "974"],
            "commune": ["Paris", "Bordeaux", "Basse-Terre", "Saint-Denis"],
        },
        index=["75056", "33063", "97105", "97411"],
    )

    filtered = ScoringEngine._filter_communes(
        df=df,
        start_commune=pd.DataFrame(),
        loc_type="france",
        loc_code=None,
    )

    assert set(filtered["dep_code"].tolist()) == {"75", "33"}
    assert "971" not in filtered["dep_code"].values
    assert "974" not in filtered["dep_code"].values


def test_offline_verification_against_local_referentiel():
    """Out-of-pipeline verification against local parquet datasets (if present)."""
    # Find local referentiels parquet file
    pattern = "app/data/datasets/*/odis_referentiels.parquet"
    matches = glob.glob(pattern)
    if not matches:
        pytest.skip("No local odis_referentiels.parquet found for offline dataset check.")

    parquet_path = matches[0]
    refs = pd.read_parquet(parquet_path)

    # 1. Check Regions
    reg_df = refs[refs["key"] == "regions"]
    parquet_metro_regs = set(
        reg_df[~reg_df["code"].astype(str).isin(["01", "02", "03", "04", "06"])]["code"].astype(str)
    )
    assert parquet_metro_regs == cfg.METROPOLITAN_REGION_CODES_SET, (
        f"Mismatch between config.py and referentiel: "
        f"Diff={parquet_metro_regs ^ cfg.METROPOLITAN_REGION_CODES_SET}"
    )

    # 2. Check Departements
    dep_df = refs[refs["key"] == "departements"]
    parquet_metro_deps = set(
        dep_df[~dep_df["code"].astype(str).str.startswith(("97", "98"))]["code"].astype(str)
    )
    assert parquet_metro_deps == cfg.METROPOLITAN_DEPT_CODES_SET, (
        f"Mismatch between config.py and referentiel: "
        f"Diff={parquet_metro_deps ^ cfg.METROPOLITAN_DEPT_CODES_SET}"
    )

    # 3. Check every metropolitan department maps to a metropolitan region
    if "reg_code" in dep_df.columns:
        metro_dep_df = dep_df[dep_df["code"].astype(str).isin(cfg.METROPOLITAN_DEPT_CODES_SET)]
        associated_regs = set(metro_dep_df["reg_code"].astype(str).unique())
        assert associated_regs.issubset(cfg.METROPOLITAN_REGION_CODES_SET), (
            f"Some metropolitan departments are associated with non-metropolitan regions: "
            f"{associated_regs - cfg.METROPOLITAN_REGION_CODES_SET}"
        )
