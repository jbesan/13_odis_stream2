"""Unit tests for P1-06: Effective weight calculation consistency, priority mappings, and criteria sanitization."""

import pytest
from app.core.models import SearchCriterias
from app.core.scoring import get_effective_weight, ScoringEngine


@pytest.mark.unit
class TestEffectiveWeightsAndMappings:
    """Test suite for weight consistency and priority mappings."""

    def test_proximity_weight_single_source_of_truth(self):
        """Verify proximity multipliers are calculated solely by get_effective_weight without double-counting."""
        # Weekly
        config_weekly = SearchCriterias(
            dept_code="33",
            freq_retour="1 fois/semaine",
        )
        w_weekly = get_effective_weight(
            "mob_dist_current_loc_scaled", config_weekly, catalog_weight=1.0
        )
        assert abs(w_weekly - 3.0) < 1e-6

        # Monthly
        config_monthly = SearchCriterias(
            dept_code="33",
            freq_retour="1 fois/mois",
        )
        w_monthly = get_effective_weight(
            "mob_dist_current_loc_scaled", config_monthly, catalog_weight=1.0
        )
        assert abs(w_monthly - 2.0) < 1e-6

        # Annual
        config_annual = SearchCriterias(
            dept_code="33",
            freq_retour="1 fois/an",
        )
        w_annual = get_effective_weight(
            "mob_dist_current_loc_scaled", config_annual, catalog_weight=1.0
        )
        assert abs(w_annual - 1.0) < 1e-6

        # No attachment
        config_none = SearchCriterias(
            dept_code="33",
            freq_retour="Pas d'attache particulière",
        )
        w_none = get_effective_weight(
            "mob_dist_current_loc_scaled", config_none, catalog_weight=1.0
        )
        assert abs(w_none - 0.0) < 1e-6

    def test_log_soc_delay_activation(
        self, sample_data, live_scores_cat, sample_incl_index, global_stats
    ):
        """Verify that log_soc_delay_scaled is activated when Logement Social is selected."""
        import geopandas as gpd
        import pandas as pd

        engine = ScoringEngine(
            df_all_communes=sample_data,
            df_bv_geo=gpd.GeoDataFrame(),
            scores_cat=live_scores_cat,
            incl_index=sample_incl_index,
            associations_data=pd.DataFrame(columns=["codgeo", "id_waldec", "count"]),
            formations_data=pd.DataFrame(columns=["codgeo", "formation_code"]),
            codformations_index=pd.DataFrame(columns=["label"]),
            global_stats=global_stats,
        )
        config = SearchCriterias(
            dept_code="33",
            logement="Logement Social",
        )
        active_criteria = engine._get_active_criteria(config)
        assert "log_soc_delay_scaled" in active_criteria
        assert "log_soc_inoc_scaled" in active_criteria
        assert "log_soc_dem_scaled" not in active_criteria

    def test_criteria_weights_sanitization(self):
        """Verify that unknown/non-existent criteria IDs are stripped from criteria_weights with a warning."""
        config = SearchCriterias(
            dept_code="33",
            criteria_weights={
                "log_loyer_moyen_appt_all_scaled": 2.5,
                "heb_chrs_scaled": 3.0,
                "non_existent_criterion_id": 5.0,
                "heb_centres_heb_scaled": 3.0,
            },
        )
        # Valid criteria keys should remain
        assert "log_loyer_moyen_appt_all_scaled" in config.criteria_weights
        assert "heb_chrs_scaled" in config.criteria_weights
        # Invalid criteria keys should be sanitized out
        assert "non_existent_criterion_id" not in config.criteria_weights
        assert "heb_centres_heb_scaled" not in config.criteria_weights
