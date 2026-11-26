"""Unit tests for Commun RNA V6 BigQuery ingest cleaners and contracts."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from pipeline.common import PipelineLogger
from pipeline.ingest import (
    _rna_v6_hebergement_verified_table_id,
    _rna_v6_refugee_verified_table_id,
    _rna_v6_table_id,
    _rna_v6_tags_ref_table_id,
    clean_hebergement_rna,
    clean_refugee_associations,
    fetch_rna_v6_commune_stats,
    fetch_rna_v6_tags_bdv,
    fetch_rna_v6_thematiques_agg,
)
from pipeline.ingest.cleaners.rna_rag import (
    _load_inclusion_label_to_slug_map,
    _normalize_inclusion_label,
)
from pipeline.run_context import PipelineRunError


def _v6_config():
    return {
        "sources": {
            "rna_rag": {"table": "rna_rag.rna_rag_clustered", "ttl_days": 365},
            "rna_v6": {
                "table": "rna_rag.national_v6_consolidated",
                "tags_ref_table": "rna_rag.referentiel_tags_canoniques_n50",
                "refugee_verified_table": "rna_rag.refugee_v6_verified",
                "hebergement_verified_table": "rna_rag.hebergement_v6_verified",
                "ttl_days": 30,
            },
            "bassins_de_vie": {},
        }
    }


def test_rna_v6_table_resolvers_validate_format(monkeypatch):
    monkeypatch.setenv("ODIS_DATA_PROJECT", "odis-test-proj")
    cfg = _v6_config()
    assert _rna_v6_table_id(cfg) == "odis-test-proj.rna_rag.national_v6_consolidated"
    assert (
        _rna_v6_tags_ref_table_id(cfg)
        == "odis-test-proj.rna_rag.referentiel_tags_canoniques_n50"
    )
    assert (
        _rna_v6_refugee_verified_table_id(cfg)
        == "odis-test-proj.rna_rag.refugee_v6_verified"
    )
    assert (
        _rna_v6_hebergement_verified_table_id(cfg)
        == "odis-test-proj.rna_rag.hebergement_v6_verified"
    )

    bad_cfg = {
        "sources": {
            "rna_v6": {
                "table": "bad_table",
                "tags_ref_table": "a.b.c",
                "refugee_verified_table": "invalid",
                "hebergement_verified_table": "invalid_heb",
            }
        }
    }
    with pytest.raises(ValueError, match="sources.rna_v6.table"):
        _rna_v6_table_id(bad_cfg)
    with pytest.raises(ValueError, match="sources.rna_v6.tags_ref_table"):
        _rna_v6_tags_ref_table_id(bad_cfg)
    with pytest.raises(ValueError, match="sources.rna_v6.refugee_verified_table"):
        _rna_v6_refugee_verified_table_id(bad_cfg)
    with pytest.raises(ValueError, match="sources.rna_v6.hebergement_verified_table"):
        _rna_v6_hebergement_verified_table_id(bad_cfg)


def test_inclusion_referential_csv_covers_all_69_themes():
    mapping = _load_inclusion_label_to_slug_map()
    assert len(mapping) == 69
    assert mapping["Maîtriser le français"] == "lecture-ecriture-calcul--maitriser-le-francais"
    assert (
        mapping[_normalize_inclusion_label("Accompagnement pour l'accès aux droits")]
        == "difficultes-administratives-ou-juridiques--accompagnement-pour-lacces-aux-droits"
    )


def test_fetch_rna_v6_commune_stats_queries_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("ODIS_DATA_PROJECT", "odis-test-proj")
    shared_dir = tmp_path / "shared"
    candidate_dir = tmp_path / "candidate"
    fake_client = MagicMock()
    fake_client.query.return_value.to_dataframe.return_value = pd.DataFrame(
        {
            "codgeo": ["75056", "33063"],
            "lien_social_count": [12500, 1400],
            "inc_asso_refug_count": [310, 42],
        }
    )

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", candidate_dir),
        patch("pipeline.ingest.bigquery.Client", return_value=fake_client),
    ):
        logger = PipelineLogger(tmp_path / "run.json")
        out = fetch_rna_v6_commune_stats(logger, _v6_config())

    assert out == candidate_dir / "rna_v6_inclusion_agg.parquet"
    assert (shared_dir / "rna_v6_inclusion_agg.parquet").exists()
    sql = fake_client.query.call_args.args[0]
    assert "COUNT(DISTINCT id)" in sql
    assert "llm_action_sociale = TRUE" in sql
    assert "llm_perimetre_action != 'international'" in sql
    assert "odis-test-proj.rna_rag.refugee_v6_verified" in sql
    assert "is_refugee_verified = TRUE" in sql
    assert "75056" in sql
    assert fake_client.query.call_args.kwargs["location"] == "europe-west1"

    saved = pd.read_parquet(out)
    assert list(saved.columns) == ["codgeo", "lien_social_count", "inc_asso_refug_count"]


def test_fetch_rna_v6_thematiques_agg_maps_labels_and_fails_on_unmapped(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ODIS_DATA_PROJECT", "odis-test-proj")
    shared_dir = tmp_path / "shared"
    candidate_dir = tmp_path / "candidate"

    fake_client = MagicMock()
    fake_client.query.return_value.to_dataframe.return_value = pd.DataFrame(
        {
            "grain": ["commune", "bdv"],
            "geo_code": ["33063", "33063"],
            "theme_label": [
                "Maîtriser le français",
                "Connaître les opportunités d'emploi",  # straight apostrophe normalized
            ],
            "count": [15, 22],
        }
    )

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", candidate_dir),
        patch("pipeline.ingest.bigquery.Client", return_value=fake_client),
    ):
        logger = PipelineLogger(tmp_path / "run.json")
        out = fetch_rna_v6_thematiques_agg(logger, _v6_config())

    saved = pd.read_parquet(out)
    assert set(saved["thematique_slug"]) == {
        "lecture-ecriture-calcul--maitriser-le-francais",
        "choisir-un-metier--connaitre-les-opportunites-demploi",
    }

    # Unmapped label must fail closed with PipelineRunError
    bad_dir = tmp_path / "bad"
    bad_client = MagicMock()
    bad_client.query.return_value.to_dataframe.return_value = pd.DataFrame(
        {
            "grain": ["commune"],
            "geo_code": ["33063"],
            "theme_label": ["Thématique Inconnue V7"],
            "count": [3],
        }
    )
    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", tmp_path / "shared2"),
        patch("pipeline.ingest.CLEAN_DIR", bad_dir),
        patch("pipeline.ingest.bigquery.Client", return_value=bad_client),
        pytest.raises(PipelineRunError, match="Unmapped RNA V6 llm_thematiques"),
    ):
        fetch_rna_v6_thematiques_agg(PipelineLogger(tmp_path / "bad.json"), _v6_config())


def test_fetch_rna_v6_tags_bdv_converts_ndarray_examples_and_caches(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ODIS_DATA_PROJECT", "odis-test-proj")
    shared_dir = tmp_path / "shared"
    candidate_dir = tmp_path / "candidate"

    df_bdv = pd.DataFrame(
        {
            "bassin_de_vie": ["33063", "64445"],
            "tag_canonique": ["volley ball", "sport"],
            "count": [12, 8],
        }
    )
    df_ref = pd.DataFrame(
        {
            "tag_canonique": ["volley ball", "sport"],
            "occ_national": [3200, 120000],
            "nb_variantes_fusionnees": [4, 2],
            "exemples_bruts": [
                np.array(["volley", "volley-ball"]),
                np.array(["sport"]),
            ],
        }
    )

    fake_client = MagicMock()
    fake_client.query.return_value.to_dataframe.side_effect = [df_bdv, df_ref]

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", candidate_dir),
        patch("pipeline.ingest.bigquery.Client", return_value=fake_client),
    ):
        logger = PipelineLogger(tmp_path / "run.json")
        bdv_out, ref_out = fetch_rna_v6_tags_bdv(logger, _v6_config())

    assert bdv_out.exists()
    assert ref_out.exists()
    saved_ref = pd.read_parquet(ref_out)
    assert list(saved_ref["exemples_bruts"].iloc[0]) == ["volley", "volley-ball"]

    # Second call reuses shared cache without calling BigQuery again
    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", tmp_path / "candidate2"),
        patch("pipeline.ingest.bigquery.Client") as client_cls,
    ):
        fetch_rna_v6_tags_bdv(PipelineLogger(tmp_path / "run2.json"), _v6_config())
    client_cls.assert_not_called()


def test_clean_refugee_associations_v6_ignores_legacy_shared_cache(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ODIS_DATA_PROJECT", "odis-test-proj")
    shared_dir = tmp_path / "shared"
    candidate_dir = tmp_path / "candidate"
    shared_dir.mkdir(parents=True)
    # Plant stale legacy cache file that must NOT be reused when rna_v6 is configured
    pd.DataFrame(
        {
            "id": ["LEGACY1"],
            "codgeo": ["75056"],
            "bassin_de_vie": ["75056"],
            "name": ["Old"],
            "description": ["Old"],
            "waldec_code": ["019000"],
        }
    ).to_parquet(shared_dir / "refugee_associations.parquet")

    candidate_dir.mkdir(parents=True)
    pd.DataFrame({"codgeo": ["33063"], "bassin_de_vie": ["33063"]}).to_parquet(
        candidate_dir / "bassins_de_vie.parquet"
    )

    fake_client = MagicMock()
    fake_client.query.return_value.to_dataframe.return_value = pd.DataFrame(
        {
            "id": ["W332000111"],
            "codgeo": ["33063"],
            "bassin_de_vie": [None],
            "name": ["Accueil Réfugiés Gironde"],
            "description": ["Accompagnement des personnes exilées"],
            "waldec_code": ["019025"],
            "nom_etablissement": ["Antenne Bordeaux"],
            "commune_nom": ["Bordeaux"],
            "tranche_effectifs": ["12"],
            "date_decla": ["2026-05-01"],
        }
    )

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", candidate_dir),
        patch("pipeline.ingest.bigquery.Client", return_value=fake_client),
    ):
        clean_refugee_associations(_v6_config(), PipelineLogger(tmp_path / "run.json"))

    assert fake_client.query.call_count == 1
    sql = fake_client.query.call_args.args[0]
    assert "odis-test-proj.rna_rag.refugee_v6_verified" in sql
    assert "is_refugee_verified = TRUE" in sql
    assert "QUALIFY ROW_NUMBER()" in sql
    assert (shared_dir / "rna_v6_refugee_associations.parquet").exists()
    out_df = pd.read_parquet(candidate_dir / "refugee_associations.parquet")
    assert out_df["id"].tolist() == ["W332000111"]
    assert out_df["bassin_de_vie"].tolist() == ["33063"]
    assert out_df["tranche_effectifs"].tolist() == ["12"]
    assert out_df["date_decla"].tolist() == ["2026-05-01"]
    status = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert "rna_v6_refugee" in status["sources"]


def test_needs_rna_v6_refugee_refresh_respects_ttl(tmp_path):
    from pipeline.ingest import context
    from pipeline.ingest.main import _needs_rna_v6_refugee_refresh

    shared_dir = tmp_path / "shared"
    clean_dir = tmp_path / "clean"
    shared_dir.mkdir()
    clean_dir.mkdir()
    cfg = _v6_config()

    with (
        patch.object(context, "SHARED_CLEAN_DIR", shared_dir),
        patch.object(context, "CLEAN_DIR", clean_dir),
    ):
        assert _needs_rna_v6_refugee_refresh(cfg) is True

        (shared_dir / "rna_v6_inclusion_agg.parquet").write_bytes(b"ok")
        (shared_dir / "rna_v6_refugee_associations.parquet").write_bytes(b"ok")
        assert _needs_rna_v6_refugee_refresh(cfg) is False


def test_sync_refugee_verified_delta_from_bq_noop_when_empty(monkeypatch):
    import polars as pl
    from pipeline.scripts import qualify_refugee_associations as q

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    empty_df = pl.DataFrame(schema={"id": pl.String, "text_source_sha256": pl.String})
    with (
        patch.object(q.bigquery, "Client") as mock_bq_cls,
        patch.object(q, "fetch_bq_candidate_delta", return_value=empty_df) as mock_fetch,
        patch.object(q, "run_decisions_batch") as mock_batch,
        patch.object(q, "merge_delta_to_bigquery") as mock_merge,
    ):
        count = q.sync_refugee_verified_delta_from_bq(project_id="odis-test-proj")

    assert count == 0
    mock_bq_cls.assert_called_once_with(project="odis-test-proj")
    mock_fetch.assert_called_once()
    mock_batch.assert_not_called()
    mock_merge.assert_not_called()


def test_sync_refugee_verified_delta_from_bq_evaluates_and_merges(tmp_path, monkeypatch):
    import polars as pl
    from pipeline.scripts import qualify_refugee_associations as q

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    delta_df = pl.DataFrame(
        {
            "id": ["W332999999"],
            "text_source_sha256": ["abc123"],
            "is_siege": [True],
            "adrs_codeinsee": ["33063"],
            "bdv_codeinsee": ["33063"],
            "title": ["ASSO TEST"],
            "titre_court": [""],
            "objet": ["Accueil des réfugiés"],
            "waldec_labels_hierarchical": ["ACTION SOCIALE"],
            "tranche_effectifs": ["01"],
            "date_decla": ["2026-11-01"],
            "llm_tags_recherche": [["réfugiés"]],
            "llm_thematiques": [["Accompagnement"]],
            "llm_publics_cibles": [["Personnes exilées"]],
            "in_exiles_tag": [True],
            "in_regex_match": [True],
            "in_topk_768d": [False],
            "cosine_768d": [None],
        }
    )
    fake_rec = q.RefugeeDecisionRecord(
        id="W332999999",
        text_source_sha256="abc123",
        p_noul=0.95,
        noul_confidence=0.95,
        role_choice="accompagnement_accueil",
        role_confidence=0.98,
        p_accompagnement_accueil=0.98,
        p_amicale_culture_culte=0.01,
        p_autre=0.01,
        input_tokens=400,
        evaluated_at="2026-11-23T15:00:00+00:00",
    )

    async def fake_run_batch(*args, **kwargs):
        return [fake_rec]

    with (
        patch.object(q.bigquery, "Client"),
        patch.object(q, "fetch_bq_candidate_delta", return_value=delta_df),
        patch.object(q, "run_decisions_batch", side_effect=fake_run_batch),
        patch.object(q, "merge_delta_to_bigquery") as mock_merge,
    ):
        count = q.sync_refugee_verified_delta_from_bq(
            project_id="odis-test-proj",
            jsonl_cache=tmp_path / "cache.jsonl",
        )

    assert count == 1
    mock_merge.assert_called_once()
    merged_arg = mock_merge.call_args.args[1]
    assert merged_arg["is_refugee_verified"].to_list() == [True]


def test_clean_hebergement_rna_v6_queries_multi_antenna_and_caches(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ODIS_DATA_PROJECT", "odis-test-proj")
    shared_dir = tmp_path / "shared"
    candidate_dir = tmp_path / "candidate"
    # Pre-existing legacy V5 cache must NOT shadow V6 rna_v6_hebergement_cols.parquet
    shared_dir.mkdir(parents=True)
    pd.DataFrame(
        {"codgeo": ["75115"], "heb_loc_iml_count": [99], "heb_habitant_count": [99]}
    ).to_parquet(shared_dir / "hebergement_rna_cols.parquet", index=False)

    fake_client = MagicMock()
    fake_client.query.return_value.to_dataframe.return_value = pd.DataFrame(
        {
            "codgeo": ["75056", "33063"],
            "heb_loc_iml_count": [14, 6],
            "heb_habitant_count": [5, 3],
        }
    )

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", shared_dir),
        patch("pipeline.ingest.CLEAN_DIR", candidate_dir),
        patch("pipeline.ingest.bigquery.Client", return_value=fake_client),
    ):
        logger = PipelineLogger(tmp_path / "run.json")
        clean_hebergement_rna(_v6_config(), logger)

    assert fake_client.query.call_count == 1
    sql = fake_client.query.call_args.args[0]
    assert "odis-test-proj.rna_rag.national_v6_consolidated" in sql
    assert "odis-test-proj.rna_rag.hebergement_v6_verified" in sql
    assert "INNER JOIN verified" in sql
    assert "is_iml_verified = TRUE" in sql
    assert "is_hebergement_citoyen_verified = TRUE" in sql
    assert "75056" in sql
    assert fake_client.query.call_args.kwargs["location"] == "europe-west1"

    out_df = pd.read_parquet(candidate_dir / "hebergement_rna_cols.parquet")
    assert list(out_df.columns) == [
        "codgeo",
        "heb_loc_iml_count",
        "heb_habitant_count",
    ]
    assert out_df["heb_loc_iml_count"].tolist() == [14, 6]
    assert out_df["heb_habitant_count"].tolist() == [5, 3]
    assert (shared_dir / "rna_v6_hebergement_cols.parquet").exists()
    status = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert "rna_v6_hebergement" in status["sources"]


def test_clean_hebergement_rna_v6_fails_closed_on_empty_result(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ODIS_DATA_PROJECT", "odis-test-proj")
    fake_client = MagicMock()
    fake_client.query.return_value.to_dataframe.return_value = pd.DataFrame(
        columns=["codgeo", "heb_loc_iml_count", "heb_habitant_count"]
    )

    with (
        patch("pipeline.ingest.SHARED_CLEAN_DIR", tmp_path / "shared"),
        patch("pipeline.ingest.CLEAN_DIR", tmp_path / "candidate"),
        patch("pipeline.ingest.bigquery.Client", return_value=fake_client),
        pytest.raises(PipelineRunError, match="RNA V6 hébergement query returned 0 rows"),
    ):
        clean_hebergement_rna(_v6_config(), PipelineLogger(tmp_path / "fail.json"))


def test_needs_rna_v6_hebergement_refresh_respects_ttl(tmp_path):
    from pipeline.ingest import context
    from pipeline.ingest.main import _needs_rna_v6_hebergement_refresh

    shared_dir = tmp_path / "shared"
    clean_dir = tmp_path / "clean"
    shared_dir.mkdir()
    clean_dir.mkdir()
    cfg = _v6_config()

    with (
        patch.object(context, "SHARED_CLEAN_DIR", shared_dir),
        patch.object(context, "CLEAN_DIR", clean_dir),
    ):
        assert _needs_rna_v6_hebergement_refresh(cfg) is True

        # Legacy hebergement_rna_cols.parquet in shared_dir must NOT satisfy V6 refresh
        (shared_dir / "hebergement_rna_cols.parquet").write_bytes(b"legacy")
        assert _needs_rna_v6_hebergement_refresh(cfg) is True

        (shared_dir / "rna_v6_hebergement_cols.parquet").write_bytes(b"v6ok")
        assert _needs_rna_v6_hebergement_refresh(cfg) is False


def test_sync_hebergement_verified_delta_from_bq_noop_when_empty(monkeypatch):
    import polars as pl
    from pipeline.scripts import qualify_hebergement_associations as qh

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    empty_df = pl.DataFrame(schema={"id": pl.String, "text_source_sha256": pl.String})
    with (
        patch.object(qh.bigquery, "Client") as mock_bq_cls,
        patch.object(qh, "fetch_bq_candidate_delta", return_value=empty_df) as mock_fetch,
        patch.object(qh, "run_decisions_batch_generic") as mock_batch,
        patch.object(qh, "merge_delta_to_bigquery") as mock_merge,
    ):
        count = qh.sync_hebergement_verified_delta_from_bq(project_id="odis-test-proj")

    assert count == 0
    mock_bq_cls.assert_called_once_with(project="odis-test-proj")
    mock_fetch.assert_called_once()
    mock_batch.assert_not_called()
    mock_merge.assert_not_called()


def test_sync_hebergement_verified_delta_from_bq_evaluates_and_merges_075(
    tmp_path, monkeypatch
):
    import polars as pl
    from pipeline.scripts import qualify_hebergement_associations as qh

    monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
    delta_df = pl.DataFrame(
        {
            "id": ["W332999998"],
            "text_source_sha256": ["def456"],
            "is_siege": [True],
            "adrs_codeinsee": ["33063"],
            "bdv_codeinsee": ["33063"],
            "title": ["ASSO TEST HEB"],
            "titre_court": [""],
            "objet": ["Intermédiation locative et sous-location solidaire"],
            "waldec_labels_hierarchical": ["LOGEMENT"],
            "tranche_effectifs": ["01"],
            "date_decla": ["2026-11-01"],
            "llm_tags_recherche": [["sous-location"]],
            "llm_thematiques": [["Louer un logement"]],
            "llm_publics_cibles": [["Familles"]],
            "llm_est_urgence": [False],
            "in_iml_regex": [True],
            "in_cit_regex": [False],
            "in_heb_theme": [True],
            "in_heb_temp_theme": [False],
            "in_topk_768d_iml": [False],
            "in_topk_768d_cit": [False],
            "cosine_768d_iml": [None],
            "cosine_768d_cit": [None],
        }
    )
    fake_rec = qh.HebergementDecisionRecord(
        id="W332999998",
        text_source_sha256="def456",
        p_iml=0.73,  # below 0.75 calibrated threshold
        iml_confidence=0.80,
        p_hebergement_citoyen=0.86,  # above 0.75 calibrated threshold
        citoyen_confidence=0.90,
        p_habitat_partage_pension=0.20,
        habitat_partage_confidence=0.85,
        p_hebergement_collectif_urgent=0.10,
        collectif_urgent_confidence=0.95,
        input_tokens=500,
        evaluated_at="2026-11-23T17:00:00+00:00",
    )

    async def fake_run_batch(*args, **kwargs):
        return [fake_rec]

    with (
        patch.object(qh.bigquery, "Client"),
        patch.object(qh, "fetch_bq_candidate_delta", return_value=delta_df),
        patch.object(qh, "run_decisions_batch_generic", side_effect=fake_run_batch),
        patch.object(qh, "merge_delta_to_bigquery") as mock_merge,
    ):
        count = qh.sync_hebergement_verified_delta_from_bq(
            project_id="odis-test-proj",
            jsonl_cache=tmp_path / "heb_cache.jsonl",
        )

    assert count == 1
    mock_merge.assert_called_once()
    merged_arg = mock_merge.call_args.args[1]
    assert merged_arg["is_iml_verified"].to_list() == [False]
    assert merged_arg["is_hebergement_citoyen_verified"].to_list() == [True]
    assert merged_arg["is_hebergement_any_verified"].to_list() == [True]
