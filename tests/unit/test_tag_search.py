"""Unit tests for the local Commun RNA V6 canonical tag search engine."""

from __future__ import annotations

import pandas as pd

from app.ui.tag_search import TagSearchIndex, normalize_tag_query


def _sample_ref_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "tag_canonique": [
                "football",
                "tennis",
                "chant choral",
                "volley ball",
                "theatre",
                "petanque",
                "volley loisir",
                "tennis de table",
            ],
            "occ_national": [
                28000,
                12000,
                14500,
                3200,
                18000,
                11000,
                250,
                4200,
            ],
            "exemples_bruts": [
                ["football", "foot"],
                ["tennis"],
                ["chorale", "chant choral"],
                ["volleyball", "volley-ball"],
                ["théâtre", "art dramatique"],
                ["pétanque", "boules"],
                ["volley detente"],
                ["ping pong", "tennis de table"],
            ],
        }
    )


def test_normalize_tag_query_folds_accents_and_punctuation():
    assert normalize_tag_query("  Volley-Ball  ") == "volley ball"
    assert normalize_tag_query("Théâtre d'Improvisation") == "theatre d improvisation"
    assert normalize_tag_query("") == ""


def test_search_ranks_exact_then_prefix_then_substring_by_occurrence():
    index = TagSearchIndex(_sample_ref_df())

    res = index.search("tennis")
    tags = [m.tag for m in res.matches]
    assert tags == ["tennis", "tennis de table"]
    assert res.matches[0].match_tier == "exact"
    assert res.matches[1].match_tier == "prefix"
    assert res.is_fuzzy_fallback is False

    volley = index.search("volley")
    assert [m.tag for m in volley.matches] == ["volley ball", "volley loisir"]


def test_search_matches_raw_variants_in_tier2():
    index = TagSearchIndex(_sample_ref_df())

    res = index.search("ping pong")
    assert len(res.matches) == 1
    assert res.matches[0].tag == "tennis de table"
    assert res.matches[0].match_tier == "variant"
    assert res.matches[0].matched_variant == "ping pong"
    assert res.is_fuzzy_fallback is False

    chorale = index.search("chorale")
    assert [m.tag for m in chorale.matches] == ["chant choral"]


def test_search_fuzzy_fallback_recovers_typos():
    index = TagSearchIndex(_sample_ref_df())

    res = index.search("voley")
    assert res.is_fuzzy_fallback is True
    assert any(m.tag == "volley ball" for m in res.matches)

    gibberish = index.search("zzzzqxj")
    assert gibberish.matches == []
    assert gibberish.is_fuzzy_fallback is False


def test_search_respects_exclude_and_short_queries_and_popular():
    index = TagSearchIndex(_sample_ref_df())

    assert index.search("v").matches == []
    assert index.search("tennis", exclude={"tennis"}).matches[0].tag == "tennis de table"

    pop = index.popular(limit=3, exclude={"football"})
    assert [m.tag for m in pop] == ["theatre", "chant choral", "tennis"]
    assert index.get_count("chant choral") == 14500
    assert index.get_count("inexistant") is None
