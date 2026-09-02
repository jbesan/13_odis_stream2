"""Runtime verification of the active release provenance manifest."""

import json
import pytest

from utils import data_loader
from utils.data_loader import (
    get_active_release_context,
    load_active_data_manifest,
    resolve_dataset_path,
)


@pytest.fixture(autouse=True)
def clear_active_release_payload_cache():
    data_loader._active_release_payload.clear()
    data_loader.load_active_data_manifest.clear()
    yield
    data_loader._active_release_payload.clear()
    data_loader.load_active_data_manifest.clear()


def test_load_active_data_manifest_reads_local_manifest(tmp_path, monkeypatch):
    """load_active_data_manifest reads data_manifest.json from the dataset directory."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    manifest_data = {
        "pipeline_run_id": "run-test-2026",
        "outputs": [
            {
                "name": "odis_communes.parquet",
                "sha256": "a" * 64,
                "size_bytes": 1024,
            }
        ],
    }
    (datasets_dir / "data_manifest.json").write_text(json.dumps(manifest_data))
    monkeypatch.setenv("ODIS_DATASETS_DIR", str(datasets_dir))

    manifest = load_active_data_manifest()
    assert manifest["pipeline_run_id"] == "run-test-2026"
    assert manifest["active_release_version"] == "run-test-2026"


def test_get_active_release_context_resolves_artifacts(tmp_path, monkeypatch):
    """get_active_release_context builds ReleaseContext with all runtime artifacts."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    manifest_data = {
        "pipeline_run_id": "run-context-test",
        "outputs": [
            {
                "name": name,
                "sha256": "b" * 64,
                "size_bytes": 2048,
            }
            for name in data_loader._RUNTIME_DATASET_FILENAMES
        ],
    }
    (datasets_dir / "data_manifest.json").write_text(json.dumps(manifest_data))
    monkeypatch.setenv("ODIS_DATASETS_DIR", str(datasets_dir))

    context = get_active_release_context()
    assert context.version == "run-context-test"
    assert context.identity == "gcs:run-context-test"
    artifact = context.artifact("odis_communes.parquet")
    assert artifact.name == "odis_communes.parquet"
    assert artifact.sha256 == "b" * 64
    assert artifact.size_bytes == 2048


def test_resolve_dataset_path_locates_local_file(tmp_path, monkeypatch):
    """resolve_dataset_path returns the local path to the artifact."""
    datasets_dir = tmp_path / "datasets"
    datasets_dir.mkdir()
    target_file = datasets_dir / "odis_communes.parquet"
    target_file.write_bytes(b"PARQUET_MOCK")
    monkeypatch.setenv("ODIS_DATASETS_DIR", str(datasets_dir))

    resolved = resolve_dataset_path("odis_communes.parquet")
    assert resolved == str(target_file)
