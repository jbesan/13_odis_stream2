"""Application-owned source labels for expert traceability.

The legacy experts return free-form Markdown.  Their former model-authored
``searched`` field is intentionally disabled in the active output schema and
kept as a documented placeholder for a future judge/audit mode.  This module
derives the user-facing source list from recorded function-tool calls (including
the direct Gemini web batch) and from the data already present in the dossier.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

from agents.grounding import extract_web_grounding


SOURCE_CATALOG: dict[str, dict[str, Any]] = {
    "dossier": {
        "label": "Dossier OD&IS — indicateurs territoriaux (INSEE, DataGouv, etc.)",
        "note": "Brief, critères et indicateurs préchargés dans le dossier.",
        "source_url": None,
    },
    "rna": {
        "label": "RNA officiel — associations",
        "note": "Répertoire national des associations et recherche sémantique locale.",
        "source_url": "https://www.data.gouv.fr/datasets/rna-agrege-a-lechelle-nationale/",
    },
    "inclusion": {
        "label": "Data Inclusion — structures d’insertion",
        "note": "Services d’inclusion et offres d’insertion présents dans le dossier ou consultés.",
        "source_url": "https://api.data.inclusion.gouv.fr",
    },
    "places": {
        "label": "Google Maps — lieux et services",
        "note": "Recherche de lieux, équipements et structures locales.",
        "source_url": None,
    },
    "routes": {
        "label": "Google Maps — itinéraires",
        "note": "Calcul d’itinéraire et de temps de trajet.",
        "source_url": None,
    },
    "france_travail": {
        "label": "France Travail — offres d’emploi",
        "note": "Offres et détails d’offres d’emploi.",
        "source_url": None,
    },
    "referentials": {
        "label": "Référentiels OD&IS",
        "note": "Référentiels utilisés pour normaliser les recherches.",
        "source_url": None,
    },
    "web": {
        "label": "Recherche Web Google",
        "note": "Recherche effectuée avec Google ; les pages sources retournées sont affichées ci-dessous lorsqu'elles sont disponibles.",
        "source_url": None,
    },
}


# Context sources are deliberately conservative: these entries describe data
# actually injected into the expert prompt, not every provider known by the
# application.
DOMAIN_CONTEXT_SOURCES: dict[str, tuple[str, ...]] = {
    "housing_expert": ("dossier",),
    "mobility_expert": ("dossier",),
    "healthcare_expert": ("dossier",),
    "education_expert": ("dossier",),
    "social_integration_expert": ("dossier", "inclusion"),
    "job_hunter": ("dossier",),
}


TOOL_SOURCE_KEYS: dict[str, str] = {
    "search_rna_rag_batch_tool": "rna",
    "search_rna_rag_batch": "rna",
    "search_places_batch_tool": "places",
    "search_places_batch": "places",
    "compute_routes_tool": "routes",
    "compute_routes": "routes",
    "search_job_offers_batch_tool": "france_travail",
    "search_job_offers_batch": "france_travail",
    "get_job_details_tool": "france_travail",
    "get_job_details": "france_travail",
    "search_inclusion_jobs_batch_tool": "inclusion",
    "search_inclusion_jobs_batch": "inclusion",
    "get_inclusion_job_details_tool": "inclusion",
    "get_inclusion_job_details": "inclusion",
    "search_referentiels_batch_tool": "referentials",
    "search_referentiels_batch": "referentials",
    "search_web_batch_tool": "web",
    "web_search_batch": "web",
}


def is_vertex_grounding_redirect(url: str | None) -> bool:
    """Return whether a URL is a Google/Vertex grounding redirect."""

    if not isinstance(url, str) or not url.strip():
        return False
    parsed = urlparse(url)
    return (
        parsed.hostname == "vertexaisearch.cloud.google.com"
        and parsed.path.startswith("/grounding-api-redirect/")
    )


def _messages_from_result(result: Any) -> list[Any]:
    """Read a result's message history without depending on one result type."""

    if result is None or not hasattr(result, "all_messages"):
        return []
    try:
        return list(result.all_messages())
    except Exception:
        return []


def _tool_names_from_result(result: Any) -> set[str]:
    """Return tool names recorded in a Pydantic AI run result.

    ``all_messages`` is intentionally accessed defensively because some
    offline tests and error paths use lightweight result doubles.  Native
    Gemini search parts expose the same ``tool_name`` attribute as ordinary
    function tool parts.
    """

    names: set[str] = set()
    for message in _messages_from_result(result):
        for part in getattr(message, "parts", ()):
            name = getattr(part, "tool_name", None)
            if name:
                names.add(str(name))
    return names


def _web_search_terms_from_result(result: Any) -> list[str]:
    """Return submitted key terms without presenting them as provider queries."""

    terms: list[str] = []
    seen: set[str] = set()
    for message in _messages_from_result(result):
        for part in getattr(message, "parts", ()):
            tool_name = str(getattr(part, "tool_name", "") or "")
            if _source_key_for_tool(tool_name) != "web":
                continue
            args: Any = getattr(part, "args", None)
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (TypeError, ValueError):
                    args = None
            if not isinstance(args, Mapping):
                continue
            searches = args.get("searches")
            if not isinstance(searches, list):
                continue
            for search in searches:
                if not isinstance(search, Mapping):
                    continue
                key_terms = search.get("key_terms")
                if not isinstance(key_terms, list):
                    continue
                for term in key_terms:
                    if not isinstance(term, str):
                        continue
                    clean_term = term.strip()
                    if clean_term and clean_term not in seen:
                        seen.add(clean_term)
                        terms.append(clean_term)
    return terms[:32]


