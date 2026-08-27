"""Normalize Gemini GroundingMetadata without trusting model-authored prose."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import urlparse


WEB_TOOL_NAMES = frozenset({"web_search", "google_search", "google_search_retrieval"})
# Application-owned wrapper around a direct Gemini Google Search call.  Its
# return content contains provider-normalized URLs, unlike ordinary function
# tool results which must not be treated as web grounding.
WEB_BATCH_TOOL_NAMES = frozenset({"search_web_batch_tool", "web_search_batch"})
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


def _field(value: Any, *names: str) -> Any:
    """Read a field from either an SDK model or a JSON-shaped mapping."""

    mapping = _mapping(value)
    if mapping:
        found = _first(mapping, *names)
        if found is not None:
            return found
    for name in names:
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


_GROUNDING_FIELD_NAMES = frozenset(
    {
        "queries",
        "web_search_queries",
        "webSearchQueries",
        "sources",
        "grounding_chunks",
        "groundingChunks",
        "supports",
        "grounding_supports",
        "groundingSupports",
        "retrieval_metadata",
        "retrievalMetadata",
    }
)


def _grounding_metadata_from_container(value: Any) -> list[Mapping[str, Any]]:
    """Find grounding metadata nested in response/message/part metadata."""

    mapping = _mapping(value)
    if not mapping:
        return []
    nested = _first(
        mapping,
        "google_grounding_metadata",
        "grounding_metadata",
        "groundingMetadata",
    )
    if nested is not None:
        nested_mapping = _mapping(nested)
        return [nested_mapping] if nested_mapping else []
    if _GROUNDING_FIELD_NAMES.intersection(mapping):
        return [mapping]
    return []


def _merge_normalized_grounding(
    target: dict[str, Any], block: Mapping[str, Any]
) -> None:
    """Merge one normalized block while deduplicating repeated SDK views."""

    for query in block.get("queries", []):
        if query not in target["queries"]:
            target["queries"].append(query)

    source_by_url = {source["url"]: source for source in target["sources"]}
    for source in block.get("sources", []):
        url = source.get("url")
        if not isinstance(url, str) or not url:
            continue
        existing = source_by_url.get(url)
        if existing is None:
            copied = dict(source)
            target["sources"].append(copied)
            source_by_url[url] = copied
        else:
            for key in ("title", "domain"):
                if not existing.get(key) and source.get(key):
                    existing[key] = source[key]

    for support in block.get("supports", []):
        if support not in target["supports"]:
            target["supports"].append(dict(support))

    retrieval = block.get("retrieval_metadata")
    if isinstance(retrieval, Mapping):
        target.setdefault("retrieval_metadata", {}).update(retrieval)


def merge_grounding_metadata(*values: Any) -> dict[str, Any]:
    """Normalize and merge raw/normalized grounding blocks idempotently."""

    merged: dict[str, Any] = {
        "queries": [],
        "query_count": 0,
        "sources": [],
        "supports": [],
    }
    for value in values:
        normalized = normalize_grounding_metadata(value)
        if normalized:
            _merge_normalized_grounding(merged, normalized)
    merged["query_count"] = len(merged["queries"])
    if not merged["queries"] and not merged["sources"] and not merged["supports"]:
        merged.pop("retrieval_metadata", None)
    return merged if any(merged.get(key) for key in ("queries", "sources", "supports")) else {}


def normalize_provider_grounding_metadata(response: Any) -> dict[str, Any]:
    """Extract grounding metadata from all response candidates and aliases.

    Gemini SDK objects expose this on ``candidate.grounding_metadata``.  The
    helper also accepts REST-like camelCase mappings and a top-level block so
    the retention hook does not depend on one SDK representation.
    """

    blocks: list[Mapping[str, Any]] = []
    for candidate in _as_list(_field(response, "candidates")):
        blocks.extend(
            _grounding_metadata_from_container(
                _field(candidate, "grounding_metadata", "groundingMetadata")
            )
        )
        # If a provider returns server-side tool invocation parts, the actual
        # Google Search call may expose provider-generated queries there. They
        # are a valid fallback for query provenance when the response omits
        # the top-level ``GroundingMetadata`` query list. URLs still come only
        # from provider grounding chunks below. The configured Vertex
        # transport does not request this circulation mode.
        content = _field(candidate, "content")
        for part in _as_list(_field(content, "parts")):
            tool_call = _field(part, "tool_call", "toolCall")
            if tool_call is None:
                continue
            tool_type = _field(tool_call, "tool_type", "toolType")
            tool_type_value = getattr(tool_type, "value", tool_type)
            if str(tool_type_value or "").upper() != "GOOGLE_SEARCH_WEB":
                continue
            args = _mapping(_field(tool_call, "args")) or {}
            queries = _strings(
                _first(args, "queries", "web_search_queries", "webSearchQueries")
            )
            if queries:
                blocks.append({"queries": queries})
    blocks.extend(
        _grounding_metadata_from_container(
            _field(response, "grounding_metadata", "groundingMetadata")
        )
    )
    return merge_grounding_metadata(*blocks)


def _strings(value: Any) -> list[str]:
    result: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
    return result


def _tool_name(part: Any) -> str:
    return str(_field(part, "tool_name", "toolName", "name") or "").strip().lower()


def _part_args(part: Any) -> Mapping[str, Any]:
    value = _field(part, "args", "arguments")
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
    url = url.strip()
    parsed_url = urlparse(url)
    if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
        return None
    record: dict[str, Any] = {"url": url}
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
    aliases = {
        "prompt_token_count": ("prompt_token_count", "promptTokenCount"),
        "candidates_token_count": (
            "candidates_token_count",
            "candidatesTokenCount",
        ),
        "total_token_count": ("total_token_count", "totalTokenCount"),
        "cached_content_token_count": (
            "cached_content_token_count",
            "cachedContentTokenCount",
        ),
        "thoughts_token_count": ("thoughts_token_count", "thoughtsTokenCount"),
        "tool_use_prompt_token_count": (
            "tool_use_prompt_token_count",
            "toolUsePromptTokenCount",
        ),
        "cache_tokens_details": ("cache_tokens_details", "cacheTokensDetails"),
        "prompt_tokens_details": (
            "prompt_tokens_details",
            "promptTokensDetails",
        ),
        "candidates_tokens_details": (
            "candidates_tokens_details",
            "candidatesTokensDetails",
        ),
        "traffic_type": ("traffic_type", "trafficType"),
    }
    result: dict[str, Any] = {}
    for canonical, names in aliases.items():
        value = _first(data, *names) if data else None
        if value is None and raw is not None:
            value = _field(raw, *names)
        if value is not None and isinstance(value, (str, int, float, bool, list, dict)):
            result[canonical] = value
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


def _metadata_from_message(message: Any) -> list[Mapping[str, Any]]:
    """Return grounding blocks retained on a response message."""

    blocks: list[Mapping[str, Any]] = []
    for field_name in ("metadata", "provider_details"):
        blocks.extend(
            _grounding_metadata_from_container(_field(message, field_name))
        )
    return blocks


def _metadata_from_part(part: Any) -> list[Mapping[str, Any]]:
    """Return grounding blocks retained directly on a native web return."""

    blocks: list[Mapping[str, Any]] = []
    for field_name in ("metadata", "provider_details"):
        blocks.extend(_grounding_metadata_from_container(_field(part, field_name)))
    return blocks


def _source_from_part_content(content: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return found
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


def _web_batch_content(
    content: Any,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Read provider queries, URLs and supports from a web-tool return."""

    if isinstance(content, str):
        try:
            content = json.loads(content)
        except (TypeError, ValueError):
            return [], [], []
    data = _mapping(content)
    if not data:
        return [], [], []
    normalized = normalize_grounding_metadata(data)
    return (
        normalized.get("queries", []),
        normalized.get("sources", []),
        normalized.get("supports", []),
    )


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

    seen_metadata: set[str] = set()

    def add_metadata(block: Mapping[str, Any]) -> bool:
        """Add a provider block once, even when attached to message and part."""

        nonlocal metadata_responses
        normalized_metadata = normalize_grounding_metadata(block)
        if not normalized_metadata:
            return False
        try:
            signature = json.dumps(
                normalized_metadata,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            signature = repr(normalized_metadata)
        if signature in seen_metadata:
            return bool(normalized_metadata.get("queries"))
        seen_metadata.add(signature)
        metadata_responses += 1
        add_queries(normalized_metadata.get("queries", []))
        add_sources(normalized_metadata.get("sources", []))
        for support in normalized_metadata.get("supports", []):
            if support not in supports:
                supports.append(support)
        return bool(normalized_metadata.get("queries"))

    for message in messages:
        # A response-level block and the same block copied onto its native
        # web-return part are one provider response.  Reset at each message so
        # two sequential model responses with the same query still count as
        # two grounding operations.
        seen_metadata.clear()
        message_metadata = merge_grounding_metadata(*_metadata_from_message(message))
        has_provider_metadata_queries = bool(message_metadata.get("queries"))
        if message_metadata:
            has_provider_metadata_queries = add_metadata(message_metadata) or has_provider_metadata_queries

        parts = _as_list(_field(message, "parts"))
        for part in parts:
            name = _tool_name(part)
            if name in WEB_BATCH_TOOL_NAMES:
                batch_queries, batch_sources, batch_supports = _web_batch_content(
                    _field(part, "content")
                )
                # The wrapper's return is already the authoritative provider
                # view.  Do not inspect its input key_terms as billable query
                # arguments, and do not count a second representation of the
                # same direct response.
                add_queries(batch_queries)
                add_sources(batch_sources)
                for support in batch_supports:
                    if support not in supports:
                        supports.append(support)
                continue
            if name not in WEB_TOOL_NAMES:
                continue
            part_metadata = merge_grounding_metadata(*_metadata_from_part(part))
            if part_metadata:
                has_provider_metadata_queries = (
                    add_metadata(part_metadata) or has_provider_metadata_queries
                )

            args = _part_args(part)
            part_queries = _strings(args.get("queries"))
            # The raw metadata and the native call describe one grounding
            # operation; do not count both for billing.
            if part_queries and not has_provider_metadata_queries:
                add_queries(part_queries)
            add_sources(_source_from_part_content(_field(part, "content")))

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
        for container in (
            _field(message, "metadata"),
            _field(message, "provider_details"),
        ):
            metadata = _mapping(container)
            if not metadata:
                continue
            usage = _mapping(
                _first(
                    metadata,
                    "google_usage_metadata",
                    "usage_metadata",
                    "googleUsageMetadata",
                )
            )
            if usage and dict(usage) not in found:
                found.append(dict(usage))
    return found


def extract_places_request_count(value: Any) -> int:
    """Count one Google Places request for each query in recorded tool args."""

    count = 0
    for message in _messages_from(value):
        for part in _as_list(_field(message, "parts")):
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
