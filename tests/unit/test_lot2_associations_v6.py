"""Unit tests for Lot 2 V6 deterministic integration (referential, ranking, hydration, context)."""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from agents.context_renderer import format_association, format_criteria, format_model
from core.associations_ranking import (
    build_refugee_associations_for_bdv,
    build_v6_inclusion_buckets,
    compute_freshness_score,
    has_active_employees,
    score_v6_candidate,
)
from core.inclusion_referential import (
    DATA_INCLUSION_THEMATIQUES,
    get_inclusion_thematiques_df,
    normalize_inclusion_label,
    resolve_slug_to_v6_label,
    resolve_v6_label_to_slug,
)
from core.models import (
    AssociationDetail,
    AssociationSearchGroup,
    CommuneResult,
    CriteriaItem,
    InclusionMetrics,
    InterestSearchGroupResult,
    SearchCriterias,
    SearchResultsData,
)
from core.postscoring import (
    hydration_providers,
    prefetch_associations_v6,
    sync_commune_data,
)
from services.rna_rag import RNARagService


def test_inclusion_referential_matches_local_csv_69() -> None:
    """Verify 69/69 parity between app referential and pipeline CSV."""
    csv_path = (
        Path(__file__).resolve().parents[2]
        / "pipeline"
        / "local"
        / "referentiel_services_inclusion.csv"
    )
    with csv_path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert len(DATA_INCLUSION_THEMATIQUES) == 69
    assert len(rows) == 69
    assert {r["Nom"]: r["Label"] for r in rows} == dict(DATA_INCLUSION_THEMATIQUES)

    df = get_inclusion_thematiques_df()
    assert len(df) == 69
    assert df.loc["lecture-ecriture-calcul--maitriser-le-francais", "label"] == (
        "Maîtriser le français"
    )
    assert (
        resolve_slug_to_v6_label("famille--garde-denfants")
        == "Garde d’enfants"
    )
    assert (
        resolve_v6_label_to_slug(normalize_inclusion_label("Garde d'enfants"))
        == "famille--garde-denfants"
    )


def test_v6_unified_ranking_and_date_decla_clamp() -> None:
    """Validate 40+35+30 continuous scoring and future typo date_decla clamping."""
    ref_day = date(2026, 11, 20)

    assert has_active_employees("01") is True
    assert has_active_employees("22") is True
    assert has_active_employees("NN") is False
    assert has_active_employees("00") is False
    assert has_active_employees(None) is False

    # Future typo year 2100 must clamp to ref_day (0 years old => max 30 pts)
    assert compute_freshness_score("2100-01-01", reference_date=ref_day) == pytest.approx(
        30.0
    )
    assert compute_freshness_score("2010-01-01", reference_date=ref_day) == pytest.approx(
        0.0
    )

    # BDV salaried fresh asso (0 + 35 + 30 = 65) beats same-commune unsalaried old asso (40 + 0 + 0 = 40)
    bdv_strong = {
        "id": "W1",
        "codgeo": "33522",
        "tranche_effectifs": "11",
        "date_decla": "2026-06-01",
    }
    local_dormant = {
        "id": "W2",
        "codgeo": "33063",
        "tranche_effectifs": "NN",
        "date_decla": "2012-01-01",
    }
    s_bdv = score_v6_candidate(bdv_strong, target_codgeo="33063", reference_date=ref_day)
    s_local = score_v6_candidate(
        local_dormant, target_codgeo="33063", reference_date=ref_day
    )
    assert s_bdv[0] == pytest.approx(65.0)
    assert s_local[0] == pytest.approx(40.0)
    assert s_bdv[0] > s_local[0]


def test_v6_dedup_by_id_and_commune_keeps_multiple_bdv_antennes() -> None:
    """Duplicate (id, codgeo) merges best SIRET & tags; distinct codgeos stay separate."""
    ref_day = date(2026, 11, 20)
    rows = [
        {
            "id": "W330001",
            "name": "CROIX ROUGE",
            "nom_etablissement": "ANTENNE QUARTIER",
            "codgeo": "33063",
            "commune_nom": "BORDEAUX",
            "bdv_codeinsee": "33063",
            "tranche_effectifs": "NN",
            "is_siege": False,
            "date_decla": "2025-01-01",
            "matched_tags": ["aide alimentaire"],
        },
        {
            "id": "W330001",
            "name": "CROIX ROUGE",
            "nom_etablissement": "DELEGATION BORDEAUX",
            "codgeo": "33063",
            "commune_nom": "BORDEAUX",
            "bdv_codeinsee": "33063",
            "tranche_effectifs": "21",
            "is_siege": True,
            "date_decla": "2026-01-01",
            "matched_tags": ["solidarité"],
        },
        {
            "id": "W330001",
            "name": "CROIX ROUGE",
            "nom_etablissement": "ANTENNE TALENCE",
            "codgeo": "33522",
            "commune_nom": "TALENCE",
            "bdv_codeinsee": "33063",
            "tranche_effectifs": "01",
            "is_siege": False,
            "date_decla": "2026-01-01",
            "matched_tags": ["solidarité"],
        },
    ]

    _, interest_groups = build_v6_inclusion_buckets(
        rows,
        target_codgeo="33063",
        target_bdv="33063",
        requested_thematique_slugs=[],
        tag_groups=[["aide alimentaire", "solidarité"]],
        reference_date=ref_day,
    )
    assert len(interest_groups) == 1
    assos = interest_groups[0].associations
    assert len(assos) == 2
    assert assos[0].codgeo == "33063"
    assert assos[0].nom_etablissement == "Delegation Bordeaux"
    assert assos[0].matched_tags == ["aide alimentaire", "solidarité"]
    assert assos[1].codgeo == "33522"
    assert assos[1].is_same_commune is False


