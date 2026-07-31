import json
from unittest.mock import MagicMock

from pipeline.manifest import DataManifestBuilder, SourceManifestItem


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


def test_manifest_includes_local_files_and_observed_run_provenance(tmp_path, monkeypatch):
    source_file = tmp_path / "source.csv"
    source_file.write_text("id\n1\n", encoding="utf-8")
    output_file = tmp_path / "bundle.txt"
    output_file.write_text("release artifact", encoding="utf-8")
    quality_report = tmp_path / "quality_report.json"
    quality_report.write_text('{"status": "PASSED"}', encoding="utf-8")
    status_file = tmp_path / "run.json"
    status_file.write_text(
        json.dumps(
            {
                "sources": {
                    "catalog": {
                        "status": "refreshed",
                        "timestamp": "2026-07-30T12:00:00+00:00",
                        "file": str(source_file),
                    },
                    "live_api": {
                        "status": "fallback_last_good",
                        "timestamp": "2026-07-29T12:00:00+00:00",
                        "file": str(source_file),
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("pipeline.manifest.STATUS_FILE", status_file)

    manifest = DataManifestBuilder(
        sources_config={"catalog": {"ttl_days": 30}},
        local_files_config={"live_api": {"format": "api", "ttl_days": 7}},
        output_path=tmp_path / "data_manifest.json",
        run_id="run-provenance",
        quality_gate={"status": "PASSED"},
        release_artifacts=["bundle.txt"],
        quality_report_path=quality_report,
    ).build()

    assert manifest.pipeline_run_id == "run-provenance"
    assert manifest.total_sources == 2
    assert {source.source_key for source in manifest.sources} == {"catalog", "live_api"}
    live_api = next(source for source in manifest.sources if source.source_key == "live_api")
    assert live_api.acquisition_status == "fallback_last_good"
    assert live_api.fallback_used is True
    assert live_api.artifact is not None
    assert manifest.outputs[0].sha256
    assert manifest.quality_report is not None
    assert manifest.configuration["sources_yaml_sha256"]
