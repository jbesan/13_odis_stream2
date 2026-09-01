import json

import pandas as pd
from unittest.mock import MagicMock, patch

from pipeline.common import PipelineLogger
from pipeline.ingest import (
    clean_hebergement_rna,
    clean_refugee_associations,
    fetch_rna_rag_stats,
)


def _write_cache(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"codgeo": ["75001"], "inc_rna_Logement_count": [3]}).to_parquet(
        path
    )


def _config():
    return {"sources": {"rna_rag": {"ttl_days": 365}}}


def test_fetch_rna_rag_stats_reuses_shared_cache_for_new_candidate(tmp_path):
    shared_dir = tmp_path / "shared"
    candidate_dir = tmp_path / "candidate"
    shared_path = shared_dir / "rna_inclusion_agg.parquet"
    _write_cache(shared_path)

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", candidate_dir),
        patch("pipeline.ingest.bigquery.Client") as client_class,
    ):
        logger = PipelineLogger(tmp_path / "run.json")
        result = fetch_rna_rag_stats(logger, _config())

    client_class.assert_not_called()
    assert result == candidate_dir / "rna_inclusion_agg.parquet"
    assert result.exists()
    state = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert state["sources"]["rna_rag"]["status"] == "reused_within_ttl"
    assert state["steps"]["fetch_rna_rag_stats"]["details"]["cache_status"] == (
        "reused_within_ttl"
    )


def test_hebergement_rna_reuses_shared_cache_without_rag_queries(tmp_path):
    shared_dir = tmp_path / "shared"
    candidate_dir = tmp_path / "candidate"
    shared_path = shared_dir / "hebergement_rna_cols.parquet"
    _write_cache(shared_path)

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", candidate_dir),
        patch("pipeline.ingest.compute_rna_rag_counts") as rag_query,
    ):
        logger = MagicMock(spec=PipelineLogger)
        clean_hebergement_rna(_config(), logger)

    rag_query.assert_not_called()
    assert (candidate_dir / "hebergement_rna_cols.parquet").exists()


def test_refugee_rna_reuses_shared_cache_without_bigquery_query(tmp_path):
    shared_dir = tmp_path / "shared"
    candidate_dir = tmp_path / "candidate"
    shared_path = shared_dir / "refugee_associations.parquet"
    _write_cache(shared_path)

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", candidate_dir),
        patch("pipeline.ingest.bigquery.Client") as client_class,
    ):
        logger = MagicMock(spec=PipelineLogger)
        clean_refugee_associations(_config(), logger)

    client_class.assert_not_called()
    assert (candidate_dir / "refugee_associations.parquet").exists()