def test_v6_buckets_top10_per_search_and_international_rule() -> None:
    """Keep international tags in Recherches while excluding them from Action sociale; cap at 10 per bucket."""
    ref_day = date(2026, 11, 20)
    rows = [
        {
            "id": "W_AFG",
            "name": "AMICALE AFGHANE",
            "codgeo": "33063",
            "commune_nom": "BORDEAUX",
            "bdv_codeinsee": "33063",
            "tranche_effectifs": "NN",
            "date_decla": "2026-01-01",
            "llm_action_sociale": True,
            "llm_perimetre_action": "international",
            "matched_themes": ["Maîtriser le français"],
            "matched_tags": ["afghanistan"],
        },
        {
            "id": "W_FLE",
            "name": "ATELIER FLE LOCAL",
            "codgeo": "33063",
            "commune_nom": "BORDEAUX",
            "bdv_codeinsee": "33063",
            "tranche_effectifs": "01",
            "date_decla": "2026-01-01",
            "llm_action_sociale": True,
            "llm_perimetre_action": "local",
            "matched_themes": ["Maîtriser le français"],
            "matched_tags": [],
        },
    ] + [
        {
            "id": f"W_VOL_{i}",
            "name": f"VOLLEY CLUB {i:02d}",
            "codgeo": "33063",
            "commune_nom": "BORDEAUX",
            "bdv_codeinsee": "33063",
            "tranche_effectifs": "NN",
            "date_decla": "2025-01-01",
            "llm_action_sociale": False,
            "llm_perimetre_action": "local",
            "matched_themes": [],
            "matched_tags": ["volley ball"],
        }
        for i in range(12)
    ]

    inclusion, interest = build_v6_inclusion_buckets(
        rows,
        target_codgeo="33063",
        target_bdv="33063",
        requested_thematique_slugs=["lecture-ecriture-calcul--maitriser-le-francais"],
        tag_groups=[["afghanistan"], ["volley ball"]],
        top_n=10,
        reference_date=ref_day,
    )

    assert [a.id for a in inclusion["Maîtriser le français"]] == ["W_FLE"]
    assert interest[0].label == "Afghanistan"
    assert [a.id for a in interest[0].associations] == ["W_AFG"]
    assert interest[1].label == "Volley ball"
    assert interest[1].total_bdv_count == 12
    assert len(interest[1].associations) == 10


def test_refugee_bdv_no_cutoff_and_assoce_url_property() -> None:
    """Refugee BDV lookup returns all rows commune-first with safe assoce_url handling."""
    df_refug = pd.DataFrame(
        [
            {
                "id": f"W_{i}" if i % 2 == 0 else f"AMALIA:{i}",
                "codgeo": "33522" if i < 10 else "33063",
                "bassin_de_vie": "33063",
                "name": f"ASSO REFUG {i:02d}",
                "description": "Accueil",
                "waldec_code": "009015",
            }
            for i in range(14)
        ]
    )
    items = build_refugee_associations_for_bdv(
        df_refug,
        target_codgeo="33063",
        target_bdv="33063",
        commune_names={"33063": "Bordeaux", "33522": "Talence"},
    )
    assert len(items) == 14
    assert all(a.is_same_commune for a in items[:4])
    assert not any(a.is_same_commune for a in items[4:])
    assert items[0].commune_nom == "Bordeaux"

    w_asso = AssociationDetail(id="W332001234", name="Test W")
    amalia_asso = AssociationDetail(id="AMALIA:98765", name="Test Amalia")
    assert w_asso.assoce_url == "https://www.assoce.fr/waldec/W332001234"
    assert amalia_asso.assoce_url is None


