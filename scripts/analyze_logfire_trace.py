#!/usr/bin/env python3
"""Analyze a Logfire/OTLP JSON trace export without API dependencies.

The input can be:

* a Logfire export (the JSON array returned by the dashboard export),
* an MCP ``query_run`` result containing ``rows``, or
* an OTLP JSON export containing ``resourceSpans``.

The command deliberately reports native provider tool calls separately from
``execute_tool`` spans.  Gemini native Google Search calls do not necessarily
produce an application tool span, while ordinary Python tools normally do.

Examples::

    python scripts/analyze_logfire_trace.py docs/saved_trace.json
    python scripts/analyze_logfire_trace.py trace.json --trace-id TRACE_ID --json
    cat trace.json | python scripts/analyze_logfire_trace.py - --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


JsonObject = dict[str, Any]


def _parse_json(value: Any) -> Any:
    """Parse JSON-looking strings while leaving ordinary strings untouched."""

    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in '[{"':
        return value
    try:
        return json.loads(stripped)
    except (TypeError, ValueError):
        return value


def _number(value: Any) -> float | None:
    """Return a finite number from scalar or common cost-object values."""

    value = _parse_json(value)
    if isinstance(value, Mapping):
        for key in ("total", "value", "amount", "cost"):
            if key in value:
                return _number(value[key])
        return None
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: Any, default: int = 0) -> int:
    number = _number(value)
    return default if number is None else max(int(number), 0)


def _epoch_seconds(value: Any) -> float | None:
    """Normalize ISO timestamps and OTLP epoch values to epoch seconds."""

    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if number >= 1e18:
            return number / 1_000_000_000
        if number >= 1e15:
            return number / 1_000_000
        if number >= 1e12:
            return number / 1_000
        return number
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return _epoch_seconds(_number(text))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _iso_timestamp(value: Any) -> str | None:
    """Normalize an OTLP timestamp for human-readable JSON output."""

    if isinstance(value, str):
        stripped = value.strip()
        try:
            datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        except ValueError:
            numeric = _number(stripped)
            if numeric is None:
                return value
            value = numeric
        else:
            return value
    seconds = _epoch_seconds(value)
    if seconds is None:
        return None
    return (
        datetime.fromtimestamp(seconds, tz=timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _otlp_attribute_value(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    for key in (
        "stringValue",
        "string_value",
        "intValue",
        "int_value",
        "doubleValue",
        "double_value",
        "boolValue",
        "bool_value",
        "bytesValue",
        "bytes_value",
        "arrayValue",
        "array_value",
        "kvlistValue",
        "kvlist_value",
    ):
        if key in value:
            nested = value[key]
            if key in {"arrayValue", "array_value"} and isinstance(nested, Mapping):
                return [
                    _otlp_attribute_value(item.get("value"))
                    for item in nested.get("values", [])
                    if isinstance(item, Mapping)
                ]
            if key in {"kvlistValue", "kvlist_value"} and isinstance(nested, Mapping):
                return {
                    str(item.get("key")): _otlp_attribute_value(item.get("value"))
                    for item in nested.get("values", [])
                    if isinstance(item, Mapping) and item.get("key") is not None
                }
            return nested
    return value


def _otlp_attributes(value: Any) -> JsonObject:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    if not isinstance(value, list):
        return {}
    result: JsonObject = {}
    for item in value:
        if isinstance(item, Mapping) and item.get("key") is not None:
            result[str(item["key"])] = _otlp_attribute_value(item.get("value"))
    return result


def _duration(record: Mapping[str, Any]) -> float | None:
    duration = _number(record.get("duration"))
    if duration is not None:
        return max(duration, 0.0)
    start = _epoch_seconds(record.get("start_timestamp"))
    end = _epoch_seconds(record.get("end_timestamp"))
    if start is None or end is None:
        return None
    return max(end - start, 0.0)


def _normalize_record(raw: Mapping[str, Any]) -> JsonObject:
    """Normalize a Logfire record or a flattened query row."""

    record: JsonObject = dict(raw)
    if not record.get("span_name") and record.get("name"):
        record["span_name"] = record["name"]
    if not record.get("trace_id") and record.get("traceId"):
        record["trace_id"] = record["traceId"]
    if not record.get("span_id") and record.get("spanId"):
        record["span_id"] = record["spanId"]
    if not record.get("parent_span_id") and record.get("parentSpanId"):
        record["parent_span_id"] = record["parentSpanId"]
    if (
        not record.get("start_timestamp")
        and record.get("startTimeUnixNano") is not None
    ):
        record["start_timestamp"] = _iso_timestamp(record["startTimeUnixNano"])
    if not record.get("end_timestamp") and record.get("endTimeUnixNano") is not None:
        record["end_timestamp"] = _iso_timestamp(record["endTimeUnixNano"])

    attributes = record.get("attributes", {})
    if isinstance(attributes, str):
        attributes = _parse_json(attributes)
    record["attributes"] = _otlp_attributes(attributes)
    if record.get("duration") is None:
        record["duration"] = _duration(record)
    record.setdefault("kind", "span")
    return record


def _flatten_otlp(payload: Mapping[str, Any]) -> list[JsonObject]:
    resource_spans = payload.get("resourceSpans") or payload.get("resource_spans")
    if not isinstance(resource_spans, list):
        return []
    records: list[JsonObject] = []
    for resource_span in resource_spans:
        if not isinstance(resource_span, Mapping):
            continue
        scope_spans = resource_span.get("scopeSpans") or resource_span.get(
            "scope_spans"
        )
        if not isinstance(scope_spans, list):
            continue
        for scope_span in scope_spans:
            if not isinstance(scope_span, Mapping):
                continue
            spans = scope_span.get("spans", [])
            if not isinstance(spans, list):
                continue
            for span in spans:
                if not isinstance(span, Mapping):
                    continue
                status = span.get("status")
                record = {
                    "trace_id": span.get("traceId") or span.get("trace_id"),
                    "span_id": span.get("spanId") or span.get("span_id"),
                    "parent_span_id": span.get("parentSpanId")
                    or span.get("parent_span_id"),
                    "span_name": span.get("name") or span.get("span_name"),
                    "start_timestamp": _iso_timestamp(
                        span.get("startTimeUnixNano") or span.get("start_timestamp")
                    ),
                    "end_timestamp": _iso_timestamp(
                        span.get("endTimeUnixNano") or span.get("end_timestamp")
                    ),
                    "duration": None,
                    "kind": "span",
                    "attributes": _otlp_attributes(span.get("attributes", [])),
                    "otel_status_code": (
                        status.get("code") if isinstance(status, Mapping) else None
                    ),
                    "otel_status_message": (
                        status.get("message") if isinstance(status, Mapping) else None
                    ),
                }
                record["duration"] = _duration(record)
                records.append(_normalize_record(record))
    return records


def extract_records(payload: Any) -> list[JsonObject]:
    """Extract normalized records from Logfire, MCP, or OTLP JSON."""

    if isinstance(payload, list):
        return [
            _normalize_record(item) for item in payload if isinstance(item, Mapping)
        ]
    if not isinstance(payload, Mapping):
        return []

    otlp_records = _flatten_otlp(payload)
    if otlp_records:
        return otlp_records

    for key in ("records", "rows"):
        value = payload.get(key)
        if isinstance(value, list):
            return [
                _normalize_record(item) for item in value if isinstance(item, Mapping)
            ]

    for key in ("structuredContent", "structured_content", "data", "result"):
        if key in payload:
            nested = extract_records(payload[key])
            if nested:
                return nested

    content = payload.get("content")
    if isinstance(content, list):
        for item in content:
            if isinstance(item, Mapping) and item.get("type") == "text":
                nested = extract_records(_parse_json(item.get("text")))
                if nested:
                    return nested

    if payload.get("span_name") or payload.get("name"):
        return [_normalize_record(payload)]
    return []


def load_records(path: str | Path) -> list[JsonObject]:
    if str(path) == "-":
        return extract_records(json.load(sys.stdin))
    with Path(path).open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return extract_records(payload)


def _attributes(record: Mapping[str, Any]) -> Mapping[str, Any]:
    value = record.get("attributes", {})
    value = _parse_json(value)
    return value if isinstance(value, Mapping) else {}


def _attribute(record: Mapping[str, Any], key: str, default: Any = None) -> Any:
    attributes = _attributes(record)
    if key in attributes:
        return attributes[key]
    return record.get(key, default)


def _string_attribute(record: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _attribute(record, key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _agent_name(record: Mapping[str, Any]) -> str:
    name = _string_attribute(record, "gen_ai.agent.name", "agent_name", "domain")
    if name:
        return name
    span_name = str(record.get("span_name") or "")
    if span_name.startswith("invoke_agent "):
        return span_name.removeprefix("invoke_agent ")
    if span_name == "Triage run finished":
        return "ts_agent"
    if span_name == "Synthesizer run finished":
        return "synthesizer"
    return "unknown"


def _model_name(record: Mapping[str, Any]) -> str | None:
    return _string_attribute(record, "gen_ai.request.model", "model_name", "model_id")


def _output_messages(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = _parse_json(_attribute(record, "gen_ai.output.messages"))
    if isinstance(value, Mapping):
        value = value.get("messages") or value.get("items") or [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _tool_calls(record: Mapping[str, Any]) -> list[JsonObject]:
    calls: list[JsonObject] = []
    for message in _output_messages(record):
        parts = message.get("parts", [])
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, Mapping):
                continue
            part_type = str(part.get("type") or "").lower()
            name = part.get("name") or part.get("tool_name")
            if not name or "response" in part_type:
                continue
            calls.append(
                {
                    "name": str(name),
                    "arguments": _parse_json(
                        part.get("arguments")
                        if part.get("arguments") is not None
                        else part.get("args", {})
                    ),
                    "builtin": bool(part.get("builtin"))
                    or str(part.get("tool_kind") or "").lower() == "builtin",
                    "id": part.get("id"),
                }
            )
    return calls


def _query_count(arguments: Any) -> int:
    arguments = _parse_json(arguments)
    if not isinstance(arguments, Mapping):
        return 0
    for key in ("queries", "searches"):
        value = arguments.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, str) and value.strip():
            return 1
    if isinstance(arguments.get("query"), str) and arguments["query"].strip():
        return 1
    return 0


def _tool_span_name(record: Mapping[str, Any]) -> str | None:
    span_name = str(record.get("span_name") or "")
    if span_name.startswith("execute_tool "):
        return span_name.removeprefix("execute_tool ")
    value = _string_attribute(record, "gen_ai.tool.name")
    return value


def _summarize_tool_args(arguments: Any) -> str:
    """Format tool arguments into a concise human-readable string."""

    arguments = _parse_json(arguments)
    if not isinstance(arguments, Mapping):
        return str(arguments) if arguments is not None else ""
    parts = []
    if "location" in arguments:
        parts.append(f"loc={arguments['location']}")
    if "codgeo" in arguments:
        parts.append(f"codgeo={arguments['codgeo']}")
    if "origin" in arguments and "destination" in arguments:
        mode = f" ({arguments['mode']})" if "mode" in arguments else ""
        parts.append(f"{arguments['origin']} -> {arguments['destination']}{mode}")
    if "job_id" in arguments:
        parts.append(f"job_id={arguments['job_id']}")
    if "queries" in arguments:
        queries = arguments["queries"]
        if isinstance(queries, list):
            parts.append(f"queries=[{', '.join(str(item) for item in queries)}]")
        else:
            parts.append(f"queries={queries}")
    elif "query" in arguments:
        parts.append(f"query={arguments['query']}")
    if "searches" in arguments:
        parts.append(f"searches={arguments['searches']}")
    if not parts:
        text = json.dumps(arguments, ensure_ascii=False)
        return text if len(text) <= 120 else text[:117] + "..."
    return " | ".join(parts)



def _has_error(record: Mapping[str, Any]) -> bool:
    if bool(record.get("is_exception")):
        return True
    if str(record.get("kind") or "").lower() in {"exception", "error"}:
        return True
    status = str(record.get("otel_status_code") or "").upper()
    if status == "ERROR":
        return True
    for key in (
        "exception_type",
        "exception_message",
        "error_type",
        "error_message",
    ):
        if _attribute(record, key):
            return True
    result = _parse_json(_attribute(record, "gen_ai.tool.call.result"))
    return isinstance(result, Mapping) and bool(result.get("error"))


def _schema_has_searched(record: Mapping[str, Any]) -> bool:
    parameters = _parse_json(_attribute(record, "model_request_parameters"))
    if not isinstance(parameters, Mapping):
        return False
    output_tools = parameters.get("output_tools", [])
    if not isinstance(output_tools, list):
        return False
    for tool in output_tools:
        if not isinstance(tool, Mapping):
            continue
        schema = tool.get("parameters_json_schema", {})
        if isinstance(schema, Mapping) and "searched" in (
            schema.get("properties") or {}
        ):
            return True
    return False


def _final_outputs(record: Mapping[str, Any]) -> list[JsonObject]:
    outputs: list[JsonObject] = []
    for call in _tool_calls(record):
        if call["name"] != "final_result":
            continue
        arguments = call["arguments"]
        if not isinstance(arguments, Mapping):
            continue
        searched = arguments.get("searched")
        result = arguments.get("result")
        outputs.append(
            {
                "agent": _agent_name(record),
                "span_id": record.get("span_id"),
                "searched_chars": len(str(searched)) if searched is not None else 0,
                "result_chars": len(str(result)) if result is not None else 0,
                "searched_present": "searched" in arguments,
                "result_present": "result" in arguments,
            }
        )
    return outputs


def _first_number(record: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = _attribute(record, key)
        if value is not None:
            return _integer(value)
    return 0


def _new_agent_stats() -> JsonObject:
    return {
        "model_requests": 0,
        "models": {},
        "model_duration_s": 0.0,
        "invocations": 0,
        "invocation_duration_s": 0.0,
        "input_tokens_inclusive": 0,
        "input_tokens_cached": 0,
        "cache_write_tokens": 0,
        "input_tokens_new": 0,
        "output_tokens": 0,
        "thought_tokens": 0,
        "operation_cost": 0.0,
        "cost_eur": 0.0,
        "cost_usd": 0.0,
        "token_cost_eur": 0.0,
        "grounding_cost_eur": 0.0,
        "places_cost_eur": 0.0,
        "tool_spans": 0,
        "tool_call_requests": 0,
        "native_tool_calls": 0,
        "native_web_search_calls": 0,
        "native_web_search_queries": 0,
        "final_result_outputs": 0,
        "searched_chars": 0,
        "result_chars": 0,
        "searched_schema_requests": 0,
    }


def _ensure_stats(stats: dict[str, JsonObject], name: str) -> JsonObject:
    if name not in stats:
        stats[name] = _new_agent_stats()
    return stats[name]


def analyze_records(records: Iterable[Mapping[str, Any]]) -> JsonObject:
    """Return stable, JSON-serializable metrics for a trace export."""

    normalized = [_normalize_record(record) for record in records]
    agent_stats: dict[str, JsonObject] = {}
    tool_stats: dict[str, JsonObject] = {}
    tool_calls: list[JsonObject] = []
    native_web_searches: list[JsonObject] = []
    final_outputs: list[JsonObject] = []
    completion_costs: dict[str, dict[str, float]] = defaultdict(
        lambda: {
            "cost_eur": 0.0,
            "cost_usd": 0.0,
            "token_cost_eur": 0.0,
            "grounding_cost_eur": 0.0,
            "places_cost_eur": 0.0,
        }
    )
    cost_eur_records = 0
    cost_usd_records = 0
    cost_component_records = 0
    operation_cost_total = 0.0
    grounding_reported = {
        "query_count": 0,
        "source_count": 0,
        "support_count": 0,
        "records": 0,
    }
    token_totals = {
        "input_tokens_inclusive": 0,
        "input_tokens_cached": 0,
        "cache_write_tokens": 0,
        "output_tokens": 0,
        "thought_tokens": 0,
    }
    counts = {
        "records": len(normalized),
        "spans": sum(str(record.get("kind")) == "span" for record in normalized),
        "logs": sum(str(record.get("kind")) == "log" for record in normalized),
        "exceptions": sum(_has_error(record) for record in normalized),
        "model_requests": 0,
        "agent_invocations": 0,
        "tool_spans": 0,
        "tool_call_requests": 0,
        "native_tool_calls": 0,
        "native_web_search_calls": 0,
        "native_web_search_queries": 0,
        "final_result_outputs": 0,
    }

    for record in normalized:
        span_name = str(record.get("span_name") or "")
        agent = _agent_name(record)
        duration = _duration(record) or 0.0
        attrs = _attributes(record)

        cost_eur = _number(attrs.get("cost_eur", record.get("cost_eur")))
        cost_usd = _number(attrs.get("cost_usd", record.get("cost_usd")))
        operation_cost = _number(
            attrs.get("operation.cost", record.get("operation.cost"))
        )
        if operation_cost is not None:
            operation_cost_total += operation_cost

        if span_name in {
            "Triage run finished",
            "Expert run finished",
            "Synthesizer run finished",
        }:
            event_agent = agent
            if cost_eur is not None:
                completion_costs[event_agent]["cost_eur"] += cost_eur
                cost_eur_records += 1
            if cost_usd is not None:
                completion_costs[event_agent]["cost_usd"] += cost_usd
                cost_usd_records += 1
            component_values = {
                key: _number(_attribute(record, key))
                for key in (
                    "token_cost_eur",
                    "grounding_cost_eur",
                    "places_cost_eur",
                )
            }
            if any(value is not None for value in component_values.values()):
                cost_component_records += 1
                for key, value in component_values.items():
                    if value is not None:
                        completion_costs[event_agent][key] += value

            grounding_values = {
                key: _number(_attribute(record, attribute))
                for key, attribute in (
                    ("query_count", "grounding_query_count"),
                    ("source_count", "grounding_source_count"),
                    ("support_count", "grounding_support_count"),
                )
            }
            if any(value is not None for value in grounding_values.values()):
                grounding_reported["records"] += 1
                for key, value in grounding_values.items():
                    if value is not None:
                        grounding_reported[key] += int(max(value, 0))

        if span_name.startswith("invoke_agent "):
            counts["agent_invocations"] += 1
            stats = _ensure_stats(agent_stats, agent)
            stats["invocations"] += 1
            stats["invocation_duration_s"] += duration

        is_model_request = span_name.startswith("chat ")
        if is_model_request:
            counts["model_requests"] += 1
            stats = _ensure_stats(agent_stats, agent)
            input_tokens = _first_number(record, "gen_ai.usage.input_tokens")
            cached_tokens = _first_number(
                record,
                "gen_ai.usage.cache_read.input_tokens",
                "gen_ai.usage.cache_read_tokens",
                "gen_ai.usage.details.cache_read_tokens",
                "gen_ai.usage.details.cached_content_tokens",
            )
            cache_write_tokens = _first_number(
                record,
                "gen_ai.usage.cache_write.input_tokens",
                "gen_ai.usage.cache_write_tokens",
                "gen_ai.usage.details.cache_write_tokens",
                "gen_ai.usage.details.cache_creation_input_tokens",
            )
            output_tokens = _first_number(record, "gen_ai.usage.output_tokens")
            thought_tokens = _first_number(
                record,
                "gen_ai.usage.details.thoughts_tokens",
                "gen_ai.usage.details.thought_tokens",
            )
            cached_tokens = min(cached_tokens, input_tokens)
            stats["model_requests"] += 1
            stats["model_duration_s"] += duration
            model_name = _model_name(record)
            if model_name:
                models = stats["models"]
                models[model_name] = models.get(model_name, 0) + 1
            stats["input_tokens_inclusive"] += input_tokens
            stats["input_tokens_cached"] += cached_tokens
            stats["cache_write_tokens"] += cache_write_tokens
            stats["input_tokens_new"] += max(input_tokens - cached_tokens, 0)
            stats["output_tokens"] += output_tokens
            stats["thought_tokens"] += thought_tokens
            stats["operation_cost"] += operation_cost or 0.0
            stats["searched_schema_requests"] += int(_schema_has_searched(record))
            token_totals["input_tokens_inclusive"] += input_tokens
            token_totals["input_tokens_cached"] += cached_tokens
            token_totals["cache_write_tokens"] += cache_write_tokens
            token_totals["output_tokens"] += output_tokens
            token_totals["thought_tokens"] += thought_tokens

            calls = _tool_calls(record)
            requested_calls = [call for call in calls if call["name"] != "final_result"]
            counts["tool_call_requests"] += len(requested_calls)
            stats["tool_call_requests"] += len(requested_calls)
            for call in calls:
                name = str(call["name"])
                normalized_name = name.lower()
                if name == "final_result":
                    continue
                if call["builtin"] or normalized_name in {
                    "web_search",
                    "google_search",
                    "google_search_retrieval",
                }:
                    counts["native_tool_calls"] += 1
                    stats["native_tool_calls"] += 1
                    if "search" in normalized_name:
                        query_count = _query_count(call["arguments"])
                        counts["native_web_search_calls"] += 1
                        counts["native_web_search_queries"] += query_count
                        stats["native_web_search_calls"] += 1
                        stats["native_web_search_queries"] += query_count
                        arguments = call["arguments"]
                        queries = (
                            arguments.get("queries", [])
                            if isinstance(arguments, Mapping)
                            else []
                        )
                        if isinstance(queries, str):
                            queries = [queries]
                        native_web_searches.append(
                            {
                                "agent": agent,
                                "span_id": record.get("span_id"),
                                "query_count": query_count,
                                "queries": [str(item) for item in queries]
                                if isinstance(queries, list)
                                else [],
                            }
                        )

            outputs = _final_outputs(record)
            final_outputs.extend(outputs)
            stats["final_result_outputs"] += len(outputs)
            stats["searched_chars"] += sum(item["searched_chars"] for item in outputs)
            stats["result_chars"] += sum(item["result_chars"] for item in outputs)

        tool_name = _tool_span_name(record)
        if tool_name and (
            span_name.startswith("execute_tool ")
            or _attribute(record, "gen_ai.tool.name")
        ):
            counts["tool_spans"] += 1
            tool = tool_stats.setdefault(
                tool_name,
                {
                    "count": 0,
                    "total_duration_s": 0.0,
                    "query_items": 0,
                    "errors": 0,
                },
            )
            tool["count"] += 1
            tool["total_duration_s"] += duration
            arguments = _parse_json(_attribute(record, "gen_ai.tool.call.arguments"))
            items = _query_count(arguments)
            tool["query_items"] += items
            has_error = _has_error(record)
            tool["errors"] += int(has_error)
            tool_calls.append(
                {
                    "tool": tool_name,
                    "agent": agent,
                    "duration_s": duration,
                    "query_items": items,
                    "arguments": arguments,
                    "arguments_summary": _summarize_tool_args(arguments),
                    "errors": int(has_error),
                    "span_id": record.get("span_id"),
                }
            )
            stats = _ensure_stats(agent_stats, agent)
            stats["tool_spans"] += 1

    # Application completion events are the authoritative EUR/USD totals when
    # available.  Otherwise the lower-level operation.cost values remain useful
    # as a provider/Logfire fallback, but their currency is intentionally not
    # guessed here.
    for agent, costs in completion_costs.items():
        stats = _ensure_stats(agent_stats, agent)
        for key, value in costs.items():
            stats[key] += value

    for output in final_outputs:
        counts["final_result_outputs"] += 1

    input_total = token_totals["input_tokens_inclusive"]
    cached_total = token_totals["input_tokens_cached"]
    searched_chars = sum(item["searched_chars"] for item in final_outputs)
    result_chars = sum(item["result_chars"] for item in final_outputs)
    text_chars = searched_chars + result_chars

    timestamps = [
        (_epoch_seconds(record.get("start_timestamp")), record.get("start_timestamp"))
        for record in normalized
        if _epoch_seconds(record.get("start_timestamp")) is not None
    ]
    end_timestamps = [
        (_epoch_seconds(record.get("end_timestamp")), record.get("end_timestamp"))
        for record in normalized
        if _epoch_seconds(record.get("end_timestamp")) is not None
    ]
    start_epoch, start_value = min(timestamps, default=(None, None))
    end_epoch, end_value = max(end_timestamps, default=(None, None))
    root_candidates = [
        record
        for record in normalized
        if str(record.get("span_name") or "") == "ODIS Graph Logic"
    ]
    root = max(root_candidates, key=lambda item: _duration(item) or 0.0, default=None)
    root_duration = _duration(root) if root else None
    overall_duration = (
        max(end_epoch - start_epoch, 0.0)
        if start_epoch is not None and end_epoch is not None
        else None
    )

    anomalies: list[str] = []
    large_outputs = [item for item in final_outputs if item["searched_chars"] > 1000]
    if large_outputs:
        anomalies.append(
            f"{len(large_outputs)} final_result.searched field(s) exceed 1,000 characters."
        )
    if counts["native_web_search_calls"] and not any(
        "web_search" in name.lower() for name in tool_stats
    ):
        anomalies.append(
            "Native Google Search calls were found in model messages but have no separate execute_tool span."
        )
    if counts["exceptions"]:
        anomalies.append(
            f"{counts['exceptions']} record(s) report an exception or error."
        )

    trace_ids = sorted(
        {str(record.get("trace_id")) for record in normalized if record.get("trace_id")}
    )
    has_cost_eur = cost_eur_records > 0
    has_cost_usd = cost_usd_records > 0
    summary: JsonObject = {
        "trace_id": trace_ids[0] if len(trace_ids) == 1 else None,
        "trace_ids": trace_ids,
        "time": {
            "start": start_value,
            "end": end_value,
            "duration_s": root_duration
            if root_duration is not None
            else overall_duration,
            "root_span": root.get("span_id") if root else None,
            "observed_window_s": overall_duration,
        },
        "counts": counts,
        "tokens": {
            **token_totals,
            "input_tokens_new": max(input_total - cached_total, 0),
            "cache_hit_ratio": cached_total / input_total if input_total else 0.0,
        },
        "costs": {
            "cost_eur": sum(item["cost_eur"] for item in completion_costs.values())
            if has_cost_eur
            else None,
            "cost_usd": sum(item["cost_usd"] for item in completion_costs.values())
            if has_cost_usd
            else None,
            "operation_cost": operation_cost_total or None,
            "token_cost_eur": sum(
                item["token_cost_eur"] for item in completion_costs.values()
            )
            if cost_component_records
            else None,
            "grounding_cost_eur": sum(
                item["grounding_cost_eur"] for item in completion_costs.values()
            )
            if cost_component_records
            else None,
            "places_cost_eur": sum(
                item["places_cost_eur"] for item in completion_costs.values()
            )
            if cost_component_records
            else None,
            "cost_eur_event_count": cost_eur_records,
            "cost_usd_event_count": cost_usd_records,
            "cost_eur_source": "application completion events"
            if has_cost_eur
            else None,
            "cost_usd_source": "application completion events"
            if has_cost_usd
            else None,
            "operation_cost_note": "Currency is not inferred from operation.cost.",
        },
        "agents": [
            {
                "name": name,
                **stats,
                "cache_hit_ratio": stats["input_tokens_cached"]
                / stats["input_tokens_inclusive"]
                if stats["input_tokens_inclusive"]
                else 0.0,
            }
            for name, stats in sorted(
                agent_stats.items(),
                key=lambda item: (
                    -item[1]["model_duration_s"],
                    item[0],
                ),
            )
        ],
        "tools": [
            {
                "name": name,
                **stats,
                "calls": [call for call in tool_calls if call["tool"] == name],
            }
            for name, stats in sorted(
                tool_stats.items(), key=lambda item: (-item[1]["count"], item[0])
            )
        ],
        "tool_calls": tool_calls,
        "native_web_searches": native_web_searches,
        "grounding": {
            "native_query_count": counts["native_web_search_queries"],
            "application_reported_query_count": grounding_reported["query_count"],
            "application_reported_source_count": grounding_reported["source_count"],
            "application_reported_support_count": grounding_reported["support_count"],
            "application_reported_records": grounding_reported["records"],
        },
        "outputs": {
            "final_result_outputs": final_outputs,
            "searched_chars": searched_chars,
            "result_chars": result_chars,
            "searched_share_of_text_fields": searched_chars / text_chars
            if text_chars
            else 0.0,
            "searched_schema_requests": sum(
                stats["searched_schema_requests"] for stats in agent_stats.values()
            ),
        },
        "anomalies": anomalies,
    }
    return summary


def _format_int(value: Any) -> str:
    return f"{int(value):,}".replace(",", " ")


def _format_seconds(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.2f} s"


def _format_cost(value: Any, symbol: str = "") -> str:
    return "n/a" if value is None else f"{symbol}{float(value):.6f}"


def render_report(summary: Mapping[str, Any], top: int = 12) -> str:
    """Render a compact human-readable report from :func:`analyze_records`."""

    counts = summary["counts"]
    tokens = summary["tokens"]
    costs = summary["costs"]
    grounding = summary["grounding"]
    time = summary["time"]
    lines = [
        f"Trace: {summary.get('trace_id') or ', '.join(summary.get('trace_ids', [])) or 'unknown'}",
        f"Window: {time.get('start') or 'n/a'} → {time.get('end') or 'n/a'}",
        f"Duration: {_format_seconds(time.get('duration_s'))} (observed window {_format_seconds(time.get('observed_window_s'))})",
        "",
        "Counts",
        f"  records={counts['records']} spans={counts['spans']} logs={counts['logs']} exceptions={counts['exceptions']}",
        f"  model_requests={counts['model_requests']} agent_invocations={counts['agent_invocations']} tool_requests={counts['tool_call_requests']} tool_spans={counts['tool_spans']}",
        f"  native_tools={counts['native_tool_calls']} web_search_calls={counts['native_web_search_calls']} web_queries={counts['native_web_search_queries']}",
        f"  grounding_reported_queries={grounding['application_reported_query_count']} sources={grounding['application_reported_source_count']} supports={grounding['application_reported_support_count']}",
        "",
        "Tokens",
        f"  input={_format_int(tokens['input_tokens_inclusive'])} new={_format_int(tokens['input_tokens_new'])} cached={_format_int(tokens['input_tokens_cached'])} write={_format_int(tokens['cache_write_tokens'])} ({tokens['cache_hit_ratio']:.1%} cache hit)",
        f"  output={_format_int(tokens['output_tokens'])} thought={_format_int(tokens['thought_tokens'])}",
        "",
        "Costs",
        f"  cost_eur={_format_cost(costs.get('cost_eur'), '€')} cost_usd={_format_cost(costs.get('cost_usd'), '$')} operation.cost={_format_cost(costs.get('operation_cost'))}",
        f"  token_cost_eur={_format_cost(costs.get('token_cost_eur'), '€')} grounding={_format_cost(costs.get('grounding_cost_eur'), '€')} places={_format_cost(costs.get('places_cost_eur'), '€')}",
        "",
        "Agents",
    ]
    for agent in list(summary.get("agents", []))[:top]:
        lines.append(
            "  "
            + f"{agent['name']}: {agent['model_requests']} req, "
            + f"total={_format_seconds(agent['invocation_duration_s'])}, "
            + f"llm={_format_seconds(agent['model_duration_s'])}, "
            + f"in={_format_int(agent['input_tokens_inclusive'])}/cached={_format_int(agent['input_tokens_cached'])}, "
            + f"out={_format_int(agent['output_tokens'])}, "
            + f"tools={agent['tool_spans']} native_web={agent['native_web_search_calls']}"
        )

    lines.extend(["", "Tools"])
    for tool in list(summary.get("tools", []))[:top]:
        lines.append(
            f"  {tool['name']}: {tool['count']} calls, "
            f"{_format_seconds(tool['total_duration_s'])}, "
            f"items={tool['query_items']}, errors={tool['errors']}"
        )
        for call in tool.get("calls", [])[:top]:
            agent_str = f"[{call['agent']}] " if call.get("agent") else ""
            summary_str = call.get("arguments_summary") or ""
            lines.append(
                f"    - {agent_str}{_format_seconds(call.get('duration_s'))}: {summary_str}"
            )

    outputs = summary["outputs"]
    lines.extend(
        [
            "",
            "Final output fields",
            f"  final_result={counts['final_result_outputs']} searched_chars={outputs['searched_chars']} result_chars={outputs['result_chars']} searched_share={outputs['searched_share_of_text_fields']:.1%}",
            f"  searched_in_output_schema_requests={outputs['searched_schema_requests']}",
        ]
    )
    if summary.get("native_web_searches"):
        lines.extend(["", "Native Web Search"])
        for search in summary["native_web_searches"][:top]:
            queries = search.get("queries") or []
            lines.append(
                f"  {search['agent']}: {search['query_count']} queries"
                + (f" — {' | '.join(queries)}" if queries else "")
            )
    if summary.get("anomalies"):
        lines.extend(["", "Warnings"])
        lines.extend(f"  - {item}" for item in summary["anomalies"])
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace_json", type=Path, help="Logfire/OTLP JSON export")
    parser.add_argument("--trace-id", help="Analyze only this trace ID")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit machine-readable JSON instead of the text report",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=12,
        help="Maximum detail rows in the text report (default: 12)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        records = load_records(args.trace_json)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Unable to read trace JSON: {exc}", file=sys.stderr)
        return 2
    if args.trace_id:
        records = [
            record for record in records if record.get("trace_id") == args.trace_id
        ]
        if not records:
            print(f"Trace ID not found: {args.trace_id}", file=sys.stderr)
            return 2
    if not records:
        print("No trace records found in the input JSON.", file=sys.stderr)
        return 2
    summary = analyze_records(records)
    if args.as_json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_report(summary, top=max(args.top, 0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
