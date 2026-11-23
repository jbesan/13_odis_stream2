"""Unit tests for Commun RNA V6 BigQuery ingest cleaners and contracts."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from pipeline.common import PipelineLogger
from pipeline.ingest import (
    _rna_v6_refugee_verified_table_id,
    _rna_v6_table_id,
    _rna_v6_tags_ref_table_id,
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

    bad_cfg = {
        "sources": {
            "rna_v6": {
                "table": "bad_table",
                "tags_ref_table": "a.b.c",
                "refugee_verified_table": "invalid",
            }
        }
    }
    with pytest.raises(ValueError, match="sources.rna_v6.table"):
        _rna_v6_table_id(bad_cfg)
    with pytest.raises(ValueError, match="sources.rna_v6.tags_ref_table"):
        _rna_v6_tags_ref_table_id(bad_cfg)
    with pytest.raises(ValueError, match="sources.rna_v6.refugee_verified_table"):
        _rna_v6_refugee_verified_table_id(bad_cfg)


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
