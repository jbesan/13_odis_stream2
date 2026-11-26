"""Deterministic ranking, deduplication, and BDV partitioning for V6 associations."""

from __future__ import annotations

from datetime import date
import string
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import pandas as pd

from core.inclusion_referential import resolve_slug_to_v6_label
from core.models import AssociationDetail, InterestSearchGroupResult

_COMMUNE_MATCH_POINTS = 40.0
_EMPLOYEES_POINTS = 35.0
_FRESHNESS_MAX_POINTS = 30.0
_TOP_N_PER_BUCKET = 10
_NON_EMPLOYER_TRANCHES = frozenset({"", "NN", "00", "NONE", "NAN"})


def _clean_text(value: Any) -> str:
    """Return stripped text with pandas NaN/None variants normalized to empty."""
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "none", "<na>"}:
        return ""
    return text


_ASSOCIATION_NAME_MAX_LEN = 80


def _format_display_name(
    raw_name: Any,
    fallback: str = "Inconnu",
    *,
    max_len: Optional[int] = None,
) -> str:
    """Format association/commune names from ALL-CAPS RNA strings cleanly."""
    cleaned = _clean_text(raw_name)
    if not cleaned:
        return fallback
    formatted = string.capwords(cleaned.lower())
    if max_len is not None and len(formatted) > max_len:
        return formatted[: max_len - 3].rstrip() + "..."
    return formatted


def _truncate_description(raw_desc: Any, max_len: int = 250) -> str:
    """Normalize and truncate long association descriptions."""
    desc = _clean_text(raw_desc)
    if len(desc) > max_len:
        return desc[:max_len] + "..."
    return desc


def _to_str_list(value: Any) -> List[str]:
    """Safely convert BigQuery array / numpy ndarray / list values to ``list[str]``."""
    if value is None:
        return []
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple, set)):
        return []
    out: List[str] = []
    for item in value:
        text = _clean_text(item)
        if text:
            out.append(text)
    return out


def has_active_employees(tranche_effectifs: Any) -> bool:
    """Return True when SIRENE ``tranche_effectifs`` indicates salaried staff.

    Args:
        tranche_effectifs: Raw SIRENE tranche code (e.g. ``'01'``, ``'NN'``, ``'00'``, ``None``).

    Returns:
        ``True`` if salaried employees are declared, ``False`` otherwise.
    """
    code = _clean_text(tranche_effectifs).upper()
    return bool(code) and code not in _NON_EMPLOYER_TRANCHES


def parse_clamped_decla_date(
    raw_date: Any, *, reference_date: Optional[date] = None
) -> Optional[date]:
    """Parse ``YYYY-MM-DD`` declaration date and clamp future typos to ``reference_date``.

    Args:
        raw_date: Raw date string/date from V6 (``national_v6_consolidated`` has rare
            typo dates such as ``2100``).
        reference_date: Reference upper bound (defaults to ``date.today()``).

    Returns:
        Clamped ``date`` object, or ``None`` if unparseable.
    """
    today = reference_date or date.today()
    if isinstance(raw_date, date):
        return min(raw_date, today)
    cleaned = _clean_text(raw_date)
    if len(cleaned) < 10:
        return None
    try:
        parsed = date.fromisoformat(cleaned[:10])
    except ValueError:
        return None
    return min(parsed, today)


def compute_freshness_score(
    raw_date: Any, *, reference_date: Optional[date] = None
) -> float:
    """Compute linear recency score in ``[0.0, 30.0]`` (<=1y: 30, >=10y: 0).

    Args:
        raw_date: Raw ``date_decla`` string.
        reference_date: Reference date for age calculation.

    Returns:
        Freshness points between ``0.0`` and ``30.0``.
    """
    today = reference_date or date.today()
    parsed = parse_clamped_decla_date(raw_date, reference_date=today)
    if parsed is None:
        return 0.0
    age_years = max(0.0, (today - parsed).days / 365.25)
    if age_years <= 1.0:
        return _FRESHNESS_MAX_POINTS
    if age_years >= 10.0:
        return 0.0
    return _FRESHNESS_MAX_POINTS * (10.0 - age_years) / 9.0


