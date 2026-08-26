"""Application-owned Gemini and Google grounding rate cards.

The provider's ``Usage`` object gives us the measured token buckets, while
this module owns the billing interpretation.  Keeping the two concerns
separate makes it explicit when a model has no confirmed EUR SKU yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


MILLION = 1_000_000
THOUSAND = 1_000

# These values are the EUR regional SKUs supplied for the current ODIS pilot.
GEMINI_3_1_FLASH_LITE_REGIONAL_INPUT_EUR_PER_MILLION = 0.241395
GEMINI_3_1_FLASH_LITE_REGIONAL_CACHED_INPUT_EUR_PER_MILLION = 0.0241395
GEMINI_3_1_FLASH_LITE_REGIONAL_OUTPUT_EUR_PER_MILLION = 1.44837

GEMINI_3_5_FLASH_LITE_REGIONAL_INPUT_EUR_PER_MILLION = 0.26334
GEMINI_3_5_FLASH_LITE_REGIONAL_CACHED_INPUT_EUR_PER_MILLION = 0.026334
GEMINI_3_5_FLASH_LITE_REGIONAL_OUTPUT_EUR_PER_MILLION = 2.1945

# Grounding charges are account/monthly-tier dependent.  The calculator below
# therefore reports a rate-card estimate, applying the free tier to the
# measured calls in the current aggregate.
GOOGLE_GROUNDING_FREE_QUERIES = 5_000
GOOGLE_GROUNDING_EUR_PER_THOUSAND = 12.2892

# The current Places request asks for ``editorialSummary``.  Google classifies
# that field as Text Search Enterprise + Atmosphere.  Keep this SKU explicit
# so removing that field later can be reflected by changing one card.
PLACES_TEXT_SEARCH_ENTERPRISE_ATMOSPHERE_FREE_REQUESTS = 1_000
PLACES_TEXT_SEARCH_ENTERPRISE_ATMOSPHERE_EUR_PER_THOUSAND = 35.112


@dataclass(frozen=True)
class GeminiRateCard:
    """Token prices for one model family.

    EUR fields are intentionally optional.  A USD-only card is still useful
    for backwards-compatible Logfire comparison, but it must not be silently
    presented as an EUR invoice amount.
    """

    model_family: str
    label: str
    input_new_usd_per_million: float
    cached_input_usd_per_million: float
    output_usd_per_million: float
    input_new_eur_per_million: float | None = None
    cached_input_eur_per_million: float | None = None
    output_eur_per_million: float | None = None
    pricing_source: str = "Google catalog"
    input_sku: str | None = None
    cached_input_sku: str | None = None
    output_sku: str | None = None

    @property
    def eur_priced(self) -> bool:
        return (
            self.input_new_eur_per_million is not None
            and self.cached_input_eur_per_million is not None
            and self.output_eur_per_million is not None
        )


GEMINI_3_1_FLASH_LITE_REGIONAL = GeminiRateCard(
    model_family="gemini-3.1-flash-lite",
    label="Gemini 3.1 Flash-Lite Regional",
    # Keep the catalog USD values for comparison with PydanticAI/Logfire.
    input_new_usd_per_million=0.25,
    cached_input_usd_per_million=0.025,
    output_usd_per_million=1.50,
    input_new_eur_per_million=GEMINI_3_1_FLASH_LITE_REGIONAL_INPUT_EUR_PER_MILLION,
    cached_input_eur_per_million=GEMINI_3_1_FLASH_LITE_REGIONAL_CACHED_INPUT_EUR_PER_MILLION,
    output_eur_per_million=GEMINI_3_1_FLASH_LITE_REGIONAL_OUTPUT_EUR_PER_MILLION,
    pricing_source="ODIS EUR SKU sheet (regional text)",
    input_sku="Gemini 3.1 Flash Lite Regional Text Input",
    cached_input_sku="Gemini 3.1 Flash Lite Regional Text Input Caching",
    output_sku="Gemini 3.1 Flash Lite Regional Text Output",
)


GEMINI_3_5_FLASH_LITE_REGIONAL = GeminiRateCard(
    model_family="gemini-3.5-flash-lite",
    label="Gemini 3.5 Flash-Lite Regional",
    input_new_usd_per_million=0.30,
    cached_input_usd_per_million=0.03,
    output_usd_per_million=2.50,
    input_new_eur_per_million=GEMINI_3_5_FLASH_LITE_REGIONAL_INPUT_EUR_PER_MILLION,
    cached_input_eur_per_million=GEMINI_3_5_FLASH_LITE_REGIONAL_CACHED_INPUT_EUR_PER_MILLION,
    output_eur_per_million=GEMINI_3_5_FLASH_LITE_REGIONAL_OUTPUT_EUR_PER_MILLION,
    pricing_source="ODIS EUR SKU sheet (regional text)",
    input_sku="Gemini 3.5 Flash Lite Regional Text Input",
    cached_input_sku="Gemini 3.5 Flash Lite Regional Text Input Caching",
    output_sku="Gemini 3.5 Flash Lite Regional Text Output",
)

# Keep the old symbol available for callers that imported the pre-confirmation
# card name; model resolution now uses the confirmed regional EUR card above.
GEMINI_3_5_FLASH_LITE_GLOBAL = GEMINI_3_5_FLASH_LITE_REGIONAL


GENERIC_GEMINI = GeminiRateCard(
    model_family="generic",
    label="Gemini (fallback)",
    input_new_usd_per_million=0.10,
    cached_input_usd_per_million=0.01,
    output_usd_per_million=0.40,
    pricing_source="Legacy application fallback; EUR unavailable",
)


def _normalize_model_id(model_id: str) -> str:
    normalized = str(model_id or "").strip().lower()
    if ":" in normalized:
        normalized = normalized.split(":", 1)[1]
    if normalized.startswith("models/"):
        normalized = normalized[7:]
    return normalized


def rate_card_for_model(model_id: str) -> GeminiRateCard:
    """Return the most specific card without applying an unsafe EUR guess."""

    normalized = _normalize_model_id(model_id)
    if "3.1-flash-lite" in normalized:
        return GEMINI_3_1_FLASH_LITE_REGIONAL
    if "3.5-flash-lite" in normalized:
        return GEMINI_3_5_FLASH_LITE_REGIONAL
    return GENERIC_GEMINI


def _tiered_cost_eur(
    request_count: int,
    *,
    free_requests: int,
    eur_per_thousand: float,
) -> float:
    billable = max(int(request_count) - free_requests, 0)
    return billable * eur_per_thousand / THOUSAND


def estimate_google_grounding_cost_eur(query_count: int) -> float:
    """Estimate Google Search/Maps grounding charge for an aggregate count."""

    return _tiered_cost_eur(
        query_count,
        free_requests=GOOGLE_GROUNDING_FREE_QUERIES,
        eur_per_thousand=GOOGLE_GROUNDING_EUR_PER_THOUSAND,
    )


def estimate_places_cost_eur(request_count: int) -> float:
    """Estimate the current Places Text Search Enterprise + Atmosphere SKU."""

    return _tiered_cost_eur(
        request_count,
        free_requests=PLACES_TEXT_SEARCH_ENTERPRISE_ATMOSPHERE_FREE_REQUESTS,
        eur_per_thousand=PLACES_TEXT_SEARCH_ENTERPRISE_ATMOSPHERE_EUR_PER_THOUSAND,
    )


@dataclass(frozen=True)
class GeminiCostEstimate:
    """Measured-token and external-grounding cost components."""

    model_id: str
    rate_card: GeminiRateCard
    input_tokens: int
    cached_input_tokens: int
    new_input_tokens: int
    output_tokens: int
    token_cost_usd: float
    token_cost_eur: float
    input_new_cost_eur: float
    input_cached_cost_eur: float
    output_cost_eur: float
    grounding_queries: int = 0
    grounding_cost_eur: float = 0.0
    places_requests: int = 0
    places_cost_eur: float = 0.0
    provider_cost_usd: float | None = None

    @property
    def eur_priced(self) -> bool:
        return self.rate_card.eur_priced

    @property
    def total_cost_eur(self) -> float:
        if not self.eur_priced:
            return 0.0
        return self.token_cost_eur + self.grounding_cost_eur + self.places_cost_eur

    @property
    def pricing_status(self) -> str:
        return "exact_eur_sku" if self.eur_priced else "eur_sku_pending"

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe breakdown for Logfire and BigQuery payloads."""

        return {
            "model_id": self.model_id,
            "model_family": self.rate_card.model_family,
            "pricing_status": self.pricing_status,
            "pricing_source": self.rate_card.pricing_source,
            "currency": "EUR",
            "input_tokens": self.input_tokens,
            "input_tokens_new": self.new_input_tokens,
            "input_tokens_cached": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "token_cost_usd": self.token_cost_usd,
            "token_cost_eur": self.token_cost_eur,
            "input_new_cost_eur": self.input_new_cost_eur,
            "input_cached_cost_eur": self.input_cached_cost_eur,
            "output_cost_eur": self.output_cost_eur,
            "grounding_queries": self.grounding_queries,
            "grounding_cost_eur": self.grounding_cost_eur,
            "places_requests": self.places_requests,
            "places_cost_eur": self.places_cost_eur,
            "cost_eur": self.total_cost_eur,
            "provider_cost_usd": self.provider_cost_usd,
            "rates_per_million": {
                "input_new_eur": self.rate_card.input_new_eur_per_million,
                "input_cached_eur": self.rate_card.cached_input_eur_per_million,
                "output_eur": self.rate_card.output_eur_per_million,
                "input_new_usd": self.rate_card.input_new_usd_per_million,
                "input_cached_usd": self.rate_card.cached_input_usd_per_million,
                "output_usd": self.rate_card.output_usd_per_million,
            },
            "skus": {
                "input": self.rate_card.input_sku,
                "cached_input": self.rate_card.cached_input_sku,
                "output": self.rate_card.output_sku,
            },
        }


