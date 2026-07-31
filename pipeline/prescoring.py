import logging
import pandas as pd
import geopandas as gpd
import numpy as np
import yaml
from shapely import wkb
from typing import Dict, Any, List
from pathlib import Path

from pipeline.common import (
    PipelineLogger,
    load_config,
    CONFIG_FILE,
    OUTPUT_DIR,
    STATUS_FILE,
)
from pipeline.quality_gate import run_quality_gate
import app.config as cfg

# Global Scores Config cache
_scores_config_cache: Dict[str, Any] = {}


def get_scores_config():
    global _scores_config_cache
    if _scores_config_cache:
        return _scores_config_cache

    app_config_path = Path(__file__).parent.parent / "app" / "scores_config.yaml"

    if app_config_path.exists():
        with open(app_config_path, "r") as f:
            full_config = yaml.safe_load(f)
            if "scores" in full_config:
                for s in full_config["scores"]:
                    _scores_config_cache[s["id"]] = {
                        "min": s.get("min_bound"),
                        "max": s.get("max_bound"),
                        "scaling_type": s.get("scaling_type", "linear"),
                        "mu": s.get("mu"),
                        "sigma": s.get("sigma"),
                        "quantile_level": s.get("quantile_level"),
                        "missing_strategy": s.get("missing_strategy", "exclude"),
                        "source_metric": s.get("source_metric"),
                        "computation": s.get("computation", "precomputed"),
                    }
    else:
        logging.warning(f"App config not found at {app_config_path}")
    return _scores_config_cache


def apply_configured_raw_missingness(
    df: pd.DataFrame, scores_config: Dict[str, Any]
) -> None:
    """Apply catalog policy to raw metrics before derived scores are calculated.

    Ingestion and build deliberately preserve missing observations. This is the
    single point where a catalog entry may deliberately turn an unavailable raw
    value into zero.
    """
    raw_metrics_to_fill = {
        conf["source_metric"]
        for conf in scores_config.values()
        if conf.get("missing_strategy") == "zero" and conf.get("source_metric")
    }

    # RNA category counts feed zero-strategy inclusion indicators.
    raw_metrics_to_fill.update(
        c
        for c in df.columns
        if c.startswith("inc_rna_") and c.endswith("_count")
    )

    for metric in raw_metrics_to_fill:
        if metric in df.columns:
            df[metric] = df[metric].fillna(0.0)


def apply_configured_score_missingness(
    df: pd.DataFrame, scores_config: Dict[str, Any]
) -> None:
    """Apply catalog policy to score outputs after all derivations/scaling."""
    for score_id, conf in scores_config.items():
        if (
            conf.get("missing_strategy") == "zero"
            and conf.get("computation") != "live"
        ):
            if score_id in df.columns:
                df[score_id] = df[score_id].fillna(0.0)
            else:
                df[score_id] = 0.0


