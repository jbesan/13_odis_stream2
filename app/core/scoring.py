# coding: utf-8
"""
Scoring module for the ODIS application.
"""

from typing import List, Dict, Set, Any, Optional, Union, Tuple, cast
import geopandas as gpd
import numpy as np
import pandas as pd
import config as cfg
from core.models import (
    SearchCriterias,
    CommuneResult,
    CommuneScoreDetail,
    SearchResultsData,
    EmploymentMetrics,
    HousingMetrics,
    EducationMetrics,
    HealthMetrics,
    InclusionMetrics,
    MobilityMetrics,
    TerritoryMetrics,
)
import logging
import logfire

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from utils.common import project_point
from services.rna_rag import RNARagService


def get_effective_weight(
    score_id: str,
    config: Optional[SearchCriterias],
    catalog_weight: float,
) -> float:
    """Calculates canonical effective weight for a criterion.

    Order of operations:
    1. Base weight: user-defined criteria_weights[score_id] if present in config, else catalog_weight.
    2. Org boost: multiplied by org_boosts[score_id] if present.
    3. Proximity frequency multiplier: applied for proximity criteria based on freq_retour.
    """
    if config is None:
        return float(catalog_weight)

    # 1. Base weight (user preference replaces catalog default)
    if config.criteria_weights and score_id in config.criteria_weights:
        weight = float(config.criteria_weights[score_id])
    else:
        weight = float(catalog_weight)

    # 2. Org boost
    org_boosts = getattr(config, "org_boosts", None)
    if org_boosts and score_id in org_boosts:
        weight *= float(org_boosts[score_id])

    # 3. Frequency multiplier for proximity criteria (F-60)
    if score_id in ["mob_epci_scaled", "mob_dist_current_loc_scaled"]:
        freq = getattr(config, "freq_retour", "Pas d'attache particulière")
        multiplier = 1.0
        if freq == "1 fois/semaine":
            multiplier = 3.0
        elif freq == "1 fois/mois":
            multiplier = 2.0
        elif freq == "1 fois/an":
            multiplier = 1.0
        else:
            multiplier = 0.0
        weight *= multiplier

    return weight


def _format_kpi_value(
    val_raw: Any,
    unit: str,
    score_id: str,
    val_scaled: Optional[float] = None,
    fmt: Optional[str] = None,
) -> Any:
    if val_raw is None or pd.isna(val_raw):
        return None
    if fmt == "bool" or (score_id == "mob_gare_scaled" and val_scaled is not None):
        return "Oui" if (val_raw == 1 or val_scaled == 1.0) else "Non"
    if isinstance(val_raw, (int, float, np.integer, np.floating)):
        try:
            f_val = float(val_raw)
            if fmt and fmt != "bool":
                return fmt.format(f_val).replace(",", " ")
            if unit == "habitants":
                v_int = int(round(f_val))
                return f"{v_int:,}".replace(",", " ") if v_int > 1000 else v_int
            elif unit in ["%", "assos/1000 hab.", "crimes+délits/1000 hab."]:
                return round(f_val, 1)
            elif f_val.is_integer():
                v_int = int(f_val)
                return f"{v_int:,}".replace(",", " ") if v_int > 1000 else v_int
            else:
                return round(f_val, 1)
        except Exception:
            return val_raw
    return val_raw


