import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.manifest import DataManifestBuilder, SourceManifestItem, DataManifest


def test_source_manifest_item_model():
    item = SourceManifestItem(
        source_key="bpe",
        name="Équipements et services BPE 2024",
        method="Data Platform Odace",
        odace_table="dim_equipement_territoire",
        annee_reference=2024,
        last_updated="2026-07-22T10:00:00Z",
        row_count=2450890,
        doc_url="https://www.insee.fr",
        certified=True,
    )
    assert item.source_key == "bpe"
    assert item.certified is True


def test_manifest_builder_with_mocked_odace(tmp_path):
    mock_sources = {
        "sources": {
            "communes": {
                "use_odace": True,
                "odace_table": "ref_commune_geo",
                "description": "Contours administratifs",
                "doc_url": "https://data.gouv.fr/communes",
            },
            "ref_epci": {
                "description": "Référentiel des EPCI",
                "doc_url": "https://data.gouv.fr/epci",
            },
        }
    }

    mock_odace = MagicMock()
    mock_odace.fetch_silver_table_detail.return_value = {
        "name": "ref_commune_geo",
        "description_fr": "Contours administratifs des communes",
        "annee_reference": 2024,
        "certified": True,
        "certified_at": "2026-01-12T00:00:00Z",
        "sources": [
            {
                "name": "bpe",
                "description": "Base BPE",
                "doc_url": "https://insee.fr/bpe",
            }
        ],
        "schema": {"row_count": 34974},
    }

    manifest_output_path = tmp_path / "data_manifest.json"

    builder = DataManifestBuilder(
        sources_config=mock_sources["sources"],
        odace_client=mock_odace,
        output_path=manifest_output_path,
    )

    manifest = builder.build()

    assert manifest.manifest_version.startswith("v")
    assert len(manifest.sources) == 2

    # Check Odace source
    odace_item = next(s for s in manifest.sources if s.source_key == "communes")
    assert odace_item.method == "Data Platform Odace"
    assert odace_item.odace_table == "ref_commune_geo"
    assert odace_item.row_count == 34974
    assert odace_item.certified is True

    # Check non-Odace source
    local_item = next(s for s in manifest.sources if s.source_key == "ref_epci")
    assert local_item.method == "Export Data.gouv.fr"

    # Verify JSON file generation
    assert manifest_output_path.exists()
    saved_data = json.loads(manifest_output_path.read_text())
    assert saved_data["manifest_version"] == manifest.manifest_version
