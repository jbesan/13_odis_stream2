from __future__ import annotations

from typing import Any

from scripts.analyze_logfire_trace import (
    analyze_records,
    extract_records,
    render_report,
)


def _record(
    span_name: str,
    start: float,
    duration: float,
    attributes: dict[str, Any] | None = None,
    *,
    kind: str = "span",
) -> dict[str, Any]:
    return {
        "trace_id": "trace-1",
        "span_id": span_name.replace(" ", "-"),
        "span_name": span_name,
        "kind": kind,
        "start_timestamp": start,
        "end_timestamp": start + duration,
        "duration": duration,
        "attributes": attributes or {},
    }


def _model_attributes(
    *,
    input_tokens: int,
    output_tokens: int,
    agent: str = "social_integration_expert",
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    output_messages: list[dict[str, Any]] | None = None,
    searched_in_schema: bool = False,
) -> dict[str, Any]:
    properties = {"result": {"type": "string"}}
    if searched_in_schema:
        properties["searched"] = {"type": "string"}
    return {
        "gen_ai.agent.name": agent,
        "gen_ai.request.model": "gemini-test",
        "gen_ai.usage.input_tokens": input_tokens,
        "gen_ai.usage.output_tokens": output_tokens,
        **(
            {"gen_ai.usage.cache_read.input_tokens": cache_read_tokens}
            if cache_read_tokens is not None
            else {}
        ),
        **(
            {"gen_ai.usage.cache_write.input_tokens": cache_write_tokens}
            if cache_write_tokens is not None
            else {}
        ),
        "gen_ai.output.messages": output_messages or [],
        "model_request_parameters": {
            "output_tools": [
                {
                    "name": "final_result",
                    "parameters_json_schema": {"properties": properties},
                }
            ]
        },
    }


def test_analyze_trace_separates_model_requests_tools_and_native_search() -> None:
    records = [
        _record("ODIS Graph Logic", 0.0, 2.5),
        _record("invoke_agent social_integration_expert", 0.1, 1.8),
        _record(
            "chat gemini-test",
            0.2,
            0.4,
            _model_attributes(
                input_tokens=100,
                output_tokens=10,
                output_messages=[
                    {
                        "role": "assistant",
                        "parts": [
                            {
                                "type": "tool_call",
                                "name": "search_places_batch_tool",
                                "arguments": {"queries": ["a", "b"]},
                            },
                            {
                                "type": "tool_call",
                                "name": "search_rna_rag_batch_tool",
                                "arguments": {"queries": ["c"]},
                            },
                        ],
                    }
                ],
            ),
        ),
        _record(
            "execute_tool search_places_batch_tool",
            0.7,
            0.2,
            {
                "gen_ai.agent.name": "social_integration_expert",
                "gen_ai.tool.call.arguments": {"queries": ["a", "b"]},
            },
        ),
        _record(
            "execute_tool search_rna_rag_batch_tool",
            0.9,
            0.3,
            {
                "gen_ai.agent.name": "social_integration_expert",
                "gen_ai.tool.call.arguments": {"queries": ["c"]},
            },
        ),
        _record(
            "chat gemini-test",
            1.2,
            0.5,
            _model_attributes(
                input_tokens=200,
                output_tokens=50,
                cache_read_tokens=150,
                cache_write_tokens=4,
                searched_in_schema=True,
                output_messages=[
                    {
                        "role": "assistant",
                        "parts": [
                            {
                                "type": "tool_call",
                                "name": "web_search",
                                "builtin": True,
                                "arguments": {"queries": ["q1", "q2"]},
                            },
                            {
                                "type": "tool_call",
                                "name": "final_result",
                                "arguments": {
                                    "searched": "abc",
                                    "result": "facts",
                                },
                            },
                        ],
                    }
                ],
            ),
        ),
        _record(
            "Expert run finished",
            2.6,
            0.0,
            {
                "domain": "social_integration_expert",
                "cost_eur": 0.03,
                "cost_usd": 0.01,
                "token_cost_eur": 0.02,
                "grounding_cost_eur": 0.005,
                "places_cost_eur": 0.005,
                "grounding_query_count": 2,
                "grounding_source_count": 3,
                "grounding_support_count": 4,
            },
            kind="log",
        ),
    ]

    summary = analyze_records(records)

    assert summary["time"]["duration_s"] == 2.5
    assert summary["counts"] == {
        "records": 7,
        "spans": 6,
        "logs": 1,
        "exceptions": 0,
        "model_requests": 2,
        "agent_invocations": 1,
        "tool_spans": 2,
        "tool_call_requests": 3,
        "native_tool_calls": 1,
        "native_web_search_calls": 1,
        "native_web_search_queries": 2,
        "final_result_outputs": 1,
    }
    assert summary["tokens"] == {
        "input_tokens_inclusive": 300,
        "input_tokens_cached": 150,
        "cache_write_tokens": 4,
        "output_tokens": 60,
        "thought_tokens": 0,
        "input_tokens_new": 150,
        "cache_hit_ratio": 0.5,
    }
    assert summary["costs"]["cost_eur"] == 0.03
    assert summary["costs"]["token_cost_eur"] == 0.02
    assert summary["costs"]["grounding_cost_eur"] == 0.005
    assert summary["costs"]["places_cost_eur"] == 0.005
    assert summary["grounding"] == {
        "native_query_count": 2,
        "application_reported_query_count": 2,
        "application_reported_source_count": 3,
        "application_reported_support_count": 4,
        "application_reported_records": 1,
    }

    social = next(
        agent
        for agent in summary["agents"]
        if agent["name"] == "social_integration_expert"
    )
    assert social["models"] == {"gemini-test": 2}
    assert social["tool_call_requests"] == 3
    assert social["tool_spans"] == 2
    assert social["native_web_search_queries"] == 2
    assert social["cost_eur"] == 0.03
    assert summary["tools"][0]["name"] == "search_places_batch_tool"
    assert summary["tools"][0]["query_items"] == 2
    assert len(summary["tools"][0]["calls"]) == 1
    assert summary["tools"][0]["calls"][0]["agent"] == "social_integration_expert"
    assert summary["tools"][0]["calls"][0]["duration_s"] == 0.2
    assert len(summary["tool_calls"]) == 2
    assert summary["outputs"]["searched_chars"] == 3
    assert summary["outputs"]["result_chars"] == 5
    assert summary["outputs"]["searched_schema_requests"] == 1
    assert any("no separate execute_tool span" in item for item in summary["anomalies"])

    report = render_report(summary)
    assert "tool_requests=3" in report
    assert "50.0% cache hit" in report
    assert "web_search_calls=1" in report
    assert "[social_integration_expert] 0.20 s: queries=[a, b]" in report