def _source_key_for_tool(tool_name: str) -> str | None:
    """Map a Pydantic AI tool name to a canonical source key."""

    if tool_name in TOOL_SOURCE_KEYS:
        return TOOL_SOURCE_KEYS[tool_name]

    normalized = tool_name.lower()
    if any(
        token in normalized
        for token in ("google_search", "web_search", "search_web", "websearch")
    ):
        return "web"
    return None


def _references_for_keys(keys: Iterable[str], *, tool_called: set[str]) -> list[dict[str, Any]]:
    """Materialize stable, serializable source references for the UI."""

    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in keys:
        if key in seen or key not in SOURCE_CATALOG:
            continue
        seen.add(key)
        catalog_entry = SOURCE_CATALOG[key]
        references.append(
            {
                "source_key": key,
                "label": catalog_entry["label"],
                "status": "consultée" if key in tool_called else "contexte",
                "note": catalog_entry["note"],
                "source_url": catalog_entry.get("source_url"),
            }
        )
    return references


def source_references_for_result(
    domain: str,
    result: Any | None,
) -> list[dict[str, Any]]:
    """Build the source ledger for one expert result.

    The dossier entries are always present because they are part of the
    prompt.  External providers are added when their tool call or provider
    grounding metadata appears in the recorded run history.  Passing ``None``
    is useful for old persisted results: the UI can still show the prompt-level
    source context without inventing historical tool calls.
    """

    tool_names = _tool_names_from_result(result)
    tool_called = {
        source_key
        for tool_name in tool_names
        if (source_key := _source_key_for_tool(tool_name)) is not None
    }
    # Provider metadata is an independent evidence of a Google search.  It can
    # survive even when the native web-search part is absent from the recorded
    # PydanticAI history, so do not require a tool part before extracting it.
    grounding = extract_web_grounding(result)
    submitted_search_terms = _web_search_terms_from_result(result)
    grounding_confirmed = bool(
        grounding.get("queries")
        or grounding.get("sources")
        or grounding.get("supports")
    )
    if (
        grounding.get("queries")
        or grounding.get("sources")
        or grounding.get("supports")
        or grounding.get("metadata_responses")
    ):
        tool_called.add("web")

    ordered_keys = list(DOMAIN_CONTEXT_SOURCES.get(domain, ("dossier",)))
    for source_key in (
        "rna",
        "inclusion",
        "places",
        "routes",
        "france_travail",
        "referentials",
        "web",
    ):
        if source_key in tool_called:
            ordered_keys.append(source_key)
    references = _references_for_keys(ordered_keys, tool_called=tool_called)

    # The direct web wrapper (and legacy persisted native-search runs) may
    # return several grounded web chunks for one search call. Expose those
    # URLs as individual application-owned sources; never parse URLs from the
    # expert's Markdown answer.
    if "web" in tool_called:
        grounded_sources = grounding.get("sources", [])
        if grounded_sources:
            expanded: list[dict[str, Any]] = []
            web_reference_number = 0
            for reference in references:
                if reference.get("source_key") != "web":
                    expanded.append(reference)
                    continue
                for source in grounded_sources:
                    url = source.get("url")
                    if not isinstance(url, str) or not url:
                        continue
                    title = source.get("title") or source.get("domain") or "Source Web Google"
                    web_reference_number += 1
                    expanded.append(
                        {
                            **reference,
                            "reference_id": f"Ref-{web_reference_number}",
                            "label": title,
                            "note": "Page source retournée par Google.",
                            "source_url": url,
                            "grounding_domain": source.get("domain"),
                            "grounding_queries": grounding.get("queries", []),
                            "search_terms": submitted_search_terms,
                            "grounding_confirmed": True,
                            "grounding_supports": grounding.get("supports", []),
                        }
                    )
            references = expanded
        else:
            for reference in references:
                if reference.get("source_key") == "web":
                    reference["grounding_queries"] = grounding.get("queries", [])
                    reference["search_terms"] = submitted_search_terms
                    reference["grounding_confirmed"] = grounding_confirmed
                    reference["grounding_supports"] = grounding.get("supports", [])
                    if not grounding_confirmed:
                        reference["status"] = "non confirmée"
                        reference["note"] = (
                            "L'outil a été appelé, mais Google n'a confirmé aucune "
                            "recherche. Aucun résumé Web n'est retenu comme preuve."
                        )
                    else:
                        reference["note"] = (
                            "Google a été consulté, mais n'a transmis aucune URL "
                            "de page source exploitable."
                        )
    return references


def source_keys(references: Iterable[dict[str, Any]]) -> list[str]:
    """Extract canonical source keys for compact Logfire attributes."""

    keys: list[str] = []
    seen: set[str] = set()
    for reference in references:
        value = reference.get("source_key")
        if not value:
            continue
        key = str(value)
        if key not in seen:
            seen.add(key)
            keys.append(key)
    return keys
