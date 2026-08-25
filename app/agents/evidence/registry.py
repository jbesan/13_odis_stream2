"""Allow-listed trusted tools and their normalization contracts."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from agents.tools import search_places_batch, search_rna_rag_batch
from core.evidence import EvidenceStatus, ToolSpecView


class RnaSearchArgs(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)
    codgeo: str = Field(pattern=r"^\d{5}$")
    top_k: int = Field(default=10, ge=1, le=20)


class PlacesSearchArgs(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=6)
    location: str = Field(min_length=2, max_length=160)


@dataclass(frozen=True)
class NormalizedToolResult:
    status: EvidenceStatus
    summary: str
    payload: Any


Adapter = Callable[[BaseModel], Awaitable[NormalizedToolResult]]


@dataclass(frozen=True)
class RegisteredTool:
    spec: ToolSpecView
    args_model: type[BaseModel]
    adapter: Adapter
    timeout_seconds: float = 20.0


async def _rna_adapter(args: BaseModel) -> NormalizedToolResult:
    assert isinstance(args, RnaSearchArgs)
    rows = await search_rna_rag_batch(args.queries, args.codgeo, args.top_k)
    status: EvidenceStatus = "resolved" if rows else "not_found"
    return NormalizedToolResult(
        status=status,
        summary=f"RNA: {len(rows)} association(s) pertinente(s) trouvée(s).",
        payload=rows[:40],
    )


async def _places_adapter(args: BaseModel) -> NormalizedToolResult:
    assert isinstance(args, PlacesSearchArgs)
    result = await search_places_batch(args.queries, args.location)
    has_results = bool(result) and not (
        isinstance(result, dict) and result.get("error")
    )
    status: EvidenceStatus = "resolved" if has_results else "not_found"
    return NormalizedToolResult(
        status=status,
        summary=(
            "Google Places: résultats disponibles."
            if has_results
            else "Google Places: aucun résultat exploitable."
        ),
        payload=result,
    )


SOCIAL_TOOL_REGISTRY: dict[str, RegisteredTool] = {
    "search_rna_rag_batch_tool": RegisteredTool(
        spec=ToolSpecView(
            tool_id="search_rna_rag_batch_tool",
            description=(
                "Recherche sémantique d'associations RNA filtrée par code INSEE; "
                "à utiliser pour l'accueil des personnes réfugiées, l'entraide, "
                "l'inclusion, les loisirs et le sport. Ne pas mettre la ville "
                "dans les queries."
            ),
            input_schema=RnaSearchArgs.model_json_schema(),
            source_tag="RNA (Répertoire National des Associations)",
            trust_tier="authoritative",
        ),
        args_model=RnaSearchArgs,
        adapter=_rna_adapter,
    ),
    "search_places_batch_tool": RegisteredTool(
        spec=ToolSpecView(
            tool_id="search_places_batch_tool",
            description=(
                "Recherche batch de services et équipements locaux; à utiliser "
                "pour les cours de FLE, centres sociaux, lieux d'accueil et "
                "équipements ou clubs sportifs."
            ),
            input_schema=PlacesSearchArgs.model_json_schema(),
            source_tag="Google Places (Équipements & Services)",
            trust_tier="corroborating",
        ),
        args_model=PlacesSearchArgs,
        adapter=_places_adapter,
    ),
}


def social_tool_specs() -> list[ToolSpecView]:
    return [registered.spec for registered in SOCIAL_TOOL_REGISTRY.values()]