def test_analyze_trace_flags_large_legacy_searched_field() -> None:
    output = [
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "name": "final_result",
                    "arguments": {"searched": "x" * 1201, "result": "ok"},
                }
            ],
        }
    ]
    summary = analyze_records(
        [
            _record(
                "chat gemini-test",
                0.0,
                0.1,
                _model_attributes(
                    input_tokens=10,
                    output_tokens=2,
                    output_messages=output,
                    searched_in_schema=True,
                ),
            )
        ]
    )

    assert summary["outputs"]["searched_chars"] == 1201
    assert summary["outputs"]["result_chars"] == 2
    assert summary["outputs"]["searched_share_of_text_fields"] > 0.99
    assert summary["outputs"]["searched_schema_requests"] == 1
    assert any("exceed 1,000 characters" in item for item in summary["anomalies"])


def test_extract_records_supports_otlp_and_mcp_rows() -> None:
    otlp_payload = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "abc",
                                "spanId": "def",
                                "name": "ODIS Graph Logic",
                                "startTimeUnixNano": "1000000000000000000",
                                "endTimeUnixNano": "1000000002500000000",
                                "attributes": [],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    otlp_records = extract_records(otlp_payload)
    assert len(otlp_records) == 1
    assert otlp_records[0]["trace_id"] == "abc"
    assert otlp_records[0]["duration"] == 2.5

    mcp_records = extract_records(
        {
            "rows": [
                {
                    "trace_id": "mcp-trace",
                    "span_name": "example",
                    "start_timestamp": "2026-01-01T00:00:00Z",
                    "end_timestamp": "2026-01-01T00:00:01Z",
                }
            ]
        }
    )
    assert len(mcp_records) == 1
    assert mcp_records[0]["duration"] == 1.0


def test_analyze_trace_counts_application_failure_events() -> None:
    summary = analyze_records(
        [
            _record(
                "Expert run failed",
                0.0,
                0.0,
                {"error_type": "TimeoutError"},
                kind="log",
            )
        ]
    )

    assert summary["counts"]["exceptions"] == 1
    assert "report an exception or error" in summary["anomalies"][0]


def test_analyze_trace_recognizes_city_suffixed_root_span() -> None:
    summary = analyze_records(
        [
            _record("ODIS Graph Logic - Marseille", 0.0, 3.2),
            _record("invoke_agent job_hunter", 0.1, 1.0),
        ]
    )

    assert summary["time"]["duration_s"] == 3.2
    assert summary["time"]["observed_window_s"] == 3.2
