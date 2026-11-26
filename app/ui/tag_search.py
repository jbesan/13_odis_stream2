"""Local high-speed canonical tag search engine for Commun RNA V6 leisure affinities."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import difflib
import re
from typing import Collection, Dict, List, Literal, Optional, Set, Tuple
import unicodedata

import pandas as pd
from pydantic import BaseModel, Field

_PUNCT_RE = re.compile(r"[-'’`\"_/\\.,;:!?()\[\]{}]+")


def normalize_tag_query(text: str) -> str:
    """Normalize user queries and tags for accent- and punctuation-insensitive lookup.

    Args:
        text: Raw user query or canonical tag string.

    Returns:
        Lowercased, ASCII-folded, whitespace-collapsed string.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", str(text))
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    cleaned = _PUNCT_RE.sub(" ", stripped.lower())
    return " ".join(cleaned.split())


class TagSearchMatch(BaseModel):
    """Single canonical tag candidate returned by ``TagSearchIndex``."""

    tag: str = Field(..., description="Canonical tag label (``tag_canonique``).")
    occ_national: int = Field(..., ge=0, description="National RNA V6 occurrence count.")
    match_tier: Literal["exact", "prefix", "substring", "variant", "fuzzy"] = Field(
        ..., description="Matching tier that surfaced this candidate."
    )
    matched_variant: Optional[str] = Field(
        default=None,
        description="Raw variant from ``exemples_bruts`` when matched via Tier 2.",
    )


class TagSearchResult(BaseModel):
    """Structured search response from ``TagSearchIndex.search``."""

    query: str = Field(..., description="Original raw user query.")
    normalized_query: str = Field(..., description="Normalized query string.")
    matches: List[TagSearchMatch] = Field(default_factory=list)
    is_fuzzy_fallback: bool = Field(
        default=False,
        description="True when only Tier 3 typo recovery returned candidates.",
    )


@dataclass(frozen=True)
class _IndexedTagEntry:
    """Pre-normalized in-memory record sorted by descending national occurrences."""

    tag: str
    occ_national: int
    norm_tag: str
    tokens: Tuple[str, ...]
    variants: Tuple[Tuple[str, str], ...]


def _extract_variants(raw_variants: object) -> Tuple[Tuple[str, str], ...]:
    """Normalize raw ``exemples_bruts`` sequences into ``(raw, norm)`` tuples."""
    if raw_variants is None:
        return ()
    if isinstance(raw_variants, str):
        items = [raw_variants]
    elif isinstance(raw_variants, Iterable):
        items = [str(v) for v in raw_variants if v is not None]
    else:
        return ()

    seen: Set[str] = set()
    parsed: List[Tuple[str, str]] = []
    for raw in items:
        raw_clean = raw.strip()
        norm = normalize_tag_query(raw_clean)
        if raw_clean and norm and norm not in seen:
            seen.add(norm)
            parsed.append((raw_clean, norm))
    return tuple(parsed)


