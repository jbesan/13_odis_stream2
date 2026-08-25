"""Application-owned source labels for expert traceability.

The legacy experts return free-form Markdown and a model-authored ``searched``
field.  That field is useful to the model but is not a reliable citation
ledger.  This module derives the user-facing source list from the tool calls
recorded by Pydantic AI and from the data already present in the dossier.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


SOURCE_CATALOG: dict[str, dict[str, Any]] = {
    "dossier": {
        "label": "Dossier OD&IS — indicateurs territoriaux",
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
        "label": "Google Places — lieux et services",
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
        "note": "Recherche Web appelée; les références détaillées ne sont pas encore exposées par le chemin legacy.",
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
}


def _tool_names_from_result(result: Any) -> set[str]:
    """Return tool names recorded in a Pydantic AI run result.

    ``all_messages`` is intentionally accessed defensively because some
    offline tests and error paths use lightweight result doubles.  Native
    Gemini search parts expose the same ``tool_name`` attribute as ordinary
    function tool parts.
    """

    if result is None or not hasattr(result, "all_messages"):
        return set()

    try:
        messages = result.all_messages()
    except Exception:
        return set()

    names: set[str] = set()
    for message in messages:
        for part in getattr(message, "parts", ()):
            name = getattr(part, "tool_name", None)
            if name:
                names.add(str(name))
    return names


def _source_key_for_tool(tool_name: str) -> str | None:
    """Map a Pydantic AI tool name to a canonical source key."""

    if tool_name in TOOL_SOURCE_KEYS:
        return TOOL_SOURCE_KEYS[tool_name]

    normalized = tool_name.lower()
    if any(token in normalized for token in ("google_search", "web_search", "websearch")):
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
    prompt.  External providers are added only when their tool call appears in
    the recorded run history.  Passing ``None`` is useful for old persisted
    results: the UI can still show the prompt-level source context without
    inventing historical tool calls.
    """

    tool_names = _tool_names_from_result(result)
    tool_called = {
        source_key
        for tool_name in tool_names
        if (source_key := _source_key_for_tool(tool_name)) is not None
    }
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
    return _references_for_keys(ordered_keys, tool_called=tool_called)


def source_keys(references: Iterable[dict[str, Any]]) -> list[str]:
    """Extract canonical source keys for compact Logfire attributes."""

    return [
        str(reference["source_key"])
        for reference in references
        if reference.get("source_key")
    ]
