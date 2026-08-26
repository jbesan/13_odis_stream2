"""Shared usage extraction for legacy and adaptive agent paths."""

from __future__ import annotations

import logging
import json
from decimal import Decimal
from typing import Any

from agents.grounding import (
    extract_google_usage_metadata,
    extract_places_request_count,
    extract_web_grounding,
)
from agents.state import UsageStats
from services.ai_pricing import calculate_gemini_cost


logger = logging.getLogger("odis_usage")


def _integer(value: Any, default: int = 0) -> int:
    return int(value) if isinstance(value, (int, float)) else default


def _float(value: Any, default: float = 0.0) -> float:
    return float(value) if isinstance(value, (int, float, Decimal)) else default


def _provider_cost_usd(usage: Any) -> float | None:
    value = getattr(usage, "cost", None)
    if isinstance(value, (int, float, Decimal)):
        return float(value)
    return None


def capture_usage_stats(result: Any, node_name: str, model_id: str) -> UsageStats:
    """Capture PydanticAI usage plus Gemini-specific cost dimensions."""

    try:
        usage = getattr(result, "usage", None)
        if callable(usage):
            usage = usage()
        if usage is None:
            return UsageStats()

        input_tokens = _integer(getattr(usage, "input_tokens", 0))
        output_tokens = _integer(getattr(usage, "output_tokens", 0))
        total_tokens = _integer(
            getattr(usage, "total_tokens", None), input_tokens + output_tokens
        )
        cache_read_tokens = _integer(getattr(usage, "cache_read_tokens", 0))
        cache_write_tokens = _integer(getattr(usage, "cache_write_tokens", 0))
        requests = _integer(getattr(usage, "requests", None), 1)
        tool_calls = _integer(getattr(usage, "tool_calls", 0))

        grounding = extract_web_grounding(result)
        google_usage_metadata = extract_google_usage_metadata(result)
        grounding_queries = _integer(grounding.get("query_count", 0))
        places_requests = extract_places_request_count(result)
        estimate = calculate_gemini_cost(
            model_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            grounding_queries=grounding_queries,
            places_requests=places_requests,
            provider_cost_usd=_provider_cost_usd(usage),
        )

        cache_hit_ratio = _float(
            getattr(usage, "cache_hit_ratio", None),
            cache_read_tokens / input_tokens if input_tokens else 0.0,
        )
        details = getattr(usage, "details", {})
        if not isinstance(details, dict):
            details = {}

        breakdown_entry = {
            **estimate.as_dict(),
            # Legacy consumers used ``model``/``cost`` as USD aliases.
            "model": model_id,
            "cost": estimate.token_cost_usd,
            "cost_usd": estimate.token_cost_usd,
            "input": input_tokens,
            "output": output_tokens,
            "total": total_tokens,
            "requests": requests,
            "tool_calls": tool_calls,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cache_hit_ratio": cache_hit_ratio,
            "provider_usage_details": dict(details),
            "grounding_queries": grounding.get("queries", []),
            "grounding_sources": grounding.get("sources", []),
            "grounding_supports": grounding.get("supports", []),
            "google_usage_metadata": google_usage_metadata,
        }

        logger.info(
            "[USAGE] %s: %s input (%s new/%s cached), %s output, "
            "EUR=%s status=%s, grounding_queries=%s, places_requests=%s",
            node_name,
            input_tokens,
            estimate.new_input_tokens,
            estimate.cached_input_tokens,
            output_tokens,
            f"{estimate.total_cost_eur:.8f}",
            estimate.pricing_status,
            grounding_queries,
            places_requests,
        )

        return UsageStats(
            input_tokens=input_tokens,
            input_tokens_new=estimate.new_input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost_usd=estimate.token_cost_usd,
            cost_eur=estimate.total_cost_eur,
            token_cost_eur=estimate.token_cost_eur,
            requests=requests,
            tool_calls=tool_calls,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            cache_hit_ratio=cache_hit_ratio,
            grounding_queries=grounding_queries,
            grounding_cost_eur=estimate.grounding_cost_eur,
            places_requests=places_requests,
            places_cost_eur=estimate.places_cost_eur,
            eur_priced=estimate.eur_priced,
            unpriced_model_requests=0 if estimate.eur_priced else 1,
            breakdown={node_name: breakdown_entry},
        )
    except Exception as exc:
        logger.warning("[USAGE] capture failed for %s: %s", node_name, exc)
        return UsageStats()


def usage_trace_attributes(result: Any, usage: UsageStats) -> dict[str, Any]:
    """Return compact scalar/JSON attributes for an application Logfire event."""

    grounding = extract_web_grounding(result)
    google_usage_metadata = extract_google_usage_metadata(result)
    first_breakdown = next(iter(usage.breakdown.values()), {})
    return {
        "cost_usd": usage.cost_usd,
        "cost_eur": usage.cost_eur,
        "token_cost_eur": usage.token_cost_eur,
        "input_tokens_new": usage.input_tokens_new,
        "input_tokens_cached": usage.cache_read_tokens,
        "grounding_query_count": usage.grounding_queries,
        "grounding_cost_eur": usage.grounding_cost_eur,
        "grounding_source_count": len(grounding.get("sources", [])),
        "grounding_support_count": len(grounding.get("supports", [])),
        "grounding_queries_json": json.dumps(
            grounding.get("queries", []), ensure_ascii=False
        ),
        "grounding_sources_json": json.dumps(
            grounding.get("sources", []), ensure_ascii=False
        ),
        "grounding_supports_json": json.dumps(
            grounding.get("supports", []), ensure_ascii=False
        ),
        "google_usage_metadata_json": json.dumps(
            google_usage_metadata, ensure_ascii=False
        ),
        "pricing_status": "exact_eur_sku" if usage.eur_priced else "eur_sku_pending",
        "pricing_source": first_breakdown.get("pricing_source", ""),
        "cost_basis": "EUR rate-card estimate; free-tier/account aggregation may differ from invoice",
        "places_requests": usage.places_requests,
        "places_cost_eur": usage.places_cost_eur,
        "unpriced_model_requests": usage.unpriced_model_requests,
    }
