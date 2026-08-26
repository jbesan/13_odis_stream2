"""Normalize Gemini GroundingMetadata without trusting model-authored prose."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any


WEB_TOOL_NAMES = frozenset({"web_search", "google_search", "google_search_retrieval"})
PLACES_TOOL_NAMES = frozenset({"search_places_batch_tool", "search_places_batch"})


def _dump(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json", by_alias=False, exclude_none=True)
        except TypeError:
            try:
                return model_dump(mode="json", exclude_none=True)
            except Exception:
                pass
        except Exception:
            pass
    return value


def _mapping(value: Any) -> Mapping[str, Any] | None:
    dumped = _dump(value)
    return dumped if isinstance(dumped, Mapping) else None


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping and mapping[name] is not None:
            return mapping[name]
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _strings(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def _tool_name(part: Any) -> str:
    return str(getattr(part, "tool_name", "") or "").strip().lower()


def _part_args(part: Any) -> Mapping[str, Any]:
    value = getattr(part, "args", None)
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return {}
    return _mapping(value) or {}


def _normalize_web_chunk(value: Any) -> dict[str, Any] | None:
    chunk = _mapping(value)
    if not chunk:
        return None
    web = _mapping(_first(chunk, "web")) or chunk
    url = _first(web, "uri", "url")
    if not isinstance(url, str) or not url.strip():
        return None
    record: dict[str, Any] = {"url": url.strip()}
    title = _first(web, "title")
    domain = _first(web, "domain")
    if isinstance(title, str) and title.strip():
        record["title"] = title.strip()
    if isinstance(domain, str) and domain.strip():
        record["domain"] = domain.strip()
    return record


def normalize_grounding_metadata(raw: Any) -> dict[str, Any]:
    """Convert raw or already-normalized grounding metadata to JSON.

    ``GroundingGoogleModel`` stores this function's output on
    ``ModelResponse.metadata``.  The extraction path may consequently see
    that output again, so the accepted input must be idempotent.
    """

    data = _mapping(raw)
    if not data:
        return {}

    queries = _strings(
        _first(data, "queries", "web_search_queries", "webSearchQueries")
    )
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    chunks = _as_list(_first(data, "sources", "grounding_chunks", "groundingChunks"))
    for chunk in chunks:
        normalized = _normalize_web_chunk(chunk)
        if normalized and normalized["url"] not in seen_urls:
            seen_urls.add(normalized["url"])
            sources.append(normalized)

    supports: list[dict[str, Any]] = []
    raw_supports = _as_list(
        _first(data, "supports", "grounding_supports", "groundingSupports")
    )
    for raw_support in raw_supports:
        support = _mapping(raw_support)
        if not support:
            continue
        # Raw provider supports nest these fields under ``segment``;
        # normalized supports keep them at the top level.
        segment = _mapping(_first(support, "segment")) or support
        chunk_indices = _first(
            support,
            "grounding_chunk_indices",
            "groundingChunkIndices",
        )
        indices = [
            int(item)
            for item in _as_list(chunk_indices)
            if isinstance(item, (int, float))
        ]
        normalized_support: dict[str, Any] = {
            "grounding_chunk_indices": indices,
        }
        text = _first(segment, "text")
        if isinstance(text, str) and text:
            normalized_support["text"] = text
        for source_name, target_name in (
            ("start_index", "start_index"),
            ("startIndex", "start_index"),
            ("end_index", "end_index"),
            ("endIndex", "end_index"),
        ):
            value = _first(segment, source_name)
            if isinstance(value, (int, float)):
                normalized_support[target_name] = int(value)
        confidence = _first(
            support,
            "confidence_scores",
            "confidenceScores",
        )
        if confidence:
            normalized_support["confidence_scores"] = [
                float(item)
                for item in _as_list(confidence)
                if isinstance(item, (int, float))
            ]
        supports.append(normalized_support)

    normalized: dict[str, Any] = {
        "queries": queries,
        "query_count": len(queries),
        "sources": sources,
        "supports": supports,
    }
    retrieval = _mapping(_first(data, "retrieval_metadata", "retrievalMetadata"))
    if retrieval:
        # Keep only scalar retrieval flags; rendered search-entry HTML is not
        # useful as an application citation and can contain provider markup.
        normalized["retrieval_metadata"] = {
            str(key): value
            for key, value in retrieval.items()
            if isinstance(value, (str, int, float, bool))
        }
    return normalized


def normalize_usage_metadata(raw: Any) -> dict[str, Any]:
    """Keep the token counters Gemini returned outside PydanticAI's summary."""

    data = _mapping(raw)
    if not data:
        return {}
    names = (
        "prompt_token_count",
        "candidates_token_count",
        "total_token_count",
        "cached_content_token_count",
        "thoughts_token_count",
        "tool_use_prompt_token_count",
        "promptTokensDetails",
        "candidatesTokensDetails",
        "traffic_type",
    )
    result: dict[str, Any] = {}
    for name in names:
        if name in data and data[name] is not None:
            value = data[name]
            if isinstance(value, (str, int, float, bool, list, dict)):
                result[name] = value
    return result


