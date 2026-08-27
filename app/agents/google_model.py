"""Small GoogleModel extension that retains provider metadata for ODIS."""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import ModelResponse, NativeToolReturnPart
from pydantic_ai.models.google import GoogleModel

from agents.grounding import (
    WEB_TOOL_NAMES,
    normalize_provider_grounding_metadata,
    normalize_usage_metadata,
)


class GroundingGoogleModel(GoogleModel):
    """Keep normalized Gemini grounding and usage metadata on each response.

    PydanticAI already maps Gemini grounding into native tool parts, but its
    standard ``ModelResponse`` intentionally omits the complete provider
    ``GroundingMetadata`` object.  Overriding the response conversion keeps
    the public PydanticAI run contract intact while preserving only the small,
    JSON-safe fields needed for billing and source traceability.

    This hook covers the non-streaming path used by the graph.  Streaming has
    a separate provider reconstruction path and remains deliberately
    unchanged until it can guarantee ordering of grounding supports.
    """

    def _process_response(self, response: Any) -> ModelResponse:
        model_response = super()._process_response(response)

        metadata: dict[str, Any] = {}
        grounding = normalize_provider_grounding_metadata(response)
        if grounding:
            metadata["google_grounding_metadata"] = grounding

        usage = normalize_usage_metadata(getattr(response, "usage_metadata", None))
        if usage:
            metadata["google_usage_metadata"] = usage

        if metadata:
            model_response.metadata = {
                **(model_response.metadata or {}),
                **metadata,
            }

        # In mixed built-in/custom-tool responses, PydanticAI may keep the
        # native web return but omit the provider chunks from its content.
        # Part metadata is application-only and is not sent back to Gemini,
        # so it is a safe second retention point for the source ledger.
        if grounding:
            for part in model_response.parts:
                if not isinstance(part, NativeToolReturnPart):
                    continue
                if str(part.tool_name or "").strip().lower() not in WEB_TOOL_NAMES:
                    continue
                existing_metadata = (
                    part.metadata if isinstance(part.metadata, dict) else {}
                )
                part.metadata = {
                    **existing_metadata,
                    "google_grounding_metadata": grounding,
                }
        return model_response
