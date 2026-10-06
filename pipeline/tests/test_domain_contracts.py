"""Unit tests for domain contracts YAML loading, validation, and schema conformance."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pipeline.ingest.config import (
    clear_domain_contracts_cache,
    get_domain_contracts,
    load_domain_contracts_yaml,
)
from pipeline.ingest.schemas import DomainContracts
import pipeline.ingest as ingest
from pipeline.ingest import context


def test_domain_contracts_load_and_validate():
    """Verify that domain_contracts.yaml is valid according to Pydantic DomainContracts."""
    clear_domain_contracts_cache()
    contracts = get_domain_contracts()

    assert isinstance(contracts, DomainContracts)
    assert len(contracts.bpe.target_types) >= 20
    assert len(contracts.electoral.nuance_labels) == 86
    assert len(contracts.electoral.far_right_nuances) == 11
    assert len(contracts.electoral.election_dates) == 12
    assert contracts.education.risky_school_class_threshold == 20.0
    assert "Transports en commun" in contracts.mobility.sustainable_modes
    assert "ccas" in contracts.inclusion.ccas_keywords
    assert contracts.rna_rag.output_dimensionality == 128
    assert len(contracts.runner.cleaned_sources) == 29
    assert "salesforce_jaccueille" in contracts.runner.cleaned_sources
    assert len(contracts.geography.plm_communes) == 3
    assert "75056" in contracts.geography.plm_communes
    assert "salesforce_jaccueille" not in contracts.runner.output_dir_steps
    assert (
        contracts.runner.clean_filenames["salesforce_jaccueille"]
        == "salesforce_jaccueille.parquet"
    )


def test_domain_contracts_invalid_schema():
    """Verify that an invalid schema raises ValidationError."""
    raw = load_domain_contracts_yaml()
    # Mutate to make invalid
    invalid_raw = raw.copy()
    invalid_raw["electoral"] = {"far_right_nuances": []}  # Missing required fields

    with pytest.raises(ValidationError):
        DomainContracts.model_validate(invalid_raw)


def test_ingest_module_attribute_interception(tmp_path):
    """Verify that _IngestModule intercepts attribute sets and synchronizes with context.py."""
    fake_clean_dir = tmp_path / "custom_clean"
    fake_output_dir = tmp_path / "custom_output"
    fake_status_file = tmp_path / "custom_status.json"

    original_clean = ingest.CLEAN_DIR
    original_output = ingest.OUTPUT_DIR
    original_status = ingest.STATUS_FILE

    try:
        ingest.CLEAN_DIR = fake_clean_dir
        ingest.OUTPUT_DIR = fake_output_dir
        ingest.STATUS_FILE = fake_status_file

        # Check access through ingest module
        assert ingest.CLEAN_DIR == fake_clean_dir
        assert ingest.OUTPUT_DIR == fake_output_dir
        assert ingest.STATUS_FILE == fake_status_file

        # Check access through context module
        assert context.CLEAN_DIR == fake_clean_dir
        assert context.OUTPUT_DIR == fake_output_dir
        assert context.STATUS_FILE == fake_status_file

    finally:
        ingest.CLEAN_DIR = original_clean
        ingest.OUTPUT_DIR = original_output
        ingest.STATUS_FILE = original_status

    assert ingest.CLEAN_DIR == original_clean
    assert context.CLEAN_DIR == original_clean


def test_clean_political_with_contracts(tmp_path):
    """Verify that clean_political correctly identifies far-right mayors based on config."""
    import pandas as pd
    from unittest.mock import MagicMock
    from pipeline.common import PipelineLogger

    fake_clean_dir = tmp_path / "clean"
    fake_raw_dir = tmp_path / "raw"
    fake_clean_dir.mkdir(parents=True)
    fake_raw_dir.mkdir(parents=True)

    fake_data = pd.DataFrame(
        [
            {"Code Insee": "01001", "Nuance": "RN", "Famille": "Extrême droite"},
            {"Code Insee": "01002", "Nuance": "SOC", "Famille": "Gauche"},
            {"Code Insee": "01003", "Nuance": "REC", "Famille": "Autre"},
        ]
    )
    raw_file = fake_raw_dir / "politique.parquet"
    fake_data.to_parquet(raw_file)

    config = {
        "sources": {
            "political_nuance": {
                "local_name": "politique.parquet",
            }
        }
    }

    original_clean = ingest.CLEAN_DIR
    original_raw = ingest.CACHE_DIR
    try:
        ingest.CLEAN_DIR = fake_clean_dir
        ingest.CACHE_DIR = fake_raw_dir

        logger = MagicMock(spec=PipelineLogger)
        ingest.clean_political(config, logger)

        out_path = fake_clean_dir / "political.parquet"
        assert out_path.exists()
        res = pd.read_parquet(out_path)
        assert len(res) == 3
        # RN is far right -> pol_num 0.0
        assert (
            res.loc[res["codgeo"] == "01001", "maire_extreme_droite"].iloc[0] is True
            or res.loc[res["codgeo"] == "01001", "maire_extreme_droite"].iloc[0] == 1
        )
        assert res.loc[res["codgeo"] == "01001", "pol_num"].iloc[0] == 0.0
        # SOC is not far right -> pol_num 1.0
        assert (
            res.loc[res["codgeo"] == "01002", "maire_extreme_droite"].iloc[0] is False
            or res.loc[res["codgeo"] == "01002", "maire_extreme_droite"].iloc[0] == 0
        )
        assert res.loc[res["codgeo"] == "01002", "pol_num"].iloc[0] == 1.0
        # REC is in far_right_nuances -> pol_num 0.0
        assert (
            res.loc[res["codgeo"] == "01003", "maire_extreme_droite"].iloc[0] is True
            or res.loc[res["codgeo"] == "01003", "maire_extreme_droite"].iloc[0] == 1
        )
        assert res.loc[res["codgeo"] == "01003", "pol_num"].iloc[0] == 0.0
    finally:
        ingest.CLEAN_DIR = original_clean
        ingest.CACHE_DIR = original_raw


def test_clean_school_effectifs_purged_and_configured(tmp_path):
    """Verify clean_school_effectifs does not require codes_postaux and uses threshold from config."""
    import pandas as pd
    from unittest.mock import MagicMock, patch
    from pipeline.common import PipelineLogger

    fake_clean_dir = tmp_path / "clean"
    fake_raw_dir = tmp_path / "raw"
    fake_clean_dir.mkdir(parents=True)
    fake_raw_dir.mkdir(parents=True)

    # Effectifs data: school 1 has 18 students in 1 class -> < 20 -> risky
    # School 2 has 25 students in 1 class -> >= 20 -> not risky
    fake_effectifs = pd.DataFrame(
        [
            {
                "numero_ecole": "UAI001",
                "rentree_scolaire": 2024,
                "nombre_total_eleves": 18,
                "nombre_total_classes": 1,
            },
            {
                "numero_ecole": "UAI002",
                "rentree_scolaire": 2024,
                "nombre_total_eleves": 25,
                "nombre_total_classes": 1,
            },
        ]
    )
    raw_file = fake_raw_dir / "effectifs.parquet"
    fake_effectifs.to_parquet(raw_file)

    # Odace Annuaire mock
    fake_annuaire = pd.DataFrame(
        [
            {"uai_code": "UAI001", "commune_insee_code": "01001"},
            {"uai_code": "UAI002", "commune_insee_code": "01001"},
        ]
    )

    config = {
        "sources": {
            "education_effectifs": {"local_name": "effectifs.parquet"},
            "education_annuaire": {"odace_table": "dim_education_annuaire"},
        }
    }

    mock_client = MagicMock()
    mock_client.fetch_table.return_value = fake_annuaire

    original_clean = ingest.CLEAN_DIR
    original_raw = ingest.CACHE_DIR
    try:
        ingest.CLEAN_DIR = fake_clean_dir
        ingest.CACHE_DIR = fake_raw_dir

        with patch("pipeline.ingest.get_odace_client", return_value=mock_client):
            logger = MagicMock(spec=PipelineLogger)
            ingest.clean_school_effectifs(config, logger)

        out_path = fake_clean_dir / "school_effectifs.parquet"
        assert out_path.exists()
        res = pd.read_parquet(out_path)
        assert len(res) == 1
        assert res["codgeo"].iloc[0] == "01001"
        assert res["total_eleves"].iloc[0] == 43
        assert res["total_classes"].iloc[0] == 2
        assert res["ecoles_count"].iloc[0] == 2
        # Only UAI001 has < 20 students per class -> 1 risky school
        assert res["risky_schools_count"].iloc[0] == 1
    finally:
        ingest.CLEAN_DIR = original_clean
        ingest.CACHE_DIR = original_raw


def test_wildcard_import_and_all_symbols():
    """Verify that wildcard import and all symbols in __all__ resolve cleanly."""
    for symbol in ingest.__all__:
        assert hasattr(ingest, symbol), (
            f"Symbol {symbol} from __all__ not found on ingest"
        )


def test_clean_mob_transports_pub_dynamic_modes(tmp_path):
    """Verify that clean_mob_transports_pub uses dynamic transport modes from config."""
    import pandas as pd
    from unittest.mock import MagicMock, patch
    from pipeline.common import PipelineLogger

    fake_clean_dir = tmp_path / "clean"
    fake_clean_dir.mkdir(parents=True)

    fake_odace = pd.DataFrame(
        [
            {"commune_insee_code": "01001", "type_transport": "bus", "nb_stations": 3},
            {
                "commune_insee_code": "01001",
                "type_transport": "tramway",
                "nb_stations": 2,
            },
        ]
    )

    mock_client = MagicMock()
    mock_client.fetch_table.return_value = fake_odace

    config = {
        "sources": {
            "mob_transports_pub": {
                "use_odace": True,
                "odace_table": "fact_transport_commun",
            }
        }
    }

    original_clean = ingest.CLEAN_DIR
    try:
        ingest.CLEAN_DIR = fake_clean_dir
        with patch("pipeline.ingest.get_odace_client", return_value=mock_client):
            logger = MagicMock(spec=PipelineLogger)
            ingest.clean_mob_transports_pub(config, logger)

        out_path = fake_clean_dir / "mob_transports_pub.parquet"
        assert out_path.exists()
        res = pd.read_parquet(out_path)
        assert len(res) == 1
        assert res["codgeo"].iloc[0] == "01001"
        assert res["nb_stops_bus"].iloc[0] == 3.0
        assert res["nb_stops_tram"].iloc[0] == 2.0
        assert res["nb_stops_metro"].iloc[0] == 0.0
        assert res["nb_stops_train"].iloc[0] == 0.0
        assert res["nb_stops_total"].iloc[0] == 5.0
    finally:
        ingest.CLEAN_DIR = original_clean


def test_clean_communes_plm_configured(tmp_path):
    """Verify clean_communes flags PLM parent communes according to geography contract."""
    import pandas as pd
    from unittest.mock import MagicMock, patch
    from pipeline.common import PipelineLogger

    fake_clean_dir = tmp_path / "clean"
    fake_clean_dir.mkdir(parents=True)

    poly_wkt = "POLYGON ((2.3 48.8, 2.4 48.8, 2.4 48.9, 2.3 48.9, 2.3 48.8))"
    poly_json = '{"type": "Polygon", "coordinates": [[[2.3, 48.8], [2.4, 48.8], [2.4, 48.9], [2.3, 48.9], [2.3, 48.8]]]}'

    fake_odace = pd.DataFrame(
        [
            {
                "commune_insee_code": "75056",
                "geometrie_geojson": poly_json,
            },
            {
                "commune_insee_code": "01001",
                "geometrie_geojson": poly_json,
            },
        ]
    )

    fake_dim_commune = pd.DataFrame(
        [
            {
                "commune_insee_code": "75056",
                "commune_sk": 1,
                "commune_label": "Paris",
                "departement_code": "75",
                "region_code": "11",
            },
            {
                "commune_insee_code": "01001",
                "commune_sk": 2,
                "commune_label": "L'Abergement-Clémenciat",
                "departement_code": "01",
                "region_code": "84",
            },
        ]
    )

    mock_client = MagicMock()
    mock_client.fetch_table.return_value = fake_odace
    mock_client.fetch_dim_commune.return_value = fake_dim_commune

    config = {
        "sources": {
            "communes": {
                "use_odace": True,
                "odace_table": "ref_commune_geo",
                "ttl_days": 90,
            }
        }
    }

    original_clean = ingest.CLEAN_DIR
    try:
        ingest.CLEAN_DIR = fake_clean_dir
        with (
            patch("pipeline.ingest.get_odace_client", return_value=mock_client),
            patch("pipeline.ingest.fetch_source", return_value=None),
        ):
            logger = MagicMock(spec=PipelineLogger)
            ingest.clean_communes(config, logger)

        out_path = fake_clean_dir / "communes.parquet"
        assert out_path.exists()
        res = pd.read_parquet(out_path)
        assert len(res) == 2
        # Paris (75056) is PLM -> 1.0
        assert res.loc[res["codgeo"] == "75056", "plm"].iloc[0] == 1.0
        # 01001 is not PLM -> NaN
        assert pd.isna(res.loc[res["codgeo"] == "01001", "plm"].iloc[0])
    finally:
        ingest.CLEAN_DIR = original_clean


def test_main_cli_salesforce_and_steps_registered():
    """Verify that main.py registers salesforce_jaccueille and all steps cleanly."""
    from unittest.mock import patch
    from pipeline.ingest.main import main

    with (
        patch("pipeline.ingest.main.run_clean_step_safely") as mock_run,
        patch("pipeline.ingest.main.load_config", return_value={"sources": {}}),
        patch("pipeline.ingest.main.PipelineLogger"),
    ):
        main(["--steps", "salesforce_jaccueille"])
        assert mock_run.called
        assert mock_run.call_args[0][0] == "salesforce_jaccueille"


def test_compute_rna_rag_counts_uses_configured_threshold():
    """Verify compute_rna_rag_counts uses similarity_threshold from domain_contracts."""
    from unittest.mock import MagicMock, patch
    import pandas as pd

    mock_bq_client = MagicMock()
    mock_query_job = MagicMock()
    mock_query_job.to_dataframe.return_value = pd.DataFrame(
        {"codgeo": ["01001"], "count": [5]}
    )
    mock_bq_client.query.return_value = mock_query_job

    mock_genai_client = MagicMock()
    mock_embed_response = MagicMock()
    mock_embed_response.embeddings = [MagicMock(values=[0.1] * 128)]
    mock_genai_client.models.embed_content.return_value = mock_embed_response

    with (
        patch("pipeline.ingest.bigquery.Client", return_value=mock_bq_client),
        patch(
            "pipeline.ingest.cleaners.rna_rag.genai.Client",
            return_value=mock_genai_client,
        ),
        patch(
            "pipeline.ingest.cleaners.rna_rag._data_project", return_value="test-proj"
        ),
        patch(
            "pipeline.ingest.cleaners.rna_rag._rna_rag_table_id",
            return_value="proj.ds.table",
        ),
    ):
        res = ingest.compute_rna_rag_counts("query text")
        assert len(res) == 1
        # Check that distance threshold passed to BigQuery query job matches 1.0 - 0.70 = 0.30
        call_kwargs = mock_bq_client.query.call_args[1]
        params = call_kwargs["job_config"].query_parameters
        dist_param = next(p for p in params if p.name == "dist_threshold")
        assert round(dist_param.value, 4) == round(1.0 - 0.70, 4)
