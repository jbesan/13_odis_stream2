import pytest
import numpy as np
from utils import data_loader
from core.scoring import ScoringEngine
from core.models import SearchCriterias


@pytest.mark.unit
def test_vacant_housing_criterion():
    """
    Verifies that the vacant housing criterion uses the new structural vacancy data.
    """
    # 1. Load Data
    try:
        app_data = data_loader.load_all_data_raw()
    except Exception as e:
        pytest.fail(f"Data loading failed: {e}")

    odis = app_data["odis"]
    scores_cat = app_data["scores_cat"]
    incl_index = app_data["incl_index"]
    associations_data = app_data["associations_data"]

    # 2. Verify Columns
    assert "log_priv_vacant_plus_2ans" in odis.columns, (
        "LOVAC column 'log_priv_vacant_plus_2ans' missing"
    )
    assert "log_vac_struct_ratio" in odis.columns, (
        "Calculated ratio 'log_vac_struct_ratio' missing"
    )

    # 3. Verify Data Integrity (Check a known sample: Ambérieu-en-Bugey 01004)
    if "01004" in odis.index:
        sample = odis.loc["01004"]
        assert sample["log_priv_vacant_plus_2ans"] == 207.0
        # Ensure log_total exists for ratio check
        if "log_total" in sample:
            expected_ratio = sample["log_priv_vacant_plus_2ans"] / sample["log_total"]
            np.testing.assert_almost_equal(
                sample["log_vac_struct_ratio"], expected_ratio, decimal=5
            )

    # 4. Verify Scoring Logic
    config = SearchCriterias(
        poids_emploi=1.0,
        poids_logement=1.0,
        poids_education=0.0,
        poids_inclusion=0.0,
        poids_sante=0.0,
        poids_mobilite=0.0,
        commune_actuelle="01004",
        loc_search_area="departement",
        nb_adultes=1,
        nb_enfants=0,
        hebergement_cible=[],
        logement="Location",
        codes_metiers=[[]],
        codes_formations=[[]],
        classe_enfants=[],
        besoin_sante=[],
        inc_services_selection=[],
        inc_asso_add_selection=[],
        criteria_weights={},
    )

    # Instantiate Engine
    engine = ScoringEngine(
        df_all_communes=odis,
        df_bv_geo=app_data["bv_geo"],
        scores_cat=scores_cat,
        incl_index=incl_index,
        associations_data=app_data["associations_data"],
        formations_data=app_data["formations_data"],
        codformations_index=app_data.get("codformations_index"),
        global_stats=app_data.get("global_stats", {}),
    )

    # Add dummy dist_current_loc to avoid KeyError if needed (Engine.run normally adds it)
    # But here we test the internal _compute_criteria_scores
    odis_copy = odis.copy()
    odis_copy["dist_current_loc"] = 0.0

    odis_scored = engine._compute_criteria_scores(odis_copy, config)

    assert "log_vac_scaled" in odis_scored.columns
    assert odis_scored["log_vac_scaled"].notna().all()

    # Check correlation
    valid_data = odis_scored[["log_vac_struct_ratio", "log_vac_scaled"]].dropna()
    if not valid_data.empty:
        correlation = valid_data["log_vac_struct_ratio"].corr(
            valid_data["log_vac_scaled"]
        )
        assert correlation > 0.9


if __name__ == "__main__":
    test_vacant_housing_criterion()
