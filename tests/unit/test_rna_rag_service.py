import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from app.services.rna_rag import (
    BM25Ranker,
    RNARagService,
    configure_bigquery_pool,
    normalize_tokens,
    strip_accents,
)


@pytest.fixture
def mock_clients():
    with (
        patch("app.services.rna_rag.bigquery.Client") as mock_bq,
        patch("app.services.rna_rag.get_gemini_client") as mock_gemini,
    ):
        # Mock BQ client project
        mock_bq_instance = mock_bq.return_value
        mock_bq_instance.project = "odis-stream2-app"

        # Mock Gemini client embed_content (supports single and batch contents)
        mock_gemini_instance = mock_gemini.return_value

        def _fake_embed_content(*, model, contents, config):
            resp = MagicMock()
            resp.embeddings = [MagicMock(values=[0.1] * 768) for _ in contents]
            return resp

        mock_gemini_instance.models.embed_content.side_effect = _fake_embed_content

        yield mock_bq_instance, mock_gemini_instance


def test_strip_accents_and_normalize_tokens():
    assert strip_accents("Éléphant à l'opéra") == "Elephant a l'opera"
    assert strip_accents("") == ""

    # Test French stopword filtering, lowercasing, accent stripping, length >= 2
    tokens = normalize_tokens("Le club de Football et d'Athlétisme pour tous")
    assert tokens == ["club", "football", "athletisme", "tous"]

    # Test query with mosque / culte
    tokens_culte = normalize_tokens("Mosquée & communauté musulmane")
    assert tokens_culte == ["mosquee", "communaute", "musulmane"]


def test_strip_accents_and_normalize_tokens_handles_nan_and_none():
    """Verifies that float NaN (from pandas missing values) and None do not raise TypeError."""
    assert strip_accents(None) == ""
    assert strip_accents(float("nan")) == ""
    assert strip_accents(np.nan) == ""

    assert normalize_tokens(None) == []
    assert normalize_tokens(float("nan")) == []
    assert normalize_tokens(np.nan) == []


def test_bm25_ranker_handles_nan_and_none_fields():
    """Verifies that candidates with NaN/None category or description are scored without crash."""
    ranker = BM25Ranker(k1=1.5, b=0.75)
    candidates = [
        {
            "id": "W1",
            "name": "Club de Football",
            "categorie": float("nan"),
            "description": None,
        },
        {
            "id": "W2",
            "name": "Secours Solidaire",
            "categorie": np.nan,
            "description": float("nan"),
        },
    ]
    results = ranker.rank(candidates, "football", top_k=2)
    assert len(results) == 1
    assert results[0]["id"] == "W1"
    assert results[0]["score"] > 0


def test_bm25_ranker_field_weighting():
    ranker = BM25Ranker(k1=1.5, b=0.75)

    candidates = [
        {
            "id": "W1",
            "name": "Football Club de Bordeaux",
            "categorie": "SPORTS",
            "description": "Pratique sportive",
        },
        {
            "id": "W2",
            "name": "Association de Quartier",
            "categorie": "SPORTS (football)",
            "description": "Animations pour jeunes",
        },
        {
            "id": "W3",
            "name": "Club de Danse",
            "categorie": "CULTURE",
            "description": "Parfois nous jouons au football en détente",
        },
    ]

    results = ranker.rank(candidates, "football", top_k=3)
    assert len(results) == 3

    # W1 has "football" in title (boost 3.0), W2 has it in category (boost 2.0), W3 in description (boost 1.0)
    assert results[0]["id"] == "W1"
    assert results[1]["id"] == "W2"
    assert results[2]["id"] == "W3"
    assert results[0]["score"] > results[1]["score"] > results[2]["score"]


def test_flatten_embedding(mock_clients):
    service = RNARagService()

    # 1. Flatten list
    flat_list = service._flatten_embedding([1.0, 2.0, 3.0])
    assert isinstance(flat_list, np.ndarray)
    assert (flat_list == [1.0, 2.0, 3.0]).all()

    # 2. Flatten numpy array
    flat_arr = service._flatten_embedding(np.array([4.0, 5.0]))
    assert (flat_arr == [4.0, 5.0]).all()

    # 3. Flatten dictionary format: {'list': [{'element': 0.1}, ...]}
    dict_format = {"list": [{"element": 0.1}, {"element": 0.2}]}
    flat_dict = service._flatten_embedding(dict_format)
    assert (flat_dict == [0.1, 0.2]).all()

    # 4. Invalid types
    with pytest.raises(ValueError):
        service._flatten_embedding("invalid")
    with pytest.raises(ValueError):
        service._flatten_embedding({"list": [{"invalid": 0.1}]})


