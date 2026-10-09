"""Context rendering and domain model serialization for ODIS AI Agent Swarm.

Extracts and centralizes domain metric formatting, detail item serialization,
and deterministic JSON context emission with 100% iso-format fidelity.
"""

import json
import logging
from typing import Any, Dict, Final, List, Optional
from pydantic import BaseModel

from core.models import CommuneResult, CriteriaItem, SearchCriterias

logger = logging.getLogger(__name__)

EXCLUDE_CRITERIA_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "odis_brief",
        "active_criteria",
        "active_categories",
        "criteria_weights",
        "poids_emploi",
        "poids_logement",
        "poids_education",
        "poids_inclusion",
        "poids_mobilite",
        "poids_sante",
        "poids_territoire",
        "target_population",
        "target_population_sigma",
        "target_population_a",
        "target_population_b",
        "target_population_c",
        "target_population_d",
        "org_boosts",
    }
)


def format_criteria_value(val: Any) -> Any:
    """Helper to recursively simplify CriteriaItem instances into labels.

    Args:
        val: Any criteria value or nested structure.

    Returns:
        Readable representation with CriteriaItem instances resolved to labels.
    """
    if val is None:
        return None
    if val.__class__.__name__ == "CriteriaItem" or isinstance(val, CriteriaItem):
        return getattr(val, "label", str(val))
    if isinstance(val, list):
        return [format_criteria_value(item) for item in val if item is not None]
    if isinstance(val, dict):
        return {k: format_criteria_value(v) for k, v in val.items() if v is not None}
    if isinstance(val, BaseModel):
        return format_model(val)
    return val


def format_criteria(criteria: Optional[SearchCriterias]) -> Dict[str, Any]:
    """Serializes SearchCriterias with readable labels, excluding internal flags and odis_brief.

    Args:
        criteria: The search criteria instance or None.

    Returns:
        Dictionary of criteria field descriptions/names to formatted values.
    """
    if criteria is None:
        return {}
    ctx: Dict[str, Any] = {}
    for name, fld in criteria.__class__.model_fields.items():
        if name in EXCLUDE_CRITERIA_FIELDS:
            continue
        val = getattr(criteria, name)
        if val is None:
            continue
        if isinstance(val, (list, dict)) and len(val) == 0:
            continue

        label = fld.description or name
        ctx[label] = format_criteria_value(val)
    return ctx


def format_commune_identity(commune: Optional[CommuneResult]) -> Dict[str, Any]:
    """Formats the base commune identification header.

    Args:
        commune: The commune result model or None.

    Returns:
        Dictionary containing INSEE code, name, population, BdV, and scores.
    """
    if commune is None:
        return {}
    return {
        "Code INSEE": commune.codgeo,
        "Nom": commune.name,
        "Population": commune.population,
        "Bassin de vie": commune.name_bdv or commune.codgeo_bdv or "N/A",
        "Score global": int((commune.global_score or 0.0) * 100),
        "Adéquation besoins": int(
            (commune.score_besoins or commune.global_score or 0.0) * 100
        ),
        "Adéquation démographique": int(
            (commune.coeff_population_gauss or 1.0) * 100
        ),
    }


def format_association(item: Any) -> str:
    """Formats association item as 'ID | Nom | Description'.

    Args:
        item: AssociationDetail or duck-typed object.

    Returns:
        Pipe-separated string representation.
    """
    asso_id = (getattr(item, "id", None) or "").strip()
    name = (getattr(item, "name", None) or "").strip()
    desc = (getattr(item, "description", None) or "").strip()
    parts = [p for p in [asso_id, name, desc] if p]
    return " | ".join(parts)


