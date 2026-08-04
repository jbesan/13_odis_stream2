"""Runtime verification of the active release provenance manifest."""

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from utils.data_loader import load_active_data_manifest


@patch("utils.data_loader.storage.Client")
def test_load_active_data_manifest_reads_the_pointer_release(mock_client, monkeypatch):
    monkeypatch.delenv("K_SERVICE", raising=False)
    manifest = {
        "manifest_version": "v2-123",
        "pipeline_run_id": "run-123",
        "sources": [],
    }
    manifest_bytes = json.dumps(manifest).encode("utf-8")
    pointer = {
        "version": "run-123",
        "manifest": {
            "name": "data_manifest.json",
            "sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        },
    }
    pointer_blob = MagicMock()
    pointer_blob.exists.return_value = True
    pointer_blob.download_as_bytes.return_value = json.dumps(pointer).encode("utf-8")
    manifest_blob = MagicMock()
    manifest_blob.download_as_bytes.return_value = manifest_bytes
    bucket = MagicMock()
    bucket.blob.side_effect = lambda name: (
        pointer_blob if name.endswith("current.json") else manifest_blob
    )
    mock_client.return_value.bucket.return_value = bucket

    active_manifest = load_active_data_manifest()

    assert active_manifest["manifest_version"] == "v2-123"
    assert active_manifest["active_release_version"] == "run-123"


@patch("utils.data_loader.storage.Client")
def test_cloud_run_rejects_a_manifest_with_the_wrong_checksum(mock_client, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "odis-app")
    pointer_blob = MagicMock()
    pointer_blob.exists.return_value = True
    pointer_blob.download_as_bytes.return_value = json.dumps(
        {
            "version": "run-123",
            "manifest": {"name": "data_manifest.json", "sha256": "not-a-hash"},
        }
    ).encode("utf-8")
    manifest_blob = MagicMock()
    manifest_blob.download_as_bytes.return_value = b"{}"
    bucket = MagicMock()
    bucket.blob.side_effect = lambda name: (
        pointer_blob if name.endswith("current.json") else manifest_blob
    )
    mock_client.return_value.bucket.return_value = bucket

    with pytest.raises(RuntimeError, match="checksum"):
        load_active_data_manifest()
