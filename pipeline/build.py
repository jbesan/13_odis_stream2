import argparse
import logging
import pandas as pd
import geopandas as gpd
import numpy as np
from pathlib import Path
import warnings
from dataclasses import dataclass
from shapely.geometry import Polygon, MultiPolygon
from shapely.validation import make_valid
from typing import Dict, Any, List, Literal, Optional, cast
from shapely import wkb


def extract_polygonal(geom):
    """Keep only Polygon/MultiPolygon parts of a geometry."""
    if geom is None:
        return None
    if geom.geom_type in ["Polygon", "MultiPolygon"]:
        return geom
    if geom.geom_type == "GeometryCollection":
        polys = [g for g in geom.geoms if g.geom_type in ["Polygon", "MultiPolygon"]]
        if not polys:
            return None
        if len(polys) == 1:
            return polys[0]
        return MultiPolygon(polys)
    return None


def normalize_geographic_codes(values: pd.Series) -> pd.Series:
    """Normalize COG/ODACE codes without losing zero-padded identifiers."""
    codes = (
        values.astype("string")
        .str.strip()
        .str.upper()
        .str.replace(r"\.0$", "", regex=True)
    )
    numeric_codes = codes.str.fullmatch(r"\d+")
    return codes.where(~numeric_codes, codes.str.zfill(2))


from pipeline.common import (
    PipelineLogger,
    load_config,
    load_dataset,
    CONFIG_FILE,
    CACHE_DIR,
    CLEAN_DIR,
    OUTPUT_DIR,
    STATUS_FILE,
)
import app.config as cfg
from pipeline.anvita import compute_anvita_scores
from pipeline.ctai import compute_ctai_scores
from pipeline.run_context import PipelineRunError


# Constants
PLM_ARRONDISSEMENTS = (
    [str(x) for x in range(75101, 75121)]
    + [str(x) for x in range(13201, 13217)]
    + [str(x) for x in range(69381, 69390)]
)

PLM_FAMILIES = {
    "75056": tuple(str(x) for x in range(75101, 75121)),
    "13055": tuple(str(x) for x in range(13201, 13217)),
    "69123": tuple(str(x) for x in range(69381, 69390)),
}


@dataclass(frozen=True)
class PLMAggregationPolicy:
    """Source-level rule for one metric in a PLM commune family."""

    fallback: Literal["parent", "sum_children", "population_weighted_mean", "max"]


# This contract follows the scores catalog's missing_strategy pattern: every
# metric is explicit, and an unclassified numerical metric blocks the build.
PLM_SUM_CHILDREN_METRICS = frozenset(
    {
        "population",
        "pop_active",
        "pop_employes",
        "pop_chomeurs",
        "pop_jeune_2016",
        "pop_jeune_2022",
        "pop_active_2016",
        "pop_active_2022",
        "log_priv_vacant_plus_2ans",
        "log_priv_total",
        "log_soc_total",
        "log_soc_inoccupes",
        "edu_maternelle_ct",
        "edu_elementaire_ct",
        "edu_college_ct",
        "edu_lycee_ct",
        "edu_eaje_ct",
        "edu_relais_petite_enfance_ct",
        "edu_alsh_ct",
        "edu_micro_creche_ct",
        "total_eleves",
        "ecoles_count",
        "risky_schools_count",
        "heb_chrs_count",
        "heb_cph_count",
        "heb_cada_count",
        "heb_fjt_count",
        "heb_pension_count",
        "heb_loc_iml_count",
        "heb_habitant_count",
        "gare_count",
        "count_hopital",
        "count_maternite",
        "count_centre_sante",
        "count_psy",
        "count_dialyse",
        "count_maison_sante",
        "count_addictologie",
        "count_pmi",
        "act_antenne_justice_count",
        "act_france_services_count",
        "act_mairie_count",
        "act_femmes_vuln_count",
        "nb_stops_bus",
        "nb_stops_tram",
        "nb_stops_metro",
        "nb_stops_train",
        "nb_stops_total",
        "inc_rna_Culturelle_count",
        "inc_rna_Emploi/IAE_count",
        "inc_rna_FLE/Alpha_count",
        "inc_rna_Juridique_count",
        "inc_rna_Logement_count",
        "inc_rna_Numérique_count",
        "inc_rna_Santé/Psy_count",
        "inc_asso_refug_count",
        "inc_siae_count",
        "lien_social_count",
    }
)
PLM_POPULATION_WEIGHTED_METRICS = frozenset(
    {
        "edu_pe_tx_couverture",
        "log_soc_delay",
        "sante_apl",
        "mob_dur_share",
        "ter_insecurite",
        "loyer_m2_moy_appt_all",
        "loyer_m2_moy_appt_t1_t2",
        "loyer_m2_moy_appt_t3_p",
        "loyer_m2_moy_house_all",
        "loyer_app_m2",
    }
)
PLM_PARENT_ONLY_METRICS = frozenset(
    {
        "pol_num",
        "MOD_OVER_OCC",
        "MOD_UNDER_OCC",
        "SEV_OVER_OCC",
        "SEV_UNDER_OCC",
        "STD_OCC",
        "VSEV_UNDER_OCC",
    }
)
PLM_MAX_METRICS = frozenset({"has_gare"})
PLM_STRUCTURAL_COLUMNS = frozenset(
    {"codgeo", "epci_code", "dep_code", "reg_code", "bassin_de_vie", "plm"}
)


def _plm_metric_policies(df: pd.DataFrame) -> Dict[str, PLMAggregationPolicy]:
    """Return and validate the complete PLM contract for this build schema."""
    policies = {
        **{
            column: PLMAggregationPolicy("sum_children")
            for column in PLM_SUM_CHILDREN_METRICS
        },
        **{
            column: PLMAggregationPolicy("population_weighted_mean")
            for column in PLM_POPULATION_WEIGHTED_METRICS
        },
        **{
            column: PLMAggregationPolicy("parent") for column in PLM_PARENT_ONLY_METRICS
        },
        **{column: PLMAggregationPolicy("max") for column in PLM_MAX_METRICS},
    }
    numeric_columns = {
        column
        for column in df.columns
        if column not in PLM_STRUCTURAL_COLUMNS
        and pd.api.types.is_numeric_dtype(df[column])
    }
    unknown_columns = numeric_columns - policies.keys()
    if unknown_columns:
        raise ValueError(
            "PLM aggregation contract is missing numerical metrics: "
            f"{sorted(unknown_columns)}"
        )
    return {column: policies[column] for column in numeric_columns}


