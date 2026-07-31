import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
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
@patch("config.get_data_path")
def test_init_datasets(
    mock_get_data_path,
    mock_read_parquet,
    mock_exists,
    mock_fetch_salesforce,
    mock_parquet_data,
):
    """Tests the initialization of datasets."""
    mock_exists.return_value = True
    mock_get_data_path.return_value = "/mock/path"
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


def test_resolve_dataset_path_local_datasets(tmp_path, monkeypatch):
    """Tests that resolve_dataset_path finds files in app/data/datasets/."""
    datasets_dir = tmp_path / "app" / "data" / "datasets"
    datasets_dir.mkdir(parents=True)
    test_file = datasets_dir / "test_dataset.parquet"
    test_file.write_text("dummy")

    monkeypatch.setattr(data_loader.cfg, "APP_DIR", str(tmp_path / "app"))

    resolved = data_loader.resolve_dataset_path("test_dataset.parquet")
    assert resolved == str(test_file)


@patch("utils.data_loader.storage.Client")
def test_resolve_dataset_path_gcs_fallback(mock_storage_client_cls, tmp_path, monkeypatch):
    """Tests that resolve_dataset_path falls back to GCS download when file is not local."""
    monkeypatch.setattr(data_loader.cfg, "APP_DIR", str(tmp_path / "nonexistent_app"))
    monkeypatch.setattr(data_loader.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    
    mock_client = MagicMock()
    mock_storage_client_cls.return_value = mock_client
    mock_bucket = MagicMock()
    mock_client.bucket.return_value = mock_bucket

    pointer_blob = MagicMock()
    pointer_blob.exists.return_value = True
    pointer_blob.download_as_bytes.return_value = b'{"version": "v-test-1"}'

    dataset_blob = MagicMock()
    dataset_blob.exists.return_value = True

    # Simulate download_to_filename writing dummy file
    def mock_download(target_path):
        from pathlib import Path
        Path(target_path).parent.mkdir(parents=True, exist_ok=True)
        Path(target_path).write_text("gcs_data")

    dataset_blob.download_to_filename.side_effect = mock_download

    def get_blob(blob_path):
        if blob_path == "datasets/current.json":
            return pointer_blob
        return dataset_blob

    mock_bucket.blob.side_effect = get_blob

    resolved = data_loader.resolve_dataset_path("salesforce_jaccueille_bdv.parquet")
    assert resolved is not None
    assert "salesforce_jaccueille_bdv.parquet" in resolved
    assert "v-test-1" in resolved
    assert dataset_blob.download_to_filename.called