def score_v6_candidate(
    row: Mapping[str, Any],
    *,
    target_codgeo: str,
    reference_date: Optional[date] = None,
) -> Tuple[float, bool, bool, bool, str, str]:
    """Compute unified continuous score (max 105 pts) + deterministic tie-breakers.

    Weights (Lot 2 validated R1 Opt B + R2):
    - Same commune (`codgeo == target_codgeo`): +40 pts
    - Active salaried staff (`tranche_effectifs` not in NULL/NN/00): +35 pts
    - Declaration freshness (`date_decla` clamped <= today): 0..30 pts

    Returns:
        Sort key tuple ordered so descending ``reverse=True`` yields best-first
        with deterministic ascending `(name, id)` tie-breaks.
    """
    row_codgeo = _clean_text(row.get("codgeo")).zfill(5)
    same_commune = row_codgeo == str(target_codgeo).strip().zfill(5)
    employees = has_active_employees(row.get("tranche_effectifs"))
    freshness = compute_freshness_score(
        row.get("date_decla"), reference_date=reference_date
    )
    total_score = (
        (_COMMUNE_MATCH_POINTS if same_commune else 0.0)
        + (_EMPLOYEES_POINTS if employees else 0.0)
        + freshness
    )
    is_siege = bool(row.get("is_siege"))
    name_clean = _clean_text(row.get("name"))
    id_clean = _clean_text(row.get("id"))
    return (total_score, same_commune, employees, is_siege, name_clean, id_clean)


def _establishment_preference_key(
    row: Mapping[str, Any], *, reference_date: Optional[date] = None
) -> Tuple[bool, bool, str, bool]:
    """Rank establishments sharing the same ``(id, codgeo)`` during deduplication."""
    employees = has_active_employees(row.get("tranche_effectifs"))
    is_siege = bool(row.get("is_siege"))
    parsed = parse_clamped_decla_date(
        row.get("date_decla"), reference_date=reference_date
    )
    decla_iso = parsed.isoformat() if parsed else ""
    has_etab_name = bool(_clean_text(row.get("nom_etablissement")))
    return (employees, is_siege, decla_iso, has_etab_name)


def _dedupe_by_id_and_commune(
    rows: Sequence[Mapping[str, Any]],
    *,
    matched_tags_per_row: Optional[Sequence[Sequence[str]]] = None,
    reference_date: Optional[date] = None,
) -> List[Tuple[Mapping[str, Any], List[str]]]:
    """Deduplicate multi-SIRET rows on ``(id, codgeo)`` while preserving distinct communes.

    For duplicate ``(id, codgeo)`` rows in the same commune, retains the strongest
    establishment and merges its ``matched_tags`` in order.
    """
    best_by_key: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    tags_by_key: Dict[Tuple[str, str], List[str]] = {}

    for idx, row in enumerate(rows):
        asso_id = _clean_text(row.get("id"))
        codgeo = _clean_text(row.get("codgeo")).zfill(5)
        if not asso_id:
            continue
        key = (asso_id, codgeo)
        row_tags = (
            list(matched_tags_per_row[idx])
            if matched_tags_per_row is not None
            else _to_str_list(row.get("matched_tags"))
        )
        merged = tags_by_key.setdefault(key, [])
        for tg in row_tags:
            if tg not in merged:
                merged.append(tg)

        existing = best_by_key.get(key)
        if existing is None or _establishment_preference_key(
            row, reference_date=reference_date
        ) > _establishment_preference_key(existing, reference_date=reference_date):
            best_by_key[key] = row

    return [(best_by_key[k], tags_by_key[k]) for k in best_by_key]