def test_refugee_bdv_40_35_30_ranking_and_dedupe() -> None:
    """Enriched 10-column refugee rows rank by 40+35+30 and dedupe (id, codgeo)."""
    ref_day = date(2026, 11, 23)
    df_refug = pd.DataFrame(
        [
            {
                "id": "W_OLD_COMMUNE",
                "codgeo": "33063",
                "bassin_de_vie": "33063",
                "name": "AAA ANCIENNE SANS SALARIE",
                "description": "Accueil",
                "waldec_code": "019025",
                "nom_etablissement": None,
                "commune_nom": "BORDEAUX",
                "tranche_effectifs": "NN",
                "date_decla": "2010-01-01",
            },
            {
                "id": "W_STRONG_LOCAL",
                "codgeo": "33063",
                "bassin_de_vie": "33063",
                "name": "ZZZ SINGA BORDEAUX",
                "description": "Accueil",
                "waldec_code": "019025",
                "nom_etablissement": "Antenne Bordeaux",
                "commune_nom": "BORDEAUX",
                "tranche_effectifs": "02",
                "date_decla": "2026-05-01",
            },
            {
                "id": "W_STRONG_LOCAL",
                "codgeo": "33063",
                "bassin_de_vie": "33063",
                "name": "ZZZ SINGA BORDEAUX",
                "description": "Accueil",
                "waldec_code": "019025",
                "nom_etablissement": "Doublon non employe",
                "commune_nom": "BORDEAUX",
                "tranche_effectifs": "NN",
                "date_decla": "2020-01-01",
            },
            {
                "id": "W_BDV_EMP",
                "codgeo": "33522",
                "bassin_de_vie": "33063",
                "name": "COALLIA TALENCE",
                "description": "Hébergement",
                "waldec_code": "019025",
                "nom_etablissement": None,
                "commune_nom": "TALENCE",
                "tranche_effectifs": "41",
                "date_decla": "2026-02-01",
            },
        ]
    )
    items = build_refugee_associations_for_bdv(
        df_refug,
        target_codgeo="33063",
        target_bdv="33063",
        reference_date=ref_day,
    )
    assert [a.id for a in items] == ["W_STRONG_LOCAL", "W_BDV_EMP", "W_OLD_COMMUNE"]
    assert items[0].has_employees is True
    assert items[0].nom_etablissement == "Antenne Bordeaux"
    assert items[0].commune_nom == "Bordeaux"
    assert items[0].refugee_focused is True
    assert items[1].is_same_commune is False
    assert items[2].has_employees is False


def test_long_association_name_truncated_to_80_chars() -> None:
    """Full RNA association names exceeding 80 chars truncate cleanly with ellipsis."""
    long_title = (
        "ASSOCIATION POUR L'ACCOMPAGNEMENT ET L'INTEGRATION DES PERSONNES "
        "EXILEES ET DEMANDEUSES D'ASILE EN LOIRE"
    )
    df_refug = pd.DataFrame(
        [
            {
                "id": "W423099999",
                "codgeo": "42218",
                "bassin_de_vie": "42218",
                "name": long_title,
                "description": "Accueil",
                "waldec_code": "019025",
                "nom_etablissement": None,
                "commune_nom": "SAINT-ETIENNE",
                "tranche_effectifs": "02",
                "date_decla": "2026-06-01",
            }
        ]
    )
    items = build_refugee_associations_for_bdv(
        df_refug, target_codgeo="42218", target_bdv="42218"
    )
    assert len(items) == 1
    assert len(items[0].name) <= 80
    assert items[0].name.endswith("...")