def safe_ratio(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Compute a ratio without confusing an unavailable/zero denominator with 0."""
    valid = numerator.notna() & denominator.notna() & denominator.gt(0)
    return numerator.div(denominator).where(valid)


def scale_series(series, min_b, max_b, inverted=False, col_name="series"):
    if series.empty:
        return series
    denom = max_b - min_b
    if denom == 0:
        logging.warning(
            f"Zero variance in series scaling for '{col_name}' (min={min_b}, max={max_b}). Defaulting to NaN."
        )
        return pd.Series(np.nan, index=series.index)

    scaled = (series - min_b) / denom
    if inverted:
        scaled = 1.0 - scaled
    return scaled.clip(0, 1)


def get_min_max_quant(series: pd.Series, q: float = 0.01) -> tuple[float, float]:
    valid_series = series.dropna()
    if valid_series.empty:
        return 0.0, 1.0

    q_min = float(valid_series.quantile(q))
    q_max = float(valid_series.quantile(1.0 - q))

    if q_max > q_min:
        return q_min, q_max

    # Safeguard: if quantile trimming collapses bounds (e.g., zero-inflated distributions),
    # fallback to observed min/max of valid data.
    obs_min = float(valid_series.min())
    obs_max = float(valid_series.max())

    col_id = getattr(series, "name", "series")
    if obs_max > obs_min:
        logging.warning(
            f"Quantile level q={q} for column '{col_id}' yielded zero-variance bounds ({q_min}, {q_max}). "
            f"Falling back to full observed bounds ({obs_min}, {obs_max})."
        )
        return obs_min, obs_max

    # If even observed data has zero variance (constant series), return safe min, min+1 fallback
    logging.warning(
        f"Column '{col_id}' has constant observed values ({obs_min})."
    )
    return obs_min, obs_min + 1.0 if obs_min != 0.0 else 1.0


def process_scaling(df, col_name, output_col, inverted=False):
    if col_name not in df.columns:
        return

    scores_config = get_scores_config()
    conf = scores_config.get(output_col, {})
    scaling_type = conf.get("scaling_type", "linear")

    if scaling_type == "gaussian":
        mu = float(conf.get("mu", 50000))
        sigma = float(conf.get("sigma", 40000))
        logging.info(
            f"Applying Gaussian scaling to {col_name} -> {output_col} (mu={mu}, sigma={sigma})"
        )
        df[output_col] = np.exp(-0.5 * ((df[col_name] - mu) / sigma) ** 2)
        return

    c_min, c_max = conf.get("min"), conf.get("max")
    if c_min is not None and c_max is not None:
        min_b, max_b = c_min, c_max
    else:
        q_level = conf.get("quantile_level") or 0.01
        min_b, max_b = get_min_max_quant(df[col_name], q_level)

    df[output_col] = scale_series(
        df[col_name], min_b, max_b, inverted, col_name=output_col
    )


# PLM consolidation is now fully handled in the build phase (pipeline/build.py)
# so that prescoring operates directly on clean commune-level data.


def apply_prescoring(config: Dict[str, Any], logger: PipelineLogger):
    """Applies pre-scoring logic (ratios, densities, scaling) to odis_communes."""
    logger.log_step("apply_prescoring", "STARTED")
    try:
        input_path = OUTPUT_DIR / "odis_communes_pre.parquet"
        output_path = OUTPUT_DIR / "odis_communes.parquet"

        if not input_path.exists():
            logging.error(f"Input file not found: {input_path}")
            logger.log_step(
                "apply_prescoring", "FAILED", {"reason": "Input file not found"}
            )
            return

        # Read as standard Parquet (WKB)
        communes_df = pd.read_parquet(input_path, engine="fastparquet")

        # Convert WKB to Geometry
        if "polygon" in communes_df.columns:
            geoms = [wkb.loads(bytes(x)) for x in communes_df["polygon"]]
            communes_gdf = gpd.GeoDataFrame(
                communes_df, geometry=geoms, crs="EPSG:4326"
            )
        else:
            # Fallback
            communes_gdf = gpd.GeoDataFrame(communes_df, geometry="geometry")

        if "population" in communes_gdf.columns:
            communes_gdf["population"] = pd.to_numeric(
                communes_gdf["population"], errors="coerce"
            ).astype("float64")

        # --- Calculated Columns ---

        scores_conf = get_scores_config()
        apply_configured_raw_missingness(communes_gdf, scores_conf)
        # 0. Load Associations for Lien Social Score (moved to build.py)
        # Block removed.

        # log_soc_inoc_ratio
        if (
            "log_soc_total" in communes_gdf.columns
            and "log_soc_inoccupes" in communes_gdf.columns
        ):
            communes_gdf["log_soc_inoc_ratio"] = safe_ratio(
                communes_gdf["log_soc_inoccupes"], communes_gdf["log_soc_total"]
            )

        # log_pp_occup (Weighted Average of Occupancy)
        # Weights:
        # SEV_OVER_OCC: 0.0
        # MOD_OVER_OCC: 0.25
        # STD_OCC: 0.5
        # MOD_UNDER_OCC: 0.75
        # SEV_UNDER_OCC: 1.0
        # VSEV_UNDER_OCC: 1.0

        occup_cols = [
            "SEV_OVER_OCC",
            "MOD_OVER_OCC",
            "STD_OCC",
            "MOD_UNDER_OCC",
            "SEV_UNDER_OCC",
            "VSEV_UNDER_OCC",
        ]
        # Ensure columns exist (should be filled in build, but good to check)
        for col in occup_cols:
            if col not in communes_gdf.columns:
                communes_gdf[col] = np.nan

        total_occup_households = communes_gdf[occup_cols].sum(axis=1, min_count=1)
        communes_gdf["log_total"] = total_occup_households  # Use as log_total (RP)

        weighted_sum_occup = (
            communes_gdf["SEV_OVER_OCC"] * 0.0
            + communes_gdf["MOD_OVER_OCC"] * 0.25
            + communes_gdf["STD_OCC"] * 0.5
            + communes_gdf["MOD_UNDER_OCC"] * 0.75
            + communes_gdf["SEV_UNDER_OCC"] * 1.0
            + communes_gdf["VSEV_UNDER_OCC"] * 1.0
        )

        communes_gdf["log_pp_occup"] = safe_ratio(
            weighted_sum_occup, total_occup_households
        )

        # metiers_offres_ratio and pop_chomage_ratio
        # Requires pop_active_be
        # pop_chomage_ratio (Still useful as a general indicator of local economy)

        if (
            "pop_active" in communes_gdf.columns
            and "pop_chomeurs" in communes_gdf.columns
        ):
            communes_gdf["pop_chomage_ratio"] = safe_ratio(
                communes_gdf["pop_chomeurs"], communes_gdf["pop_active"]
            )

        # --- Pre-calculate Ratios and Scaled Scores (Optimization) ---

        # 2. Logement Vacant Structurel Ratio
        # 2. Logement Vacant Structurel Ratio
        if (
            "log_priv_total" in communes_gdf.columns
            and "log_priv_vacant_plus_2ans" in communes_gdf.columns
        ):
            communes_gdf["log_vac_struct_ratio"] = safe_ratio(
                communes_gdf["log_priv_vacant_plus_2ans"],
                communes_gdf["log_priv_total"],
            )

            communes_gdf["lien_social_density"] = safe_ratio(
                communes_gdf["lien_social_count"] * 1000,
                communes_gdf["population"],
            )

        # SIAE Associations Density (New F-39)
        if (
            "population" in communes_gdf.columns
            and "inc_siae_count" in communes_gdf.columns
        ):
            communes_gdf["inc_siae_density"] = safe_ratio(
                communes_gdf["inc_siae_count"] * 1000,
                communes_gdf["population"],
            )

        # 4. Risque Fermeture (Count of schools with < 20 students/class)
        # We use the count directly. Lower is better.
        if "risky_schools_count" in communes_gdf.columns:
            communes_gdf["risque_fermeture_ratio"] = communes_gdf[
                "risky_schools_count"
            ]
        else:
            communes_gdf["risque_fermeture_ratio"] = np.nan

        # ... (Creches Density)
        # Hebergement Densities (New F-42)
        if "population" in communes_gdf.columns:
            if "inc_asso_refug_count" in communes_gdf.columns:
                communes_gdf["inc_asso_refug_density"] = safe_ratio(
                    communes_gdf["inc_asso_refug_count"] * 1000,
                    communes_gdf["population"],
                )
            if "heb_loc_iml_count" in communes_gdf.columns:
                communes_gdf["heb_loc_iml_density"] = safe_ratio(
                    communes_gdf["heb_loc_iml_count"] * 1000,
                    communes_gdf["population"],
                )
            if "heb_habitant_count" in communes_gdf.columns:
                communes_gdf["heb_habitant_density"] = safe_ratio(
                    communes_gdf["heb_habitant_count"] * 1000,
                    communes_gdf["population"],
                )

        # Load App Config for Scores (Source of Truth)
        scores_config = get_scores_config()
        socle_admin_list: List[Any] = []

        # Updated Housing Rent Scaling (ODACE source)
        # Using concise names as per user request: appt_all, appt_t1_t2, appt_t3_p, house_all
        # We KEEP the raw data (euros/m2) and add the _scaled suffix
        # logging.info(f"DEBUG: communes_gdf cols before scaling: {[c for c in communes_gdf.columns if 'loyer' in c]}")
        for col, target in [
            ("loyer_m2_moy_appt_all", "log_loyer_moyen_appt_all_scaled"),
            ("loyer_m2_moy_appt_t1_t2", "log_loyer_moyen_appt_t1_t2_scaled"),
            ("loyer_m2_moy_appt_t3_p", "log_loyer_moyen_appt_t3_p_scaled"),
            ("loyer_m2_moy_house_all", "log_loyer_moyen_house_all_scaled"),
        ]:
            if col in communes_gdf.columns:
                process_scaling(communes_gdf, col, target, inverted=True)
            else:
                logging.warning(f"ODACE Rent column {col} missing for scaling.")

        process_scaling(
            communes_gdf, "log_vac_scaled", "log_vac_scaled"
        )  # wait, log_vac_scaled vs log_vac_struct_ratio?
        # Fixed logic:
        process_scaling(communes_gdf, "log_vac_struct_ratio", "log_vac_scaled")
        process_scaling(communes_gdf, "lien_social_density", "inc_asso_core_scaled")
        process_scaling(communes_gdf, "inc_asso_refug_density", "inc_asso_refug_scaled")
        process_scaling(communes_gdf, "inc_siae_density", "inc_siae_density_scaled")

        # ter_pol_scaled (already 0-1)
        if "pol_num" in communes_gdf.columns:
            communes_gdf["ter_pol_scaled"] = communes_gdf["pol_num"]

        # ter_anvita_scaled (already 0-1)
        if "ter_anvita_member" in communes_gdf.columns:
            communes_gdf["ter_anvita_scaled"] = communes_gdf["ter_anvita_member"]

        # ter_ctai_scaled (already 0-1)
        if "ter_ctai_member" in communes_gdf.columns:
            communes_gdf["ter_ctai_scaled"] = communes_gdf["ter_ctai_member"]

        process_scaling(communes_gdf, "log_pp_occup", "log_occup_scaled")

        # Hebergement Scaling (New F-42)
        process_scaling(communes_gdf, "heb_loc_iml_density", "heb_loc_iml_scaled")
        process_scaling(
            communes_gdf, "heb_habitant_density", "heb_asso_habitant_scaled"
        )

        # New 2026 Metrics
        process_scaling(
            communes_gdf, "log_soc_delay", "log_soc_delay_scaled", inverted=True
        )
        process_scaling(
            communes_gdf, "sante_apl", "sante_rdv_delay_scaled", inverted=False
        )
        process_scaling(
            communes_gdf, "mob_dur_share", "mob_dur_share_scaled", inverted=False
        )
        process_scaling(
            communes_gdf, "ter_insecurite", "ter_insecurite_scaled", inverted=True
        )

        # Defragment DataFrame memory layout
        communes_gdf = communes_gdf.copy()

        # Population Decline (Inverted logic handled in process_scaling)
        if (
            "pop_jeune_2016" in communes_gdf.columns
            and "pop_jeune_2022" in communes_gdf.columns
        ):
            communes_gdf["youth_growth_rate"] = safe_ratio(
                communes_gdf["pop_jeune_2022"] - communes_gdf["pop_jeune_2016"],
                communes_gdf["pop_jeune_2016"],
            )

        if (
            "pop_active_2016" in communes_gdf.columns
            and "pop_active_2022" in communes_gdf.columns
        ):
            communes_gdf["workclass_growth_rate"] = safe_ratio(
                communes_gdf["pop_active_2022"] - communes_gdf["pop_active_2016"],
                communes_gdf["pop_active_2016"],
            )

        if "youth_growth_rate" in communes_gdf.columns:
            process_scaling(
                communes_gdf, "youth_growth_rate", "youth_decline_scaled", inverted=True
            )

        if "workclass_growth_rate" in communes_gdf.columns:
            process_scaling(
                communes_gdf,
                "workclass_growth_rate",
                "workclass_decline_scaled",
                inverted=True,
            )

        if (
            "log_soc_total" in communes_gdf.columns
            and "log_soc_inoccupes" in communes_gdf.columns
        ):
            communes_gdf["log_soc_inoc_ratio"] = safe_ratio(
                communes_gdf["log_soc_inoccupes"], communes_gdf["log_soc_total"]
            )
            process_scaling(communes_gdf, "log_soc_inoc_ratio", "log_soc_inoc_scaled")

        # edu_classes_ferm_scaled
        # Logic was: max count -> 1.0 (inverted=False in previous edit).
        # User said: "schools with classes at risk are closing are more likely to welcome new families -> higher is better"
        # So Higher Ratio (Risk Count) -> Higher Score. Standard scaling.
        # But wait, previous edit said: inverted=False.
        # Let's keep it standard.
        process_scaling(
            communes_gdf, "risque_fermeture_ratio", "edu_classes_ferm_scaled"
        )

        process_scaling(
            communes_gdf, "edu_pe_tx_couverture", "edu_petite_enfance_scaled"
        )  # Usually 0-100? or 0-1?

        # mob_gare_scaled
        if "has_gare" in communes_gdf.columns:
            # Binary score: 1 if present, 0 if not
            communes_gdf["mob_gare_scaled"] = (
                communes_gdf["has_gare"].gt(0).where(
                    communes_gdf["has_gare"].notna()
                ).astype(float)
            )

        # Static Boolean Scores (Education)
        for col, score_col in [
            ("edu_maternelle_ct", "edu_maternelle_scaled"),
            ("edu_elementaire_ct", "edu_elementaire_scaled"),
            ("edu_college_ct", "edu_college_scaled"),
            ("edu_lycee_ct", "edu_lycee_scaled"),
        ]:
            if col in communes_gdf.columns:
                communes_gdf[score_col] = communes_gdf[col].gt(0).where(
                    communes_gdf[col].notna()
                ).astype(float)

        # Static Boolean Scores (Housing / Hébergement)
        for col, score_col in [
            ("heb_chrs_count", "heb_chrs_scaled"),
            ("heb_cph_count", "heb_cph_scaled"),
            ("heb_cada_count", "heb_cada_scaled"),
            ("heb_fjt_count", "heb_fjt_scaled"),
            ("heb_pension_count", "heb_pension_scaled"),
        ]:
            if col in communes_gdf.columns:
                communes_gdf[score_col] = communes_gdf[col].gt(0).where(
                    communes_gdf[col].notna()
                ).astype(float)

        # Static Boolean Scores (Sante)
        for col, score_col in [
            ("count_hopital", "sante_hopital_scaled"),
            ("count_maternite", "sante_maternite_scaled"),
            ("count_centre_sante", "sante_centre_sante_scaled"),
            ("count_psy", "sante_psy_scaled"),
            ("count_dialyse", "sante_dialyse_scaled"),
            ("count_maison_sante", "sante_maison_sante_scaled"),
            ("count_addictologie", "sante_addictologie_scaled"),
            ("count_pmi", "sante_pmi_scaled"),
        ]:
            if col in communes_gdf.columns:
                communes_gdf[score_col] = communes_gdf[col].gt(0).where(
                    communes_gdf[col].notna()
                ).astype(float)
            # 2. Add static scores that don't need calc (just rename/copy effectively, but already done in build?)
        # Actually most are calculated.
        # But 'inc_population_scaled' etc are done above.

        # --- Drop Unused Columns ---
        cols_to_drop = [
            "MOD_OVER_OCC",
            "MOD_UNDER_OCC",
            "SEV_OVER_OCC",
            "SEV_UNDER_OCC",
            "STD_OCC",
            "VSEV_UNDER_OCC",  # *_OCC
            # 'total_eleves', 'ecoles_count', # KEEP for details
            "log_total",
            "log_soc_total",
            "log_soc_inoccupes",
            # 'pol_num', #'log_priv_vacant_plus_2ans', # KEEP for details
            # 'edu_maternelle_ct', 'edu_elementaire_ct', 'edu_college_ct', 'edu_lycee_ct', # KEEP
            "svc_incl_count",
            # 'count_hopital', 'count_psy', 'count_maternite', # KEEP for details
            # 'log_soc_inoc_ratio', 'log_pp_occup', # KEEP for details
            # 'metiers_offres_ratio', 'pop_chomage_ratio', # KEEP
            # 'log_vac_struct_ratio', 'risque_fermeture_ratio', 'bpe_creches_density', # 'lien_social_density', # KEEP
            #'edu_pe_tx_couverture', # 'bpe_creches_count', # KEEP
            # 'lien_social_count', # KEEP
            # 'pop_active', 'pop_employes', 'pop_chomeurs' # KEEP
        ]

        # --- Socle Administratif (Pre-calculated) ---
        # Load POIs to get inclusion services
        pois_path = OUTPUT_DIR / "odis_pois.parquet"
        if pois_path.exists():
            try:
                default_socle_admin = cfg.DEFAULT_INC_SERVICES_CORE

                pois_df = pd.read_parquet(pois_path, engine="fastparquet")
                incl_pois = pois_df[pois_df["category"] == "incl_services"].copy()

                if not incl_pois.empty:
                    import ast

                    def parse_types(x):
                        if not isinstance(x, str):
                            return []
                        x = x.strip()
                        if not x:
                            return []
                        try:
                            val = ast.literal_eval(x)
                            if isinstance(val, list):
                                return val
                            return [str(val)]
                        except (ValueError, SyntaxError):
                            # It's a raw string slug
                            return [x]

                    # Explode types for analysis
                    incl_pois["services_list"] = (
                        incl_pois["type"].astype(str).apply(parse_types)
                    )
                    exploded = incl_pois.explode("services_list")

                    socle_slugs = set(default_socle_admin)
                    if socle_slugs:
                        exploded["is_socle"] = exploded["services_list"].isin(
                            socle_slugs
                        )
                        socle_presence = (
                            exploded[exploded["is_socle"]]
                            .groupby("codgeo", observed=True)["services_list"]
                            .nunique()
                        )

                        max_score = len(socle_slugs)
                        socle_scores = socle_presence / max_score

                        # Assign using map on codgeo
                        communes_gdf["inc_services_core_scaled"] = communes_gdf[
                            "codgeo"
                        ].map(socle_scores)

                        # Save Raw Count
                        communes_gdf["socle_match_count"] = (
                            communes_gdf["codgeo"]
                            .map(socle_presence)
                        )
                    else:
                        communes_gdf["inc_services_core_scaled"] = np.nan
                        communes_gdf["socle_match_count"] = np.nan

                    logger.log_step("inc_services_core_scaled", "CALCULATED")
                else:
                    communes_gdf["inc_services_core_scaled"] = np.nan

            except Exception as e:
                logging.exception(
                    "❌ [PRESCORING FAILURE] Failed to calculate socle admin score"
                )
                communes_gdf["inc_services_core_scaled"] = np.nan
        else:
            logging.warning("pois.parquet not found, skipping socle admin score")
            communes_gdf["inc_services_core_scaled"] = np.nan

        communes_gdf.drop(
            columns=[c for c in cols_to_drop if c in communes_gdf.columns], inplace=True
        )

        # Additional drop request from user
        more_cols_to_drop = [
            "pop_jeune_2016",
            "pop_jeune_2022",
            "pop_active_2016",
            "pop_active_2022",
            "libelle_bassin_de_vie",
            "has_gare",
            "inc_siae_count",  #'gare_count', # KEEP
            #'risky_schools_count', # KEEP
            "log_priv_total",
        ]
        communes_gdf.drop(
            columns=[c for c in more_cols_to_drop if c in communes_gdf.columns],
            inplace=True,
        )

        # Optimization: Vectorized float64 to float32 conversion to prevent fragmentation
        float64_cols = list(communes_gdf.select_dtypes(include=["float64"]).columns)
        if float64_cols:
            communes_gdf[float64_cols] = communes_gdf[float64_cols].astype("float32")

        if "inc_services_core_scaled" not in communes_gdf.columns:
            communes_gdf["inc_services_core_scaled"] = np.nan

        apply_configured_score_missingness(communes_gdf, scores_conf)

        # Defragment DataFrame memory layout after batch column operations
        communes_gdf = communes_gdf.copy()

        # Save
        if "geometry" in communes_gdf.columns:
            # SOTA: Keep only metric numerical coordinates in the massive `odis` dataframe to avoid geometry overhead for fast Euclidean distance computations
            # LAMBERT-93 (EPSG:2154)
            metric_geo = communes_gdf.geometry.to_crs("EPSG:2154")
            cents = metric_geo.centroid
            communes_gdf["centroid_lon"] = cents.x.values
            communes_gdf["centroid_lat"] = cents.y.values

            # Ensure we are in EPSG:4326 (WGS84) before serializing polygons to WKB for the UI
            if communes_gdf.crs != "EPSG:4326":
                temp_gdf = communes_gdf.to_crs("EPSG:4326")
                communes_gdf["polygon"] = temp_gdf.geometry.to_wkb()
            else:
                communes_gdf["polygon"] = communes_gdf.geometry.to_wkb()

            # Drop the heavy metric geometry to keep the dataframe lightweight
            communes_gdf.drop(columns=["geometry"], inplace=True)

        pd.DataFrame(communes_gdf).to_parquet(
            output_path, compression="brotli", index=False, engine="fastparquet"
        )

        # Run Quality Gate validation on published dataset
        run_quality_gate(
            communes_path=output_path,
            status_path=STATUS_FILE,
            dataset_name="odis_communes.parquet",
            ask_user_on_failure=True,
        )

        logger.log_step(
            "apply_prescoring",
            "COMPLETED",
            {
                "columns": len(communes_gdf.columns),
                "path": str(output_path),
                "rows": len(communes_gdf),
            },
        )

    except Exception as e:
        logger.log_step("apply_prescoring", "ERROR", {"error": str(e)})
        logging.error(f"Prescoring failed: {e}")
        raise e


def score_bassins_de_vie(config: Dict[str, Any], logger: PipelineLogger):
    """Calculates scores for Bassins de Vie."""
    logger.log_step("score_bassins_de_vie", "STARTED")
    try:
        bv_path = OUTPUT_DIR / "odis_bassins_de_vie.parquet"
        communes_path = OUTPUT_DIR / "odis_communes.parquet"

        if not bv_path.exists() or not communes_path.exists():
            logging.error("BV or Communes parquet not found.")
            return

        # Read as standard Parquet (WKB) - BV
        bv_df = pd.read_parquet(bv_path, engine="fastparquet")
        if "polygon" in bv_df.columns:
            geoms = [wkb.loads(bytes(x)) for x in bv_df["polygon"]]
            bv_gdf = gpd.GeoDataFrame(bv_df, geometry=geoms, crs=cfg.PROJECTED_CRS)
        else:
            bv_gdf = gpd.GeoDataFrame(bv_df, geometry="geometry")

        # Read as standard Parquet (WKB) - Communes
        communes_df = pd.read_parquet(communes_path, engine="fastparquet")
        # We don't need geometry for communes here, just scores.

        # We need Aggregated Counts which should be in 'bv_gdf' if build.py did its job.

        # --- 1. Ratios & Densities ---

        # Lien Social
        bv_gdf["lien_social_density"] = safe_ratio(
            bv_gdf["lien_social_count"] * 1000,
            bv_gdf["population_bv"],
        )

        # SIAE Density (New F-39)
        if "inc_siae_count" in bv_gdf.columns and "population_bv" in bv_gdf.columns:
            bv_gdf["inc_siae_density"] = safe_ratio(
                bv_gdf["inc_siae_count"] * 1000,
                bv_gdf["population_bv"],
            )

        # Refugee Associations (Inclusion)
        if (
            "inc_asso_refug_count" in bv_gdf.columns
            and "population_bv" in bv_gdf.columns
        ):
            bv_gdf["inc_asso_refug_density"] = safe_ratio(
                bv_gdf["inc_asso_refug_count"] * 1000,
                bv_gdf["population_bv"],
            )

        # --- 2. Scaling ---
        # Align with config-driven scaling used for communes
        if "lien_social_density" in bv_gdf.columns:
            process_scaling(bv_gdf, "lien_social_density", "inc_asso_core_scaled")

        if "inc_asso_refug_density" in bv_gdf.columns:
            process_scaling(bv_gdf, "inc_asso_refug_density", "inc_asso_refug_scaled")

        if "inc_siae_density" in bv_gdf.columns:
            process_scaling(bv_gdf, "inc_siae_density", "inc_siae_density_scaled")

        # --- 3. Weighted Averages & Sums of Metrics from Communes ---
        metrics_to_avg = [
            "inc_services_core_scaled",
            "inc_asso_core_scaled",
            "inc_asso_refug_scaled",
            "inc_siae_density_scaled",
            "edu_classes_ferm_scaled",
            "log_vac_scaled",
            "log_occup_scaled",
            "log_soc_inoc_scaled",
            "edu_petite_enfance_scaled",
            "sante_hopital_scaled",
            "sante_maternite_scaled",
            "sante_centre_sante_scaled",
            "sante_psy_scaled",
            "sante_dialyse_scaled",
            "sante_maison_sante_scaled",
            "sante_addictologie_scaled",
            "sante_pmi_scaled",
            "edu_lycee_scaled",
            "edu_college_scaled",
            "edu_maternelle_scaled",
            "edu_elementaire_scaled",
            "youth_decline_scaled",
            "workclass_decline_scaled",
            "heb_chrs_scaled",
            "heb_cph_scaled",
            "heb_cada_scaled",
            "heb_fjt_scaled",
            "heb_pension_scaled",
            "heb_loc_iml_scaled",
            "heb_asso_habitant_scaled",
            "log_soc_delay_scaled",
            "sante_rdv_delay_scaled",
            "mob_dur_share_scaled",
            "ter_insecurite_scaled",
            # Raw metrics for display in UI/PDF details
            "mob_dur_share",
            "risque_fermeture_ratio",
            "ter_insecurite",
            "inc_asso_refug_density",
            "inc_siae_density",
            "lien_social_density",
            "edu_pe_tx_couverture",
            "log_pp_occup",
            "log_soc_delay",
            "log_soc_inoc_ratio",
            "log_vac_struct_ratio",
            "loyer_m2_moy_appt_all",
            "loyer_m2_moy_appt_t1_t2",
            "loyer_m2_moy_appt_t3_p",
            "loyer_m2_moy_house_all",
            "pol_num",
            "sante_apl",
            "ter_anvita_member",
            "ter_ctai_member",
            "workclass_growth_rate",
            "youth_growth_rate",
        ]

        raw_sum_metrics = [
            "count_addictologie",
            "count_dialyse",
            "count_hopital",
            "count_maison_sante",
            "count_maternite",
            "count_pmi",
            "count_psy",
            "heb_cada_count",
            "heb_chrs_count",
            "heb_cph_count",
            "heb_fjt_count",
            "heb_habitant_count",
            "heb_loc_iml_count",
            "heb_pension_count",
        ]

        all_target_metrics = list(set(metrics_to_avg + raw_sum_metrics))

        # Idempotency: Drop existing metrics to prevent duplication during merge
        cols_to_drop_bv = [col for col in all_target_metrics if col in bv_gdf.columns]
        for col in all_target_metrics:
            if f"{col}_x" in bv_gdf.columns:
                cols_to_drop_bv.append(f"{col}_x")
            if f"{col}_y" in bv_gdf.columns:
                cols_to_drop_bv.append(f"{col}_y")
        if cols_to_drop_bv:
            bv_gdf.drop(columns=list(set(cols_to_drop_bv)), errors="ignore", inplace=True)

        if "population_bv" in bv_gdf.columns and "population" not in bv_gdf.columns:
            bv_gdf["population"] = bv_gdf["population_bv"]

        communes_subset = communes_df[
            ["codgeo", "bassin_de_vie", "population"]
            + [m for m in all_target_metrics if m in communes_df.columns]
        ].copy()

        if "bassin_de_vie" in communes_subset.columns:
            for metric in metrics_to_avg:
                if metric in communes_subset.columns:
                    valid = (
                        communes_subset[metric].notna()
                        & communes_subset["population"].notna()
                    )
                    communes_subset[f"{metric}_w"] = (
                        communes_subset[metric] * communes_subset["population"]
                    ).where(valid)
                    communes_subset[f"{metric}_population"] = communes_subset[
                        "population"
                    ].where(valid)

            grouped = communes_subset.groupby("bassin_de_vie", observed=True)
            bv_aggs = pd.DataFrame(index=grouped.groups.keys())
            for metric in metrics_to_avg:
                if metric in communes_subset.columns:
                    metric_population = grouped[f"{metric}_population"].sum(
                        min_count=1
                    )
                    bv_aggs[metric] = grouped[f"{metric}_w"].sum(
                        min_count=1
                    ) / metric_population

            for metric in raw_sum_metrics:
                if metric in communes_subset.columns:
                    bv_aggs[metric] = grouped[metric].sum(min_count=1)

            # Merge back
            if "bassin_de_vie" in bv_gdf.columns:
                bv_gdf = bv_gdf.merge(
                    bv_aggs, left_on="bassin_de_vie", right_index=True, how="left"
                )
            else:
                # assume index matches if sorted? Safe to use merge if we have key.
                # If bv_gdf has 'bassin_de_vie' as column.
                pass

        apply_configured_score_missingness(bv_gdf, get_scores_config())

        # --- 4. Special cases ---
        # Clean up
        # Clean up
        if "geometry" in bv_gdf.columns:
            bv_gdf["polygon"] = bv_gdf.geometry.to_wkb()
            bv_gdf.drop(columns=["geometry"], inplace=True)

        # Robust index reset to avoid level_0 duplication
        bv_export = pd.DataFrame(bv_gdf)
        if "level_0" in bv_export.columns:
            bv_export.drop(columns=["level_0"], inplace=True)

        bv_export.reset_index().to_parquet(
            bv_path, compression="brotli", index=False, engine="fastparquet"
        )
        logger.log_step("score_bassins_de_vie", "COMPLETED", {"rows": len(bv_gdf)})

    except Exception as e:
        logger.log_step("score_bassins_de_vie", "ERROR", {"error": str(e)})
        logging.error(f"Score BV failed: {e}")


def main(argv=None):
    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)
    apply_prescoring(config, logger)
    score_bassins_de_vie(config, logger)


if __name__ == "__main__":
    main()
