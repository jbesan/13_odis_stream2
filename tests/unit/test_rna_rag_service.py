import pytest
import numpy as np
import pandas as pd
from unittest.mock import patch, MagicMock
from app.services.rna_rag import RNARagService


@pytest.fixture
def mock_clients():
    with (
        patch("app.services.rna_rag.bigquery.Client") as mock_bq,
        patch("agents.agent_config.get_gemini_client") as mock_gemini,
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


def test_get_embedding(mock_clients):
    bq, gemini = mock_clients
    service = RNARagService()

    emb = service._get_embedding("hello")
    assert isinstance(emb, np.ndarray)
    assert len(emb) == 128
    # Assert it was normalized (norm should be 1.0)
    assert np.isclose(np.linalg.norm(emb), 1.0)


def test_get_associations_semantic(mock_clients):
    bq, gemini = mock_clients
    # Mock BigQuery query result
    mock_query_job = MagicMock()
    mock_df = pd.DataFrame(
        [{"id": "W1", "name": "Football Club", "codgeo": "75056", "score": 0.8}]
    )
    mock_query_job.to_dataframe.return_value = mock_df
    bq.query.return_value = mock_query_job

    service = RNARagService()
    results = service.get_associations_semantic("football", codgeos=["75056"])

    assert len(results) == 1
    assert results[0]["name"] == "Football Club"
    assert bq.query.called


def test_get_associations_by_codgeo(mock_clients):
    bq, gemini = mock_clients
    # Mock BQ query result
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