def consolidate_plm_communes(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse PLM children using the declared per-metric source contract."""
    df = df.copy()
    df["codgeo"] = df["codgeo"].astype(str)
    metric_policies = _plm_metric_policies(df)

    for parent, children in PLM_FAMILIES.items():
        if not (df["codgeo"] == parent).any():
            logging.warning(f"Parent code {parent} not found in communes data.")
            continue

        children_mask = df["codgeo"].isin(children)
        parent_mask = df["codgeo"] == parent
        present_children = set(df.loc[children_mask, "codgeo"])

        for column, policy in metric_policies.items():
            parent_value = df.loc[parent_mask, column].iloc[0]
            if policy.fallback == "parent" or pd.notna(parent_value):
                continue

            if set(children) != present_children:
                raise ValueError(
                    f"PLM {parent} has no parent value for '{column}' and only "
                    f"{len(present_children)}/{len(children)} child rows."
                )

            child_values = df.loc[children_mask, column]
            if policy.fallback == "sum_children":
                # min_count=1 preserves missingness while allowing a legitimate 0.
                df.loc[parent_mask, column] = child_values.sum(min_count=1)
            elif policy.fallback == "population_weighted_mean":
                child_populations = df.loc[children_mask, "population"]
                valid = (
                    child_values.notna()
                    & child_populations.notna()
                    & child_populations.gt(0)
                )
                if valid.any():
                    df.loc[parent_mask, column] = (
                        child_values.loc[valid] * child_populations.loc[valid]
                    ).sum() / child_populations.loc[valid].sum()
            elif policy.fallback == "max":
                df.loc[parent_mask, column] = child_values.max(skipna=True)

    all_children = [child for children in PLM_FAMILIES.values() for child in children]
    df = df[~df["codgeo"].isin(all_children)].copy()

    return df


def consolidate_plm_vertical(
    df: pd.DataFrame, codgeo_col: str, group_cols: list, sum_col: str
) -> pd.DataFrame:
    """Map child counts to PLM parents without duplicating parent source rows."""

    df = df.copy()
    df[codgeo_col] = df[codgeo_col].astype(str)

    new_rows = []
    for global_code, arrondissements in PLM_FAMILIES.items():
        arr_df = df[df[codgeo_col].isin(arrondissements)]
        if not arr_df.empty:
            grouped = arr_df.groupby(group_cols)[sum_col].sum().reset_index()
            grouped[codgeo_col] = global_code
            parent_keys = df.loc[df[codgeo_col] == global_code, group_cols]
            if not parent_keys.empty:
                grouped_index = pd.MultiIndex.from_frame(grouped[group_cols])
                parent_index = pd.MultiIndex.from_frame(parent_keys.drop_duplicates())
                grouped = grouped.loc[~grouped_index.isin(parent_index)]
            if not grouped.empty:
                new_rows.append(grouped)

    if new_rows:
        df = pd.concat([df] + new_rows, ignore_index=True)
    return df


def consolidate_plm_detail_list(
    df: pd.DataFrame, codgeo_col: str, parent_bdvs: Optional[Dict[Any, Any]] = None
) -> pd.DataFrame:
    """Map child detail rows to PLM parents, preserving existing parent records."""

    df = df.copy()
    df[codgeo_col] = df[codgeo_col].astype(str)
    if "bassin_de_vie" in df.columns:
        df["bassin_de_vie"] = df["bassin_de_vie"].astype(str)

    new_rows = []
    for global_code, arrondissements in PLM_FAMILIES.items():
        arr_df = df[df[codgeo_col].isin(arrondissements)].copy()
        if not arr_df.empty:
            arr_df[codgeo_col] = global_code
            if "bassin_de_vie" in arr_df.columns and parent_bdvs:
                arr_df["bassin_de_vie"] = parent_bdvs.get(global_code, global_code)
            if "id" in arr_df.columns:
                parent_ids = set(
                    df.loc[df[codgeo_col] == global_code, "id"].dropna().astype(str)
                )
                arr_df = arr_df[~arr_df["id"].astype(str).isin(parent_ids)]
            new_rows.append(arr_df)

    if new_rows:
        df = pd.concat([df] + new_rows, ignore_index=True)
    return df


def build_communes(config: Dict[str, Any], logger: PipelineLogger) -> gpd.GeoDataFrame:
    """Builds the main ODIS Communes dataset."""
    logger.log_step("build_communes", "STARTED")
    try:
        # 1. Load Base Communes (Clean)
        communes_path = CLEAN_DIR / "communes.parquet"
        if not communes_path.exists():
            logging.error("Clean Communes file not found. Run ingest first.")
            logger.log_step(
                "build_communes", "FAILED", {"reason": "Clean Communes file not found"}
            )
            return gpd.GeoDataFrame()

        # Read with pandas (WKB) and reconstruct GDF
        communes_df = pd.read_parquet(communes_path)
        if "polygon" in communes_df.columns:
            geoms = [wkb.loads(bytes(x)) for x in communes_df["polygon"]]
            # Initialize with the native CRS (4326)
            communes_gdf = gpd.GeoDataFrame(
                communes_df, geometry=geoms, crs="EPSG:4326"
            )
        else:
            communes_gdf = gpd.GeoDataFrame(communes_df, geometry="geometry")

        # 2. Merge Indicators
        # Helper to merge
        def merge_clean(name: str, cols: Optional[List[Any]] = None):
            nonlocal communes_gdf
            path = CLEAN_DIR / f"{name}.parquet"
            if path.exists():
                df = pd.read_parquet(path)
                if cols:
                    # Ensure codgeo is present
                    cols_to_use = ["codgeo"] + [
                        c for c in cols if c in df.columns and c != "codgeo"
                    ]
                    df = df[cols_to_use]

                if name == "population_details":
                    pass

                communes_gdf = communes_gdf.merge(df, on="codgeo", how="left")
            else:
                logging.warning(f"Clean {name} file not found.")
                # Optional: Detailed log if impactful?
                # logger.log_step("build_communes_merge", "WARNING", {"missing": name})

        # Merge BMO (Stats only + code_be) - DEPRECATED
        # merge_clean("bmo_stats", ['metiers_offres_diff', 'metiers_tension_diff', 'code_be'])

        # Merge Population
        merge_clean("population", ["population"])

        # Merge Population Active
        merge_clean("population_active", ["pop_active", "pop_employes", "pop_chomeurs"])

        # Merge Population Details (Age Breakdown)
        merge_clean(
            "population_details",
            ["pop_jeune_2016", "pop_jeune_2022", "pop_active_2016", "pop_active_2022"],
        )

        # Merge LOVAC
        merge_clean("lovac", ["pp_vacant_plus_2ans_25", "log_priv_total_24"])

        # Merge RPLS
        merge_clean("rpls", ["log_soc_total", "log_soc_inoccupes"])

        # Merge CAF
        merge_clean("caf", ["taux_couverture"])

        # Merge Education (from BPE25)
        merge_clean(
            "bpe_education_cols",
            [
                "edu_maternelle_ct",
                "edu_elementaire_ct",
                "edu_college_ct",
                "edu_lycee_ct",
                "edu_eaje_ct",
                "edu_relais_petite_enfance_ct",
                "edu_alsh_ct",
                "edu_micro_creche_ct",
            ],
        )

        # Merge Political
        merge_clean("political", ["pol_num", "maire_extreme_droite"])

        # Merge Electoral History
        merge_clean("electoral_history", ["electoral_history"])

        # Merge Housing Occupation
        merge_clean(
            "housing_occupation",
            [
                "MOD_OVER_OCC",
                "MOD_UNDER_OCC",
                "SEV_OVER_OCC",
                "SEV_UNDER_OCC",
                "STD_OCC",
                "VSEV_UNDER_OCC",
            ],
        )

        # Merge School Effectifs
        merge_clean(
            "school_effectifs", ["total_eleves", "ecoles_count", "risky_schools_count"]
        )

        # Merge Hebergement metrics (from BPE25)
        merge_clean(
            "bpe_hebergement_cols",
            [
                "heb_chrs_count",
                "heb_cph_count",
                "heb_cada_count",
                "heb_fjt_count",
                "heb_pension_count",
            ],
        )
        merge_clean("hebergement_rna_cols", ["heb_loc_iml_count", "heb_habitant_count"])

        # Merge Gares (from BPE25)
        merge_clean("bpe_gares_cols", ["gare_count", "has_gare"])

        # Merge Santé metrics (from BPE25)
        merge_clean(
            "bpe_sante_cols",
            [
                "count_hopital",
                "count_maternite",
                "count_centre_sante",
                "count_psy",
                "count_dialyse",
                "count_maison_sante",
                "count_addictologie",
                "count_pmi",
            ],
        )

        # Merge Action Sociale (from BPE25)
        merge_clean(
            "bpe_action_sociale_cols",
            [
                "act_antenne_justice_count",
                "act_france_services_count",
                "act_mairie_count",
                "act_femmes_vuln_count",
            ],
        )

        # Merge mobility metrics
        merge_clean(
            "mob_transports_pub",
            [
                "nb_stops_bus",
                "nb_stops_tram",
                "nb_stops_metro",
                "nb_stops_train",
                "nb_stops_total",
            ],
        )

        # Merge USH Logement Social Delay (EPCI level)
        path_ush = CLEAN_DIR / "log_soc_delay.parquet"
        if path_ush.exists():
            df_ush = pd.read_parquet(path_ush)
            communes_gdf = communes_gdf.merge(
                df_ush, left_on="epci", right_on="epci_code", how="left"
            )
            if "epci_code" in communes_gdf.columns:
                communes_gdf.drop(columns=["epci_code"], inplace=True)
            logging.info("USH Housing delay merged at EPCI level.")

        # Merge Santé APL
        merge_clean("sante_apl", ["sante_apl"])

        # Merge Mobilité Durable
        merge_clean("mob_durable", ["mob_dur_share"])

        # Merge Insécurité
        merge_clean("ter_insecurite", ["ter_insecurite"])

        # Merge RNA RAG Inclusion Stats (New)
        # This brings in inc_rna_{category}_count columns
        merge_clean("rna_inclusion_agg")

        # Merge SIAE Structures Count (New F-39)
        siae_path = CLEAN_DIR / "structures_inclusion.parquet"
        if siae_path.exists():
            siae_df = pd.read_parquet(siae_path)
            siae_agg = (
                siae_df.groupby("codgeo").size().rename("inc_siae_count").reset_index()
            )
            communes_gdf = communes_gdf.merge(siae_agg, on="codgeo", how="left")
            logging.info(f"SIAE structures counts merged from {siae_path}.")
        else:
            logging.warning(f"SIAE structures file not found at {siae_path}.")
            communes_gdf["inc_siae_count"] = np.nan

        # Calculate lien_social_count from RAG categories
        # 'lien_social_count' is used for inc_asso_core_scaled (Lien Social Density)
        # Any association with is_inclusion_relevant=True in BQ contributes here.
        rna_cols = [
            c
            for c in communes_gdf.columns
            if c.startswith("inc_rna_") and c.endswith("_count")
        ]
        if rna_cols:
            communes_gdf["lien_social_count"] = communes_gdf[rna_cols].sum(
                axis=1, min_count=1
            )
            logging.info(
                f"RNA RAG: Calculated lien_social_count from {len(rna_cols)} categories."
            )
        else:
            communes_gdf["lien_social_count"] = np.nan

        # Merge Odace Rent Data
        # The candidate communes artifact carries commune_sk from Odace
        # dim_commune. Do not read a separately generated shared mapping here:
        # that would break run isolation and could mix runs.
        try:
            rent_path = CLEAN_DIR / "odace_loyer_annonce.parquet"
            profil_path = CLEAN_DIR / "odace_logement_profil.parquet"
            if "commune_sk" not in communes_gdf.columns:
                raise PipelineRunError(
                    "Candidate communes artifact is missing the Odace commune_sk key"
                )
            if rent_path.exists() and profil_path.exists():
                df_rent = pd.read_parquet(rent_path)
                df_profil = pd.read_parquet(profil_path)

                # Merge profile info to get human labels
                df_merged = df_rent.merge(
                    df_profil, on="logement_profil_sk", how="inner"
                )

                # Create a standardized column name for each profile
                def get_col_name(row):
                    type_bien = str(row["logement_type"]).lower()
                    typologie = str(row["typologie"]).lower()

                    # Target: appt_all, appt_t1_t2, appt_t3_p, house_all
                    tb = "appt" if "appartement" in type_bien else "house"

                    if "toutes" in typologie:
                        suffix = "all"
                    elif "t1" in typologie:
                        suffix = "t1_t2"
                    elif "t3" in typologie:
                        suffix = "t3_p"
                    else:
                        suffix = "unknown"

                    return f"loyer_m2_moy_{tb}_{suffix}"

                df_merged["odace_col"] = df_merged.apply(get_col_name, axis=1)

                # Pivot: 1 row per commune_sk, columns are the 4 housing types
                # FIX: Coerce to numeric before pivot to avoid "agg function failed [how->mean,dtype->object]"
                # Keep NaNs instead of fillna(0) to avoid turning missing data into zero-rent
                df_merged["loyer_m2_moy"] = pd.to_numeric(
                    df_merged["loyer_m2_moy"], errors="coerce"
                )

                df_pivot = df_merged.pivot_table(
                    index="commune_sk",
                    columns="odace_col",
                    values="loyer_m2_moy",
                    aggfunc="mean",  # Should be unique per sk/col anyway
                ).reset_index()

                # Ensure all 4 expected columns exist even if no data for some profiles
                expected_cols = [
                    "loyer_m2_moy_appt_all",
                    "loyer_m2_moy_appt_t1_t2",
                    "loyer_m2_moy_appt_t3_p",
                    "loyer_m2_moy_house_all",
                ]
                for c in expected_cols:
                    if c not in df_pivot.columns:
                        df_pivot[c] = np.nan

                # Merge into main GDF on commune_sk
                if "commune_sk" in communes_gdf.columns:
                    communes_gdf = communes_gdf.merge(
                        df_pivot, on="commune_sk", how="left"
                    )
                    # logging.info(
                    #     f"Odace Rent: Merged pivoted data. Columns added: {list(df_pivot.columns)}"
                    # )
                    # logging.info(f"DEBUG: communes_gdf cols after merge: {[c for c in communes_gdf.columns if 'loyer' in c]}")
            else:
                raise PipelineRunError(
                    "Odace rent clean files are missing from the candidate"
                )
        except Exception as e:
            logging.error(f"Failed to merge Odace Rent: {e}")
            if isinstance(e, PipelineRunError):
                raise
            raise PipelineRunError("Required Odace rent merge failed") from e

        # Odace rent is the active source. A missing/invalid join must fail the
        # candidate instead of silently reviving the archived loyers dataset.
        rent_col = "loyer_m2_moy_appt_all"
        valid_odace = (
            communes_gdf[rent_col].notna() & (communes_gdf[rent_col] > 0)
            if rent_col in communes_gdf.columns
            else pd.Series(False, index=communes_gdf.index)
        )
        if valid_odace.sum() < 100:
            raise PipelineRunError(
                "Odace rent primary data is missing or invalid "
                f"({valid_odace.sum()} valid rows); refusing legacy rent fallback"
            )

        # Associations merge (Deprecated - Now handled via RNA RAG above)

        # Merge Refugee Associations Count - NOW HANDLED via rna_inclusion_agg.parquet
        # (See fetch_rna_rag_stats in ingest.py and merge_clean("rna_inclusion_agg") above)

        # --- Health Counts & BPE columns are now pre-aggregated in clean_bpe ---

        # Renames
        rename_map = {
            "taux_couverture": "edu_pe_tx_couverture",
            "pp_vacant_plus_2ans_25": "log_priv_vacant_plus_2ans",
            "log_priv_total_24": "log_priv_total",
            "code_be": "bassin_emploi",
            "nom": "libgeo",
            "departement": "dep_code",
            "region": "reg_code",
            "epci": "epci_code",
        }
        communes_gdf.rename(columns=rename_map, inplace=True)

        # Ensure epci_nom exists (placeholder if missing)
        if "epci_nom" not in communes_gdf.columns:
            if "epci_code" in communes_gdf.columns:
                communes_gdf["epci_nom"] = communes_gdf["epci_code"]  # Fallback
            else:
                communes_gdf["epci_nom"] = "Inconnu"

        # Calculated Columns
        # Moved to prescoring.py

        # Rounding
        for col in ["pop_active", "pop_employes", "pop_chomeurs"]:
            if col in communes_gdf.columns:
                communes_gdf[col] = pd.to_numeric(
                    communes_gdf[col], errors="coerce"
                ).round(0)

        # Centroids & Geometry
        # CRITICAL: We project the STORAGE to EPSG:2154 (Lambert-93) for performance and consistency.
        # This allows scoring.py to run without constantly re-projecting.

        if communes_gdf.crs != cfg.PROJECTED_CRS:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=DeprecationWarning,
                    message=".*array with ndim > 0 to a scalar is deprecated.*",
                )
                communes_gdf = communes_gdf.to_crs(cfg.PROJECTED_CRS)

        # Store centroids in projected CRS (for fast distance calc)
        communes_gdf["geometry"] = communes_gdf.geometry.make_valid()
        communes_gdf["geometry"] = communes_gdf.geometry.apply(extract_polygonal)
        communes_gdf = communes_gdf[communes_gdf.geometry.notnull()].copy()
        communes_gdf["centroid"] = communes_gdf.geometry.centroid

        # 4. Bassins de Vie Mapping (for pop_be)
        # We need to load BV mapping. It's in the raw zip usually, but we can extract it or maybe we should have cleaned it?
        # Let's load it from cache as in etl.py
        bv_cfg = config["sources"]["bassins_de_vie"]
        bv_path = CACHE_DIR / bv_cfg["archive_file"]

        if bv_path.exists():
            bv_df = load_dataset(bv_path, bv_cfg)
            bv_df = bv_df.rename(
                columns={
                    "Code géographique": "CODGEO",
                    "Bassin de vie 2022": "bassin_de_vie",
                    "Libellé géographique du bassin de vie 2022": "libelle_bassin_de_vie",
                }
            )
            if "CODGEO" in bv_df.columns and "bassin_de_vie" in bv_df.columns:
                bv_df["CODGEO"] = bv_df["CODGEO"].astype(str).str.zfill(5)
                bv_mapping = bv_df[
                    ["CODGEO", "bassin_de_vie", "libelle_bassin_de_vie"]
                ].set_index("CODGEO")
                communes_gdf = communes_gdf.join(bv_mapping, on="codgeo", how="left")

                # FIX: Handle PLM Arrondissements (Paris, Lyon, Marseille)
                # Arrondissements often don't have a BV code in the official file, but belong to the city BV.
                # Paris: 75101-75120 -> 75056
                # Lyon: 69381-69389 -> 69123
                # Marseille: 13201-13216 -> 13055

                # We can use the global dictionary to lookup the BV for the main city code
                paris_bv = (
                    bv_mapping.loc["75056", "bassin_de_vie"]
                    if "75056" in bv_mapping.index
                    else "75056"
                )
                lyon_bv = (
                    bv_mapping.loc["69123", "bassin_de_vie"]
                    if "69123" in bv_mapping.index
                    else "69123"
                )
                mars_bv = (
                    bv_mapping.loc["13055", "bassin_de_vie"]
                    if "13055" in bv_mapping.index
                    else "13055"
                )

                paris_bv_label = (
                    bv_mapping.loc["75056", "libelle_bassin_de_vie"]
                    if "75056" in bv_mapping.index
                    else "Paris"
                )
                lyon_bv_label = (
                    bv_mapping.loc["69123", "libelle_bassin_de_vie"]
                    if "69123" in bv_mapping.index
                    else "Lyon"
                )
                mars_bv_label = (
                    bv_mapping.loc["13055", "libelle_bassin_de_vie"]
                    if "13055" in bv_mapping.index
                    else "Marseille"
                )

                # Paris Arrondissements
                paris_mask = communes_gdf["codgeo"].between("75101", "75120")

                communes_gdf.loc[
                    paris_mask & communes_gdf["bassin_de_vie"].isna(), "bassin_de_vie"
                ] = paris_bv
                communes_gdf.loc[
                    paris_mask & communes_gdf["libelle_bassin_de_vie"].isna(),
                    "libelle_bassin_de_vie",
                ] = paris_bv_label

                # Check patch result
                # (patched_paris assignment removed as it was unused)

                # Lyon Arrondissements
                lyon_mask = communes_gdf["codgeo"].between("69381", "69389")
                communes_gdf.loc[
                    lyon_mask & communes_gdf["bassin_de_vie"].isna(), "bassin_de_vie"
                ] = lyon_bv
                communes_gdf.loc[
                    lyon_mask & communes_gdf["libelle_bassin_de_vie"].isna(),
                    "libelle_bassin_de_vie",
                ] = lyon_bv_label

                # Marseille Arrondissements
                mars_mask = communes_gdf["codgeo"].between("13201", "13216")
                communes_gdf.loc[
                    mars_mask & communes_gdf["bassin_de_vie"].isna(), "bassin_de_vie"
                ] = mars_bv
                communes_gdf.loc[
                    mars_mask & communes_gdf["libelle_bassin_de_vie"].isna(),
                    "libelle_bassin_de_vie",
                ] = mars_bv_label

                # Calculate pop_active_be for Ratio
                # We need pop_active per commune first. It is already in communes_gdf.
                # Group by bassin_emploi (which is code_be from BMO, but we just joined BV mapping which is bassin_de_vie)
                # Wait, the user asked for "population active du bassin d'emploi".
                # 'bassin_emploi' comes from BMO stats merge.

                # Ratios moved to prescoring.py

        # --- Pre-calculate Ratios and Scaled Scores (Optimization) ---
        # Moved to prescoring.py

        # --- Drop Unused Columns ---
        # --- Drop Unused Columns ---
        # Moved to prescoring.py

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        # LOGGING CRS STATE
        # logger.log_step("build_communes", "DEBUG", {"crs": str(communes_gdf.crs)})

        # Apply PLM Consolidation and arrondissement filtering
        original_crs = communes_gdf.crs
        consolidated_df = consolidate_plm_communes(communes_gdf)
        communes_gdf = gpd.GeoDataFrame(
            consolidated_df, geometry="geometry", crs=original_crs
        )

        # Compute ANVITA scores after PLM consolidation to avoid summing/averaging arrondissement metrics
        excel_path = (
            Path(__file__).parent
            / "data_private"
            / "Tableau de suivi off - membres CT ANVITA.xlsx"
        )
        communes_gdf["ter_anvita_member"] = compute_anvita_scores(
            communes_df=communes_gdf, cache_raw_dir=CACHE_DIR, excel_path=excel_path
        )

        # Compute CTAI scores after PLM consolidation
        ctai_json_path = (
            Path(__file__).parent / "data_private" / "ctai_signataires.json"
        )
        communes_gdf["ter_ctai_member"] = compute_ctai_scores(
            communes_df=communes_gdf, cache_raw_dir=CACHE_DIR, json_path=ctai_json_path
        )

        # Save polygons as WKB in WGS84 (4326) for direct map rendering
        # We ensure we are in 4326 before converting to WKB
        if communes_gdf.crs != "EPSG:4326":
            temp_gdf = communes_gdf.to_crs("EPSG:4326")
            communes_gdf["polygon"] = temp_gdf.geometry.to_wkb()
        else:
            communes_gdf["polygon"] = communes_gdf.geometry.to_wkb()

        # Drop the geometry column and conversion artifacts

        # Drop the geometry column and conversion artifacts to avoid GeoParquet metadata overriding
        # Also drop 'centroid' (shapely objects) which fails to serialize. app/data_loader.py will re-calc it.
        # FIX: Drop names (libgeo, libelle_bassin_de_vie) as they are now in referentiels
        cols_to_drop = ["geometry", "centroid", "libgeo", "libelle_bassin_de_vie"]
        # Handle case where columns might not exist (e.g. if already dropped or renamed)
        cols_to_drop = [c for c in cols_to_drop if c in communes_gdf.columns]
        df_to_save = communes_gdf.drop(columns=cols_to_drop).copy()

        output_path = OUTPUT_DIR / "odis_communes_pre.parquet"
        # logging.info(f"DEBUG: Saving to {output_path}. Columns: {[c for c in df_to_save.columns if 'loyer' in c]}")
        df_to_save.to_parquet(
            output_path, compression="brotli", index=False
        )
        logger.log_step(
            "build_communes",
            "CREATED",
            {"path": str(output_path), "rows": len(df_to_save)},
        )

        return cast(gpd.GeoDataFrame, communes_gdf)

    except Exception as e:
        logger.log_step("build_communes", "ERROR", {"error": str(e)})
        logging.error(f"Build Communes failed: {e}")
        raise PipelineRunError("Required build step 'communes' failed") from e


def build_bassins_de_vie(
    communes_gdf: gpd.GeoDataFrame, config: Dict[str, Any], logger: PipelineLogger
):
    """Aggregates Communes to Bassins de Vie."""
    logger.log_step("build_bassins_de_vie", "STARTED")
    try:
        if communes_gdf.empty or "bassin_de_vie" not in communes_gdf.columns:
            logging.warning("Cannot build BV: Communes empty or missing bassin_de_vie.")
            return

        # Dissolve
        # Fix geometries
        if "polygon" in communes_gdf.columns:
            # Only set geometry to 'polygon' if it's not already the active geometry
            # AND if it seems to contain geometry objects (not bytes)
            if communes_gdf.geometry.name != "polygon":
                if not isinstance(communes_gdf["polygon"].iloc[0], bytes):
                    geoms = [make_valid(wkb.loads(x)) for x in communes_gdf["polygon"]]
                    communes_gdf = communes_gdf.set_geometry(geoms)
        communes_gdf = communes_gdf.set_geometry("geometry")

        communes_gdf["geometry"] = communes_gdf.geometry.apply(make_valid)

        numeric_cols = [
            "population",
            "log_soc_total",
            "log_soc_inoccupes",
            "edu_maternelle_ct",
            "edu_elementaire_ct",
            "ecoles_count",
            "lien_social_count",
            "svc_incl_count",
            "pop_active",
            "pop_employes",
            "pop_chomeurs",
            "log_priv_vacant_plus_2ans",
            "metiers_offres_diff",
            "bpe_creches_count",
            "inc_siae_count",
            "heb_centres_heb_cap",
            "heb_foyers_count",
            "heb_loc_iml_count",
            "heb_habitant_count",
        ]
        # metiers_offres_diff was dropped in build_communes, so we can't sum it here if we load from there.
        # But wait, build_bassins_de_vie takes the returned communes_gdf.
        # If I dropped it, I can't aggregate it.
        # But for BV level, maybe we want the ratio too?
        # The user didn't explicitly ask for ratio in BV dataset, but "renommer la colonne en 'metiers_offres_ratio'" implies globally?
        # Actually, for BV dataset, we aggregate communes.
        # If we want unemployment ratio in BV, we need sum(chomeurs) / sum(active).
        # We have pop_active and pop_chomeurs in numeric_cols.

        agg_dict = {col: "sum" for col in numeric_cols if col in communes_gdf.columns}
        # 4. Dissolve by Bassin de Vie
        # Fix invalid geometries before dissolve
        # 1. Try buffer(0)
        # 1. Clean geometries for dissolve
        communes_gdf["geometry"] = communes_gdf.geometry.make_valid()
        communes_gdf["geometry"] = communes_gdf.geometry.apply(extract_polygonal)
        communes_gdf = communes_gdf[communes_gdf.geometry.notnull()].copy()

        bv_gdf = communes_gdf[communes_gdf["bassin_de_vie"].notnull()].dissolve(
            by="bassin_de_vie", aggfunc=agg_dict
        )

        # FIX: Remove holes from the dissolved polygons
        # Some communes might be "enclaves" or topological errors might create holes.
        # We want the BV to be a solid shape covering everything.
        def remove_holes(geom):
            if isinstance(geom, Polygon):
                return Polygon(geom.exterior)
            elif isinstance(geom, MultiPolygon):
                parts = [Polygon(p.exterior) for p in geom.geoms]
                return MultiPolygon(parts)
            return geom

        # Use the active geometry column
        bv_gdf[bv_gdf.geometry.name] = bv_gdf.geometry.apply(remove_holes)

        bv_gdf.rename(columns={"population": "population_bv"}, inplace=True)

        # Calculate pop_chomage_ratio for BV
        if "pop_active" in bv_gdf.columns and "pop_chomeurs" in bv_gdf.columns:
            bv_gdf["pop_chomage_ratio"] = np.where(
                bv_gdf["pop_active"] > 0,
                bv_gdf["pop_chomeurs"] / bv_gdf["pop_active"],
                0.0,
            )

        # J'Accueille host counts have been moved to BigQuery for security.
        # They are now dynamic fetched in the app.
        bv_gdf["heb_jaccueille_count"] = 0.0

        # Add Label - REMOVED (Now in Referentiels)
        # bv_cfg = config['sources']['bassins_de_vie']
        # bv_path = CACHE_DIR / bv_cfg['archive_file']
        # if bv_path.exists():
        # Logic removed to avoid adding 'libgeo' back
        #    pass

        # Explicitly convert to WKB to ensure we save the PROJECTED geometry (EPSG:2154)
        if bv_gdf.crs != cfg.PROJECTED_CRS:
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=DeprecationWarning,
                    message=".*array with ndim > 0 to a scalar is deprecated.*",
                )
                bv_gdf = bv_gdf.to_crs(cfg.PROJECTED_CRS)

        bv_gdf["polygon"] = bv_gdf.geometry.to_wkb()

        # Drop geometry to avoid GeoParquet 4326 default
        # Also drop name columns if present (libgeo)
        cols_to_drop = ["geometry", "libgeo", "libelle_bassin_de_vie"]
        cols_to_drop = [c for c in cols_to_drop if c in bv_gdf.columns]

        df_to_save = pd.DataFrame(bv_gdf.drop(columns=cols_to_drop))

        output_path = OUTPUT_DIR / "odis_bassins_de_vie.parquet"
        df_to_save.reset_index().to_parquet(
            output_path, compression="brotli", index=False
        )
        logger.log_step(
            "build_bassins_de_vie",
            "CREATED",
            {"path": str(output_path), "rows": len(df_to_save)},
        )

    except Exception as e:
        logger.log_step("build_bassins_de_vie", "ERROR", {"error": str(e)})
        logging.error(f"Build BV failed: {e}")


def build_vertical_tables(config: Dict[str, Any], logger: PipelineLogger):
    """Generates vertical lookup tables."""
    logger.log_step("build_vertical_tables", "STARTED")
    try:
        # 2. Associations
        assoc_path = CLEAN_DIR / "associations_vertical.parquet"
        if assoc_path.exists():
            df = pd.read_parquet(assoc_path)
            df = consolidate_plm_vertical(df, "codgeo", ["id_waldec"], "count")
            df = df[~df["codgeo"].isin(PLM_ARRONDISSEMENTS)].copy()
            out = OUTPUT_DIR / "odis_associations_agg.parquet"
            df.to_parquet(out, compression="brotli", index=False)
            logger.log_step("build_vertical_tables", "ASSOCIATIONS", {"path": str(out)})

        # 3. Structures Inclusion (CCAS/CIAS)
        struct_path = CLEAN_DIR / "structures_inclusion.parquet"
        if struct_path.exists():
            df = pd.read_parquet(struct_path)
            parent_bdvs = {"75056": "75056", "13055": "13055", "69123": "69123"}
            df = consolidate_plm_detail_list(df, "codgeo", parent_bdvs)
            df = df[~df["codgeo"].isin(PLM_ARRONDISSEMENTS)].copy()
            out = OUTPUT_DIR / "odis_ccas.parquet"
            df.to_parquet(out, compression="brotli", index=False)
            logger.log_step("build_vertical_tables", "STRUCTURES", {"path": str(out)})

        # 4. Formations
        form_path = CLEAN_DIR / "formations_annuaire.parquet"
        if form_path.exists():
            df = pd.read_parquet(form_path)
            df_agg = (
                df.groupby(["codgeo", "formation_code"])
                .size()
                .rename("count")
                .reset_index()
            )
            df_agg = consolidate_plm_vertical(
                df_agg, "codgeo", ["formation_code"], "count"
            )
            df_agg = df_agg[~df_agg["codgeo"].isin(PLM_ARRONDISSEMENTS)].copy()

            out = OUTPUT_DIR / "odis_formations_agg.parquet"
            df_agg.to_parquet(
                out, compression="brotli", index=False
            )
            logger.log_step("build_vertical_tables", "FORMATIONS", {"path": str(out)})

        # 5. Refugee Associations (Detailed List)
        refug_path = CLEAN_DIR / "refugee_associations.parquet"
        if refug_path.exists():
            df = pd.read_parquet(refug_path)
            parent_bdvs = {"75056": "75056", "13055": "13055", "69123": "69123"}
            df = consolidate_plm_detail_list(df, "codgeo", parent_bdvs)
            df = df[~df["codgeo"].isin(PLM_ARRONDISSEMENTS)].copy()
            out = OUTPUT_DIR / "odis_refugee_associations.parquet"
            df.to_parquet(out, compression="brotli", index=False)
            logger.log_step(
                "build_vertical_tables", "REFUGEE_ASSOCIATIONS", {"path": str(out)}
            )

    except Exception as e:
        logger.log_step("build_vertical_tables", "ERROR", {"error": str(e)})


def generate_pois(config: Dict[str, Any], logger: PipelineLogger):
    """Generates POIs from clean sources."""
    logger.log_step("generate_pois", "STARTED")
    try:
        pois_list = []

        # 1. Inclusion Services (Cleaned in Ingest)
        incl_clean_path = CLEAN_DIR / "services_inclusion.parquet"
        if incl_clean_path.exists():
            incl_df = pd.read_parquet(incl_clean_path)
            # logging.info(f"Inclusion Clean File Found: {len(incl_df)} rows")

            # Create unique ID from id_structure and service_slug
            import hashlib

            def generate_hash_id(row):
                composite_key = f"{row['id_structure']}_{row['service_slug']}"
                return hashlib.md5(composite_key.encode()).hexdigest()

            incl_pois = pd.DataFrame(
                {
                    "id": incl_df.apply(generate_hash_id, axis=1),
                    "name": incl_df["nom"],
                    "type": incl_df["service_slug"].astype(str),
                    "category": "incl_services",
                    "lat": incl_df["latitude"],
                    "lon": incl_df["longitude"],
                    "codgeo": incl_df["codgeo"],
                }
            )
            pois_list.append(incl_pois)
        else:
            logging.warning("Clean services_inclusion.parquet not found. Run ingest.")

        # 2. BPE - POIs (Ecoles, Sante, Hebergement, Mairie, Gares)
        bpe_pois_path = CLEAN_DIR / "bpe_pois.parquet"
        if bpe_pois_path.exists():
            bpe_pois_df = pd.read_parquet(bpe_pois_path)
            pois_list.append(bpe_pois_df)

        if pois_list:
            all_pois = pd.concat(pois_list, ignore_index=True)

            if "codgeo" in all_pois.columns:
                parent_bdvs = {"75056": "75056", "13055": "13055", "69123": "69123"}
                all_pois = consolidate_plm_detail_list(all_pois, "codgeo", parent_bdvs)
                all_pois = all_pois[
                    ~all_pois["codgeo"].isin(PLM_ARRONDISSEMENTS)
                ].copy()

            # Optimize types
            all_pois["category"] = all_pois["category"].astype("category")
            all_pois["type"] = all_pois["type"].astype("category")
            all_pois["lat"] = all_pois["lat"].astype("float32")
            all_pois["lon"] = all_pois["lon"].astype("float32")
            if "codgeo" in all_pois.columns:
                all_pois["codgeo"] = all_pois["codgeo"].astype("category")

            output_path = OUTPUT_DIR / "odis_pois.parquet"
            all_pois.to_parquet(
                output_path, compression="brotli", index=False
            )
            logger.log_step("generate_pois", "CREATED", {"path": str(output_path)})

    except Exception as e:
        logger.log_step("generate_pois", "ERROR", {"error": str(e)})


def generate_referentiels(config: Dict[str, Any], logger: PipelineLogger):
    """Generates referentiels."""
    logger.log_step("generate_referentiels", "STARTED")
    try:
        refs_list: List[Any] = []

        if refs_list:
            all_refs = pd.concat(refs_list, ignore_index=True)
            output_path = OUTPUT_DIR / "odis_referentiels.parquet"
            all_refs.to_parquet(output_path)
            logger.log_step(
                "generate_referentiels", "CREATED", {"path": str(output_path)}
            )

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

    try:
        # Formations
        form_ref_path = CLEAN_DIR / "formations_referentiel.parquet"
        if form_ref_path.exists():
            form_df = pd.read_parquet(form_ref_path)
            # Expected: code, label
            if "code" in form_df.columns and "label" in form_df.columns:
                form_ref = pd.DataFrame(
                    {
                        "key": "formation_codes",
                        "code": form_df["code"],
                        "label": form_df["label"],
                        #'metadata': None # Removed
                    }
                )
                refs_list.append(form_ref)

        if refs_list:
            all_refs = pd.concat(refs_list, ignore_index=True)
            output_path = OUTPUT_DIR / "referentiels.parquet"
            all_refs.to_parquet(output_path)
            logger.log_step(
                "generate_referentiels", "CREATED", {"path": str(output_path)}
            )

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

    try:
        # Inclusion Services Referentiel (Local CSV)
        incl_cfg = config["sources"].get("referentiel_services_inclusion")
        if incl_cfg:
            incl_path = CACHE_DIR / incl_cfg["local_name"]
            if incl_path.exists():
                # Expected cols: Nom, Label
                incl_df = load_dataset(incl_path, incl_cfg)
                incl_df.columns = [c.strip() for c in incl_df.columns]

                if "Nom" in incl_df.columns and "Label" in incl_df.columns:
                    incl_ref = pd.DataFrame(
                        {
                            "key": "inclusion_services",
                            "code": incl_df["Nom"],
                            "label": incl_df["Label"],
                            #'metadata': None # Removed
                        }
                    )
                    refs_list.append(incl_ref)
                    logger.log_step(
                        "generate_referentiels", "INCLUSION", {"count": len(incl_ref)}
                    )
                else:
                    logging.warning(
                        f"Inclusion Referentiel: Missing columns. Found: {incl_df.columns}"
                    )

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

    try:
        # WALDEC
        waldec_path = CLEAN_DIR / "referentiel_waldec.parquet"
        if waldec_path.exists():
            waldec_df = pd.read_parquet(waldec_path)
            if "code" in waldec_df.columns and "label" in waldec_df.columns:
                waldec_ref = pd.DataFrame(
                    {
                        "key": "waldec_codes",
                        "code": waldec_df["code"],
                        "label": waldec_df["label"],
                    }
                )
                refs_list.append(waldec_ref)
                logger.log_step(
                    "generate_referentiels", "WALDEC", {"count": len(waldec_ref)}
                )

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR", {"error": str(e)})

    try:
        # Communes (from Clean or Raw)
        # We need code (codgeo) and label (libgeo or nom)
        # We can load the clean communes file
        communes_path = CLEAN_DIR / "communes.parquet"
        if communes_path.exists():
            # Clean file uses 'nom' instead of 'libgeo'
            communes_df = pd.read_parquet(
                communes_path, columns=["codgeo", "nom"]
            )
            if "codgeo" in communes_df.columns and "nom" in communes_df.columns:
                communes_ref = pd.DataFrame(
                    {
                        "key": "communes",
                        "code": communes_df["codgeo"],
                        "label": communes_df["nom"],
                    }
                )
                refs_list.append(communes_ref)
                logger.log_step(
                    "generate_referentiels", "COMMUNES", {"count": len(communes_ref)}
                )

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR_COMMUNES", {"error": str(e)})

    try:
        # Bassins de Vie
        bv_cfg = config["sources"]["bassins_de_vie"]
        bv_path = CACHE_DIR / bv_cfg["archive_file"]
        if bv_path.exists():
            # Load raw to get names
            df_bv_source = load_dataset(bv_path, bv_cfg)
            # 'Bassin de vie 2022', 'Libellé géographique du bassin de vie 2022'
            if (
                "Bassin de vie 2022" in df_bv_source.columns
                and "Libellé géographique du bassin de vie 2022" in df_bv_source.columns
            ):
                bv_ref = df_bv_source[
                    ["Bassin de vie 2022", "Libellé géographique du bassin de vie 2022"]
                ].drop_duplicates()
                bv_ref.columns = ["code", "label"]

                bv_ref = pd.DataFrame(
                    {
                        "key": "bassins_de_vie",
                        "code": bv_ref["code"].astype(str),
                        "label": bv_ref["label"],
                    }
                )
                refs_list.append(bv_ref)
                logger.log_step(
                    "generate_referentiels", "BASSINS_VIE", {"count": len(bv_ref)}
                )

    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR_BASSINS_VIE", {"error": str(e)})

    geo_path = CACHE_DIR / "odace_dim_geo.parquet"
    geo_df = pd.DataFrame()
    if geo_path.exists():
        geo_df = pd.read_parquet(geo_path)

    try:
        # Regions. Run-scoped candidates do not materialize the legacy
        # ``clean/regions.parquet`` file, so use the canonical raw COG source.
        regions_path = CLEAN_DIR / "regions.parquet"
        regions_df = None
        if regions_path.exists():
            regions_df = pd.read_parquet(regions_path)
        else:
            regions_cfg = config.get("sources", {}).get("regions_ref", {})
            regions_name = regions_cfg.get("local_name", "referentiel_regions.json")
            regions_raw_path = CACHE_DIR / regions_name
            if regions_raw_path.exists():
                regions_df = load_dataset(regions_raw_path, regions_cfg)
                code_column = "REG" if "REG" in regions_df.columns else "code"
                label_column = (
                    "LIBELLE" if "LIBELLE" in regions_df.columns else "label"
                )
                if code_column in regions_df.columns and label_column in regions_df.columns:
                    regions_df = regions_df.rename(
                        columns={code_column: "code", label_column: "label"}
                    )

        # ODACE dim_geo is the authority for which region codes are actually
        # attached to the commune hierarchy.  The COG file supplies labels,
        # since this ODACE export exposes region_code but no region-level rows
        # or region labels of its own.
        geo_region_codes = None
        if "region_code" in geo_df.columns:
            geo_region_codes = set(
                normalize_geographic_codes(geo_df["region_code"].dropna())
            )

        if regions_df is not None and {"code", "label"}.issubset(regions_df.columns):
            regions_ref = pd.DataFrame(
                {
                    "key": "regions",
                    "code": normalize_geographic_codes(regions_df["code"]),
                    "label": regions_df["label"].astype(str).str.strip(),
                }
            ).drop_duplicates(subset=["code"])
            if geo_region_codes is not None:
                missing_labels = geo_region_codes - set(regions_ref["code"])
                if missing_labels:
                    raise PipelineRunError(
                        "ODACE dim_geo contains region codes absent from the region "
                        "referential: "
                        + ", ".join(sorted(missing_labels))
                    )
                regions_ref = regions_ref[regions_ref["code"].isin(geo_region_codes)]
            refs_list.append(regions_ref)
            logger.log_step(
                "generate_referentiels", "REGIONS", {"count": len(regions_ref)}
            )
    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR_REGIONS", {"error": str(e)})

    try:
        # Departements
        deps_path = CLEAN_DIR / "departements.parquet"
        if deps_path.exists():
            deps_df = pd.read_parquet(deps_path)
            deps_ref = pd.DataFrame(
                {
                    "key": "departements",
                    "code": normalize_geographic_codes(deps_df["code"]),
                    "label": deps_df["label"],
                    "reg_code": (
                        normalize_geographic_codes(deps_df["reg_code"])
                        if "reg_code" in deps_df.columns
                        else None
                    ),
                }
            )
            if "departement_code" in geo_df.columns:
                geo_department_codes = set(
                    normalize_geographic_codes(geo_df["departement_code"].dropna())
                )
                department_codes = set(deps_ref["code"])
                missing_departments = geo_department_codes - department_codes
                if missing_departments:
                    raise PipelineRunError(
                        "ODACE dim_geo contains department codes absent from the "
                        "department referential: "
                        + ", ".join(sorted(missing_departments))
                    )
            refs_list.append(deps_ref)
            logger.log_step(
                "generate_referentiels", "DEPARTEMENTS", {"count": len(deps_ref)}
            )
    except Exception as e:
        logger.log_step(
            "generate_referentiels", "ERROR_DEPARTEMENTS", {"error": str(e)}
        )

    try:
        # ROME Codes (Referential from API)
        rome_path = CACHE_DIR / "rome_referential_api.parquet"
        if rome_path.exists():
            rome_df = pd.read_parquet(rome_path)
            # Expected: code, label
            if "code" in rome_df.columns and "label" in rome_df.columns:
                rome_ref = pd.DataFrame(
                    {
                        "key": "rome_codes",
                        "code": rome_df["code"].astype(str),
                        "label": rome_df["label"],
                    }
                )
                refs_list.append(rome_ref)
                logger.log_step(
                    "generate_referentiels", "ROME_CODES", {"count": len(rome_ref)}
                )
    except Exception as e:
        logger.log_step("generate_referentiels", "ERROR_ROME_CODES", {"error": str(e)})

    # A release without these keys makes the first form page unusable.  Fail
    # the candidate here rather than publishing a referential that only fails
    # later when the Streamlit widget indexes an empty region list.
    present_keys = {
        str(ref["key"].iloc[0])
        for ref in refs_list
        if not ref.empty and "key" in ref.columns
    }
    missing_keys = {"communes", "departements", "regions"} - present_keys
    if missing_keys:
        raise PipelineRunError(
            "Referential generation is incomplete; missing keys: "
            + ", ".join(sorted(missing_keys))
        )

    # Final concatenation and save for all referentiels
    if refs_list:
        all_refs = pd.concat(refs_list, ignore_index=True)
        output_path = OUTPUT_DIR / "odis_referentiels.parquet"
        all_refs.to_parquet(output_path)
        logger.log_step("generate_referentiels", "CREATED", {"path": str(output_path)})


def main(argv=None):
    parser = argparse.ArgumentParser(description="ODIS Build Pipeline")
    parser.add_argument(
        "--steps",
        type=str,
        help="Comma-separated list of steps to run (e.g. communes,pois)",
    )
    args = parser.parse_args(argv)

    logger = PipelineLogger(STATUS_FILE)
    config = load_config(CONFIG_FILE)

    steps_map = {
        "communes": build_communes,
        "bassins_de_vie": lambda cfg, log: build_bassins_de_vie(communes_gdf, cfg, log),
        "vertical_tables": build_vertical_tables,
        "pois": generate_pois,
        "referentiels": generate_referentiels,
    }

    selected_steps = (
        args.steps.split(",")
        if args.steps
        else ["communes", "bassins_de_vie", "vertical_tables", "pois", "referentiels"]
    )

    required_outputs = {
        "communes": ["odis_communes_pre.parquet"],
        "bassins_de_vie": ["odis_bassins_de_vie.parquet"],
        "vertical_tables": [
            "odis_associations_agg.parquet",
            "odis_ccas.parquet",
            "odis_formations_agg.parquet",
            "odis_refugee_associations.parquet",
        ],
        "pois": ["odis_pois.parquet"],
        "referentiels": ["odis_referentiels.parquet"],
    }

    communes_gdf = None
    if "communes" in selected_steps or "bassins_de_vie" in selected_steps:
        # We need communes for BV
        communes_gdf = build_communes(config, logger)
        missing = [
            name
            for name in required_outputs["communes"]
            if not (OUTPUT_DIR / name).exists()
        ]
        if missing:
            raise PipelineRunError(
                f"Build step 'communes' produced no output: {missing}"
            )

    for step_name in selected_steps:
        if step_name == "communes":
            continue  # Already run
        if step_name in steps_map:
            try:
                if step_name == "bassins_de_vie":
                    build_bassins_de_vie(communes_gdf, config, logger)
                else:
                    steps_map[step_name](config, logger)
            except Exception as e:
                logging.exception(
                    f"❌ [BUILD FAILURE] Error running build step '{step_name}'"
                )
                logger.log_step("build_all", "ERROR", {"failed_step": step_name})
                raise
            missing = [
                name
                for name in required_outputs[step_name]
                if not (OUTPUT_DIR / name).exists()
            ]
            if missing:
                logger.log_step(
                    "build_all", "ERROR", {"failed_step": step_name, "missing": missing}
                )
                raise PipelineRunError(
                    f"Build step '{step_name}' produced no required output: {missing}"
                )
        else:
            logging.warning(f"Unknown build step: {step_name}")

    logger.log_step("build_all", "COMPLETED")


if __name__ == "__main__":
    main()
