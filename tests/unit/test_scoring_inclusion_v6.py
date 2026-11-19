"""Unit tests for Commun RNA V6 inclusion & leisure scoring (BdV + tiers + UI details)."""

from __future__ import annotations

import pandas as pd
import pytest

from core import scoring
from core.models import CriteriaItem, SearchCriterias


def _build_fixture_engine(live_scores_cat):
    df_all = pd.DataFrame(
        {
            "codgeo": ["33001", "33063", "64445"],
            "libgeo": ["Village Rural", "Bordeaux", "Pau"],
            "dep_code": ["33", "33", "64"],
            "reg_code": ["75", "75", "75"],
            "bassin_de_vie": ["BV_BDX", "BV_BDX", "BV_PAU"],
            "population": [500, 260000, 77000],
            "epci_code": ["EP1", "EP1", "EP2"],
            "inc_asso_core_scaled": [0.4, 0.8, 0.6],
            "inc_asso_refug_scaled": [0.2, 0.9, 0.5],
            "inc_siae_density_scaled": [0.1, 0.7, 0.4],
            "met_scaled": [0.5, 0.5, 0.5],
            "log_vac_scaled": [0.5, 0.5, 0.5],
            "log_soc_inoc_scaled": [0.5, 0.5, 0.5],
            "sante_hopital_scaled": [0.5, 0.5, 0.5],
            "mob_trans_pub_stop_scaled": [0.5, 0.5, 0.5],
        }
    ).set_index("codgeo")

    incl_index = pd.DataFrame(
        {
            "codgeo": ["33063", "64445"],
            "key": [
                {"lecture-ecriture-calcul--maitriser-le-francais"},
                {"logement--etre-loge"},
            ],
        }
    ).set_index("codgeo")

    asso_thematiques = pd.DataFrame(
        {
            "grain": ["commune", "commune", "bdv", "bdv"],
            "geo_code": ["33063", "64445", "BV_BDX", "BV_PAU"],
            "thematique_slug": [
                "lecture-ecriture-calcul--maitriser-le-francais",
                "logement--etre-loge",
                "lecture-ecriture-calcul--maitriser-le-francais",
                "logement--etre-loge",
            ],
            "count": [3, 1, 6, 2],
        }
    )

    asso_tags_bdv = pd.DataFrame(
        {
            "bassin_de_vie": ["BV_BDX", "BV_BDX", "BV_PAU"],
            "tag_canonique": ["volley ball", "chant choral", "volley ball"],
            "count": [2, 1, 1],
        }
    )

    engine = scoring.ScoringEngine(
        df_all_communes=df_all,
        df_bv_geo=pd.DataFrame(),
        scores_cat=live_scores_cat,
        incl_index=incl_index,
        asso_thematiques_data=asso_thematiques,
        asso_tags_bdv_data=asso_tags_bdv,
    )
    return engine, df_all


def test_di_services_bdv_boost_and_social_assos_graduated_tiers(live_scores_cat):
    engine, df_all = _build_fixture_engine(live_scores_cat)
    cfg = SearchCriterias(
        commune_actuelle=CriteriaItem(code="64445", label="Pau"),
        loc_search_area="region",
        loc_search_code=["75"],
        inc_services_selection=[
            CriteriaItem(
                code="lecture-ecriture-calcul--maitriser-le-francais",
                label="Maîtriser le français",
            )
        ],
        inc_asso_add_selection=[],
    )

    scored = engine.run(cfg)

    # Village Rural (33001): 0 DI in commune, 100% DI in BdV BV_BDX -> 0 + 1*(1.0*0.8) = 0.8
    assert scored.loc["33001", "inc_services_incl_scaled"] == pytest.approx(0.8)
    # Pau (64445): 0 DI matching 'francais' in commune or BV_PAU -> 0.0
    assert scored.loc["64445", "inc_services_incl_scaled"] == pytest.approx(0.0)

    # Village Rural: 0 assos in commune -> tier 0.0; BV_BDX has 6 assos -> tier 1.0 -> combined 0.8
    assert scored.loc["33001", "inc_asso_thematiques_scaled"] == pytest.approx(0.8)
    # Bordeaux: 3 assos in commune -> tier 1.0 -> combined 1.0
    assert scored.loc["33063", "inc_asso_thematiques_scaled"] == pytest.approx(1.0)
    # Pau: 0 assos matching 'francais' -> 0.0
    assert scored.loc["64445", "inc_asso_thematiques_scaled"] == pytest.approx(0.0)

    # Verify UI details expose uncombined commune vs BDV percentages
    details = engine.format_city_details(scored.loc["33001"], cfg)
    incl_map = {item.score_id: item for item in details.scores["inclusion"]}
    assert incl_map["inc_services_incl_scaled"].bdv_applied is True
    assert incl_map["inc_services_incl_scaled"].score_normalise_commune == pytest.approx(0.0)
    assert incl_map["inc_services_incl_scaled"].score_normalise_bdv == pytest.approx(1.0)
    assert str(incl_map["inc_services_incl_scaled"].valeur_kpi_commune).startswith("0.0")
    assert str(incl_map["inc_services_incl_scaled"].valeur_kpi_bdv).startswith("100.0")
    assert incl_map["inc_asso_thematiques_scaled"].bdv_applied is True
    assert incl_map["inc_asso_thematiques_scaled"].score_normalise_commune == pytest.approx(0.0)
    assert incl_map["inc_asso_thematiques_scaled"].score_normalise_bdv == pytest.approx(1.0)


def test_leisure_tags_pure_bdv_graduated_scoring_and_details(live_scores_cat):
    engine, _ = _build_fixture_engine(live_scores_cat)
    cfg = SearchCriterias(
        commune_actuelle=CriteriaItem(code="64445", label="Pau"),
        loc_search_area="region",
        loc_search_code=["75"],
        inc_services_selection=[],
        inc_asso_add_selection=[
            CriteriaItem(code="volley ball", label="Volley ball"),
            CriteriaItem(code="chant choral", label="Chant choral"),
            CriteriaItem(code="006030", label="Legacy Code Ignored"),
        ],
    )

    scored = engine.run(cfg)
    # BV_BDX has volley ball (count=2 -> 1.0) + chant choral (count=1 -> 0.7) => 0.85
    assert scored.loc["33001", "inc_asso_add_scaled"] == pytest.approx(0.85)
    # BV_PAU has volley ball (count=1 -> 0.7) + chant choral (0 -> 0.0) => 0.35
    assert scored.loc["64445", "inc_asso_add_scaled"] == pytest.approx(0.35)
    # No commune-grain uncombined column must exist for pure-BDV leisure tags
    assert "inc_asso_add_scaled_uncombined_commune" not in scored.columns
    assert "inc_services_incl_scaled" not in scored.columns
    assert "inc_asso_thematiques_scaled" not in scored.columns

    details = engine.format_city_details(scored.loc["33001"], cfg)
    incl_map = {item.score_id: item for item in details.scores["inclusion"]}
    leisure = incl_map["inc_asso_add_scaled"]
    assert leisure.bdv_applied is True
    assert leisure.score_normalise_commune is None
    assert leisure.valeur_kpi_commune is None
    assert leisure.score_normalise_bdv == pytest.approx(0.85)
    assert leisure.valeur_kpi_bdv == "85"
