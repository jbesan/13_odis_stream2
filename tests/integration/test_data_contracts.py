import os
from utils.data_loader import load_all_data_raw, load_scores_config_as_df
import config as cfg


def test_data_columns_contract():
    """
    Verifies that all indicator columns configured in scores_config.yaml
    actually exist in the raw Parquet file or can be resolved.
    """
    # 1. Load configuration
    scores_path = os.path.join(cfg.APP_DIR, cfg.SCORES_CAT_FILE)
    assert os.path.exists(scores_path), f"Configuration file {scores_path} is missing!"

    scores_cat = load_scores_config_as_df(scores_path)

    # 2. Load the actual loaded data
    data = load_all_data_raw()
    odis = data["odis"]

    # 3. Define metrics that are dynamically computed at runtime
    # These do not need to exist in the static Parquet file
    dynamic_metrics = {
        "dist_current_loc",
        "epci_code",  # Handled via EPCI lookup
        "met_match_adult1",  # Computed via live jobs
        "met_match_adult2",  # Computed via live jobs
        "met_match_adult1_tension",
        "met_match_adult2_tension",
        "met_siae_match_adult1",
        "met_siae_match_adult2",
        "form_match_adult1",
        "form_match_adult2",
        "inc_services_incl_scaled",  # Computed via incl_index
        "inc_asso_add_scaled",  # Computed via associations_data
        "inc_asso_core_scaled",  # Computed via associations_data
        "mob_epci_scaled",  # Computed dynamically
        "affinite_density",  # Computed dynamically
        "edu_maternelle",  # Precomputed scaled column is loaded, raw count is edu_maternelle_ct
        "edu_elementaire",
        "edu_college",
        "edu_lycee",
        "has_gare",  # Precomputed scaled column is loaded, no raw has_gare metric
        "mob_trans_pub_stop_density",  # Computed dynamically from transit stop counts
        "ter_strategic_locations_scaled",  # Computed dynamically
    }

    missing_scores = []
    missing_metrics = []

    for _, row in scores_cat.iterrows():
        score_id = row["score"]
        metric = row["metric"]
        computation = row.get("computation", "live")

        # Precomputed scores must exist directly as columns in the loaded odis dataframe
        if computation == "precomputed":
            if score_id not in odis.columns:
                missing_scores.append((score_id, "Precomputed score column missing"))

        # Raw metrics must exist in odis columns if they are not dynamically computed
        if metric and metric not in dynamic_metrics:
            if metric not in odis.columns and metric not in odis.index.names:
                # Some metrics might be loaded into separate tables (like associations/education POIs),
                # let's verify if they are handled by POIs or need to be in odis
                is_poi_metric = any(
                    x in score_id
                    for x in [
                        "edu_structures_scaled",
                        "besoins_match_scaled",
                    ]
                )
                if not is_poi_metric:
                    missing_metrics.append((score_id, metric))

    # Report errors clearly
    errors = []
    if missing_scores:
        errors.append(f"Missing precomputed score columns: {missing_scores}")
    if missing_metrics:
        errors.append(f"Missing metric source columns: {missing_metrics}")

    assert not errors, "\n".join(errors)