def _row_to_association_detail(
    row: Mapping[str, Any],
    *,
    target_codgeo: str,
    matched_tags: Optional[List[str]] = None,
    commune_names: Optional[Mapping[str, str]] = None,
    reference_date: Optional[date] = None,
    refugee_focused: bool = False,
) -> AssociationDetail:
    """Project a V6 candidate dictionary into an enriched ``AssociationDetail``."""
    name = _format_display_name(
        row.get("name"),
        fallback="Inconnu",
        max_len=_ASSOCIATION_NAME_MAX_LEN,
    )
    raw_etab = _clean_text(row.get("nom_etablissement"))
    etab_name: Optional[str] = None
    if raw_etab and raw_etab.lower() != _clean_text(row.get("name")).lower():
        etab_name = _format_display_name(raw_etab)

    codgeo = _clean_text(row.get("codgeo")).zfill(5) or None
    raw_commune = _clean_text(row.get("commune_nom"))
    if raw_commune:
        commune_nom: Optional[str] = _format_display_name(raw_commune)
    elif commune_names and codgeo and codgeo in commune_names:
        commune_nom = commune_names[codgeo]
    else:
        commune_nom = None
    is_same_commune = codgeo == str(target_codgeo).strip().zfill(5) if codgeo else None
    parsed_date = parse_clamped_decla_date(
        row.get("date_decla"), reference_date=reference_date
    )
    raw_tranche = _clean_text(row.get("tranche_effectifs")) or None
    waldec_label = (
        _clean_text(row.get("waldec_label"))
        or _clean_text(row.get("waldec_code"))
        or "Action Sociale"
    )

    return AssociationDetail(
        id=_clean_text(row.get("id")),
        name=name,
        nom_etablissement=etab_name,
        description=_truncate_description(row.get("description")),
        waldec_label=waldec_label,
        codgeo=codgeo,
        commune_nom=commune_nom,
        is_same_commune=is_same_commune,
        date_decla=parsed_date.isoformat() if parsed_date else None,
        has_employees=has_active_employees(raw_tranche),
        tranche_effectifs=raw_tranche,
        matched_tags=list(matched_tags or []),
        refugee_focused=refugee_focused,
    )


def _rank_deduped_pairs(
    deduped: List[Tuple[Mapping[str, Any], List[str]]],
    *,
    target_codgeo: str,
    reference_date: Optional[date] = None,
) -> List[Tuple[Mapping[str, Any], List[str]]]:
    """Sort deduplicated ``(row, tags)`` pairs by descending unified score then tie-breaks."""

    def _key(pair: Tuple[Mapping[str, Any], List[str]]) -> Tuple[float, bool, bool, str, str]:
        row = pair[0]
        score, same_commune, employees, _, _, _ = score_v6_candidate(
            row, target_codgeo=target_codgeo, reference_date=reference_date
        )
        return (
            -score,
            not same_commune,
            not employees,
            _clean_text(row.get("name")).lower(),
            _clean_text(row.get("id")),
        )

    return sorted(deduped, key=_key)


def build_v6_inclusion_buckets(
    v6_rows: Sequence[Mapping[str, Any]],
    *,
    target_codgeo: str,
    target_bdv: str,
    requested_thematique_slugs: Sequence[str],
    tag_groups: Sequence[Sequence[str]],
    top_n: int = _TOP_N_PER_BUCKET,
    reference_date: Optional[date] = None,
) -> Tuple[Dict[str, List[AssociationDetail]], List[InterestSearchGroupResult]]:
    """Build Top-N ranked V6 buckets for one commune across themes and interest searches.

    Args:
        v6_rows: Raw BigQuery rows across one or more BDVs.
        target_codgeo: Target commune 5-digit INSEE code.
        target_bdv: Normalized Bassin de Vie code of ``target_codgeo``.
        requested_thematique_slugs: Selected Data Inclusion slugs from criteria.
        tag_groups: Structured interest tag groups (one list of tags per Recherche).
        top_n: Cutoff per thematic and per Recherche (default ``10``).
        reference_date: Optional date override for deterministic tests.

    Returns:
        Tuple ``(inclusion_by_theme_label, centres_interet_groups)``.
    """
    bdv_norm = _clean_text(target_bdv).removesuffix(".0")
    bdv_rows = [
        r
        for r in v6_rows
        if _clean_text(r.get("bdv_codeinsee")).removesuffix(".0") == bdv_norm
    ]

    # 1. Resolve ordered requested V6 thematic labels
    requested_labels: List[str] = []
    for slug in requested_thematique_slugs:
        label = resolve_slug_to_v6_label(slug)
        if label and label not in requested_labels:
            requested_labels.append(label)

    inclusion_by_theme: Dict[str, List[AssociationDetail]] = {}
    for theme_label in requested_labels:
        matching_rows = [
            r
            for r in bdv_rows
            if bool(r.get("llm_action_sociale"))
            and _clean_text(r.get("llm_perimetre_action")).lower() != "international"
            and theme_label in _to_str_list(r.get("matched_themes"))
        ]
        if not matching_rows:
            continue
        deduped = _rank_deduped_pairs(
            _dedupe_by_id_and_commune(matching_rows, reference_date=reference_date),
            target_codgeo=target_codgeo,
            reference_date=reference_date,
        )
        inclusion_by_theme[theme_label] = [
            _row_to_association_detail(
                row,
                target_codgeo=target_codgeo,
                reference_date=reference_date,
            )
            for row, _ in deduped[:top_n]
        ]

    # 2. Structured interest searches (keep international perimeter per D3)
    interest_results: List[InterestSearchGroupResult] = []
    for group_tags in tag_groups:
        clean_group_tags = list(
            dict.fromkeys(_clean_text(t) for t in group_tags if _clean_text(t))
        )
        if not clean_group_tags:
            continue
        tag_set = set(clean_group_tags)

        group_rows: List[Mapping[str, Any]] = []
        group_matched_tags: List[List[str]] = []
        for r in bdv_rows:
            row_tags = [
                tg for tg in _to_str_list(r.get("matched_tags")) if tg in tag_set
            ]
            if row_tags:
                group_rows.append(r)
                group_matched_tags.append(row_tags)

        deduped = _rank_deduped_pairs(
            _dedupe_by_id_and_commune(
                group_rows,
                matched_tags_per_row=group_matched_tags,
                reference_date=reference_date,
            ),
            target_codgeo=target_codgeo,
            reference_date=reference_date,
        )
        label = ", ".join(t.capitalize() for t in clean_group_tags[:4])
        if len(clean_group_tags) > 4:
            label = f"{label}, +{len(clean_group_tags) - 4}"

        interest_results.append(
            InterestSearchGroupResult(
                label=label,
                tags=clean_group_tags,
                total_bdv_count=len(deduped),
                associations=[
                    _row_to_association_detail(
                        row,
                        target_codgeo=target_codgeo,
                        matched_tags=tags,
                        reference_date=reference_date,
                    )
                    for row, tags in deduped[:top_n]
                ],
            )
        )

    return inclusion_by_theme, interest_results