def test_get_embedding_backwards_compatibility(mock_clients):
    bq, gemini = mock_clients
    service = RNARagService()

    emb = service._get_embedding("hello")
    assert isinstance(emb, np.ndarray)
    assert len(emb) == 768
    assert np.isclose(np.linalg.norm(emb), 1.0)
    assert service.embedding_model == "gemini-embedding-001"


def test_get_embeddings_batch_single_call(mock_clients):
    bq, gemini = mock_clients
    service = RNARagService()

    embs = service.get_embeddings_batch(["soutien psychologique", "interprétariat médical"])
    assert len(embs) == 2
    assert all(len(v) == 768 and np.isclose(np.linalg.norm(v), 1.0) for v in embs)
    gemini.models.embed_content.assert_called_once()
    call_kwargs = gemini.models.embed_content.call_args.kwargs
    assert call_kwargs["model"] == "gemini-embedding-001"
    assert call_kwargs["config"] == {
        "output_dimensionality": 768,
        "task_type": "RETRIEVAL_QUERY",
    }


def test_get_associations_semantic_bm25(mock_clients):
    bq, gemini = mock_clients
    mock_query_job = MagicMock()
    mock_df = pd.DataFrame(
        [
            {
                "id": "W332000001",
                "name": "Football Club de Bordeaux",
                "primary_category": "011",
                "code_waldec": "011075",
                "categorie": "football",
                "description": "Club de foot pour tous",
                "codgeo": "33063",
            },
            {
                "id": "W332000002",
                "name": "Tennis Club",
                "primary_category": "011",
                "code_waldec": "011080",
                "categorie": "tennis",
                "description": "Club de raquette",
                "codgeo": "33063",
            },
        ]
    )
    mock_query_job.to_dataframe.return_value = mock_df
    bq.query.return_value = mock_query_job

    service = RNARagService()
    results = service.get_associations_semantic("football", codgeos=["33063"], top_k=5)

    assert len(results) == 1
    assert results[0]["id"] == "W332000001"
    assert results[0]["name"] == "Football Club de Bordeaux"
    assert results[0]["score"] > 0
    assert results[0]["is_same_commune"] is True
    assert results[0]["assoce_url"] == "https://www.assoce.fr/waldec/W332000001"
    assert bq.query.called