class ScoringEngine:
    """
    The engine responsible for running the ODIS scoring algorithm.
    """

    df_all_communes: pd.DataFrame
    df_bv_geo: gpd.GeoDataFrame
    scores_cat: pd.DataFrame
    incl_index: pd.DataFrame
    associations_data: pd.DataFrame
    formations_data: pd.DataFrame
    codformations_index: Optional[pd.DataFrame]
    waldec_index: Optional[pd.DataFrame]
    global_stats: Optional[Dict[str, Any]]
    bv_data: gpd.GeoDataFrame
    annuaire_ecoles: pd.DataFrame
    annuaire_sante: pd.DataFrame
    annuaire_inclusion: pd.DataFrame
    inclusion_services_index: pd.DataFrame
    regio_referentiel: Optional[pd.DataFrame]
    rome_index: pd.DataFrame
    refugee_associations_data: pd.DataFrame
    live_jobs_data: pd.DataFrame
    siae_jobs_data: pd.DataFrame
    rna_rag_service: Optional[RNARagService]
    current_city_scored_row: Optional[pd.Series]
    _associations_cache: Dict[str, Dict[str, Any]]

    @staticmethod
    def _filter_communes(
        df: pd.DataFrame,
        start_commune: pd.DataFrame,
        loc_type: str,
        loc_code: Union[str, List[str], None],
        config: Optional[SearchCriterias] = None,
    ) -> pd.DataFrame:
        if loc_type == "departement":
            # Handle both single string and list for backward compatibility and flexibility
            codes = [loc_code] if isinstance(loc_code, str) else (loc_code or [])
            filtered_df = df[df["dep_code"].isin(codes)]
        elif loc_type == "region":
            codes = [loc_code] if isinstance(loc_code, str) else (loc_code or [])
            filtered_df = df[df["reg_code"].isin(codes)]
        elif loc_type == "france":
            filtered_df = df[~df["dep_code"].astype(str).str.startswith(("97", "98"))]
        else:
            filtered_df = pd.DataFrame()

        # Apply J'Accueille geographic restriction filter if enabled
        if (
            config is not None
            and getattr(config, "org_strategic_locations_filter", False)
            and getattr(config, "org_context", None) == "jaccueille"
        ):
            # 1. Bassin de vie level: presence of at least one contact (accueillant) OR prospect
            bv_with_presence = df[
                (df.get("heb_accueillants_count", 0) > 0)
                | (df.get("prospects_count", 0) > 0)
            ]["bassin_de_vie"].dropna().unique()

            # 2. Department level: must contain coordinators (strategic locations)
            strategic_deps = getattr(config, "org_strategic_locations", [])
            bv_in_strategic_deps = df[df["dep_code"].isin(strategic_deps)][
                "bassin_de_vie"
            ].dropna().unique()

            # 3. Inner join: intersection of both sets of bassins de vie
            valid_bvs = set(bv_with_presence).intersection(set(bv_in_strategic_deps))

            # Filter final list of communes
            filtered_df = filtered_df[filtered_df["bassin_de_vie"].isin(valid_bvs)]

        return filtered_df

    @staticmethod
    def _scale_series(
        series: pd.Series,
        min_val: float,
        max_val: float,
        scaling_type: str = "linear",
        mu: Optional[float] = None,
        sigma: Optional[float] = None,
    ) -> pd.Series:
        if scaling_type == "gaussian" and mu is not None and sigma is not None:
            return np.exp(-0.5 * ((series - mu) / sigma) ** 2)

        if max_val == min_val:
            return pd.Series(0.0, index=series.index)
        return ((series - min_val) / (max_val - min_val)).clip(0, 1)

    def _get_bounds(self, score_id: str) -> Tuple[float, float]:
        if self.global_stats and score_id in self.global_stats:
            return self.global_stats[score_id]["min"], self.global_stats[score_id][
                "max"
            ]
        row = self.scores_cat[self.scores_cat["score"] == score_id]
        if not row.empty:
            return (
                float(row.iloc[0]["min_bound"])
                if pd.notna(row.iloc[0]["min_bound"])
                else 0.0,
                float(row.iloc[0]["max_bound"])
                if pd.notna(row.iloc[0]["max_bound"])
                else 1.0,
            )
        return 0.0, 1.0

    def _compute_distance_score(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        """
        Calculates linear distance to user's current location.
        🧪 SOTA: Numpy vectorization on metric coordinates for ultra-fast scoring
        """
        current_codgeo_raw = config.commune_actuelle
        current_codgeo = (
            current_codgeo_raw.code
            if hasattr(current_codgeo_raw, "code")
            else current_codgeo_raw
        )

        target_lon, target_lat = None, None

        # Priority mapping from the actively processed dataframe
        if current_codgeo in df.index and "centroid_lon" in df.columns:
            target_lon = df.loc[current_codgeo, "centroid_lon"]
            target_lat = df.loc[current_codgeo, "centroid_lat"]
        elif (
            self.df_all_communes is not None
            and current_codgeo in self.df_all_communes.index
            and "centroid_lon" in self.df_all_communes.columns
        ):
            target_lon = self.df_all_communes.loc[current_codgeo, "centroid_lon"]
            target_lat = self.df_all_communes.loc[current_codgeo, "centroid_lat"]

        if (
            target_lon is not None
            and target_lat is not None
            and pd.notna(target_lon)
            and "centroid_lon" in df.columns
        ):
            # EPSG:2154 is metric (meters). Simple euclidean math avoids geometry overhead entirely
            dx = df["centroid_lon"] - target_lon
            dy = df["centroid_lat"] - target_lat
            df.loc[:, "dist_current_loc"] = np.sqrt(dx**2 + dy**2)

        # Scale if computed
        if "dist_current_loc" in df.columns:
            min_b, max_b = self._get_bounds("mob_dist_current_loc_scaled")
            if pd.isna(max_b):
                max_b = 50000.0  # Default 50km
            # Inverse scale: closer is better
            scaled = self._scale_series(df["dist_current_loc"], min_b, max_b)
            df.loc[:, "mob_dist_current_loc_scaled"] = 1.0 - scaled

        return df

    def _compute_category_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        # Operating in-place on the provided DataFrame

        # Use cached active criteria if available
        active = (
            config.active_criteria
            if config.active_criteria is not None
            else self._get_active_criteria(config)
        )

        # Compute for all categories discovered in config
        for category in self.categories:
            # Skip if category totally irrelevant
            if category == "education" and getattr(config, "nb_enfants", 1) == 0:
                continue

            # Find columns for this category that are active
            cat_scores = self.scores_cat[self.scores_cat.cat == category]

            # Filter only active scores
            active_score_defs = cat_scores[cat_scores["score"].isin(active)]

            if active_score_defs.empty:
                continue

            scores_val: List[Any] = []
            weights_val: List[Any] = []

            for _, s_row in active_score_defs.iterrows():
                sid = s_row["score"]
                bdv_f = float(s_row.get("bdv_factor", 0.0))

                # 1. Get Commune Value
                val_commune = df[sid] if sid in df.columns else None

                # 2. Get BDV Value
                sid_bdv = f"{sid}_bdv"
                val_bdv = df[sid_bdv] if sid_bdv in df.columns else None

                # 3. Parity Check: Log warning if an active criteria is missing from data
                if (
                    config.active_criteria is not None
                    and sid in config.active_criteria
                    and val_commune is None
                    and val_bdv is None
                ):
                    logger.warning(
                        f"⚠️ [SCORING] Active criterion '{sid}' (or '{sid_bdv}') is MISSING from the input data. Score will be defaulted to 0."
                    )
                # 4. Read config-driven missing_strategy
                missing_strat = s_row.get("missing_strategy", "exclude")
                if missing_strat == "zero":
                    if val_commune is not None:
                        val_commune = val_commune.fillna(0.0)
                    if val_bdv is not None:
                        val_bdv = val_bdv.fillna(0.0)

                # Combine using bdv_factor (Multi-mode: Bonus or Malus)
                if val_commune is not None and val_bdv is not None:
                    s_c = val_commune.fillna(0.0)
                    s_b = val_bdv.fillna(0.0)
                    if bdv_f > 0.0:
                        # Formula: Sc + (1 - Sc) * (Sb * factor)
                        # Bassin de Vie opportunities act as a bonus to local ones
                        combined = s_c + (1.0 - s_c) * (s_b * bdv_f)
                        val = pd.Series(
                            np.where(val_commune.notna() | val_bdv.notna(), combined, np.nan),
                            index=df.index,
                        )
                    elif bdv_f < 0.0:
                        # Proportional Malus: Reduces score based on "lack of goodness" in BdV
                        # Formula: Sc - Sc * (1.0 - Sb) * abs(factor)
                        combined = s_c - s_c * (1.0 - s_b) * abs(bdv_f)
                        val = pd.Series(
                            np.where(val_commune.notna(), combined, np.nan),
                            index=df.index,
                        )
                    else:
                        val = val_commune
                elif val_commune is not None:
                    val = val_commune
                elif val_bdv is not None:
                    val = pd.Series(
                        np.where(val_bdv.notna(), val_bdv * max(bdv_f, 0.0), np.nan),
                        index=df.index,
                    )
                else:
                    continue  # Skip if no data available for this criterion

                # Apply canonical effective weight calculation
                catalog_w = float(s_row["weight"])
                weight = get_effective_weight(sid, config, catalog_w)

                # Save uncombined commune value for BdV transparency details
                if val_commune is not None:
                    df[f"{sid}_uncombined_commune"] = val_commune

                # Track valid weights per row (using non-nullity of original sources)
                # If both are null, weight is 0
                has_data = None
                if val_commune is not None and val_bdv is not None:
                    has_data = val_commune.notna() | val_bdv.notna()
                elif val_commune is not None:
                    has_data = val_commune.notna()
                elif val_bdv is not None:
                    has_data = val_bdv.notna()

                valid_weight = weight * (
                    has_data.astype(float) if has_data is not None else 1.0
                )

                # SKIP CURRENT COMMUNE FOR PROXIMITY SCORING
                # The current commune should not be boosted by its own proximity, so we skip the criteria entirely for it.
                if sid in ["mob_epci_scaled", "mob_dist_current_loc_scaled"]:
                    c_act = getattr(config, "commune_actuelle", None)
                    c_code_val = (
                        c_act.code if c_act and hasattr(c_act, "code") else c_act
                    )
                    current_codgeo = str(c_code_val) if c_code_val else ""
                    is_current = df.index == str(current_codgeo)
                    # Set weight to 0 so it's excluded from the denominator
                    valid_weight = cast(Any, np.where(is_current, 0.0, valid_weight))
                # Ensure float dtype
                val = pd.Series(val, index=df.index, dtype=float)

                # Replace NaN with 0 for score addition (since valid_weight handles the skip)
                scores_val.append(
                    np.nan_to_num(val.to_numpy(), nan=0.0) * valid_weight
                )  # Use valid_weight for per-row weighting
                weights_val.append(valid_weight)

                # IMPORTANT: save the combined value back to the dataframe
                # so it can be picked up by format_city_details for the UI breakdown.
                df[sid] = val

            if weights_val:
                scores_arr = np.nan_to_num(sum(scores_val), nan=0.0).astype(float)
                denom_arr = np.array(sum(weights_val), dtype=float)
                raw_scores = np.divide(
                    scores_arr,
                    denom_arr,
                    out=np.zeros_like(denom_arr, dtype=float),
                    where=denom_arr > 0,
                )
                s = pd.Series(np.where(denom_arr > 0, raw_scores, np.nan), index=df.index, dtype=float)

                # Absolute Category Score (0.0 to 1.0): raw weighted mean of active criteria
                df[f"{category}_cat_score"] = s

        return df

    @logfire.instrument("_compute_weighted_score: {config}")
    def _compute_weighted_score(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.Series:
        h = config.compute_hash()
        logfire.info("Computing weighted score for hash: {search_hash}", search_hash=h)
        total_score = pd.Series(0.0, index=df.index)
        total_weight = 0.0

        weights = {cat: getattr(config, f"poids_{cat}", 0.0) for cat in self.categories}

        for cat, weight in weights.items():
            # Robust Check: Force exclusion if conditions met, even if column exists
            if cat == "education" and config.nb_enfants == 0:
                continue

            # Skip if category score not computed (e.g. no children)
            col = f"{cat}_cat_score"
            if col not in df.columns:
                continue

            val = df[col].fillna(0)
            valid_mask = df[col].notna()  # Where score exists

            weighted_val = val * weight
            total_score += weighted_val

            # Add weight where valid
            current_weight_series = pd.Series(0.0, index=df.index)
            current_weight_series[valid_mask] = weight
            total_weight += current_weight_series

        # Ensure no division by zero
        if isinstance(total_weight, (int, float)):
            return total_score / total_weight if total_weight > 0 else total_score
        else:
            return (total_score / total_weight).fillna(0)

    def _prune_irrelevant_metrics(
        self, df: pd.DataFrame, config: SearchCriterias, aggressive: bool = False
    ) -> pd.DataFrame:
        """
        Prunes redundant columns to optimize memory usage.
        Conservative approach by default, aggressive approach clears everything except essential UI/Map columns.
        """
        if df is None or df.empty:
            return df

        to_drop = []

        if aggressive:
            # SOTA Optimization: Keep identifiers, scores, AND essential geometries for the filtered subset.
            # We only keep geometries for the search area (e.g. 1 department),
            # which is lightweight enough (~1MB) for the session state.
            keep_cols = {
                "libgeo",
                "weighted_score",
                "population",
                "dep_code",
                "reg_code",
                "epci_code",
                "bassin_de_vie",
                "libelle_bassin_de_vie",
                "polygon",
                "centroid",
            }
            to_drop = [c for c in df.columns if c not in keep_cols]
        else:
            # 1. Deny-list: Explicitly requested redundant BdV columns
            to_drop = ["polygon_bdv", "libgeo_bdv", "centroid_bdv"]

            # 2. Selective Pruning: Drop unselected high-level scores
            active_ids = None
            if (
                hasattr(config, "active_criteria")
                and config.active_criteria is not None
            ):
                active_ids = set(config.active_criteria)
            else:
                try:
                    active_ids = set(self._get_active_criteria(config))
                except Exception:
                    pass

            if active_ids:
                scaled_cols = [c for c in df.columns if c.endswith("_scaled")]
                for col in scaled_cols:
                    if col not in active_ids:
                        to_drop.append(col)

        actual_drops = [c for c in to_drop if c in df.columns]
        if actual_drops:
            df = df.drop(columns=actual_drops)

        return df

    @classmethod
    def from_app_data(cls, app_data: Dict[str, Any]) -> "ScoringEngine":
        """
        Factory method to create a ScoringEngine from the standard app_data dictionary.
        """
        return cls(
            df_all_communes=app_data.get("odis", pd.DataFrame()),
            df_bv_geo=app_data.get("bv_geo", pd.DataFrame()),
            scores_cat=app_data.get("scores_cat", pd.DataFrame()),
            incl_index=app_data.get("incl_index", pd.DataFrame()),
            associations_data=app_data.get("associations_data", pd.DataFrame()),
            formations_data=app_data.get("formations_data", pd.DataFrame()),
            codformations_index=app_data.get("codformations_index"),
            waldec_index=app_data.get("waldec_index"),
            global_stats=app_data.get("global_stats"),
            bv_data=app_data.get("bv_data"),
            annuaire_ecoles=app_data.get("annuaire_ecoles", pd.DataFrame()),
            annuaire_sante=app_data.get("annuaire_sante", pd.DataFrame()),
            annuaire_inclusion=app_data.get("annuaire_inclusion", pd.DataFrame()),
            inclusion_services_index=app_data.get(
                "inclusion_services_index", pd.DataFrame()
            ),
            regio_referentiel=app_data.get("regio_referentiel"),
            rome_index=app_data.get("rome_index", pd.DataFrame()),
            refugee_associations_data=app_data.get(
                "refugee_associations_data", pd.DataFrame()
            ),
            live_jobs_data=app_data.get("live_jobs_data", pd.DataFrame()),
            siae_jobs_data=app_data.get("siae_jobs_data", pd.DataFrame()),
            rna_rag_service=app_data.get("rna_rag_service"),
        )

    def __init__(
        self,
        df_all_communes: pd.DataFrame,
        df_bv_geo: gpd.GeoDataFrame,
        scores_cat: pd.DataFrame,
        incl_index: pd.DataFrame,
        associations_data: pd.DataFrame,
        formations_data: pd.DataFrame,
        codformations_index: Optional[pd.DataFrame] = None,
        waldec_index: Optional[pd.DataFrame] = None,
        global_stats: Optional[Dict[str, Any]] = None,
        bv_data: gpd.GeoDataFrame = None,
        annuaire_ecoles: pd.DataFrame = pd.DataFrame(),
        annuaire_sante: pd.DataFrame = pd.DataFrame(),
        annuaire_inclusion: pd.DataFrame = pd.DataFrame(),
        inclusion_services_index: pd.DataFrame = pd.DataFrame(),
        regio_referentiel: Optional[pd.DataFrame] = None,
        rome_index: pd.DataFrame = pd.DataFrame(),
        refugee_associations_data: pd.DataFrame = pd.DataFrame(),
        live_jobs_data: pd.DataFrame = pd.DataFrame(),
        siae_jobs_data: pd.DataFrame = pd.DataFrame(),
        rna_rag_service: Optional[RNARagService] = None,
    ):
        self.current_city_scored_row = None
        self.df_all_communes = df_all_communes
        self.df_bv_geo = df_bv_geo
        self.scores_cat = scores_cat
        self.incl_index = incl_index
        self.associations_data = associations_data
        self.formations_data = formations_data
        self.global_stats = global_stats
        self.bv_data = bv_data if bv_data is not None else df_bv_geo
        self.annuaire_ecoles = annuaire_ecoles
        self.annuaire_sante = annuaire_sante
        self.annuaire_inclusion = annuaire_inclusion
        self.inclusion_services_index = inclusion_services_index
        self.codformations_index = codformations_index
        self.waldec_index = waldec_index
        self.rome_index = rome_index
        self.refugee_associations_data = refugee_associations_data
        self.live_jobs_data = live_jobs_data
        self.siae_jobs_data = siae_jobs_data

        # Initialize RNA RAG Service if not provided
        self.rna_rag_service = rna_rag_service
        if self.rna_rag_service is None:
            try:
                self.rna_rag_service = RNARagService()
            except Exception as e:
                logger.warning(
                    f"Could not initialize RNARagService in ScoringEngine: {e}"
                )

        # Batch cache for associations (Store for detailed results)
        self._associations_cache = {}

        # Discover and normalize categories from the scoring definitions
        if not self.scores_cat.empty and "cat" in self.scores_cat.columns:
            self.categories = sorted(
                [
                    cat.replace("é", "e").replace("ê", "e").replace("à", "a").lower()
                    for cat in self.scores_cat["cat"].unique()
                ]
            )
        else:
            self.categories = []
        logger.info(f"ScoringEngine initialized with categories: {self.categories}")

    def _get_active_criteria(self, config: Optional[SearchCriterias]) -> Set[str]:
        """Centralized logic to determine which criteria are active based on config."""
        active = set()

        # 1. Baseline Criteria: Always active (Dynamic discovery from config)
        if not self.scores_cat.empty and "baseline" in self.scores_cat.columns:
            baseline_ids = self.scores_cat[self.scores_cat["baseline"] == True][
                "score"
            ].tolist()
            active.update(baseline_ids)

        # If no config provided, we default to baseline + any other present scores
        if config is None:
            if not self.scores_cat.empty and "score" in self.scores_cat.columns:
                # Filter to only those present in the main dataset
                return {c for c in active if c in self.df_all_communes.columns}
            return active

        # 2. Proximity scores (conditional on local search)
        freq_retour = getattr(config, "freq_retour", "Pas d'attache particulière")
        if (
            self._is_local_search(config)
            and freq_retour != "Pas d'attache particulière"
        ):
            active.add("mob_epci_scaled")
            active.add("mob_dist_current_loc_scaled")

        # 3. Employment & Formations (Only if specific adult was searched)
        nb_adultes = getattr(config, "nb_adultes", 0)
        codes_metiers = getattr(config, "codes_metiers", [])
        codes_formations = getattr(config, "codes_formations", [])

        for i in range(nb_adultes):
            adult_idx = i + 1
            # Employment
            if i < len(codes_metiers) and codes_metiers[i]:
                active.add(f"met_match_adult{adult_idx}_scaled")
                active.add(f"met_match_adult{adult_idx}_tension_scaled")
                active.add(f"met_siae_match_adult{adult_idx}_scaled")

            # Formations
            if i < len(codes_formations) and codes_formations[i]:
                active.add(f"form_match_adult{adult_idx}_scaled")

        # 4. Logement & Hébergement (Conditional activation)
        heb_sel = getattr(config, "hebergement_cible", [])
        if "Location avec Intermédiation" in heb_sel:
            active.add("heb_loc_iml_scaled")
            active.add("log_vac_scaled")

        if "Centre d'hébergement et de réinsertion sociale (CHRS)" in heb_sel:
            active.add("heb_chrs_scaled")

        if "Centre provisoire d'hébergement (CPH)" in heb_sel:
            active.add("heb_cph_scaled")

        if "Centre d'accueil de demandeurs d'asile (CADA)" in heb_sel:
            active.add("heb_cada_scaled")

        if "Foyer de Jeunes Travailleurs (FJT)" in heb_sel:
            active.add("heb_fjt_scaled")

        if "Pensions de Famille" in heb_sel:
            active.add("heb_pension_scaled")

        if "Chez l'habitant" in heb_sel:
            active.add("heb_asso_habitant_scaled")
            active.add("heb_jaccueille_accueillants_score")
            active.add("heb_jaccueille_prospects_score")
            active.add("log_occup_scaled")

        # Rent scaling activation (if Location or IML)
        logement_type = getattr(config, "logement", "Location")
        if logement_type == "Location" or "Location avec Intermédiation" in heb_sel:
            active.add("log_vac_scaled")
            type_log_attr = getattr(config, "type_logement", "appt_all")
            type_log = (
                type_log_attr.code if hasattr(type_log_attr, "code") else type_log_attr
            )
            active.add(f"log_loyer_moyen_{type_log}_scaled")

        if logement_type == "Logement Social":
            active.add("log_soc_inoc_scaled")
            active.add("log_soc_delay_scaled")

        # 5. Education (Conditional on children)
        nb_enfants = getattr(config, "nb_enfants", 0)
        if nb_enfants > 0:
            active.add("youth_decline_scaled")
            active.add("edu_classes_ferm_scaled")
            edu_map = {
                "Crèche / Assistante Maternelle": "edu_petite_enfance_scaled",
                "Petite Enfance/Crêche": "edu_petite_enfance_scaled",
                "Maternelle": "edu_maternelle_scaled",
                "Elémentaire": "edu_elementaire_scaled",
                "Collège": "edu_college_scaled",
                "Lycée": "edu_lycee_scaled",
            }
            # Add specific levels
            for level in getattr(config, "classe_enfants", []):
                if level in edu_map:
                    active.add(edu_map[level])

        # 6. Sante (Conditional on needs)
        besoin_sante_list = getattr(config, "besoin_sante", [])

        sante_map = {
            "Hôpital": "sante_hopital_scaled",
            "Maternité": "sante_maternite_scaled",
            "Soutien Psychologique": "sante_psy_scaled",
            "Dialyse": "sante_dialyse_scaled",
            "Maison de santé": "sante_maison_sante_scaled",
            "Addictologie": "sante_addictologie_scaled",
            "Santé maternelle et infantile (PMI)": "sante_pmi_scaled",
        }

        for besoin in besoin_sante_list:
            if besoin in sante_map:
                active.add(sante_map[besoin])

        # 7. Inclusion (Additional optional criteria)
        inc_services = getattr(config, "inc_services_selection", [])
        if inc_services:
            active.add("inc_services_incl_scaled")

        if getattr(config, "inc_asso_add_selection", []):
            active.add("inc_asso_add_scaled")

        # 8. Territory (Partners & Strategic Locations)
        if getattr(config, "org_strategic_locations", []):
            active.add("ter_strategic_locations_scaled")

        return active

    def format_city_details(
        self, row: pd.Series, config: Optional[SearchCriterias] = None
    ) -> CommuneResult:
        """
        Formats detailed information for a city to be displayed in the UI.
        Returns a CommuneResult Pydantic model.
        Hydrates static data (geometries, labels) from the shared global dataset.
        """
        codgeo_str = str(row["codgeo"]) if "codgeo" in row else str(row.name)

        # 🧪 SOTA: Hydrate static data from the shared global dataframe (Singleton)
        # This allows 'row' to only contain the computed results (scores).
        try:
            static_row = self.df_all_communes.loc[codgeo_str]
        except KeyError:
            # Fallback if the code is not in the baseline (unlikely)
            static_row = row

        # Identity
        identity = {
            "codgeo": codgeo_str,
            "name": static_row.get("libgeo", "Inconnu"),
            "population": int(round(static_row.get("population", 0))),
            "bassin_de_vie": static_row.get("libelle_bassin_de_vie", "N/A"),
            "global_score": float(row.get("weighted_score", 0.0))
            if "weighted_score" in row
            else 0.0,
        }

        # Domain Objects (Using unified models for robust typing)
        emploi_data = EmploymentMetrics()
        edu_data = EducationMetrics()
        sante_data = HealthMetrics()
        incl_data = InclusionMetrics()
        mob_data = MobilityMetrics()
        logement_data = HousingMetrics()
        territoire_data = TerritoryMetrics()

        # Populate territory defaults
        territoire_data.ter_insecurite = float(
            static_row.get("ter_insecurite_rate", 0.0)
        )
        territoire_data.is_strategic = bool(static_row.get("is_strategic", False))
        
        med_val = static_row.get("maire_extreme_droite")
        territoire_data.maire_extreme_droite = bool(med_val) if pd.notna(med_val) else False
        
        eh_val = static_row.get("electoral_history")
        territoire_data.electoral_history = str(eh_val) if pd.notna(eh_val) else None

        # Populate mobility & static defaults from static_row
        mob_data.bus_stops = int(static_row.get("nb_stops_bus", 0))
        mob_data.tram_stops = int(static_row.get("nb_stops_tram", 0))
        mob_data.metro_stops = int(static_row.get("nb_stops_metro", 0))
        mob_data.train_stops = int(static_row.get("nb_stops_train", 0))
        mob_data.total_stops = int(static_row.get("nb_stops_total", 0))
        mob_data.stop_density = float(static_row.get("mob_trans_pub_stop_density", 0.0))

        # Populate proximity metrics
        if "mob_epci_scaled" in row:
            val = row["mob_epci_scaled"]
            if pd.notna(val):
                mob_data.is_same_epci = bool(val == 1.0)

        mob_data.mob_dur_share = float(static_row.get("mob_dur_share", 0.0))

        if "dist_current_loc" in row:
            val = row["dist_current_loc"]
            if pd.notna(val):
                # dist_current_loc is in meters (EPSG:2154)
                mob_data.distance_to_current_km = round(float(val) / 1000.0, 1)

        # Populate logement defaults
        logement_data.host_count = int(static_row.get("heb_accueillants_count", 0))
        logement_data.log_soc_delay = float(static_row.get("log_soc_delay", 0.0))
        sante_data.sante_rdv_delay = float(static_row.get("sante_apl", 0.0))

        # Extract lat/lon from geometry if available (Use static_row)
        lat, lon = 0.0, 0.0

        if "centroid_lon" in static_row and pd.notna(static_row["centroid_lon"]):
            try:
                # Project from Lambert-93 (2154) to Lat/Lon (4326) for UI/Analysis consumers
                curr_x, curr_y = static_row["centroid_lon"], static_row["centroid_lat"]
                lon, lat = project_point(
                    curr_x, curr_y, from_crs="EPSG:2154", to_crs="EPSG:4326"
                )
            except Exception:
                pass

        # Use cached active criteria if available
        active_ids = (
            config.active_criteria
            if config and config.active_criteria is not None
            else self._get_active_criteria(config)
        )

        # 🧪 SOTA: Dynamic category discovery for weights based on the poids_{cat} convention
        cat_weights = {
            cat: getattr(config, f"poids_{cat}", 1.0) for cat in self.categories
        }

        # Skip categories based on config
        if config:
            # Education remains optional (only if children are present)
            if config.nb_enfants == 0:
                cat_weights["education"] = 0.0

        # 2. Identify displayed criteria and compute internal weights based on visibility
        displayed_items: List[Dict[str, Any]] = []
        cat_internal_weights: Dict[
            str, float
        ] = {}  # sum of w_crit for displayed items in each normalized cat
        active_norm_cats = set()

        for _, score_row in self.scores_cat.iterrows():
            score_id = score_row["score"]
            val_scaled = (
                float(row[score_id])
                if score_id in row and pd.notna(row[score_id])
                else None
            )

            # Skip if not active or if value is missing
            if (config and score_id not in active_ids) or val_scaled is None:
                continue

            cat = score_row["cat"]
            norm_cat = cat.replace("é", "e").replace("ê", "e").replace("à", "a").lower()
            active_norm_cats.add(norm_cat)

            w_crit = get_effective_weight(
                score_id, config, float(score_row["weight"])
            )

            cat_internal_weights[norm_cat] = (
                cat_internal_weights.get(norm_cat, 0.0) + w_crit
            )
            displayed_items.append(
                {
                    "score_row": score_row,
                    "val_scaled": val_scaled,
                    "norm_cat": norm_cat,
                    "w_crit": w_crit,
                }
            )

        # 3. Total Category Weight Sum (Effective for displayed items)
        total_cat_weight = sum(cat_weights[c] for c in active_norm_cats)
        if total_cat_weight == 0:
            total_cat_weight = 1.0

        # Structured Scores for CommuneResult
        structured_scores: Dict[str, List[CommuneScoreDetail]] = {}

        # 4. Populate details with correctly weighted items
        for item in displayed_items:
            score_row = item["score_row"]
            cat = score_row["cat"]
            norm_cat = item["norm_cat"]
            val_scaled = item["val_scaled"]
            w_crit = item["w_crit"]
            score_id = score_row["score"]

            if norm_cat not in structured_scores:
                structured_scores[norm_cat] = []

            # Improved Valeur KPI (Checking both shared data and computed results)
            val_raw = None
            raw_metric_col = score_row["metric"]

            # KPI could be either in computed results OR in static shared data
            src_row = (
                static_row
                if raw_metric_col in static_row
                else (row if raw_metric_col in row else None)
            )

            if (
                src_row is not None
                and raw_metric_col in src_row
                and pd.notna(src_row[raw_metric_col])
            ):
                val = src_row[raw_metric_col]
                d_factor = float(score_row.get("display_factor", 1.0))
                if pd.api.types.is_number(val):
                    val_raw = float(val * d_factor)
                else:
                    try:
                        val_raw = float(val) * d_factor
                    except:
                        val_raw = val
            elif score_id in row and pd.notna(row[score_id]):
                # Fallback to the scaled score itself if the raw metric is missing from the dataset (e.g. for precomputed indicators like has_gare)
                val = row[score_id]
                d_factor = float(score_row.get("display_factor", 1.0))
                if pd.api.types.is_number(val):
                    val_raw = float(val * d_factor)
                else:
                    val_raw = val

            # Format val_raw and val_kpi_bdv using canonical formatting helper
            unit = score_row.get("unit", score_row.get("description", ""))
            fmt = score_row.get("format", None)
            val_raw = _format_kpi_value(val_raw, unit, score_id, val_scaled, fmt)

            # Impact = (w_crit / sum_weights_in_cat) * (cat_weight / total_cat_weight)
            rel_weight = (w_crit / cat_internal_weights[norm_cat]) * (
                cat_weights[norm_cat] / total_cat_weight
            )

            # BdV details extraction
            bdv_f = float(score_row.get("bdv_factor", 0.0))
            bdv_applied = False
            val_kpi_commune = val_raw
            val_kpi_bdv = None
            score_norm_commune = None
            score_norm_bdv = None

            if bdv_f != 0.0:
                sid_bdv = f"{score_id}_bdv"
                has_bdv_data = (
                    (sid_bdv in row and pd.notna(row[sid_bdv]))
                    or (sid_bdv in static_row and pd.notna(static_row[sid_bdv]))
                )
                if has_bdv_data:
                    bdv_applied = True
                    score_norm_commune = (
                        float(row[f"{score_id}_uncombined_commune"])
                        if f"{score_id}_uncombined_commune" in row
                        and pd.notna(row[f"{score_id}_uncombined_commune"])
                        else val_scaled
                    )
                    score_norm_bdv = (
                        float(row[sid_bdv])
                        if sid_bdv in row and pd.notna(row[sid_bdv])
                        else (
                            float(static_row[sid_bdv])
                            if sid_bdv in static_row and pd.notna(static_row[sid_bdv])
                            else None
                        )
                    )
                    val_kpi_commune = val_raw
                    raw_bdv_col = f"{raw_metric_col}_bdv"
                    bdv_src = (
                        static_row
                        if (static_row is not None and raw_bdv_col in static_row)
                        else (row if (row is not None and raw_bdv_col in row) else None)
                    )
                    if (
                        bdv_src is not None
                        and raw_bdv_col in bdv_src
                        and pd.notna(bdv_src[raw_bdv_col])
                    ):
                        b_val = bdv_src[raw_bdv_col]
                        d_factor = float(score_row.get("display_factor", 1.0))
                        if pd.api.types.is_number(b_val):
                            raw_b = float(b_val * d_factor)
                        else:
                            raw_b = b_val
                        val_kpi_bdv = _format_kpi_value(raw_b, unit, score_id, score_norm_bdv, fmt)

            structured_scores[norm_cat].append(
                CommuneScoreDetail(
                    label=score_row.get("label", score_id),
                    score_id=score_id,
                    valeur_kpi=val_raw,
                    score_normalise=val_scaled,
                    unit=unit,
                    relative_weight=round(rel_weight * 100, 1),
                    valeur_kpi_commune=val_kpi_commune,
                    valeur_kpi_bdv=val_kpi_bdv,
                    score_normalise_commune=score_norm_commune,
                    score_normalise_bdv=score_norm_bdv,
                    bdv_factor=bdv_f,
                    bdv_applied=bdv_applied,
                    strong_point_text=str(score_row.get("score_affichage"))
                    if pd.notna(score_row.get("score_affichage"))
                    else "",
                    high_value_adjective=str(score_row.get("high_value_adjective"))
                    if pd.notna(score_row.get("high_value_adjective"))
                    else "",
                )
            )

        # 2. Housing Details (Pricing Specifics)
        housing_types = ["appt_all", "appt_t1_t2", "appt_t3_p", "house_all"]
        for ht in housing_types:
            raw_col = f"loyer_m2_moy_{ht}"
            scaled_col = f"log_loyer_moyen_{ht}_scaled"

            variant_data = {
                "raw": float(row[raw_col])
                if raw_col in row and pd.notna(row[raw_col])
                else None,
                "scaled": float(row[scaled_col])
                if scaled_col in row and pd.notna(row[scaled_col])
                else None,
            }
            logement_data.housing_price_variants[ht] = variant_data

            # Set top-level raw value if it's the selected type
            type_log = None
            if config and config.type_logement:
                type_log = (
                    config.type_logement.code
                    if hasattr(config.type_logement, "code")
                    else config.type_logement
                )

            if config and type_log == ht:
                logement_data.price_per_sqm = variant_data["raw"]
            elif not config and ht == "appt_all":
                logement_data.price_per_sqm = variant_data["raw"]

        # 3. Emploi (Top 10 from Live Jobs & Formations)
        c_code = codgeo_str
        if c_code:
            # --- Live Jobs Match (ROME) ---
            if not self.live_jobs_data.empty:
                live_city = self.live_jobs_data[
                    self.live_jobs_data["commune"] == c_code
                ]
                if not live_city.empty:
                    # Global Summary
                    live_summary = (
                        live_city.groupby("romeLibelle")["total_postes"].sum().to_dict()
                    )
                    emploi_data.standard_jobs_summary = live_summary
                    emploi_data.standard_jobs_total = int(
                        live_city["total_postes"].sum()
                    )

                    # Matching Summary (filtered by config)
                    if config and config.codes_metiers:
                        # Flatten the list of lists of ROME codes
                        target_romes = set()
                        for codes in config.codes_metiers:
                            if isinstance(codes, list):
                                for c in codes:
                                    val = c.code if hasattr(c, "code") else c
                                    if len(val) == 5:
                                        target_romes.add(val)
                            elif isinstance(codes, str) and len(codes) == 5:
                                target_romes.add(codes)
                            elif hasattr(codes, "code"):
                                val = codes.code
                                if len(val) == 5:
                                    target_romes.add(val)

                        if target_romes:
                            matching_city = live_city[
                                live_city["romeCode"].isin(target_romes)
                            ]
                            emploi_data.standard_jobs_matching_summary = (
                                matching_city.groupby("romeLibelle")["total_postes"]
                                .sum()
                                .to_dict()
                            )
                            emploi_data.standard_jobs_matching_total = int(
                                matching_city["total_postes"].sum()
                            )

                    # Top 10 unique labels by volume with postes count
                    top_live = (
                        live_city.groupby("romeLibelle")["total_postes"]
                        .sum()
                        .sort_values(ascending=False)
                        .head(10)
                    )
                    emploi_data.top_professions = [
                        f"{label} ({int(vol)} postes)"
                        for label, vol in top_live.items()
                    ]
                else:
                    emploi_data.standard_jobs_total = 0
                    emploi_data.standard_jobs_matching_total = 0
                    emploi_data.top_professions = []

            # --- SIAE Jobs Match (New F-39) ---
            if not self.siae_jobs_data.empty:
                siae_city = self.siae_jobs_data[
                    self.siae_jobs_data["codgeo"] == codgeo_str
                ]
                if not siae_city.empty:
                    # Map rome to label using rome_index if rome_label is missing
                    if "rome_label" in siae_city.columns:
                        group_keys = siae_city["rome_label"]
                    elif not self.rome_index.empty:
                        group_keys = (
                            siae_city["rome"]
                            .map(self.rome_index["label"])
                            .fillna(siae_city["rome"])
                        )
                    else:
                        group_keys = siae_city["rome"]

                    emploi_data.inclusive_jobs_total = int(len(siae_city))
                    emploi_data.inclusive_jobs_summary = (
                        siae_city.groupby(group_keys).size().to_dict()
                    )
                    emploi_data.inclusive_jobs_matching_summary = {}
                    emploi_data.inclusive_jobs_matching_total = 0

                    if config and config.codes_metiers:
                        siae_prefixes = set()
                        for codes in config.codes_metiers:
                            if isinstance(codes, list):
                                for c in codes:
                                    val = c.code if hasattr(c, "code") else c
                                    if len(val) >= 3:
                                        siae_prefixes.add(val[:3])
                            else:
                                val = codes.code if hasattr(codes, "code") else codes
                                if isinstance(val, str) and len(val) >= 3:
                                    siae_prefixes.add(val[:3])

                        if siae_prefixes:
                            # Use 'rome' column
                            siae_matching = siae_city[
                                siae_city["rome"].str[:3].isin(siae_prefixes)
                            ]
                            matching_dict = (
                                siae_matching.groupby(
                                    group_keys.loc[siae_matching.index]
                                )
                                .size()
                                .to_dict()
                            )
                            emploi_data.inclusive_jobs_matching_summary = matching_dict
                            emploi_data.inclusive_jobs_matching_total = sum(
                                matching_dict.values()
                            )
                else:
                    emploi_data.inclusive_jobs_total = 0
                    emploi_data.inclusive_jobs_summary = {}
                    emploi_data.inclusive_jobs_matching_summary = {}
                    emploi_data.inclusive_jobs_matching_total = 0

            # Formations logic remains
            if not self.formations_data.empty:
                city_forms = self.formations_data[
                    self.formations_data["codgeo"] == c_code
                ]
                if not city_forms.empty:
                    if (
                        self.codformations_index is not None
                        and not self.codformations_index.empty
                    ):
                        form_codes = city_forms["formation_code"].astype(str)
                        merged_f = form_codes.to_frame("formation_code").merge(
                            self.codformations_index,
                            left_on="formation_code",
                            right_index=True,
                            how="left",
                        )
                        merged_f["label"] = merged_f["label"].fillna(
                            merged_f["formation_code"]
                        )
                        emploi_data.training_programs = sorted(
                            merged_f["label"].unique().tolist()
                        )

                        # --- Matching Training Logic (F-33) ---
                        if config and config.codes_formations:
                            target_forms = set()
                            for adult_forms in config.codes_formations:
                                if isinstance(adult_forms, list):
                                    for f in adult_forms:
                                        val = f.code if hasattr(f, "code") else f
                                        target_forms.add(str(val))

                            if target_forms:
                                matching_f_codes = form_codes[
                                    form_codes.isin(target_forms)
                                ]
                                if not matching_f_codes.empty:
                                    matching_labels = self.codformations_index.loc[
                                        self.codformations_index.index.intersection(
                                            matching_f_codes.unique()
                                        ),
                                        "label",
                                    ]
                                    emploi_data.training_programs_matching = sorted(
                                        matching_labels.tolist()
                                    )
                    else:
                        emploi_data.training_programs = sorted(
                            city_forms["formation_code"].unique().tolist()
                        )

        # 4. Education & Sante Counts & Grouped Etablissements
        for dom, mapping, annuaire, data_obj in [
            (
                "education",
                {
                    "maternelle": "edu_maternelle_ct",
                    "elementaire": "edu_elementaire_ct",
                    "college": "edu_college_ct",
                    "lycee": "edu_lycee_ct",
                },
                self.annuaire_ecoles,
                edu_data,
            ),
            (
                "sante",
                {
                    "hopital": "count_hopital",
                    "maternite": "count_maternite",
                    "psy": "count_psy",
                },
                self.annuaire_sante,
                sante_data,
            ),
        ]:
            for key, col in mapping.items():
                if col in row:
                    data_obj.facility_counts[key] = int(row[col])

            if codgeo_str and not annuaire.empty:
                # Extra safety: filter by codgeo and category to avoid leaks
                city_pois = annuaire[
                    (annuaire["codgeo"] == codgeo_str) & (annuaire["category"] == dom)
                ]
                if not city_pois.empty:
                    # Group by 'type' or fallback to 'categorie'
                    type_col = (
                        "type"
                        if "type" in city_pois.columns
                        else ("categorie" if "categorie" in city_pois.columns else None)
                    )
                    # Safely find a label column
                    label_col = (
                        "label"
                        if "label" in city_pois.columns
                        else ("name" if "name" in city_pois.columns else None)
                    )

                    if type_col and label_col:
                        grouped = (
                            city_pois.groupby(type_col, observed=True)[label_col]
                            .apply(lambda x: sorted(list(set(x))))
                            .to_dict()
                        )
                        data_obj.facility_details = grouped

        # 6. Inclusion (Grouped by Thematic)
        incl_data = InclusionMetrics()
        incl_data.cat_score = float(row.get("inclusion_cat_score", 0.0))

        if codgeo_str and not self.annuaire_inclusion.empty:
            city_incl = self.annuaire_inclusion[
                self.annuaire_inclusion["codgeo"] == codgeo_str
            ]
            if not city_incl.empty:
                # Group by 'thematiques'
                if "thematiques" in city_incl.columns:
                    label_col = (
                        "label"
                        if "label" in city_incl.columns
                        else ("name" if "name" in city_incl.columns else None)
                    )
                    if label_col:
                        # Group by thematic codes first
                        grouped_incl_raw = (
                            city_incl.groupby("thematiques", observed=True)[label_col]
                            .apply(list)
                            .to_dict()
                        )

                        # Map codes to labels using inclusion_services_index (safely)
                        grouped_incl = {}
                        for code, names in grouped_incl_raw.items():
                            label = code
                            try:
                                if (
                                    hasattr(self, "inclusion_services_index")
                                    and self.inclusion_services_index is not None
                                    and code in self.inclusion_services_index.index
                                ):
                                    val = self.inclusion_services_index.loc[
                                        code, "label"
                                    ]
                                    label = val if isinstance(val, str) else val.iloc[0]
                            except:
                                pass
                            grouped_incl[label] = sorted(list(set(names)))

                        incl_data.services_grouped = grouped_incl
        # 6b. Detailed Associations (Refugee & Inclusion) from BigQuery
        # SOTA Pattern: Use pre-fetched cache if available, otherwise return empty + "loading" status
        refugee_list = []
        inclusion_list_by_cat = {}
        total_incl_count = 0

        cached_data = self._associations_cache.get(codgeo_str)

        if cached_data:
            refugee_list = cached_data.get("refugee", [])
            inclusion_list_by_cat = cached_data.get("inclusion", {})
            total_incl_count = sum(len(l) for l in inclusion_list_by_cat.values())

        incl_data.asso_refugee_list = refugee_list
        incl_data.asso_refugee_count = len(refugee_list)
        incl_data.asso_inclusion_list_by_cat = inclusion_list_by_cat
        incl_data.asso_inclusion_count = total_incl_count
        # 8. Calculate dynamic category scores (weighted average of active criteria)
        cat_final_scores = {}
        for norm_cat in active_norm_cats:
            cat_items = [it for it in displayed_items if it["norm_cat"] == norm_cat]
            if cat_items and cat_internal_weights.get(norm_cat, 0) > 0:
                cat_final_scores[norm_cat] = (
                    sum(it["val_scaled"] * it["w_crit"] for it in cat_items)
                    / cat_internal_weights[norm_cat]
                )
            else:
                cat_final_scores[norm_cat] = 0.0

        # Map discovered category scores to their respective metric data objects
        # Note: variable names use slightly different slugs than the YAML categories
        metrics_mapping = {
            "emploi": emploi_data,
            "logement": logement_data,
            "education": edu_data,
            "sante": sante_data,
            "inclusion": incl_data,
            "mobilite": mob_data,
            "territoire": territoire_data,
        }
        for cat_slug, score_val in cat_final_scores.items():
            if cat_slug in metrics_mapping:
                metrics_mapping[cat_slug].cat_score = float(score_val)
        territoire_data.is_strategic = bool(
            row.get("ter_strategic_locations_scaled", 0.0) == 1.0
        )

        return CommuneResult(
            codgeo=str(row.name),
            name=static_row.get("libgeo", "Inconnu"),
            population=int(static_row.get("population", 0)),
            codgeo_bdv=str(static_row.get("bassin_de_vie", "Inconnu")),
            name_bdv=static_row.get("libelle_bassin_de_vie", "Inconnu"),
            global_score=float(row.get("weighted_score", 0.0)),
            scores=structured_scores,
            employment=emploi_data,
            housing=logement_data,
            education=edu_data,
            health=sante_data,
            inclusion=incl_data,
            mobility=mob_data,
            territoire=territoire_data,
        )

    def create_search_results(
        self, processed_gdf: gpd.GeoDataFrame, config: SearchCriterias
    ) -> SearchResultsData:
        """Helper to create a SearchResultsData object from the scoring results."""

        # 1. Identify the current city and shortlisted city
        c_code_raw = config.commune_actuelle
        c_code = c_code_raw.code if hasattr(c_code_raw, "code") else c_code_raw

        p_code_raw = getattr(config, "commune_pressentie", None)
        p_code = (
            p_code_raw.code
            if p_code_raw and hasattr(p_code_raw, "code")
            else p_code_raw
        )

        # 2. Extract current location data for comparison
        current_geo = None

        # Try to get it from the actively scored dataframe first (best case: fully scored)
        if c_code in processed_gdf.index:
            try:
                # Need to convert Series to single-row DataFrame if it's the only way, but format_city_details takes a Series
                current_row = processed_gdf.loc[c_code]
                if isinstance(current_row, pd.DataFrame):
                    current_row = current_row.iloc[0]
                current_geo = self.format_city_details(current_row, config)
            except Exception as e:
                logger.warning(f"Failed to format scored current city {c_code}: {e}")

        # Fallback to base data if it was filtered out early (e.g. by region/dept filter)
        if current_geo is None and self.current_city_scored_row is not None:
            current_geo = self.format_city_details(self.current_city_scored_row, config)
        elif current_geo is None and c_code in self.df_all_communes.index:
            # Basic static data without search context scores
            current_geo = self.format_city_details(
                self.df_all_communes.loc[c_code], config
            )

        # Extract commune pressentie details if present
        commune_pressentie_details = None
        if p_code and p_code in processed_gdf.index:
            try:
                p_row = processed_gdf.loc[p_code]
                if isinstance(p_row, pd.DataFrame):
                    p_row = p_row.iloc[0]
                commune_pressentie_details = self.format_city_details(p_row, config)
            except Exception as e:
                logger.error(f"Failed to format scored shortlisted city {p_code}: {e}")

        # 3. Filter out current city, PLM family, and shortlisted city from the results list
        # Detect PLM family (either parent or arrondissement)
        plm_prefix = None
        parent_c = None
        if c_code in cfg.PLM_MAPPING:
            plm_prefix = cfg.PLM_MAPPING[c_code]
        else:
            # Check if c_code is an arrondissement (e.g. '13201' starts with '132')
            for parent_code, prefix in cfg.PLM_MAPPING.items():
                if str(c_code).startswith(prefix):
                    plm_prefix = prefix
                    parent_c = parent_code
                    break

        if plm_prefix:
            mask = ~processed_gdf.index.astype(str).str.startswith(plm_prefix)
            if parent_c:
                mask = mask & (processed_gdf.index != parent_c)
            mask = mask & (processed_gdf.index != c_code)
            if p_code:
                mask = mask & (processed_gdf.index != str(p_code))
            display_gdf = processed_gdf[mask]
        else:
            mask = processed_gdf.index != c_code
            if p_code:
                mask = mask & (processed_gdf.index != str(p_code))
            display_gdf = processed_gdf[mask]

        # 4. Generate Top 5 Communes
        top_5 = display_gdf.head(5)
        results = []
        for idx, row in top_5.iterrows():
            # Add safety bounds
            try:
                details = self.format_city_details(row, config)
                results.append(details)
            except Exception as e:
                logger.error(f"Error formatting details for city {idx}: {e}")
        return SearchResultsData(
            search_hash=config.compute_hash(),
            results=results,
            current_geo=current_geo,
            commune_pressentie=commune_pressentie_details,
        )

    def get_city_details(self, codgeo: str) -> CommuneResult:
        """Retrieves detailed information using static data."""
        if codgeo not in self.df_all_communes.index:
            raise KeyError(f"Commune code {codgeo} not found.")

        return self.format_city_details(self.df_all_communes.loc[codgeo])

    def run_optimized(
        self, config: SearchCriterias, log_prefix: str = "search_results"
    ) -> Tuple[SearchResultsData, pd.DataFrame]:
        """
        Orchestrates the full scoring pipeline with optimized memory management.
        Returns a tuple (SearchResultsData model, pruned DataFrame for map).
        """
        # 1. Compute full scores via legacy run()
        results_raw = self.run(config)

        # 2. Extract into Pydantic model while we still have all columns
        model = self.create_search_results(results_raw, config)

        # 3. Aggressively prune the DataFrame to only what's needed for the map
        # This reduces the size of the objects stored in Streamlit session state
        self._prune_irrelevant_metrics(results_raw, config, aggressive=True)

        # Convert to standard DataFrame to remove GeoPandas overhead in session state
        if isinstance(results_raw, gpd.GeoDataFrame):
            results_raw = pd.DataFrame(
                results_raw.drop(columns="geometry", errors="ignore")
            )

        return model, results_raw

    def run(
        self, config: SearchCriterias, log_prefix: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Orchestrates the full scoring pipeline.
        Returns the FULL unpruned DataFrame (useful for tests and detailed analysis).
        """
        logger.debug(f"⚙️ [ENGINE] Starting run with Profile: {config.weight_profile}")
        if not config.active_criteria:
            config.active_criteria = self._get_active_criteria(config)

        # Derive active categories
        if config.active_criteria:
            active_mask = self.scores_cat["score"].isin(config.active_criteria)
            cats = self.scores_cat[active_mask]["cat"].unique()
            normalized = {str(c).lower().replace("é", "e") for c in cats}
            config.active_categories = sorted(list(normalized))

        c_code_obj = getattr(config, "commune_actuelle", None)
        c_code = (
            c_code_obj.code
            if c_code_obj and hasattr(c_code_obj, "code")
            else c_code_obj
        )

        # Fallback to Paris
        if not c_code:
            c_code = "75056"

        start_commune = self.df_all_communes.loc[[c_code]]
        loc_type = config.loc_search_area or "departement"
        loc_code = config.loc_search_code

        if not loc_code and loc_type != "france":
            loc_col = "dep_code" if loc_type == "departement" else "reg_code"
            loc_code = start_commune.iloc[0][loc_col]

        communes_to_score = self._filter_communes(
            df=self.df_all_communes,
            start_commune=start_commune,
            loc_type=loc_type,
            loc_code=loc_code,
            config=config,
        )

        # Early conservative pruning
        communes_to_score = self._prune_irrelevant_metrics(
            communes_to_score, config, aggressive=False
        )

        # Score candidate pool strictly without out-of-pool comparators
        results = self._compute_scores(communes_to_score, config)

        # If current city or commune pressentie are out-of-pool, score them separately
        # after candidate pool scoring so they cannot alter candidate scores or ranking
        p_code_obj = getattr(config, "commune_pressentie", None)
        p_code = (
            p_code_obj.code
            if p_code_obj and hasattr(p_code_obj, "code")
            else p_code_obj
        )

        extra_dfs = []
        c_code_str = str(c_code) if c_code is not None else None
        if c_code_str and c_code_str in self.df_all_communes.index and c_code_str not in results.index:
            c_df = self._prune_irrelevant_metrics(
                self.df_all_communes.loc[[c_code_str]], config, aggressive=False
            )
            extra_dfs.append(self._compute_scores(c_df, config))

        p_code_str = str(p_code) if p_code is not None else None
        if (
            p_code_str
            and p_code_str in self.df_all_communes.index
            and p_code_str not in results.index
        ):
            p_df = self._prune_irrelevant_metrics(
                self.df_all_communes.loc[[p_code_str]], config, aggressive=False
            )
            extra_dfs.append(self._compute_scores(p_df, config))

        if extra_dfs:
            results = pd.concat([results] + extra_dfs)

        del communes_to_score
        return results

    def _compute_scores(
        self, df_search: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        if df_search.empty:
            return df_search

        # Distance
        odis_search = df_search
        if "dist_current_loc" not in odis_search.columns:
            odis_search = self._compute_distance_score(odis_search, config)

        # Merge BdV Data
        if (
            self.bv_data is not None
            and not self.bv_data.empty
            and "bassin_de_vie" in odis_search.columns
        ):
            # Ensure type consistency for merge
            odis_search = odis_search.assign(
                bassin_de_vie=odis_search["bassin_de_vie"].astype(str)
            )

            bv_cols = [
                c
                for c in self.bv_data.columns
                if c not in odis_search.columns or c == "bassin_de_vie"
            ]

            odis_search = pd.merge(
                odis_search,
                self.bv_data.add_suffix("_bdv"),
                left_on="bassin_de_vie",
                right_index=True,
                how="left",
            )

        odis_scored = self._compute_criteria_scores(odis_search, config)
        odis_exploded = self._compute_category_scores(odis_scored, config)
        odis_exploded["weighted_score"] = self._compute_weighted_score(
            odis_exploded, config
        )

        # Final pruning
        self._prune_irrelevant_metrics(odis_exploded, config, aggressive=False)

        # Sort by weighted score
        odis_sorted = odis_exploded.sort_values(by="weighted_score", ascending=False)

        # 🧪 SOTA: Limit the number of polygons
        if len(odis_sorted) > cfg.MAX_MAP_POLYGONS:
            top_k = odis_sorted.head(cfg.MAX_MAP_POLYGONS)

            c_code_raw = config.commune_actuelle
            c_code = c_code_raw.code if hasattr(c_code_raw, "code") else c_code_raw

            if c_code and c_code in odis_sorted.index and c_code not in top_k.index:
                top_k = pd.concat([top_k, odis_sorted.loc[[c_code]]])

            p_code_raw = getattr(config, "commune_pressentie", None)
            p_code = p_code_raw.code if hasattr(p_code_raw, "code") else p_code_raw

            if p_code and p_code in odis_sorted.index and p_code not in top_k.index:
                top_k = pd.concat([top_k, odis_sorted.loc[[p_code]]])

            return top_k

        return odis_sorted

    def _compute_employment_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        # Operating in-place

        # --- Live Jobs (ROME-based) ---
        if any(config.codes_metiers) and not self.live_jobs_data.empty:
            commune_to_bdv = self.df_all_communes["bassin_de_vie"].dropna().to_dict()

            for i in range(config.nb_adultes):
                adult_key = f"adult{i + 1}"
                adult_romes = set()

                if i < len(config.codes_metiers):
                    for c in config.codes_metiers[i]:
                        # Handle CriteriaItem or str
                        code = c.code if hasattr(c, "code") else str(c)
                        if len(code) == 5 and code[0].isalpha() and code[1:].isdigit():
                            adult_romes.add(code)

                if not adult_romes:
                    df[f"met_match_{adult_key}_scaled"] = 0.0
                    if "bassin_de_vie" in df.columns:
                        df[f"met_match_{adult_key}_bdv_scaled"] = 0.0
                    df[f"met_match_{adult_key}_tension_scaled"] = 0.0
                    continue

                target_live = self.live_jobs_data[
                    self.live_jobs_data["romeCode"].isin(adult_romes)
                ]

                # City Sum
                commune_live_counts = target_live.groupby("commune")[
                    "total_postes"
                ].sum()
                col_raw = f"met_match_{adult_key}"
                df[col_raw] = df.index.map(commune_live_counts).fillna(0)

                # Tension Sum
                col_tension_raw = f"met_match_{adult_key}_tension"
                if "nb_offres_tension" in target_live.columns:
                    commune_tension_counts = target_live.groupby("commune")[
                        "nb_offres_tension"
                    ].sum()
                    df[col_tension_raw] = df.index.map(commune_tension_counts).fillna(0)
                else:
                    df[col_tension_raw] = 0.0

                # BdV Sum
                col_bdv_raw = f"met_match_{adult_key}_bdv"
                if "bassin_de_vie" in df.columns:
                    bdv_series = target_live["commune"].map(commune_to_bdv)
                    bdv_live_counts = target_live.groupby(bdv_series)[
                        "total_postes"
                    ].sum()
                    df[col_bdv_raw] = df["bassin_de_vie"].map(bdv_live_counts).fillna(0)
                else:
                    df[col_bdv_raw] = 0.0

                # Scaling
                s_def = (
                    self.scores_cat[
                        self.scores_cat["score"] == f"{col_raw}_scaled"
                    ].iloc[0]
                    if not self.scores_cat[
                        self.scores_cat["score"] == f"{col_raw}_scaled"
                    ].empty
                    else {}
                )
                min_c, max_c = self._get_bounds(f"{col_raw}_scaled")
                if pd.isna(max_c):
                    max_c = 10.0
                df[f"{col_raw}_scaled"] = self._scale_series(
                    df[col_raw],
                    min_c,
                    max_c,
                    scaling_type=s_def.get("scaling_type", "linear"),
                    mu=s_def.get("mu"),
                    sigma=s_def.get("sigma"),
                )

                s_def_bdv = (
                    self.scores_cat[
                        self.scores_cat["score"] == f"{col_raw}_scaled_bdv"
                    ].iloc[0]
                    if not self.scores_cat[
                        self.scores_cat["score"] == f"{col_raw}_scaled_bdv"
                    ].empty
                    else {}
                )
                min_b, max_b = self._get_bounds(f"{col_raw}_scaled_bdv")
                if pd.isna(max_b):
                    max_b = 50.0
                df[f"{col_raw}_scaled_bdv"] = self._scale_series(
                    df[col_bdv_raw],
                    min_b,
                    max_b,
                    scaling_type=s_def_bdv.get("scaling_type", "linear"),
                    mu=s_def_bdv.get("mu"),
                    sigma=s_def_bdv.get("sigma"),
                )

                s_def_t = (
                    self.scores_cat[
                        self.scores_cat["score"] == f"{col_tension_raw}_scaled"
                    ].iloc[0]
                    if not self.scores_cat[
                        self.scores_cat["score"] == f"{col_tension_raw}_scaled"
                    ].empty
                    else {}
                )
                min_t, max_t = self._get_bounds(f"{col_tension_raw}_scaled")
                if pd.isna(max_t):
                    max_t = 5.0
                df[f"{col_tension_raw}_scaled"] = self._scale_series(
                    df[col_tension_raw],
                    min_t,
                    max_t,
                    scaling_type=s_def_t.get("scaling_type", "linear"),
                    mu=s_def_t.get("mu"),
                    sigma=s_def_t.get("sigma"),
                )

                # --- SIAE Jobs Matching (New F-39) ---
                col_siae_raw = f"met_siae_match_{adult_key}"
                df[col_siae_raw] = 0.0

                if self.siae_jobs_data is not None and not self.siae_jobs_data.empty:
                    # SIAE matching uses 3rd digit prefix
                    siae_prefixes = {c[:3] for c in adult_romes if len(c) >= 3}

                    siae_match = self.siae_jobs_data[
                        self.siae_jobs_data["rome"].str[:3].isin(siae_prefixes)
                    ]

                    if not siae_match.empty:
                        siae_counts = siae_match.groupby("codgeo").size()
                        df[col_siae_raw] = df.index.map(siae_counts).fillna(0)

                # Scaling SIAE
                s_def_s = (
                    self.scores_cat[
                        self.scores_cat["score"] == f"{col_siae_raw}_scaled"
                    ].iloc[0]
                    if not self.scores_cat[
                        self.scores_cat["score"] == f"{col_siae_raw}_scaled"
                    ].empty
                    else {}
                )
                min_s, max_s = self._get_bounds(f"{col_siae_raw}_scaled")
                if pd.isna(max_s):
                    max_s = 5.0
                df[f"{col_siae_raw}_scaled"] = self._scale_series(
                    df[col_siae_raw],
                    min_s,
                    max_s,
                    scaling_type=s_def_s.get("scaling_type", "linear"),
                    mu=s_def_s.get("mu"),
                    sigma=s_def_s.get("sigma"),
                )

        # --- Formations ---
        if any(config.codes_formations):
            relevant_formations = self.formations_data[
                self.formations_data["codgeo"].isin(df.index)
            ]
            form_map = (
                relevant_formations.groupby("codgeo")["formation_code"]
                .apply(set)
                .to_dict()
            )

            commune_to_bdv = self.df_all_communes["bassin_de_vie"].dropna().to_dict()
            bdv_series = relevant_formations["codgeo"].map(commune_to_bdv)
            form_map_bdv = (
                relevant_formations.groupby(bdv_series)["formation_code"]
                .apply(set)
                .to_dict()
            )

            for i in range(config.nb_adultes):
                if i < len(config.codes_formations) and config.codes_formations[i]:
                    adult_key = f"adult{i + 1}"
                    # Handle CriteriaItem
                    prefs = {
                        c.code if hasattr(c, "code") else str(c)
                        for c in config.codes_formations[i]
                    }

                    col_name = f"form_match_codes_{adult_key}"
                    df[col_name] = df.index.map(
                        lambda c: list(form_map.get(c, set()).intersection(prefs))
                    )

                    # Match Score Local
                    score_key = f"form_match_{adult_key}"
                    df[score_key] = df.index.map(
                        lambda c: len(form_map.get(c, set()).intersection(prefs))
                    )
                    s_def_fl = (
                        self.scores_cat[
                            self.scores_cat["score"] == f"{score_key}_scaled"
                        ].iloc[0]
                        if not self.scores_cat[
                            self.scores_cat["score"] == f"{score_key}_scaled"
                        ].empty
                        else {}
                    )
                    min_b, max_b = self._get_bounds(f"{score_key}_scaled")
                    if pd.isna(max_b):
                        max_b = float(len(prefs))
                    df[f"{score_key}_scaled"] = self._scale_series(
                        df[score_key].fillna(0),
                        min_b,
                        max_b,
                        scaling_type=s_def_fl.get("scaling_type", "linear"),
                        mu=s_def_fl.get("mu"),
                        sigma=s_def_fl.get("sigma"),
                    )

                    # Match Score BdV
                    if "bassin_de_vie" in df.columns:
                        df[f"{score_key}_bdv"] = df["bassin_de_vie"].map(
                            lambda b: len(
                                form_map_bdv.get(b, set()).intersection(prefs)
                            )
                        )
                        s_def_fb = (
                            self.scores_cat[
                                self.scores_cat["score"] == f"{score_key}_scaled_bdv"
                            ].iloc[0]
                            if not self.scores_cat[
                                self.scores_cat["score"] == f"{score_key}_scaled_bdv"
                            ].empty
                            else {}
                        )
                        min_b, max_b = self._get_bounds(f"{score_key}_scaled_bdv")
                        if pd.isna(max_b):
                            max_b = float(len(prefs))
                        df[f"{score_key}_scaled_bdv"] = self._scale_series(
                            df[f"{score_key}_bdv"].fillna(0),
                            min_b,
                            max_b,
                            scaling_type=s_def_fb.get("scaling_type", "linear"),
                            mu=s_def_fb.get("mu"),
                            sigma=s_def_fb.get("sigma"),
                        )

            # Aggregate formation names
            if (
                self.codformations_index is not None
                and not self.codformations_index.empty
            ):

                def get_all_labels(row):
                    codes = set()
                    for i in range(config.nb_adultes):
                        col = f"form_match_codes_adult{i + 1}"
                        if col in row and isinstance(row[col], list):
                            codes.update(row[col])
                    return [
                        self.codformations_index.loc[c, "label"]
                        if c in self.codformations_index.index
                        else c
                        for c in codes
                    ]

                df["noms_formations"] = df.apply(get_all_labels, axis=1)
            else:
                df["noms_formations"] = [[] for _ in range(len(df))]

        return df

    def _compute_sante_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        """Health structures are now statically precomputed in the parquet dataset."""
        return df

    def _compute_mobility_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        # Operating in-place

        # --- Density ---
        if "nb_stops_total" in df.columns:
            df["mob_trans_pub_stop_density"] = (
                df["nb_stops_total"] / df["population"].replace(0, 1)
            ) * 1000
            s_def_mob = (
                self.scores_cat[
                    self.scores_cat["score"] == "mob_trans_pub_density_scaled"
                ].iloc[0]
                if not self.scores_cat[
                    self.scores_cat["score"] == "mob_trans_pub_density_scaled"
                ].empty
                else {}
            )
            min_b, max_b = self._get_bounds("mob_trans_pub_density_scaled")
            if pd.isna(max_b):
                max_b = 10.0
            df["mob_trans_pub_density_scaled"] = self._scale_series(
                df["mob_trans_pub_stop_density"],
                min_b,
                max_b,
                scaling_type=s_def_mob.get("scaling_type", "linear"),
                mu=s_def_mob.get("mu"),
                sigma=s_def_mob.get("sigma"),
            )

        # --- EPCI Bonus ---
        current_epci = None
        current_reg = None
        current_dep = None

        # Resolve current location details
        c_code_obj = getattr(config, "commune_actuelle", None)
        if c_code_obj:
            c_code = c_code_obj.code if hasattr(c_code_obj, "code") else c_code_obj
            if c_code in self.df_all_communes.index:
                cur_row = self.df_all_communes.loc[c_code]
                current_epci = cur_row["epci_code"]
                current_reg = cur_row["reg_code"]
                current_dep = cur_row["dep_code"]

        if self._is_local_search(config) and current_epci:
            df["mob_epci_scaled"] = np.where(df["epci_code"] == current_epci, 1.0, 0.0)
        else:
            df["mob_epci_scaled"] = 0.0

        return df

    def _compute_education_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        """
        Placeholder function. Education scores (e.g. school capacities) are static metrics
        that do not depend on dynamic user input. They are pre-computed and pre-scaled offline
        in pipeline/prescoring.py and stored directly in the commune Parquet files.
        They are dynamically aggregated by category in ScoringEngine._compute_category_scores.
        """
        return df

    def _compute_housing_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        """
        Placeholder function. Housing scores (e.g. rent costs, vacancy ratios) are static metrics
        that do not depend on dynamic user input. They are pre-computed and pre-scaled offline
        in pipeline/prescoring.py and stored directly in the commune Parquet files.
        They are dynamically aggregated by category in ScoringEngine._compute_category_scores.
        """
        return df

    def _compute_inclusion_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        # Operating in-place

        # Population Score (F-50) - Dynamic re-calculation
        if (
            "ter_population_scaled" in self._get_active_criteria(config)
            and "population" in df.columns
        ):
            mu = getattr(config, "target_population", 50000)
            sigma = getattr(config, "target_population_sigma", 25000)

            df["ter_population_scaled"] = self._scale_series(
                df["population"], 0, 0, scaling_type="gaussian", mu=mu, sigma=sigma
            )

            # --- F-13: BdV Saturation Check ---
            if "population_bv_bdv" in df.columns:
                df["ter_population_scaled_bdv"] = self._scale_series(
                    df["population_bv_bdv"],
                    0,
                    0,
                    scaling_type="gaussian",
                    mu=mu,
                    sigma=sigma,
                )

        if "inc_asso_core_scaled" not in df.columns:
            df["inc_asso_core_scaled"] = 0.0

        # Affinities
        inc_asso_add = getattr(config, "inc_asso_add_selection", [])
        if inc_asso_add:
            interest_codes = set()
            for i in inc_asso_add:
                # Logic to handle CriteriaItem or str
                code = i.code if hasattr(i, "code") else str(i)
                interest_codes.add(code)

            if interest_codes:
                # Normalization logic
                expanded_interests = set()
                for c in interest_codes:
                    expanded_interests.add(c)
                    if c.startswith("0"):
                        expanded_interests.add(c.lstrip("0"))
                    else:
                        expanded_interests.add("0" + c)

                affinite_assos = self.associations_data[
                    self.associations_data["id_waldec"]
                    .astype(str)
                    .str.startswith(tuple(expanded_interests), na=False)
                ]
                affinite_counts = (
                    affinite_assos.groupby("codgeo")["count"]
                    .sum()
                    .reindex(df.index, fill_value=0)
                )

                if "population" in df.columns:
                    df["affinite_density"] = (affinite_counts * 1000) / df["population"]
                else:
                    df["affinite_density"] = 0.0
                min_b, max_b = self._get_bounds("inc_asso_add_scaled")
                s_def_inc = (
                    self.scores_cat[
                        self.scores_cat["score"] == "inc_asso_add_scaled"
                    ].iloc[0]
                    if not self.scores_cat[
                        self.scores_cat["score"] == "inc_asso_add_scaled"
                    ].empty
                    else {}
                )
                df["inc_asso_add_scaled"] = self._scale_series(
                    df["affinite_density"],
                    min_b,
                    max_b,
                    scaling_type=s_def_inc.get("scaling_type", "linear"),
                    mu=s_def_inc.get("mu"),
                    sigma=s_def_inc.get("sigma"),
                )
            else:
                if "inc_asso_add_scaled" in df.columns:
                    df.drop(columns=["inc_asso_add_scaled"], inplace=True)
        else:
            if "inc_asso_add_scaled" in df.columns:
                df.drop(columns=["inc_asso_add_scaled"], inplace=True)

        # Inclusion Services (Merged Selection)
        needed = set()
        for i in getattr(config, "inc_services_selection", []):
            needed.add(i.code if hasattr(i, "code") else str(i))

        if needed:

            def count_matches(available):
                if not isinstance(available, set):
                    return 0
                return sum(1 for n in needed if any(n in a for a in available))

            if "key" not in df.columns:
                df = df.join(self.incl_index, how="left")

            df["inc_services_incl_scaled"] = df["key"].apply(count_matches) / len(
                needed
            )
        else:
            if "inc_services_incl_scaled" in df.columns:
                df.drop(columns=["inc_services_incl_scaled"], inplace=True)

        return df

    def _compute_criteria_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        # Orchestrator
        df = self._compute_employment_scores(df, config)
        df = self._compute_mobility_scores(df, config)
        df = self._compute_sante_scores(df, config)
        df = self._compute_inclusion_scores(df, config)
        df = self._compute_housing_scores(df, config)
        df = self._compute_education_scores(df, config)
        df = self._compute_territory_scores(df, config)

        # Pruning
        df = self._prune_irrelevant_metrics(df, config)
        return df

    def _compute_territory_scores(
        self, df: pd.DataFrame, config: SearchCriterias
    ) -> pd.DataFrame:
        """Computes binary boost for strategic locations."""
        if getattr(config, "org_strategic_locations", []):
            zone_type = getattr(config, "org_strategic_locations_type", "departement")
            col_to_check = "dep_code" if zone_type == "departement" else "bassin_de_vie"

            if col_to_check in df.columns:
                df["ter_strategic_locations_scaled"] = (
                    df[col_to_check].isin(config.org_strategic_locations).astype(float)
                )
            else:
                df["ter_strategic_locations_scaled"] = 0.0
        else:
            df["ter_strategic_locations_scaled"] = 0.0
        return df

    def _is_local_search(self, config: SearchCriterias) -> bool:
        """Determines if the search is happening within the user's current area."""
        c_code_raw = getattr(config, "commune_actuelle", None)
        if not c_code_raw:
            return False

        c_code = c_code_raw.code if hasattr(c_code_raw, "code") else c_code_raw

        if c_code not in self.df_all_communes.index:
            return False

        cur_row = self.df_all_communes.loc[c_code]
        current_dep = cur_row["dep_code"]
        current_reg = cur_row["reg_code"]

        # Search area must either be the same department or same region
        if config.loc_search_area == "region":
            codes = (
                config.loc_search_code
                if isinstance(config.loc_search_code, list)
                else [config.loc_search_code]
            )
            return str(current_reg) in [str(c) for c in codes]

        if config.loc_search_area == "departement":
            codes = (
                config.loc_search_code
                if isinstance(config.loc_search_code, list)
                else [config.loc_search_code]
            )
            return str(current_dep) in [str(c) for c in codes]

        return False