def build_refugee_associations_for_bdv(
    refugee_df: Optional[pd.DataFrame],
    *,
    target_codgeo: str,
    target_bdv: str,
    commune_names: Optional[Mapping[str, str]] = None,
    reference_date: Optional[date] = None,
) -> List[AssociationDetail]:
    """Filter local RAM ``refugee_associations_data`` on BDV and rank ``40 + 35 + 30``.

    Returns the full deduplicated list (no hard truncation) so UI/snapshots retain
    all qualified BDV associations and can toggle between Top 10 and full view.
    Gracefully falls back when a legacy 6-column parquet lacks ``tranche_effectifs``
    or ``date_decla``.

    Args:
        refugee_df: Loaded ``odis_refugee_associations.parquet`` DataFrame.
        target_codgeo: Target commune 5-digit INSEE code.
        target_bdv: Bassin de Vie code of ``target_codgeo``.
        commune_names: Optional lookup mapping ``codgeo -> commune_nom``.
        reference_date: Reference date for declaration freshness scoring.

    Returns:
        Full deduplicated list of refugee-support associations across the BDV,
        sorted by unified ``40 + 35 + 30`` score descending.
    """
    if refugee_df is None or not isinstance(refugee_df, pd.DataFrame) or refugee_df.empty:
        return []
    if "bassin_de_vie" not in refugee_df.columns or "codgeo" not in refugee_df.columns:
        return []

    bdv_norm = _clean_text(target_bdv).removesuffix(".0")
    if not bdv_norm:
        return []

    df = refugee_df.copy()
    df["_bv"] = (
        df["bassin_de_vie"]
        .where(df["bassin_de_vie"].notna(), "")
        .astype(str)
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )
    sub = df[df["_bv"] == bdv_norm]
    if sub.empty:
        return []

    target_norm = str(target_codgeo).strip().zfill(5)
    deduped = _dedupe_by_id_and_commune(
        sub.to_dict("records"),
        reference_date=reference_date,
    )
    ranked = _rank_deduped_pairs(
        deduped,
        target_codgeo=target_norm,
        reference_date=reference_date,
    )
    return [
        _row_to_association_detail(
            row,
            target_codgeo=target_norm,
            matched_tags=tags,
            commune_names=commune_names,
            reference_date=reference_date,
            refugee_focused=True,
        )
        for row, tags in ranked
    ]