def _messages_from(value: Any) -> list[Any]:
    if value is None:
        return []
    all_messages = getattr(value, "all_messages", None)
    if callable(all_messages):
        try:
            return list(all_messages())
        except Exception:
            return []
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, Mapping)):
        return list(value)
    return []


def _metadata_from_message(message: Any) -> Mapping[str, Any] | None:
    metadata = _mapping(getattr(message, "metadata", None))
    if not metadata:
        return None
    grounding = _first(metadata, "google_grounding_metadata", "grounding_metadata")
    return _mapping(grounding)


def _source_from_part_content(content: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in _as_list(content):
        normalized = _normalize_web_chunk(item)
        if normalized:
            found.append(normalized)
            continue
        mapping = _mapping(item)
        if mapping and _mapping(_first(mapping, "web")):
            nested = _normalize_web_chunk(mapping)
            if nested:
                found.append(nested)
    return found


def extract_web_grounding(value: Any) -> dict[str, Any]:
    """Extract queries, URLs, titles and supports from a run or message list.

    Query arguments are a fallback for provider versions that expose the
    native call but omit ``groundingChunks``.  URLs are emitted only when they
    came from provider metadata/tool-return content, never from report text.
    """

    messages = _messages_from(value)
    queries: list[str] = []
    sources: list[dict[str, Any]] = []
    supports: list[dict[str, Any]] = []
    seen_queries: set[str] = set()
    seen_urls: set[str] = set()
    query_count = 0
    metadata_responses = 0

    def add_queries(items: list[str], *, count: bool = True) -> None:
        nonlocal query_count
        if count:
            query_count += len(items)
        for query in items:
            if query not in seen_queries:
                seen_queries.add(query)
                queries.append(query)

    def add_sources(items: Iterable[dict[str, Any]]) -> None:
        for source in items:
            url = source.get("url")
            if isinstance(url, str) and url not in seen_urls:
                seen_urls.add(url)
                sources.append(dict(source))

    for message in messages:
        raw_metadata = _metadata_from_message(message)
        if raw_metadata:
            normalized_metadata = normalize_grounding_metadata(raw_metadata)
            if normalized_metadata:
                metadata_responses += 1
                add_queries(normalized_metadata.get("queries", []))
                add_sources(normalized_metadata.get("sources", []))
                supports.extend(normalized_metadata.get("supports", []))

        parts = getattr(message, "parts", ())
        has_provider_metadata_queries = bool(raw_metadata)
        for part in parts:
            name = _tool_name(part)
            if name not in WEB_TOOL_NAMES:
                continue
            args = _part_args(part)
            part_queries = _strings(args.get("queries"))
            # The raw metadata and the native call describe one grounding
            # operation; do not count both for billing.
            if part_queries and not has_provider_metadata_queries:
                add_queries(part_queries)
            add_sources(_source_from_part_content(getattr(part, "content", None)))

    return {
        "queries": queries,
        "query_count": query_count,
        "sources": sources,
        "supports": supports,
        "metadata_responses": metadata_responses,
        "has_sources": bool(sources),
    }


def extract_google_usage_metadata(value: Any) -> list[dict[str, Any]]:
    """Return normalized provider usage blocks retained on model responses."""

    found: list[dict[str, Any]] = []
    for message in _messages_from(value):
        metadata = _mapping(getattr(message, "metadata", None))
        if not metadata:
            continue
        usage = _mapping(_first(metadata, "google_usage_metadata", "usage_metadata"))
        if usage:
            found.append(dict(usage))
    return found


def extract_places_request_count(value: Any) -> int:
    """Count one Google Places request for each query in recorded tool args."""

    count = 0
    for message in _messages_from(value):
        for part in getattr(message, "parts", ()):
            name = _tool_name(part)
            if name not in PLACES_TOOL_NAMES:
                continue
            args = _part_args(part)
            queries = args.get("queries")
            if isinstance(queries, (list, tuple)):
                count += min(len(queries), 20)
            elif queries:
                count += 1
    return count
