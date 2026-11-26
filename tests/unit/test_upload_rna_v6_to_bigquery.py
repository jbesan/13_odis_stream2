"""Unit tests for scripts/upload_rna_v6_to_bigquery.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from google.cloud import bigquery

from scripts import upload_rna_v6_to_bigquery as uploader


def _make_schema_field(name: str, field_type: str, mode: str) -> bigquery.SchemaField:
    return bigquery.SchemaField(name=name, field_type=field_type, mode=mode)


def _valid_consolidated_table(
    *,
    num_rows: int = uploader.EXPECTED_CONSOLIDATED_ROWS,
    clustering: list[str] | None = None,
    list_mode: str = "REPEATED",
    list_type: str = "STRING",
) -> MagicMock:
    table = MagicMock(spec=bigquery.Table)
    table.full_table_id = "proj.rna_rag.national_v6_consolidated"
    table.num_rows = num_rows
    table.clustering_fields = (
        list(uploader.EXPECTED_CONSOLIDATED_CLUSTERING)
        if clustering is None
        else clustering
    )
    table.schema = [
        _make_schema_field(col, list_type, list_mode)
        for col in uploader.EXPECTED_CONSOLIDATED_LIST_COLS
    ]
    return table


def _valid_tags_ref_table(
    *,
    num_rows: int = uploader.EXPECTED_TAGS_REF_ROWS,
    exemples_type: str = "STRING",
    exemples_mode: str = "REPEATED",
) -> MagicMock:
    table = MagicMock(spec=bigquery.Table)
    table.full_table_id = "proj.rna_rag.referentiel_tags_canoniques_n50"
    table.num_rows = num_rows
    table.schema = [
        _make_schema_field("tag_canonique", "STRING", "NULLABLE"),
        _make_schema_field("exemples_bruts", exemples_type, exemples_mode),
    ]
    return table


def test_validate_target_table_name_rejects_legacy_and_invalid() -> None:
    """Legacy production tables and malformed identifiers must be rejected."""
    for bad_name in ("rna_rag", "rna_rag_clustered", "", "bad-table", "proj.ds.tbl"):
        with pytest.raises(ValueError):
            uploader.validate_target_table_name(bad_name)

    uploader.validate_target_table_name("national_v6_consolidated")
    uploader.validate_target_table_name("referentiel_tags_canoniques_n50")


def test_build_parquet_load_job_config_enables_list_inference() -> None:
    """Parquet load config must enable list inference and preserve clustering."""
    cfg = uploader.build_parquet_load_job_config(
        clustering_fields=uploader.EXPECTED_CONSOLIDATED_CLUSTERING
    )
    assert cfg.source_format == bigquery.SourceFormat.PARQUET
    assert cfg.write_disposition == bigquery.WriteDisposition.WRITE_TRUNCATE
    assert cfg.parquet_options is not None
    assert cfg.parquet_options.enable_list_inference is True
    assert cfg.clustering_fields == ["bdv_codeinsee", "llm_action_sociale"]


def test_upload_file_to_gcs_skips_matching_md5_and_uploads_on_mismatch(
    tmp_path: Path,
) -> None:
    """GCS upload is skipped on matching MD5 and executed on mismatch or absence."""
    sample = tmp_path / "sample.parquet"
    sample.write_bytes(b"parquet-payload")
    expected_md5 = uploader.compute_md5_base64(sample)

    # 1. Dry-run makes zero GCS calls
    client_dry = MagicMock()
    uri_dry = uploader.upload_file_to_gcs(
        client_dry, sample, "my-bucket", "sources/rna_v6/sample.parquet", dry_run=True
    )
    assert uri_dry == "gs://my-bucket/sources/rna_v6/sample.parquet"
    client_dry.bucket.assert_not_called()

    # 2. Matching MD5 skips upload
    blob_match = MagicMock()
    blob_match.exists.return_value = True
    blob_match.md5_hash = expected_md5
    client_match = MagicMock()
    client_match.bucket.return_value.blob.return_value = blob_match

    uploader.upload_file_to_gcs(
        client_match, sample, "my-bucket", "sources/rna_v6/sample.parquet"
    )
    blob_match.reload.assert_called_once()
    blob_match.upload_from_filename.assert_not_called()

    # 3. Mismatched MD5 triggers upload
    blob_diff = MagicMock()
    blob_diff.exists.return_value = True
    blob_diff.md5_hash = "different-md5=="
    client_diff = MagicMock()
    client_diff.bucket.return_value.blob.return_value = blob_diff

    uploader.upload_file_to_gcs(
        client_diff, sample, "my-bucket", "sources/rna_v6/sample.parquet"
    )
    blob_diff.upload_from_filename.assert_called_once_with(str(sample), timeout=600)

    # 4. Missing local file raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        uploader.upload_file_to_gcs(
            client_diff, tmp_path / "missing.parquet", "my-bucket", "p.parquet"
        )


def test_verify_loaded_tables_success_and_guards() -> None:
    """Verification succeeds on valid tables and fails on RECORD lists, counts, or clustering."""
    legacy_table = MagicMock(spec=bigquery.Table)
    legacy_table.num_rows = 1_682_994

    smoke_row = {
        "siege_rows": uploader.EXPECTED_SIEGE_ROWS,
        "succursale_rows": uploader.EXPECTED_SUCCURSALE_ROWS,
        "action_sociale_fr_rows": 596_506,
        "refug_fr_rows": 13_246,
        "volley_bdv_count": 650,
    }

    def _make_client(cons_table: MagicMock, ref_table: MagicMock) -> MagicMock:
        bq_client = MagicMock()
        table_map = {
            "proj.rna_rag.national_v6_consolidated": cons_table,
            "proj.rna_rag.referentiel_tags_canoniques_n50": ref_table,
            "proj.rna_rag.rna_rag_clustered": legacy_table,
        }
        bq_client.get_table.side_effect = lambda tid: table_map[tid]
        bq_client.query.return_value.result.return_value = [smoke_row]
        return bq_client

    # Happy path
    summary = uploader.verify_loaded_tables(
        _make_client(_valid_consolidated_table(), _valid_tags_ref_table()),
        "proj.rna_rag.national_v6_consolidated",
        "proj.rna_rag.referentiel_tags_canoniques_n50",
        project="proj",
        dataset="rna_rag",
        location="europe-west1",
    )
    assert summary["total_rows"] == uploader.EXPECTED_CONSOLIDATED_ROWS
    assert summary["legacy_rna_rag_clustered_rows"] == 1_682_994

    # List column loaded as RECORD instead of REPEATED STRING
    with pytest.raises(RuntimeError, match="REPEATED STRING"):
        uploader.verify_loaded_tables(
            _make_client(
                _valid_consolidated_table(list_type="RECORD", list_mode="NULLABLE"),
                _valid_tags_ref_table(),
            ),
            "proj.rna_rag.national_v6_consolidated",
            "proj.rna_rag.referentiel_tags_canoniques_n50",
            project="proj",
            dataset="rna_rag",
            location="europe-west1",
        )

    # Row count mismatch
    with pytest.raises(RuntimeError, match="Row count mismatch"):
        uploader.verify_loaded_tables(
            _make_client(
                _valid_consolidated_table(num_rows=100),
                _valid_tags_ref_table(),
            ),
            "proj.rna_rag.national_v6_consolidated",
            "proj.rna_rag.referentiel_tags_canoniques_n50",
            project="proj",
            dataset="rna_rag",
            location="europe-west1",
        )

    # Clustering mismatch
    with pytest.raises(RuntimeError, match="Clustering mismatch"):
        uploader.verify_loaded_tables(
            _make_client(
                _valid_consolidated_table(clustering=["bdv_codeinsee"]),
                _valid_tags_ref_table(),
            ),
            "proj.rna_rag.national_v6_consolidated",
            "proj.rna_rag.referentiel_tags_canoniques_n50",
            project="proj",
            dataset="rna_rag",
            location="europe-west1",
        )


def test_merge_embeddings_and_verify_guards() -> None:
    """Merge SQL handles first-time vs idempotent runs and verifies 0 NULLs."""
    cons_without_emb = _valid_consolidated_table()
    cons_with_emb = _valid_consolidated_table()
    cons_with_emb.schema.append(
        _make_schema_field("embedding_gemini_768", "FLOAT", "REPEATED")
    )

    bq_first = MagicMock()
    bq_first.get_table.return_value = cons_without_emb
    rows = uploader.merge_embeddings_into_consolidated(
        bq_first,
        "proj.rna_rag.national_v6_consolidated",
        "proj.rna_rag.national_v6_embeddings_768d",
    )
    assert rows == uploader.EXPECTED_CONSOLIDATED_ROWS
    sql_first = bq_first.query.call_args.args[0]
    assert "c.*, e.embedding_gemini_768" in sql_first
    assert "CLUSTER BY bdv_codeinsee, llm_action_sociale" in sql_first

    bq_rerun = MagicMock()
    bq_rerun.get_table.return_value = cons_with_emb
    bq_rerun.query.return_value.result.return_value = [
        {
            "total_rows": uploader.EXPECTED_CONSOLIDATED_ROWS,
            "null_embeddings": 0,
            "invalid_dim_rows": 0,
        }
    ]
    uploader.merge_embeddings_into_consolidated(
        bq_rerun,
        "proj.rna_rag.national_v6_consolidated",
        "proj.rna_rag.national_v6_embeddings_768d",
    )
    sql_rerun = bq_rerun.query.call_args_list[0].args[0]
    assert "c.* EXCEPT (embedding_gemini_768), e.embedding_gemini_768" in sql_rerun

    verified = uploader.verify_merged_embeddings(
        bq_rerun,
        "proj.rna_rag.national_v6_consolidated",
    )
    assert verified["null_embeddings"] == 0

    bq_rerun.query.return_value.result.return_value = [
        {
            "total_rows": uploader.EXPECTED_CONSOLIDATED_ROWS,
            "null_embeddings": 3,
            "invalid_dim_rows": 0,
        }
    ]
    with pytest.raises(RuntimeError, match="NULL embedding_gemini_768"):
        uploader.verify_merged_embeddings(
            bq_rerun,
            "proj.rna_rag.national_v6_consolidated",
        )