def test_prefetch_v6_short_circuits_bq_and_syncs_groups() -> None:
    """Zero-criteria searches skip BQ while hydrating RAM refugee data; criteria route cleanly."""
    engine = MagicMock()
    engine._associations_cache = {}
    engine.rna_rag_service = MagicMock()
    engine.df_all_communes = pd.DataFrame(
        {"bassin_de_vie": ["33063"], "libgeo": ["Bordeaux"]},
        index=pd.Index(["33063"], name="codgeo"),
    )
    engine.refugee_associations_data = pd.DataFrame(
        [
            {
                "id": "W_REF",
                "codgeo": "33063",
                "bassin_de_vie": "33063",
                "name": "ENTRAIDE EXILES",
                "description": "Accompagnement",
                "waldec_code": "009015",
            }
        ]
    )

    out_no_criteria = prefetch_associations_v6(
        engine, ["33063"], thematique_slugs=[], tag_groups=[]
    )
    engine.rna_rag_service.get_associations_v6_bdv.assert_not_called()
    assert len(out_no_criteria["33063"]["refugee"]) == 1

    # Sync checks: even when asso_inclusion_list_by_cat is empty, centres_interet_groups syncs
    commune = CommuneResult(codgeo="33063", name="Bordeaux")
    bg_store = {
        "enrichment": {
            "33063": {
                "refugee": out_no_criteria["33063"]["refugee"],
                "inclusion": {},
                "centres_interet_groups": [
                    {
                        "label": "Afghanistan",
                        "tags": ["afghanistan"],
                        "total_bdv_count": 1,
                        "associations": [
                            {
                                "id": "W_AFG",
                                "name": "Amicale Afghane",
                                "is_same_commune": True,
                                "commune_nom": "Bordeaux",
                                "matched_tags": ["afghanistan"],
                            }
                        ],
                    }
                ],
            }
        }
    }
    sync_commune_data(commune, bg_store)
    assert commune.inclusion.asso_refugee_count == 1
    assert len(commune.inclusion.asso_centres_interet_groups) == 1
    assert commune.inclusion.asso_centres_interet_count == 1

    # Verify hydration_providers passes configured slugs and groups
    cfg = SearchCriterias(
        commune_actuelle=CriteriaItem(code="33063", label="Bordeaux"),
        inc_services_selection=[
            CriteriaItem(
                code="lecture-ecriture-calcul--maitriser-le-francais",
                label="Maîtriser le français",
            )
        ],
        inc_asso_add_groups=[AssociationSearchGroup(tags=["afghanistan", "culture afghane"])],
    )
    providers = hydration_providers(engine, cfg, SearchResultsData(results=[commune]), is_ai_free=True)
    assoc_provider = next(p for p in providers if p.name == "associations")
    assert assoc_provider.fetch.keywords["use_v6"] is True
    assert assoc_provider.fetch.keywords["tag_groups"] == [
        ["afghanistan", "culture afghane"]
    ]
    assert assoc_provider.fetch.keywords["thematique_slugs"] == [
        "lecture-ecriture-calcul--maitriser-le-francais"
    ]


def test_context_renderer_and_bq_v6_sql(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate compact context rendering for groups/badges and V6 BQ query parameters."""
    cfg = SearchCriterias(
        commune_actuelle=CriteriaItem(code="33063", label="Bordeaux"),
        inc_asso_add_groups=[
            AssociationSearchGroup(tags=["afghanistan", "culture afghane"])
        ],
        inc_asso_add_selection=[CriteriaItem(code="afghanistan", label="Afghanistan")],
    )
    criteria_ctx = format_criteria(cfg)
    assert "Recherches structurées" in str(criteria_ctx) or [
        ["Afghanistan", "Culture afghane"]
    ] in criteria_ctx.values()

    legacy = AssociationDetail(id="W1", name="Test", description="Desc")
    assert format_association(legacy) == "W1 | Test | Desc"

    v6_asso = AssociationDetail(
        id="AMALIA:42",
        name="Club Talence",
        nom_etablissement="Antenne Sud",
        commune_nom="Talence",
        is_same_commune=False,
        matched_tags=["afghanistan"],
        description="Culture",
    )
    assert (
        format_association(v6_asso)
        == "AMALIA:42 | Club Talence (Antenne Sud) [Talence · afghanistan] | Culture"
    )

    inc_model = InclusionMetrics(
        asso_centres_interet_groups=[
            InterestSearchGroupResult(
                label="Afghanistan",
                tags=["afghanistan"],
                total_bdv_count=12,
                associations=[v6_asso],
            )
        ],
        asso_centres_interet_count=1,
    )
    rendered = format_model(inc_model)
    assert (
        "Nombre total d'associations retournées dans les recherches de centres d'intérêt"
        not in rendered
    )
    grp_serialized = rendered[
        "Associations BDV groupées par recherche de besoins spécifiques et centres d'intérêt"
    ]
    assert grp_serialized[0]["total_bdv"] == 12
    assert "associations" not in grp_serialized[0]
    assert len(grp_serialized[0]["top_associations_affichees"]) == 1

    monkeypatch.setenv("ODIS_DATA_PROJECT", "proj-test")
    with (
        patch("services.rna_rag.bigquery.Client") as mock_bq_cls,
        patch("services.rna_rag.get_gemini_client"),
    ):
        mock_client = mock_bq_cls.return_value
        mock_client.query.return_value.to_dataframe.return_value = pd.DataFrame(
            [{"id": "W1", "name": "Test"}]
        )
        svc = RNARagService()
        res = svc.get_associations_v6_bdv(["33063"], ["Maîtriser le français"], ["volley ball"])
        assert len(res) == 1
        sql_sent, kwargs = mock_client.query.call_args[0][0], mock_client.query.call_args[1]
        assert "national_v6_consolidated" in sql_sent
        assert "etat_admin IS NULL OR etat_admin != 'F'" in sql_sent
        assert kwargs["location"] == "europe-west1"
