import numpy as np
import pandas as pd
import pytest
from unittest.mock import MagicMock, patch

from app.services.rna_rag import (
    BM25Ranker,
    RNARagService,
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
        mock_bq_instance.project = "odis-stream2"

        # Mock Gemini client embed_content
        mock_gemini_instance = mock_gemini.return_value
        mock_response = MagicMock()
        mock_response.embeddings = [MagicMock(values=[0.1] * 128)]
        mock_gemini_instance.models.embed_content.return_value = mock_response

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
    assert len(emb) == 128
    assert np.isclose(np.linalg.norm(emb), 1.0)


def test_get_associations_semantic_bm25(mock_clients):
    bq, gemini = mock_clients
    mock_query_job = MagicMock()
    mock_df = pd.DataFrame(
        [
            {
                "id": "W1",
                "name": "Football Club de Bordeaux",
                "primary_category": "011",
                "code_waldec": "011075",
                "categorie": "football",
                "description": "Club de foot pour tous",
                "codgeo": "33063",
            },
            {
                "id": "W2",
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
    assert results[0]["id"] == "W1"
    assert results[0]["name"] == "Football Club de Bordeaux"
    assert results[0]["score"] > 0
    assert bq.query.called


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