def format_job_offer(item: Any) -> str:
    """Formats job offer as 'ID | Titre chez Entreprise (Contrat) [ROME: Code] - Description'.

    Args:
        item: JobOfferDetail or duck-typed object.

    Returns:
        Compact single-line string representation.
    """
    oid = (getattr(item, "id", None) or "").strip()
    title = (getattr(item, "title", None) or "").strip()
    comp = (getattr(item, "company", None) or "").strip()
    comp_str = f" chez {comp}" if comp else ""
    ctype = (
        getattr(item, "contract_type", None)
        or getattr(item, "contract_label", None)
        or ""
    ).strip()
    ctype_str = f" ({ctype})" if ctype else ""
    rome = (getattr(item, "rome_code", None) or "").strip()
    rome_str = f" [ROME: {rome}]" if rome else ""

    desc = (getattr(item, "description", None) or "").strip()
    if desc:
        desc_clean = " ".join(desc.split())
        if len(desc_clean) > 200:
            desc_clean = desc_clean[:197] + "..."
        desc_str = f" - {desc_clean}"
    else:
        desc_str = ""

    header = f"{title}{comp_str}{ctype_str}{rome_str}"
    if oid:
        return f"{oid} | {header}{desc_str}"
    return f"{header}{desc_str}"


def format_inclusion_services(
    services_dict: Optional[Dict[str, List[Any]]],
) -> Dict[str, List[str]]:
    """Formats inclusion services grouped by thématique -> list of unique structures with distance/commune.

    Args:
        services_dict: Mapping of thematic categories to service items.

    Returns:
        Mapping of themes to unique structure strings.
    """
    if not services_dict:
        return {}
    result: Dict[str, List[str]] = {}
    for theme, srv_list in services_dict.items():
        if not srv_list:
            continue
        structures: List[str] = []
        seen: set[str] = set()
        for srv in srv_list:
            struct_name = (
                getattr(srv, "nom_structure", None) or "Structure locale"
            ).strip()
            dist = getattr(srv, "distance_km", None)
            commune = (getattr(srv, "commune_nom", None) or "").strip()

            dist_parts: List[str] = []
            if dist is not None:
                dist_parts.append(f"{dist} km")
            if commune:
                dist_parts.append(commune)

            dist_str = f" ({' - '.join(dist_parts)})" if dist_parts else ""
            struct_key = f"{struct_name}{dist_str}"

            if struct_key not in seen:
                seen.add(struct_key)
                structures.append(struct_key)

        if structures:
            result[theme] = structures
    return result


def format_detail_item(item: Any) -> Any:
    """Formats individual detail items compactly.

    Args:
        item: Detail item (CriteriaItem, AssociationDetail, JobOfferDetail,
              InclusionServiceDetail, CommuneScoreDetail, BaseModel, list, dict, or scalar).

    Returns:
        Compactly serialized representation.
    """
    if item is None:
        return None
    cname = item.__class__.__name__
    if cname == "CriteriaItem" or isinstance(item, CriteriaItem):
        return getattr(item, "label", str(item))

    if cname == "AssociationDetail":
        return format_association(item)

    if cname == "JobOfferDetail":
        return format_job_offer(item)

    if cname == "InclusionServiceDetail":
        srv_name = getattr(item, "name", "") or ""
        struct = getattr(item, "nom_structure", "") or ""
        struct_part = f" par {struct}" if struct else ""
        dist = getattr(item, "distance_km", None)
        commune = getattr(item, "commune_nom", "") or ""
        dist_part = (
            f" (à {dist} km{f' - {commune}' if commune else ''})"
            if dist is not None
            else ""
        )
        return f"{srv_name}{struct_part}{dist_part}"

    if cname == "CommuneScoreDetail":
        label = getattr(item, "label", "N/A")
        metric_type = getattr(item, "metric_type", "continuous")
        status_label = getattr(item, "status_label", None)
        vkpi = getattr(item, "valeur_kpi", None)
        unit = getattr(item, "unit", "") or ""
        score = getattr(item, "score_normalise", 0.0)
        weight = getattr(item, "relative_weight", 0.0)
        if metric_type == "discrete" and status_label:
            return f"{label}: {status_label} (score: {round(float(score), 2)}, poids relatif: {weight}%)"
        if vkpi is not None:
            unit_clean = unit.strip()
            kpi_str = f"{vkpi} {unit_clean}" if unit_clean else f"{vkpi}"
        else:
            kpi_str = "N/A"
        return f"{label}: {kpi_str}, score: {round(float(score), 2)}, poids relatif: {weight}%"

    if isinstance(item, BaseModel):
        return format_model(item)
    if isinstance(item, list):
        return [format_detail_item(x) for x in item]
    if isinstance(item, dict):
        return {k: format_detail_item(v) for k, v in item.items()}
    return item