class TagSearchIndex:
    """Three-tier in-memory search index over ``odis_association_tags_ref.parquet``.

    Supports sub-10ms interactive filtering in Streamlit ``@st.fragment`` components:
    1. Canonical exact / prefix / substring matches ordered by ``occ_national`` DESC.
    2. Raw variant matches over ``exemples_bruts`` when Tier 1 does not fill ``limit``.
    3. Typo-tolerant ``difflib`` fallback (cutoff ``0.75``) only when Tiers 1+2 yield 0 hits.
    """

    def __init__(self, ref_df: Optional[pd.DataFrame]) -> None:
        """Build search index from ``referentiel_tags_canoniques_n50`` rows.

        Args:
            ref_df: DataFrame with ``tag_canonique``, ``occ_national``, and optional
                ``exemples_bruts`` columns.
        """
        self._entries: List[_IndexedTagEntry] = []
        self._counts: Dict[str, int] = {}
        self._norm_tag_to_idx: Dict[str, int] = {}
        self._token_to_indices: Dict[str, List[int]] = {}

        if ref_df is None or ref_df.empty or "tag_canonique" not in ref_df.columns:
            self._fuzzy_vocab: List[str] = []
            return

        work = ref_df.copy()
        if "occ_national" not in work.columns:
            work["occ_national"] = 0
        work["tag_canonique"] = work["tag_canonique"].astype(str).str.strip()
        work = work[work["tag_canonique"] != ""]
        work["occ_national"] = (
            pd.to_numeric(work["occ_national"], errors="coerce").fillna(0).astype(int)
        )
        work = work.drop_duplicates(subset=["tag_canonique"], keep="first")
        work = work.sort_values(
            by=["occ_national", "tag_canonique"], ascending=[False, True]
        )

        has_variants = "exemples_bruts" in work.columns
        for idx, row in enumerate(work.itertuples(index=False)):
            tag = str(getattr(row, "tag_canonique"))
            occ = int(getattr(row, "occ_national"))
            norm_tag = normalize_tag_query(tag)
            tokens = tuple(dict.fromkeys(norm_tag.split())) if norm_tag else ()
            raw_vars = getattr(row, "exemples_bruts") if has_variants else None
            variants = tuple(
                pair for pair in _extract_variants(raw_vars) if pair[1] != norm_tag
            )
            entry = _IndexedTagEntry(
                tag=tag,
                occ_national=occ,
                norm_tag=norm_tag,
                tokens=tokens,
                variants=variants,
            )
            self._entries.append(entry)
            self._counts[tag] = occ
            if norm_tag and norm_tag not in self._norm_tag_to_idx:
                self._norm_tag_to_idx[norm_tag] = idx
            for tok in tokens:
                if len(tok) >= 3:
                    self._token_to_indices.setdefault(tok, []).append(idx)

        self._fuzzy_vocab = list(
            dict.fromkeys(
                [k for k in self._norm_tag_to_idx] + list(self._token_to_indices.keys())
            )
        )

    @property
    def is_empty(self) -> bool:
        """Return True when the index holds no canonical tags."""
        return len(self._entries) == 0

    def get_count(self, tag: str) -> Optional[int]:
        """Look up national association count for a canonical tag."""
        return self._counts.get(tag)

    def popular(
        self,
        limit: int = 12,
        exclude: Optional[Collection[str]] = None,
    ) -> List[TagSearchMatch]:
        """Return the most frequent canonical tags nationwide.

        Args:
            limit: Maximum number of tags to return.
            exclude: Optional collection of already-selected canonical tags to omit.

        Returns:
            List of popular ``TagSearchMatch`` items sorted by ``occ_national`` DESC.
        """
        excluded = set(exclude or ())
        results: List[TagSearchMatch] = []
        for entry in self._entries:
            if entry.tag in excluded:
                continue
            results.append(
                TagSearchMatch(
                    tag=entry.tag,
                    occ_national=entry.occ_national,
                    match_tier="prefix",
                )
            )
            if len(results) >= limit:
                break
        return results

    def search(
        self,
        query: str,
        limit: int = 12,
        exclude: Optional[Collection[str]] = None,
    ) -> TagSearchResult:
        """Search canonical tags across exact/prefix/substring, variants, and fuzzy fallback.

        Args:
            query: Raw search string typed by the user.
            limit: Maximum number of matches returned.
            exclude: Optional collection of canonical tags to omit (e.g. already selected).

        Returns:
            A ``TagSearchResult`` containing ranked ``TagSearchMatch`` items.
        """
        norm_q = normalize_tag_query(query)
        if not norm_q or len(norm_q) < 2 or self.is_empty:
            return TagSearchResult(query=query, normalized_query=norm_q)

        excluded = set(exclude or ())
        exact_hits: List[TagSearchMatch] = []
        prefix_hits: List[TagSearchMatch] = []
        sub_hits: List[TagSearchMatch] = []
        matched_tags: Set[str] = set()

        # Tier 1: Canonical tag matching (entries already sorted by occ_national DESC)
        for entry in self._entries:
            if entry.tag in excluded:
                continue
            norm_tag = entry.norm_tag
            if norm_tag == norm_q:
                exact_hits.append(
                    TagSearchMatch(
                        tag=entry.tag,
                        occ_national=entry.occ_national,
                        match_tier="exact",
                    )
                )
                matched_tags.add(entry.tag)
            elif norm_tag.startswith(norm_q) or any(
                tok.startswith(norm_q) for tok in entry.tokens
            ):
                prefix_hits.append(
                    TagSearchMatch(
                        tag=entry.tag,
                        occ_national=entry.occ_national,
                        match_tier="prefix",
                    )
                )
                matched_tags.add(entry.tag)
            elif norm_q in norm_tag:
                sub_hits.append(
                    TagSearchMatch(
                        tag=entry.tag,
                        occ_national=entry.occ_national,
                        match_tier="substring",
                    )
                )
                matched_tags.add(entry.tag)

        tier1 = exact_hits + prefix_hits + sub_hits
        if len(tier1) >= limit:
            return TagSearchResult(
                query=query,
                normalized_query=norm_q,
                matches=tier1[:limit],
            )

        # Tier 2: Merged raw variants (exemples_bruts) when Tier 1 leaves room
        variant_prefix: List[TagSearchMatch] = []
        variant_sub: List[TagSearchMatch] = []
        for entry in self._entries:
            if entry.tag in excluded or entry.tag in matched_tags or not entry.variants:
                continue
            matched_raw: Optional[str] = None
            is_var_prefix = False
            for raw_var, norm_var in entry.variants:
                if norm_var.startswith(norm_q):
                    matched_raw = raw_var
                    is_var_prefix = True
                    break
                if matched_raw is None and norm_q in norm_var:
                    matched_raw = raw_var
            if matched_raw is not None:
                item = TagSearchMatch(
                    tag=entry.tag,
                    occ_national=entry.occ_national,
                    match_tier="variant",
                    matched_variant=matched_raw,
                )
                if is_var_prefix:
                    variant_prefix.append(item)
                else:
                    variant_sub.append(item)
                matched_tags.add(entry.tag)

        combined = tier1 + variant_prefix + variant_sub
        if combined:
            return TagSearchResult(
                query=query,
                normalized_query=norm_q,
                matches=combined[:limit],
            )

        # Tier 3: Typo recovery only when Tiers 1+2 returned zero matches
        fuzzy_matches = self._fuzzy_search(norm_q, limit=limit, excluded=excluded)
        return TagSearchResult(
            query=query,
            normalized_query=norm_q,
            matches=fuzzy_matches,
            is_fuzzy_fallback=bool(fuzzy_matches),
        )

    def _fuzzy_search(
        self, norm_q: str, limit: int, excluded: Set[str]
    ) -> List[TagSearchMatch]:
        """Run difflib close-match recovery over indexed tags and tokens."""
        if len(norm_q) < 3 or not self._fuzzy_vocab:
            return []

        close_terms = difflib.get_close_matches(
            norm_q, self._fuzzy_vocab, n=limit * 2, cutoff=0.75
        )
        if not close_terms:
            return []

        candidate_indices: Set[int] = set()
        for term in close_terms:
            if term in self._norm_tag_to_idx:
                candidate_indices.add(self._norm_tag_to_idx[term])
            for idx in self._token_to_indices.get(term, ()):
                candidate_indices.add(idx)

        ordered_entries = sorted(
            (self._entries[idx] for idx in candidate_indices),
            key=lambda e: (-e.occ_national, e.tag),
        )
        results: List[TagSearchMatch] = []
        for entry in ordered_entries:
            if entry.tag in excluded:
                continue
            results.append(
                TagSearchMatch(
                    tag=entry.tag,
                    occ_national=entry.occ_national,
                    match_tier="fuzzy",
                )
            )
            if len(results) >= limit:
                break
        return results
