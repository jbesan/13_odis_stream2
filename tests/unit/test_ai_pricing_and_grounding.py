from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pydantic_ai.messages import ModelResponse

from agents.google_model import GroundingGoogleModel
from agents.graph import capture_usage
from agents.grounding import extract_web_grounding
from agents.source_registry import source_references_for_result
from services.ai_pricing import calculate_gemini_cost


def test_gemini_31_cost_separates_new_and_cached_input_at_eur_skus():
    estimate = calculate_gemini_cost(
        "google:gemini-3.1-flash-lite",
        input_tokens=1_000,
        cache_read_tokens=400,
        output_tokens=100,
    )

    assert estimate.new_input_tokens == 600
    assert estimate.cached_input_tokens == 400
    assert estimate.eur_priced is True
    assert estimate.input_new_cost_eur == pytest.approx(0.000144837)
    assert estimate.input_cached_cost_eur == pytest.approx(0.0000096558)
    assert estimate.output_cost_eur == pytest.approx(0.000144837)
    assert estimate.total_cost_eur == pytest.approx(0.0002993298)


def test_gemini_35_cost_uses_confirmed_regional_eur_skus():
    estimate = calculate_gemini_cost(
        "google:gemini-3.5-flash-lite",
        input_tokens=1_000,
        cache_read_tokens=400,
        output_tokens=100,
    )

    assert estimate.new_input_tokens == 600
    assert estimate.cached_input_tokens == 400
    assert estimate.eur_priced is True
    assert estimate.pricing_status == "exact_eur_sku"
    assert estimate.input_new_cost_eur == pytest.approx(0.000158004)
    assert estimate.input_cached_cost_eur == pytest.approx(0.0000105336)
    assert estimate.output_cost_eur == pytest.approx(0.00021945)
    assert estimate.total_cost_eur == pytest.approx(0.0003879876)
    assert estimate.token_cost_usd == pytest.approx(0.000442)


def test_grounding_metadata_is_normalized_and_not_double_counted_with_native_call():
    message = SimpleNamespace(
        metadata={
            "google_grounding_metadata": {
                "web_search_queries": ["aide locale FLE Bordeaux"],
                "grounding_chunks": [
                    {
                        "web": {
                            "uri": "https://example.org/fle",
                            "title": "Cours de français",
                            "domain": "example.org",
                        }
                    }
                ],
                "grounding_supports": [
                    {
                        "segment": {"text": "Cours disponibles", "start_index": 0},
                        "grounding_chunk_indices": [0],
                    }
                ],
            }
        },
        parts=[
            SimpleNamespace(
                tool_name="web_search",
                args={"queries": ["aide locale FLE Bordeaux"]},
                content=None,
            )
        ],
    )
    result = SimpleNamespace(all_messages=lambda: [message])

    grounding = extract_web_grounding(result)

    assert grounding["query_count"] == 1
    assert grounding["queries"] == ["aide locale FLE Bordeaux"]
    assert grounding["sources"] == [
        {
            "url": "https://example.org/fle",
            "title": "Cours de français",
            "domain": "example.org",
        }
    ]
    assert grounding["supports"][0]["grounding_chunk_indices"] == [0]


def test_extract_web_grounding_preserves_adapter_normalized_metadata():
    message = SimpleNamespace(
        metadata={
            "google_grounding_metadata": {
                "queries": ["résultat Valence Betis"],
                "query_count": 1,
                "sources": [
                    {
                        "url": "https://example.org/match",
                        "title": "Résultat du match",
                        "domain": "example.org",
                    }
                ],
                "supports": [
                    {
                        "grounding_chunk_indices": [0],
                        "text": "Valence 0 - 1 Betis",
                        "start_index": 0,
                        "end_index": 19,
                    }
                ],
            }
        },
        parts=[
            SimpleNamespace(
                tool_name="web_search",
                args={"queries": ["résultat Valence Betis"]},
                content=None,
            )
        ],
    )
    result = SimpleNamespace(all_messages=lambda: [message])

    grounding = extract_web_grounding(result)

    assert grounding["query_count"] == 1
    assert grounding["queries"] == ["résultat Valence Betis"]
    assert grounding["sources"][0]["url"] == "https://example.org/match"
    assert grounding["supports"] == [
        {
            "grounding_chunk_indices": [0],
            "text": "Valence 0 - 1 Betis",
            "start_index": 0,
            "end_index": 19,
        }
    ]
    assert grounding["metadata_responses"] == 1

    captured = capture_usage(
        SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                requests=1,
                tool_calls=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
            ),
            all_messages=lambda: [message],
        ),
        "housing_expert",
        "google:gemini-3.5-flash-lite",
    )

    assert captured.grounding_queries == 1
    assert captured.breakdown["housing_expert"]["grounding_queries"] == [
        "résultat Valence Betis"
    ]


def test_source_ledger_exposes_provider_grounded_urls():
    message = SimpleNamespace(
        metadata={
            "google_grounding_metadata": {
                "web_search_queries": ["association locale"],
                "grounding_chunks": [
                    {"web": {"uri": "https://example.org/a", "title": "Association A"}}
                ],
            }
        },
        parts=[
            SimpleNamespace(
                tool_name="web_search", args={"queries": ["association locale"]}
            )
        ],
    )
    result = SimpleNamespace(all_messages=lambda: [message])

    references = source_references_for_result("social_integration_expert", result)

    web = [reference for reference in references if reference["source_key"] == "web"]
    assert len(web) == 1
    assert web[0]["label"] == "Association A"
    assert web[0]["source_url"] == "https://example.org/a"
    assert web[0]["grounding_queries"] == ["association locale"]


def test_capture_usage_records_grounding_and_places_requests():
    usage = SimpleNamespace(
        input_tokens=1_000,
        output_tokens=100,
        total_tokens=1_100,
        requests=2,
        tool_calls=2,
        cache_read_tokens=400,
        cache_write_tokens=600,
        cache_hit_ratio=0.4,
    )
    result = SimpleNamespace(
        usage=usage,
        all_messages=lambda: [
            SimpleNamespace(
                parts=[
                    SimpleNamespace(
                        tool_name="web_search",
                        args={"queries": ["q1", "q2"]},
                        content=None,
                    ),
                    SimpleNamespace(
                        tool_name="search_places_batch_tool",
                        args={"queries": ["FLE", "mairie"], "location": "Bordeaux"},
                        content=None,
                    ),
                ]
            )
        ],
    )

    captured = capture_usage(
        result, "social_integration_expert", "google:gemini-3.1-flash-lite"
    )

    assert captured.input_tokens_new == 600
    assert captured.grounding_queries == 2
    assert captured.places_requests == 2
    assert captured.cost_eur > 0
    assert captured.eur_priced is True


def test_grounding_google_model_keeps_normalized_provider_metadata():
    response = SimpleNamespace(
        candidates=[
            SimpleNamespace(
                grounding_metadata={
                    "web_search_queries": ["test"],
                    "grounding_chunks": [{"web": {"uri": "https://example.org"}}],
                }
            )
        ],
        usage_metadata={"prompt_token_count": 12, "cached_content_token_count": 5},
    )
    base_response = ModelResponse(parts=[])

    with patch.object(
        GroundingGoogleModel.__mro__[1], "_process_response", return_value=base_response
    ):
        model = object.__new__(GroundingGoogleModel)
        converted = model._process_response(response)

    assert converted.metadata["google_grounding_metadata"]["sources"][0]["url"] == (
        "https://example.org"
    )
    assert (
        converted.metadata["google_usage_metadata"]["cached_content_token_count"] == 5
    )
