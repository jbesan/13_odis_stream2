import pytest
import pandas as pd
import geopandas as gpd
from core import scoring
from core.models import SearchCriterias, CriteriaItem
from pipeline.employment_coverage import METROPOLITAN_DEPARTMENTS


@pytest.fixture
def base_sample_data():
    """Simple 3-row dataset for fast isolated unit tests."""
    data = {
        "codgeo": ["13055", "13001", "64445"],
        "libgeo": ["Marseille", "Aix", "Pau"],
        "dep_code": ["13", "13", "64"],
        "reg_code": ["93", "93", "75"],
        "bassin_de_vie": ["13055", "13001", "64445"],
        "population": [870000, 140000, 77000],
        "epci_code": ["200054807", "200054807", "246401722"],
        "nb_stops_total": [100, 20, 5],
        "sante_hopital_scaled": [0.8, 0.4, 0.1],
    }
    return pd.DataFrame(data).set_index("codgeo")


def test_compute_sante_scores_direct(base_sample_data, live_scores_cat):
    """Direct test to verify _compute_sante_scores is a no-op passthrough leaving the df clean."""
    engine = scoring.ScoringEngine(
        df_all_communes=base_sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
    )

    config = SearchCriterias(
        nb_adultes=1,
        nb_enfants=0,
        besoin_sante=["Hôpital"],
        codes_metiers=[[]],
        codes_formations=[[]],
    )

    df = base_sample_data.copy()
    res = engine._compute_sante_scores(df, config)

    # Verify that the no-op leaves columns intact and does not inject legacy column
    assert "sante_structures_scaled" not in res.columns
    assert res.loc["13055", "sante_hopital_scaled"] == 0.8
    assert res.loc["13001", "sante_hopital_scaled"] == 0.4


def test_compute_territory_scores_direct(base_sample_data, live_scores_cat):
    """Direct test for strategic territory scoring boost."""
    engine = scoring.ScoringEngine(
        df_all_communes=base_sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
    )

    config = SearchCriterias(
        nb_adultes=1, nb_enfants=0, codes_metiers=[[]], codes_formations=[[]]
    )

    # 1. No strategic locations configured
    df1 = base_sample_data.copy()
    res1 = engine._compute_territory_scores(df1, config)
    assert "ter_strategic_locations_scaled" in res1.columns
    assert (res1["ter_strategic_locations_scaled"] == 0.0).all()

    # 2. Strategic department configured to '13' (Bouches-du-Rhône)
    config.org_strategic_locations = ["13"]
    config.org_strategic_locations_type = "departement"
    df2 = base_sample_data.copy()
    res2 = engine._compute_territory_scores(df2, config)

    assert res2.loc["13055", "ter_strategic_locations_scaled"] == 1.0
    assert res2.loc["13001", "ter_strategic_locations_scaled"] == 1.0
    assert res2.loc["64445", "ter_strategic_locations_scaled"] == 0.0


def test_compute_mobility_scores_direct(base_sample_data, live_scores_cat):
    """Direct test for transit stop density and local EPCI matching."""
    engine = scoring.ScoringEngine(
        df_all_communes=base_sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
    )

    # Local search configuration: current location Marseille (13055), searching department '13'
    config = SearchCriterias(
        commune_actuelle=CriteriaItem(code="13055", label="Marseille"),
        loc_search_area="departement",
        loc_search_code=["13"],
        nb_adultes=1,
        nb_enfants=0,
        codes_metiers=[[]],
        codes_formations=[[]],
    )

    df = base_sample_data.copy()
    res = engine._compute_mobility_scores(df, config)

    assert "mob_trans_pub_stop_density" in res.columns
    assert "mob_trans_pub_density_scaled" in res.columns
    assert "mob_epci_scaled" in res.columns

    # Marseille and Aix share EPCI code '200054807'. Pau is '246401722'.
    assert res.loc["13055", "mob_epci_scaled"] == 1.0
    assert res.loc["13001", "mob_epci_scaled"] == 1.0
    assert res.loc["64445", "mob_epci_scaled"] == 0.0


def test_compute_employment_scores_direct(base_sample_data, live_scores_cat):
    """Direct test for ROME-based job opportunities and SIAE matches."""
    live_jobs = pd.DataFrame(
        {
            "commune": ["13055", "13055", "13001"],
            "romeCode": ["K1302", "K1302", "M1805"],
            "total_postes": [5, 2, 1],
            "nb_offres_tension": [2, 1, 0],
        }
    )

    siae_jobs = pd.DataFrame({"codgeo": ["13055"], "rome": ["K1302"]})

    engine = scoring.ScoringEngine(
        df_all_communes=base_sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        live_jobs_data=live_jobs,
        live_jobs_coverage=pd.DataFrame(
            {"department": METROPOLITAN_DEPARTMENTS, "status": "success"}
        ),
        siae_jobs_data=siae_jobs,
        siae_jobs_coverage=pd.DataFrame(
            {"department": METROPOLITAN_DEPARTMENTS, "status": "success"}
        ),
    )

    config = SearchCriterias(
        nb_adultes=1,
        nb_enfants=0,
        codes_metiers=[[CriteriaItem(code="K1302", label="Aide domicile")]],
        codes_formations=[[]],
    )

    df = base_sample_data.copy()
    res = engine._compute_employment_scores(df, config)

    assert "met_match_adult1" in res.columns
    assert "met_match_adult1_tension" in res.columns
    assert "met_siae_match_adult1" in res.columns

    # Marseille (13055) should have 7 jobs (5 + 2) and 3 tension jobs (2 + 1)
    assert res.loc["13055", "met_match_adult1"] == 7.0
    assert res.loc["13055", "met_match_adult1_tension"] == 3.0

    # Marseille has SIAE match for K130 (first 3 chars prefix match)
    assert res.loc["13055", "met_siae_match_adult1"] == 1.0


def test_employment_scores_stay_unavailable_without_complete_coverage(
    base_sample_data, live_scores_cat
):
    engine = scoring.ScoringEngine(
        df_all_communes=base_sample_data,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=live_scores_cat,
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        live_jobs_data=pd.DataFrame(),
        live_jobs_coverage=pd.DataFrame(
            {"department": ["13"], "status": ["success"]}
        ),
    )
    config = SearchCriterias(
        nb_adultes=1,
        nb_enfants=0,
        codes_metiers=[[CriteriaItem(code="K1302", label="Aide domicile")]],
        codes_formations=[[]],
    )

    res = engine._compute_employment_scores(base_sample_data.copy(), config)

    assert "met_match_adult1_scaled" not in res.columns
    assert "met_match_adult1_scaled" in engine._unavailable_runtime_scores