def calculate_gemini_cost(
    model_id: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    grounding_queries: int = 0,
    places_requests: int = 0,
    provider_cost_usd: float | None = None,
) -> GeminiCostEstimate:
    """Calculate cache-aware Gemini and grounding costs from measured usage.

    PydanticAI normalizes ``input_tokens`` as an inclusive bucket: cached
    tokens are included in it.  Therefore only ``input_tokens - cache_read``
    is charged at the new-input rate.
    """

    card = rate_card_for_model(model_id)
    input_total = max(int(input_tokens), 0)
    cached = min(max(int(cache_read_tokens), 0), input_total)
    new_input = input_total - cached
    output = max(int(output_tokens), 0)

    token_cost_usd = (
        new_input * card.input_new_usd_per_million
        + cached * card.cached_input_usd_per_million
        + output * card.output_usd_per_million
    ) / MILLION

    input_new_cost_eur = (
        new_input * card.input_new_eur_per_million / MILLION
        if card.input_new_eur_per_million is not None
        else 0.0
    )
    input_cached_cost_eur = (
        cached * card.cached_input_eur_per_million / MILLION
        if card.cached_input_eur_per_million is not None
        else 0.0
    )
    output_cost_eur = (
        output * card.output_eur_per_million / MILLION
        if card.output_eur_per_million is not None
        else 0.0
    )
    token_cost_eur = input_new_cost_eur + input_cached_cost_eur + output_cost_eur

    return GeminiCostEstimate(
        model_id=str(model_id),
        rate_card=card,
        input_tokens=input_total,
        cached_input_tokens=cached,
        new_input_tokens=new_input,
        output_tokens=output,
        token_cost_usd=token_cost_usd,
        token_cost_eur=token_cost_eur,
        input_new_cost_eur=input_new_cost_eur,
        input_cached_cost_eur=input_cached_cost_eur,
        output_cost_eur=output_cost_eur,
        grounding_queries=max(int(grounding_queries), 0),
        grounding_cost_eur=estimate_google_grounding_cost_eur(grounding_queries),
        places_requests=max(int(places_requests), 0),
        places_cost_eur=estimate_places_cost_eur(places_requests),
        provider_cost_usd=provider_cost_usd,
    )