def test_get_associations_semantic_hybrid_rrf_and_dedup(mock_clients):
    bq, gemini = mock_clients
    mock_query_job = MagicMock()
    mock_df = pd.DataFrame(
        [
            # Dense #1 only (synonym without lexical match)
            {
                "id": "W420000001",
                "entity_id": "W420000001::siege",
                "is_siege": True,
                "name": "Reseau Ecoute Exil",
                "nom_etablissement": None,
                "description": "Accompagnement traumatismes psychiques",
                "waldec_label": "Action sociale",
                "codgeo": "42218",
                "commune_nom": "SAINT-ETIENNE",
                "date_decla": "2024-03-10",
                "tranche_effectifs": "02",
                "llm_tags_recherche": ["ecoute", "trauma"],
                "dense_rank": 1,
                "cosine_sim": 0.84123,
            },
            # Dual match: Dense #2 + BM25 #1 -> should rank #1 after RRF!
            {
                "id": "W420000002",
                "entity_id": "W420000002::siege",
                "is_siege": True,
                "name": "Centre Soutien Psychologique",
                "nom_etablissement": None,
                "description": "Soutien psychologique pour allophones",
                "waldec_label": "Sante mentale",
                "codgeo": "42095",
                "commune_nom": "FIRMINY",
                "date_decla": "2023-11-05",
                "tranche_effectifs": "NN",
                "llm_tags_recherche": ["soutien psychologique"],
                "dense_rank": 2,
                "cosine_sim": 0.81234,
            },
            # Same association & same commune (succursale) -> deduplicated against siege above!
            {
                "id": "W420000002",
                "entity_id": "W420000002::antenne1",
                "is_siege": False,
                "name": "Centre Soutien Psychologique",
                "nom_etablissement": "Antenne Firminy",
                "description": "Soutien psychologique pour allophones",
                "waldec_label": "Sante mentale",
                "codgeo": "42095",
                "commune_nom": "FIRMINY",
                "date_decla": "2023-11-05",
                "tranche_effectifs": "NN",
                "llm_tags_recherche": ["soutien psychologique"],
                "dense_rank": 3,
                "cosine_sim": 0.79,
            },
        ]
    )
    mock_query_job.to_dataframe.return_value = mock_df
    bq.query.return_value = mock_query_job

    service = RNARagService()
    results = service.get_associations_semantic(
        "soutien psychologique",
        codgeos=["42218"],
        bv_code="42218",
        top_k=10,
    )

    assert len(results) == 2
    assert results[0]["id"] == "W420000002"
    assert results[0]["is_same_commune"] is False
    assert results[0]["has_employees"] is False
    assert results[0]["matched_terms"] == ["soutien", "psychologique"]
    assert results[1]["id"] == "W420000001"
    assert results[1]["is_same_commune"] is True
    assert results[1]["has_employees"] is True
    assert results[1]["cosine_sim"] == 0.8412
    assert "llm_thematiques" not in results[0]
    assert "tags_recherche" not in results[0]

    sql_sent = bq.query.call_args.args[0]
    assert "national_v6_consolidated" in sql_sent
    assert "TRIM(bdv_codeinsee) = @bv_code" in sql_sent
    assert "LOWER(TRIM(llm_perimetre_action)) != 'international'" in sql_sent
    assert "COSINE_DISTANCE(embedding_gemini_768, @query_vec)" in sql_sent


def test_get_associations_semantic_rejects_full_table_scan(mock_clients):
    service = RNARagService()
    with pytest.raises(ValueError, match="full-table scan is forbidden"):
        service.get_associations_semantic("football")


def test_get_associations_semantic_empty_query(mock_clients):
    bq, gemini = mock_clients
    service = RNARagService()
    # If query contains only stopwords or empty text, returns empty list without calling BQ
    results = service.get_associations_semantic("de la le", codgeos=["33063"])
    assert results == []
    assert not bq.query.called


def test_get_associations_by_codgeo(mock_clients):
    bq, gemini = mock_clients
    mock_query_job = MagicMock()
    mock_df = pd.DataFrame(
        [{"id": "W2", "name": "Secours Populaire", "codgeo": "75056"}]
    )
    mock_query_job.to_dataframe.return_value = mock_df
    bq.query.return_value = mock_query_job

    service = RNARagService()
    results = service.get_associations_by_codgeo(["75056"])

    assert len(results) == 1
    assert results[0]["name"] == "Secours Populaire"
    assert bq.query.called


def test_configure_bigquery_pool_mounts_adapters():
    """Verifies configure_bigquery_pool configures HTTPAdapter on both _http and _auth_request.session."""
    mock_client = MagicMock()
    mock_http = MagicMock()
    mock_auth_req = MagicMock()
    mock_auth_session = MagicMock()

    mock_client._http = mock_http
    mock_http._auth_request = mock_auth_req
    mock_auth_req.session = mock_auth_session

    configure_bigquery_pool(mock_client, pool_size=50)

    # Check that mount was called for both https:// and http:// on _http
    assert mock_http.mount.call_count == 2
    # Check that mount was called for both https:// and http:// on auth session
    assert mock_auth_session.mount.call_count == 2


def test_configure_bigquery_pool_real_session():
    """Verifies that configure_bigquery_pool sets pool_maxsize=50 on real requests.Session objects."""
    import requests

    class FakeClient:
        def __init__(self):
            self._http = requests.Session()
            auth_req = MagicMock()
            auth_req.session = requests.Session()
            self._http._auth_request = auth_req

    fake_client = FakeClient()
    configure_bigquery_pool(fake_client, pool_size=50)

    assert fake_client._http.adapters["https://"]._pool_maxsize == 50
    assert fake_client._http.adapters["http://"]._pool_maxsize == 50
    assert (
        fake_client._http._auth_request.session.adapters["https://"]._pool_maxsize == 50
    )
    assert (
        fake_client._http._auth_request.session.adapters["http://"]._pool_maxsize == 50
    )