def format_model(model: Optional[BaseModel]) -> Dict[str, Any]:
    """Formats a domain metric sub-model with human-readable descriptions and compact detail items.

    Args:
        model: Pydantic model instance or None.

    Returns:
        Dictionary of field descriptions to compactly serialized values.
    """
    if model is None:
        return {}
    ctx: Dict[str, Any] = {}
    for name, fld in model.__class__.model_fields.items():
        val = getattr(model, name)
        if val is None:
            continue

        label = fld.description or name

        # Skip raw un-geocoded services_grouped; keep rich services_detailed only
        if name == "services_grouped":
            continue

        # Special Handling for refugee associations in RNA
        if name == "asso_refugee_list":
            if not val:
                ctx[label] = (
                    "Aucune association spécifique recensée dans le Répertoire National des Associations (RNA) pour cette commune."
                )
            else:
                ctx[label] = format_detail_item(val)
            continue

        if isinstance(val, (list, dict)) and len(val) == 0:
            continue

        # Special Handling for inclusion services grouped by structure
        if name == "services_detailed" and isinstance(val, dict):
            formatted_srvs = format_inclusion_services(val)
            if formatted_srvs:
                ctx[label] = formatted_srvs
            continue

        ctx[label] = format_detail_item(val)
    return ctx


def resolve_focus_city(state: Any) -> Optional[CommuneResult]:
    """Resolves the complete focus-city record used by agent prompts.

    Args:
        state: GraphState or state-like container.

    Returns:
        The resolved CommuneResult or None.
    """
    if state is None:
        return None
    focus_city = getattr(state, "focus_city", None)
    if isinstance(focus_city, str):
        focus_city = CommuneResult(name=focus_city, codgeo="")
    search_results = getattr(state, "search_results", None)
    if focus_city and search_results and hasattr(search_results, "get_by_code"):
        codgeo = getattr(focus_city, "codgeo", None)
        if codgeo:
            resolved = search_results.get_by_code(codgeo)
            if resolved:
                return resolved
    return focus_city


def dump_context(context: Dict[str, Any], *, compact: bool = True) -> str:
    """Serializes context deterministically into JSON while preserving semantic order.

    Args:
        context: Dictionary to serialize.
        compact: If True, uses no whitespace separators; if False, indents by 2.

    Returns:
        JSON string.
    """
    if compact:
        return json.dumps(
            context,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return json.dumps(context, ensure_ascii=False, indent=2)


def auto_build_context(model: Any, visibility_key: str = "all") -> Any:
    """Backward-compatible helper redirecting to typed formatting.

    Args:
        model: Value or model to format.
        visibility_key: Deprecated visibility key (kept for signature compatibility).

    Returns:
        Formatted data structure.
    """
    if model is None:
        return None
    if isinstance(model, CriteriaItem):
        return model.label
    if isinstance(model, BaseModel):
        return format_model(model)
    if isinstance(model, list):
        return [auto_build_context(item, visibility_key) for item in model]
    if isinstance(model, dict):
        return {k: auto_build_context(v, visibility_key) for k, v in model.items()}
    return model


# Backward compatibility aliases
_format_criteria_value = format_criteria_value
_format_criteria = format_criteria
_format_commune_identity = format_commune_identity
_format_association = format_association
_format_job_offer = format_job_offer
_format_inclusion_services = format_inclusion_services
_format_detail_item = format_detail_item
_format_model = format_model
_focus_city = resolve_focus_city
_dump_context = dump_context
_auto_build_context = auto_build_context
_process_value = format_detail_item
