import os
import pytest
import pandas as pd
from unittest.mock import patch
from utils import data_loader


@pytest.fixture
def mock_parquet_data():
    """Mocks the parquet data for testing."""
    # Mock ODIS
    odis_df = pd.DataFrame(
        {
            "codgeo": ["01001", "01002"],
            "libgeo": ["Commune A", "Commune B"],
            "population": [1000, 2000],
            "dep_code": ["01", "01"],
            "reg_code": ["84", "84"],
            "bassin_de_vie": ["BV1", "BV1"],
            "met_scaled": [0.5, 0.6],
            "polygon": [
                b"\x01\x03\x00\x00\x00\x01\x00\x00\x00\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
                b"\x01\x03\x00\x00\x00\x01\x00\x00\x00\x05\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xf0?\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
            ],
        }
    )

    # Mock POIs
    pois_df = pd.DataFrame(
        {
            "id": ["1", "2", "3"],
            "category": ["education", "sante", "incl_services"],
            "type": ["Ecole", "Hopital", "CAF"],
            "name": ["Ecole A", "Hopital B", "CAF C"],
            "lat": [45.0, 45.1, 45.2],
            "lon": [5.0, 5.1, 5.2],
            "codgeo": ["01001", "01001", "01002"],
        }
    )

    # Mock Referentiels
    ref_df = pd.DataFrame(
        {
            "key": ["rome_codes", "rome_codes"],
            "code": ["M1805", "M1801"],
            "label": ["Dev", "Test"],
        }
    )

    return odis_df, pois_df, ref_df


@patch("utils.data_loader.fetch_salesforce_jaccueille_bdv")
@patch("utils.data_loader.os.path.exists")
@patch("utils.data_loader.pd.read_parquet")
@patch("utils.data_loader.resolve_dataset_path", side_effect=lambda path: path)
def test_init_datasets(
    mock_resolve_dataset_path,
    mock_read_parquet,
    mock_exists,
    mock_fetch_salesforce,
    mock_parquet_data,
):
    """Tests the initialization of datasets."""
    mock_exists.return_value = True
    mock_fetch_salesforce.return_value = pd.DataFrame(
        columns=["bassin_de_vie", "contact_count", "lead_count"]
    )
    odis_df, pois_df, ref_df = mock_parquet_data

    # Configure mock side effects for different files
    def side_effect(path, columns=None, **kwargs):
        if "odis_communes" in path:
            return odis_df
        elif "pois" in path:
            return pois_df
        elif "referentiels" in path:
            return ref_df
        elif "vertical" in path or "associations" in path:
            return pd.DataFrame()
        elif "global_stats" in path:  # Added fallback for global_stats
            return pd.DataFrame()  # Return an empty DataFrame as a fallback
        return pd.DataFrame()

    mock_read_parquet.side_effect = side_effect

    # Run init_datasets
    data = data_loader.load_all_data_raw()

    # Assertions
    assert "odis" in data
    assert "pois" in data
    assert "annuaire_ecoles" in data
    assert "annuaire_sante" in data

    # Check ODIS
    assert len(data["odis"]) == 2
    assert "population" in data["odis"].columns

    # Check POIs split
    assert len(data["annuaire_ecoles"]) == 1
    assert data["annuaire_ecoles"].iloc[0]["name"] == "Ecole A"

    assert len(data["annuaire_sante"]) == 1
    assert data["annuaire_sante"].iloc[0]["name"] == "Hopital B"

    # Check Referentiels
    assert "rome_index" in data
    assert not data["rome_index"].empty
    assert "Dev" in data["rome_index"]["label"].values


def test_get_salesforce_jaccueille_counts(monkeypatch):
    """Salesforce's published BDV artifact is the sole source of score inputs."""
    source = pd.DataFrame(
        {
            "bassin_de_vie": ["BV1", "BV1", "BV2"],
            "contact_count": [2, 3, 1],
            "lead_count": [4, 5, 0],
        }
    )
    monkeypatch.setattr(data_loader, "fetch_salesforce_jaccueille_bdv", lambda: source)

    counts = data_loader.get_salesforce_jaccueille_counts()

    assert counts.to_dict("records") == [
        {"bassin_de_vie": "BV1", "heb_accueillants_count": 5, "prospects_count": 9},
        {"bassin_de_vie": "BV2", "heb_accueillants_count": 1, "prospects_count": 0},
    ]


def test_resolve_dataset_path_local(tmp_path, monkeypatch):
    """resolve_dataset_path locates the file in the active dataset directory."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    target_file = datasets_dir / "salesforce_jaccueille_bdv.parquet"
    target_file.write_bytes(b"MOCK_PARQUET")
    monkeypatch.setenv("ODIS_DATASETS_DIR", str(datasets_dir))

    release_context = data_loader.ReleaseContext(
        bucket_name="odis-stream2-eu",
        datasets_prefix="datasets",
        version="v-test-1",
        artifacts=(
            data_loader.ReleaseArtifact(
                name="salesforce_jaccueille_bdv.parquet",
                sha256="abc",
                size_bytes=len(b"MOCK_PARQUET"),
            ),
        ),
    )
    resolved = data_loader.resolve_dataset_path(
        "salesforce_jaccueille_bdv.parquet", release_context=release_context
    )
    assert resolved is not None
    assert "salesforce_jaccueille_bdv.parquet" in resolved
    assert os.path.exists(resolved)


def test_dataset_cache_base_dir_routing(monkeypatch, tmp_path):
    """Verifies that dataset cache directory routes properly across environments."""
    # 1. Custom ODIS_CACHE_DIR override
    monkeypatch.setenv("ODIS_CACHE_DIR", str(tmp_path / "custom_cache"))
    assert data_loader._get_dataset_cache_base_dir() == str(tmp_path / "custom_cache")

    # 2. Custom ODIS_DATASETS_DIR override
    monkeypatch.delenv("ODIS_CACHE_DIR", raising=False)
    monkeypatch.setenv("ODIS_DATASETS_DIR", str(tmp_path / "datasets_dir"))
    assert data_loader._get_dataset_cache_base_dir() == str(tmp_path / "datasets_dir")

    # 3. Default routing (points to an existing directory or temp)
    monkeypatch.delenv("ODIS_DATASETS_DIR", raising=False)
    base_dir = data_loader._get_dataset_cache_base_dir()
    assert isinstance(base_dir, str)
