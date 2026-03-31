
# coding: utf-8
"""
Scoring module for the ODIS application.
"""
from typing import List, Dict, Set, Any, Optional, Union, Tuple
import geopandas as gpd
import numpy as np
import pandas as pd
import itertools
import string
import warnings
import config as cfg
from core.models import (
    SearchCriterias, CommuneResult, CommuneScoreDetail, SearchResultsData,
    EmploymentMetrics, HousingMetrics, EducationMetrics, HealthMetrics, 
    InclusionMetrics, MobilityMetrics
)
from shapely.geometry import Point
import logging
import gc
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from utils.logger import log_search_results
from utils.common import project_point
from services.rna_rag import RNARagService

class ScoringEngine:
    """
    The engine responsible for running the ODIS scoring algorithm.
    """
    @staticmethod
    def _filter_communes(df: pd.DataFrame, start_commune: pd.DataFrame, loc_type: str, loc_code: Optional[str]) -> pd.DataFrame:
        if loc_type == 'departement': return df[df['dep_code'] == loc_code].copy()
        elif loc_type == 'region': return df[df['reg_code'] == loc_code].copy()
        elif loc_type == 'france': return df[~df['dep_code'].astype(str).str.startswith(('97', '98'))].copy()
        return pd.DataFrame()

    @staticmethod
    def _scale_series(series: pd.Series, min_val: float, max_val: float, scaling_type: str = 'linear', mu: Optional[float] = None, sigma: Optional[float] = None) -> pd.Series:
        if scaling_type == 'gaussian' and mu is not None and sigma is not None:
             return np.exp(-0.5 * ((series - mu) / sigma)**2)
        
        if max_val == min_val: return pd.Series(0.0, index=series.index)
        return ((series - min_val) / (max_val - min_val)).clip(0, 1)

    def _get_bounds(self, score_id: str) -> Tuple[float, float]:
        if self.global_stats and score_id in self.global_stats: 
            return self.global_stats[score_id]['min'], self.global_stats[score_id]['max']
        row = self.scores_cat[self.scores_cat['score'] == score_id]
        if not row.empty:
            return (float(row.iloc[0]['min_bound']) if pd.notna(row.iloc[0]['min_bound']) else 0.0,
                    float(row.iloc[0]['max_bound']) if pd.notna(row.iloc[0]['max_bound']) else 1.0)
        return 0.0, 1.0

    def _compute_distance_score(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        """
        Calculates linear distance to user's current location.
        🧪 SOTA: Numpy vectorization on metric coordinates for ultra-fast scoring
        """
        current_codgeo_raw = config.commune_actuelle
        current_codgeo = current_codgeo_raw.code if hasattr(current_codgeo_raw, 'code') else current_codgeo_raw
        
        target_lon, target_lat = None, None
        
        # Priority mapping from the actively processed dataframe
        if current_codgeo in df.index and 'centroid_lon' in df.columns:
            target_lon = df.loc[current_codgeo, 'centroid_lon']
            target_lat = df.loc[current_codgeo, 'centroid_lat']
        elif self.df_all_communes is not None and current_codgeo in self.df_all_communes.index and 'centroid_lon' in self.df_all_communes.columns:
            target_lon = self.df_all_communes.loc[current_codgeo, 'centroid_lon']
            target_lat = self.df_all_communes.loc[current_codgeo, 'centroid_lat']
        
        if target_lon is not None and target_lat is not None and pd.notna(target_lon) and 'centroid_lon' in df.columns:
             # EPSG:2154 is metric (meters). Simple euclidean math avoids geometry overhead entirely
             dx = df['centroid_lon'] - target_lon
             dy = df['centroid_lat'] - target_lat
             df['dist_current_loc'] = np.sqrt(dx**2 + dy**2)
        
        # Scale if computed
        if 'dist_current_loc' in df.columns:
             min_b, max_b = self._get_bounds('mob_dist_current_loc_scaled')
             if pd.isna(max_b): max_b = 50000.0 # Default 50km
             # Inverse scale: closer is better
             scaled = self._scale_series(df['dist_current_loc'], min_b, max_b)
             df['mob_dist_current_loc_scaled'] = 1.0 - scaled
        
        return df

    def _compute_category_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        # Operating in-place on the provided DataFrame
        
        # Use cached active criteria if available
        active = config.active_criteria if config.active_criteria is not None else self._get_active_criteria(config)

        # Compute for all categories
        categories = ['emploi', 'logement', 'education', 'inclusion', 'mobilite', 'sante']
        for category in categories:
            # Skip if category totally irrelevant
            if category == 'education' and getattr(config, 'nb_enfants', 1) == 0: continue
            if category == 'sante' and getattr(config, 'besoin_sante', 'Aucun') == 'Aucun': continue

            # Find columns for this category that are active
            cat_scores = self.scores_cat[self.scores_cat.cat == category]
            
            # Filter only active scores
            active_score_defs = cat_scores[cat_scores['score'].isin(active)]
            
            if active_score_defs.empty: continue
            
            scores_val = []
            weights_val = []
            
            for _, s_row in active_score_defs.iterrows():
                 sid = s_row['score']
                 bdv_f = float(s_row.get('bdv_factor', 0.0))
                 
                 # 1. Get Commune Value
                 val_commune = df[sid] if sid in df.columns else None
                 
                 # 2. Get BDV Value
                 sid_bdv = f"{sid}_bdv"
                 val_bdv = df[sid_bdv] if sid_bdv in df.columns else None
                 
                 # 3. Parity Check: Log warning if an active criteria is missing from data
                 if config.active_criteria is not None and sid in config.active_criteria and val_commune is None and val_bdv is None:
                      logger.warning(f"⚠️ [SCORING] Active criterion '{sid}' (or '{sid_bdv}') is MISSING from the input data. Score will be defaulted to 0.")
                 # 4. Combine using bdv_factor (Non-penalizing Boost Logic)
                 if val_commune is not None and val_bdv is not None:
                      # Formula: Sc + (1 - Sc) * (Sb * factor)
                      # Bassin de Vie opportunities act as a bonus to local ones
                      s_c = val_commune.fillna(0)
                      s_b = val_bdv.fillna(0)
                      val = s_c + (1.0 - s_c) * (s_b * bdv_f)
                 elif val_commune is not None:
                      val = val_commune.fillna(0)
                 elif val_bdv is not None:
                      val = val_bdv.fillna(0)
                 else:
                      continue # Skip if no data available for this criterion
                 
                 # Apply user or catalog weights
                 weight = 1.0
                 if sid in config.criteria_weights:
                      weight *= config.criteria_weights[sid]
                 else:
                      weight *= float(s_row['weight'])

                 # Track valid weights per row (using non-nullity of original sources)
                 # If both are null, weight is 0
                 if val_commune is not None and val_bdv is not None:
                      has_data = val_commune.notna() | val_bdv.notna()
                 elif val_commune is not None:
                      has_data = val_commune.notna()
                 else:
                      has_data = val_bdv.notna()
                      
                 valid_weight = weight * has_data.astype(float)
                 scores_val.append(val * weight)
                 weights_val.append(valid_weight)
                 
                 # IMPORTANT: save the combined value back to the dataframe 
                 # so it can be picked up by format_city_details for the UI breakdown.
                 df[sid] = val
            
            if weights_val:
                 denom = sum(weights_val)
                 # Avoid division by zero
                 df[f"{category}_cat_score"] = np.where(denom > 0, sum(scores_val) / denom, 0.0)

        return df

    def _compute_weighted_score(self, df: pd.DataFrame, config: SearchCriterias) -> pd.Series:
        total_score = pd.Series(0.0, index=df.index)
        total_weight = 0.0
        
        weights = {
            'emploi': config.poids_emploi,
            'logement': config.poids_logement,
            'education': config.poids_education,
            'inclusion': config.poids_inclusion,
            'mobilite': config.poids_mobilite,
            'sante': config.poids_sante
        }
        
        for cat, weight in weights.items():
            # Robust Check: Force exclusion if conditions met, even if column exists
            if cat == 'education' and config.nb_enfants == 0: continue
            if cat == 'sante' and config.besoin_sante == 'Aucun': continue

            # Skip if category score not computed (e.g. no children)
            col = f"{cat}_cat_score"
            if col not in df.columns: continue
            
            val = df[col].fillna(0)
            valid_mask = df[col].notna() # Where score exists
            
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

    def _prune_irrelevant_metrics(self, df: pd.DataFrame, config: SearchCriterias, aggressive: bool = False) -> pd.DataFrame:
        """
        Prunes redundant columns to optimize memory usage.
        Conservative approach by default, aggressive approach clears everything except essential UI/Map columns.
        """
        if df is None or df.empty:
            return df
            
        to_drop = []
        
        if aggressive:
            # SOTA Optimization: Keep identifiers, scores, AND essential geometries for the filtered subset.
            # We only keep geometries for the search area (e.g. results for 1 department),
            # which is lightweight enough (~1MB) for the session state.
            keep_cols = {'libgeo', 'weighted_score', 'dep_code', 'reg_code', 'epci_code', 'bassin_de_vie', 'libelle_bassin_de_vie', 'polygon', 'centroid'}
            to_drop = [c for c in df.columns if c not in keep_cols]
        else:
            # 1. Deny-list: Explicitly requested redundant BdV columns
            to_drop = ['polygon_bdv', 'libgeo_bdv', 'centroid_bdv']
            
            # 2. Selective Pruning: Drop unselected high-level scores
            active_ids = None
            if hasattr(config, 'active_criteria') and config.active_criteria is not None:
                active_ids = set(config.active_criteria)
            else:
                try:
                    active_ids = set(self._get_active_criteria(config))
                except Exception:
                    pass

            if active_ids:
                scaled_cols = [c for c in df.columns if c.endswith('_scaled')]
                for col in scaled_cols:
                    if col not in active_ids:
                        to_drop.append(col)
            
        actual_drops = [c for c in to_drop if c in df.columns]
        if actual_drops:
            df.drop(columns=actual_drops, inplace=True)
            
        return df

    @classmethod
    def from_app_data(cls, app_data: Dict[str, Any]) -> 'ScoringEngine':
        """
        Factory method to create a ScoringEngine from the standard app_data dictionary.
        """
        return cls(
            df_all_communes=app_data.get('odis', pd.DataFrame()),
            df_odis_geo=app_data.get('odis_geo', gpd.GeoDataFrame()),
            df_bv_geo=app_data.get('bv_geo', pd.DataFrame()),
            df_area_geo=app_data.get('area_geo', pd.DataFrame()),
            scores_cat=app_data.get('scores_cat', pd.DataFrame()),
            incl_index=app_data.get('incl_index', pd.DataFrame()),
            associations_data=app_data.get('associations_data', pd.DataFrame()),
            formations_data=app_data.get('formations_data', pd.DataFrame()),
            codformations_index=app_data.get('codformations_index'),
            waldec_index=app_data.get('waldec_index'),
            global_stats=app_data.get('global_stats'),
            bv_data=app_data.get('bv_data'),
            annuaire_ecoles=app_data.get('annuaire_ecoles', pd.DataFrame()),
            annuaire_sante=app_data.get('annuaire_sante', pd.DataFrame()),
            annuaire_inclusion=app_data.get('annuaire_inclusion', pd.DataFrame()),
            inclusion_services_index=app_data.get('inclusion_services_index', pd.DataFrame()),
            regio_referentiel=app_data.get('regio_referentiel'),
            rome_index=app_data.get('rome_index', pd.DataFrame()),
            refugee_associations_data=app_data.get('refugee_associations_data', pd.DataFrame()),
            live_jobs_data=app_data.get('live_jobs_data', pd.DataFrame()),
            siae_jobs_data=app_data.get('siae_jobs_data', pd.DataFrame()),
            bmo_vertical=app_data.get('bmo_vertical', pd.DataFrame()),
            rna_rag_service=app_data.get('rna_rag_service')
        )

    def __init__(
        self,
        df_all_communes: pd.DataFrame,
        df_odis_geo: gpd.GeoDataFrame,
        df_bv_geo: gpd.GeoDataFrame,
        df_area_geo: gpd.GeoDataFrame,
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
        bmo_vertical: pd.DataFrame = pd.DataFrame(), # Deprecated
        rna_rag_service: Optional[RNARagService] = None
    ):
        self.current_city_scored_row = None
        self.df_all_communes = df_all_communes
        self.df_odis_geo = df_odis_geo
        self.df_bv_geo = df_bv_geo
        self.df_area_geo = df_area_geo
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
        self.bmo_vertical = bmo_vertical
        
        # Initialize RNA RAG Service if not provided
        # Initialize RNA RAG Service if not provided
        self.rna_rag_service = rna_rag_service
        if self.rna_rag_service is None:
            try:
                self.rna_rag_service = RNARagService()
            except Exception as e:
                logger.warning(f"Could not initialize RNARagService in ScoringEngine: {e}")
        
        # Batch cache for associations (Store for detailed results)
        self._associations_cache: Dict[str, Dict[str, Any]] = {}

    def _get_active_criteria(self, config: Optional[SearchCriterias]) -> Set[str]:
        """Centralized logic to determine which criteria are active based on config."""
        active = set()
        
        # If no config provided, we default to all present scores
        if config is None:
             return {c for c in self.scores_cat['score'] if c in self.df_all_communes.columns}

        # 1. Categories that are always active (even if partial)
        active.add('workclass_decline_scaled')
        active.add('mob_gare_scaled')
        active.add('mob_trans_pub_density_scaled')
        
        # Only add proximity scores if it's a local search
        if self._is_local_search(config):
            active.add('mob_epci_scaled')
            active.add('mob_dist_current_loc_scaled')
        
        # 2. Employment & Formations (Only if specific adult was searched)
        nb_adultes = getattr(config, 'nb_adultes', 0)
        codes_metiers = getattr(config, 'codes_metiers', [])
        codes_formations = getattr(config, 'codes_formations', [])
        
        for i in range(nb_adultes):
            adult_idx = i + 1
            # Employment
            if i < len(codes_metiers) and codes_metiers[i]:
                active.add(f'met_match_adult{adult_idx}_scaled')
                active.add(f'met_match_adult{adult_idx}_tension_scaled')
                active.add(f'met_siae_match_adult{adult_idx}_scaled') 

            # Formations
            if i < len(codes_formations) and codes_formations[i]:
                active.add(f'form_match_adult{adult_idx}_scaled')

        # 3. Logement
        # F-42: Hebergement Refinements
        heb_sel = getattr(config, 'hebergement_cible', [])
        if "Location avec Intermédiation" in heb_sel:
            active.add('heb_loc_iml_scaled')
            active.add('log_vac_scaled')
        
        if "Centres d'Hébergement (CHRS, CPH)" in heb_sel:
            active.add('heb_centres_heb_scaled')
            
        if "Foyers & Pensions de Famille" in heb_sel:
            active.add('heb_foyers_scaled')
            
        if "Chez l'habitant" in heb_sel:
            active.add('heb_asso_habitant_scaled')
            active.add('heb_jaccueille_score')
            active.add('log_occup_scaled')
            
        # Rent scaling activation (if Location or IML)
        logement_type = getattr(config, 'logement', 'Location')
        if logement_type == 'Location' or "Location avec Intermédiation" in heb_sel:
             active.add('log_vac_scaled')
         # Handle both formats: log_loyer_moyen_appt_all_scaled and log_loyer_moyen_scaled_appartement_toutes
             type_log_attr = getattr(config, 'type_logement', 'appt_all')
             type_log = type_log_attr.code if hasattr(type_log_attr, 'code') else type_log_attr
             active.add(f'log_loyer_moyen_{type_log}_scaled')
             if type_log == 'appartement_toutes':
                 active.add('log_loyer_moyen_scaled_appartement_toutes')
             elif type_log == 'appt_all':
                 active.add('log_loyer_moyen_appt_all_scaled')

        if logement_type == 'Logement Social':
            active.add('log_soc_inoc_scaled')
            active.add('log_soc_dem_scaled')

        # 4. Education
        nb_enfants = getattr(config, 'nb_enfants', 0)
        if nb_enfants > 0:
            active.add('youth_decline_scaled')
            active.add('edu_classes_ferm_scaled')
            edu_map = {
                'Crèche / Assistante Maternelle': 'edu_petite_enfance_scaled',
                'Petite Enfance/Crêche': 'edu_petite_enfance_scaled',
                'Maternelle': 'edu_maternelle_scaled',
                'Elémentaire': 'edu_elementaire_scaled',
                'Collège': 'edu_college_scaled',
                'Lycée': 'edu_lycee_scaled'
            }
            # Add specific levels
            for level in getattr(config, 'classe_enfants', []):
                if level in edu_map: active.add(edu_map[level])

        # 5. Sante
        besoin_sante = getattr(config, 'besoin_sante', 'Aucun')
        if besoin_sante != 'Aucun':
             sante_map = {
                 'Hôpital': 'sante_hopital_scaled',
                 'Maternité': 'sante_maternite_scaled',
                 'Soutien Psychologique & Addictologie': 'sante_psy_scaled',
                 'Psychiatrie': 'sante_psy_scaled'
             }
             if besoin_sante in sante_map:
                 active.add(sante_map[besoin_sante])
             active.add('sante_structures_scaled')

        # 6. Inclusion
        active.add('inc_pol_scaled')
        active.add('inc_population_scaled')
        active.add('inc_asso_core_scaled')
        # F-26: Refugee Associations
        active.add('inc_asso_refug_scaled') 
        active.add('inc_siae_density_scaled') # New F-39: SIAE Density
        
        inc_services_add = getattr(config, 'inc_services_add_selection', [])
        inc_services_core = getattr(config, 'inc_services_core_selection', [])
        if inc_services_add or inc_services_core: 
            active.add('inc_services_incl_scaled')
            
        if getattr(config, 'inc_asso_add_selection', []): 
            active.add('inc_asso_add_scaled')
        
        # 7. Population Target (F-50)
        if hasattr(config, 'target_population'): # Only if explicitly requested or part of full model
            active.add('inc_population_scaled')

        return active

    
    def format_city_details(self, row: pd.Series, config: Optional[SearchCriterias] = None) -> CommuneResult:
        """
        Formats detailed information for a city to be displayed in the UI.
        Returns a CommuneResult Pydantic model.
        Hydrates static data (geometries, labels) from the shared global dataset.
        """
        codgeo_str = str(row['codgeo']) if 'codgeo' in row else str(row.name)
        
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
            "name": static_row.get('libgeo', 'Inconnu'),
            "population": int(round(static_row.get('population', 0))),
            "bassin_de_vie": static_row.get('libelle_bassin_de_vie', 'N/A'),
            "global_score": float(row.get('weighted_score', 0.0)) if 'weighted_score' in row else 0.0
        }

        # Domain Objects (Using unified models for robust typing)
        emploi_data = EmploymentMetrics()
        edu_data = EducationMetrics()
        sante_data = HealthMetrics()
        incl_data = InclusionMetrics()
        mob_data = MobilityMetrics()
        logement_data = HousingMetrics()
        
        # Populate mobility & static defaults from static_row
        mob_data.bus_stops = int(static_row.get('nb_stops_bus', 0))
        mob_data.tram_stops = int(static_row.get('nb_stops_tram', 0))
        mob_data.metro_stops = int(static_row.get('nb_stops_metro', 0))
        mob_data.train_stops = int(static_row.get('nb_stops_train', 0))
        mob_data.total_stops = int(static_row.get('nb_stops_total', 0))
        mob_data.stop_density = float(static_row.get('mob_trans_pub_stop_density', 0.0))
        
        # Populate logement defaults
        logement_data.host_count = int(static_row.get('heb_accueillants_count', 0))

        # Extract lat/lon from geometry if available (Use static_row)
        lat, lon = 0.0, 0.0

        if 'centroid_lon' in static_row and pd.notna(static_row['centroid_lon']):
             try:
                 # Project from Lambert-93 (2154) to Lat/Lon (4326) for UI/Analysis consumers
                 curr_x, curr_y = static_row['centroid_lon'], static_row['centroid_lat']
                 lon, lat = project_point(curr_x, curr_y, from_crs='EPSG:2154', to_crs='EPSG:4326')
             except Exception:
                 pass

        # Use cached active criteria if available
        active_ids = config.active_criteria if config and config.active_criteria is not None else self._get_active_criteria(config)

        # 1. Normalize and aggregate category weights
        cat_weights = {
            'emploi': config.poids_emploi if config else 100.0,
            'logement': config.poids_logement if config else 100.0,
            'education': config.poids_education if config else 100.0,
            'inclusion': config.poids_inclusion if config else 100.0,
            'mobilite': config.poids_mobilite if config else 100.0,
            'sante': config.poids_sante if config else 100.0
        }
        
        # Skip categories based on config
        if config:
            if config.nb_enfants == 0: cat_weights['education'] = 0.0
            if config.besoin_sante == 'Aucun': 
                cat_weights['sante'] = 0.0
        
        # 2. Identify displayed criteria and compute internal weights based on visibility
        displayed_items = []
        cat_internal_weights = {} # sum of w_crit for displayed items in each normalized cat
        active_norm_cats = set()
        
        for _, score_row in self.scores_cat.iterrows():
            score_id = score_row['score']
            val_scaled = float(row[score_id]) if score_id in row and pd.notna(row[score_id]) else None
            
            # Skip if not active or if value is missing
            if (config and score_id not in active_ids) or val_scaled is None:
                continue
            
            cat = score_row['cat']
            norm_cat = cat
            if norm_cat in ['mobilité', 'mobilite']:
                norm_cat = 'mobilite'
            elif norm_cat in ['santé', 'sante']:
                norm_cat = 'sante'
            active_norm_cats.add(norm_cat)
            
            w_crit = float(score_row['weight'])
            if config and score_id in config.criteria_weights: w_crit *= config.criteria_weights[score_id]
            
            cat_internal_weights[norm_cat] = cat_internal_weights.get(norm_cat, 0.0) + w_crit
            displayed_items.append({
                "score_row": score_row,
                "val_scaled": val_scaled,
                "norm_cat": norm_cat,
                "w_crit": w_crit
            })

        # 3. Total Category Weight Sum (Effective for displayed items)
        total_cat_weight = sum(cat_weights[c] for c in active_norm_cats)
        if total_cat_weight == 0: total_cat_weight = 1.0

        # Structured Scores for CommuneResult
        structured_scores: Dict[str, List[CommuneScoreDetail]] = {}

        # 4. Populate details with correctly weighted items
        for item in displayed_items:
            score_row = item['score_row']
            cat = score_row['cat']
            norm_cat = item['norm_cat']
            val_scaled = item['val_scaled']
            w_crit = item['w_crit']
            score_id = score_row['score']

            if norm_cat not in structured_scores: structured_scores[norm_cat] = []
            
            # Improved Valeur KPI (Checking both shared data and computed results)
            val_raw = None
            raw_metric_col = score_row['metric']
            
            # KPI could be either in computed results OR in static shared data
            src_row = static_row if raw_metric_col in static_row else (row if raw_metric_col in row else None)
            
            if src_row is not None and raw_metric_col in src_row and pd.notna(src_row[raw_metric_col]):
                val = src_row[raw_metric_col]
                d_factor = float(score_row.get('display_factor', 1.0))
                if pd.api.types.is_number(val):
                    val_raw = float(val * d_factor)
                else:
                    try:
                         val_raw = float(val) * d_factor
                    except:
                         val_raw = val
            
            # Format val_raw for display (preserving underlying type logic)
            unit = score_row.get('unit', score_row.get('description', ''))
            if isinstance(val_raw, (int, float)):
                if unit == "habitants":
                    val_raw = int(round(float(val_raw) / 1000) * 1000)
                elif unit == "%" or unit == "assos/1000 hab.":
                    val_raw = round(float(val_raw), 1)
                elif float(val_raw).is_integer():
                    val_raw = int(val_raw)
                else:
                    val_raw = round(float(val_raw), 1)
            
            # Impact = (w_crit / sum_weights_in_cat) * (cat_weight / total_cat_weight)
            rel_weight = (w_crit / cat_internal_weights[norm_cat]) * (cat_weights[norm_cat] / total_cat_weight)

            structured_scores[norm_cat].append(CommuneScoreDetail(
                label=score_row.get('label', score_id),
                score_id=score_id,
                valeur_kpi=val_raw,
                score_normalise=val_scaled,
                unit=unit,
                relative_weight=round(rel_weight * 100, 1)
            ))

        # 2. Housing Details (ODACE Specifics)
        housing_types = ['appt_all', 'appt_t1_t2', 'appt_t3_p', 'house_all']
        for ht in housing_types:
            raw_col = f"loyer_m2_moy_{ht}"
            scaled_col = f"log_loyer_moyen_{ht}_scaled"
            
            variant_data = {
                "raw": float(row[raw_col]) if raw_col in row and pd.notna(row[raw_col]) else None,
                "scaled": float(row[scaled_col]) if scaled_col in row and pd.notna(row[scaled_col]) else None
            }
            logement_data.odace_all_variants[ht] = variant_data
            
            # Set top-level raw value if it's the selected type
            type_log = None
            if config and config.type_logement:
                type_log = config.type_logement.code if hasattr(config.type_logement, 'code') else config.type_logement
            
            if config and type_log == ht:
                logement_data.price_per_sqm = variant_data['raw']
            elif not config and ht == 'appt_all':
                logement_data.price_per_sqm = variant_data['raw']

        # 3. Emploi (Top 10 from Live Jobs & Formations)
        c_code = codgeo_str
        if c_code:
            # --- Live Jobs Match (ROME) ---
            if not self.live_jobs_data.empty:
                live_city = self.live_jobs_data[self.live_jobs_data['commune'] == c_code].copy()
                if not live_city.empty:
                    # Global Summary
                    live_summary = live_city.groupby('romeLibelle')['total_postes'].sum().to_dict()
                    emploi_data.standard_jobs_summary = live_summary
                    emploi_data.standard_jobs_total = int(live_city['total_postes'].sum())
                    
                    # Matching Summary (filtered by config)
                    if config and config.codes_metiers:
                        # Flatten the list of lists of ROME codes
                        target_romes = set()
                        for codes in config.codes_metiers:
                            if isinstance(codes, list):
                                for c in codes:
                                    val = c.code if hasattr(c, 'code') else c
                                    if len(val) == 5: target_romes.add(val)
                            elif isinstance(codes, str) and len(codes) == 5:
                                target_romes.add(codes)
                            elif hasattr(codes, 'code'):
                                val = codes.code
                                if len(val) == 5: target_romes.add(val)
                        
                        if target_romes:
                            matching_city = live_city[live_city['romeCode'].isin(target_romes)]
                            emploi_data.standard_jobs_matching_summary = matching_city.groupby('romeLibelle')['total_postes'].sum().to_dict()
                            emploi_data.standard_jobs_matching_total = int(matching_city['total_postes'].sum())

                    # Top 10 unique labels by volume with postes count
                    top_live = live_city.groupby('romeLibelle')['total_postes'].sum().sort_values(ascending=False).head(10)
                    emploi_data.top_professions = [f"{label} ({int(vol)} postes)" for label, vol in top_live.items()]
                else:
                    emploi_data.standard_jobs_total = 0
                    emploi_data.standard_jobs_matching_total = 0
                    emploi_data.top_professions = []

            # --- SIAE Jobs Match (New F-39) ---
            if not self.siae_jobs_data.empty:
                siae_city = self.siae_jobs_data[self.siae_jobs_data['codgeo'] == codgeo_str].copy()
                if not siae_city.empty:
                    # Map rome to label using rome_index if rome_label is missing
                    if 'rome_label' not in siae_city.columns and not self.rome_index.empty:
                        siae_city['rome_label'] = siae_city['rome'].map(self.rome_index['label']).fillna(siae_city['rome'])
                    
                    # Fallback for display if no label at all
                    label_col = 'rome_label' if 'rome_label' in siae_city.columns else 'rome'
                    
                    emploi_data.inclusive_jobs_total = int(len(siae_city))
                    emploi_data.inclusive_jobs_summary = siae_city.groupby(label_col).size().to_dict()
                    emploi_data.inclusive_jobs_matching_summary = {}
                    emploi_data.inclusive_jobs_matching_total = 0
                    
                    if config and config.codes_metiers:
                        siae_prefixes = set()
                        for codes in config.codes_metiers:
                            if isinstance(codes, list):
                                for c in codes:
                                    val = c.code if hasattr(c, 'code') else c
                                    if len(val) >= 3: siae_prefixes.add(val[:3])
                            else:
                                val = codes.code if hasattr(codes, 'code') else codes
                                if isinstance(val, str) and len(val) >= 3:
                                    siae_prefixes.add(val[:3])
                        
                        if siae_prefixes:
                            # Use 'rome' column
                            siae_matching = siae_city[siae_city['rome'].str[:3].isin(siae_prefixes)]
                            matching_dict = siae_matching.groupby(label_col).size().to_dict()
                            emploi_data.inclusive_jobs_matching_summary = matching_dict
                            emploi_data.inclusive_jobs_matching_total = sum(matching_dict.values())
                else:
                    emploi_data.inclusive_jobs_total = 0
                    emploi_data.inclusive_jobs_summary = {}
                    emploi_data.inclusive_jobs_matching_summary = {}
                    emploi_data.inclusive_jobs_matching_total = 0
            
            # Formations logic remains
            if not self.formations_data.empty:
                 city_forms = self.formations_data[self.formations_data['codgeo'] == c_code].copy()
                 if not city_forms.empty:
                     if self.codformations_index is not None and not self.codformations_index.empty:
                         # Robust type conversion for merge keys
                         city_forms['formation_code'] = city_forms['formation_code'].astype(str)
                         merged_f = city_forms.merge(self.codformations_index, left_on='formation_code', right_index=True, how='left')
                         merged_f['label'] = merged_f['label'].fillna(merged_f['formation_code'])
                         emploi_data.training_programs = sorted(merged_f['label'].unique().tolist())
                     else:
                         emploi_data.training_programs = sorted(city_forms['formation_code'].unique().tolist())

        # 4. Education & Sante Counts & Grouped Etablissements
        for dom, mapping, annuaire, data_obj in [
            ('education', {'maternelle': 'edu_maternelle_ct', 'elementaire': 'edu_elementaire_ct', 'college': 'edu_college_ct', 'lycee': 'edu_lycee_ct'}, self.annuaire_ecoles, edu_data), 
            ('sante', {'hopital': 'count_hopital', 'maternite': 'count_maternite', 'psy': 'count_psy'}, self.annuaire_sante, sante_data)
        ]:
            for key, col in mapping.items():
                if col in row: data_obj.facility_counts[key] = int(row[col])
            
            if codgeo_str and not annuaire.empty:
                # Extra safety: filter by codgeo and category to avoid leaks
                city_pois = annuaire[(annuaire['codgeo'] == codgeo_str) & (annuaire['category'] == dom)]
                if not city_pois.empty:
                    # Group by 'type' or fallback to 'categorie'
                    type_col = 'type' if 'type' in city_pois.columns else ('categorie' if 'categorie' in city_pois.columns else None)
                    # Safely find a label column
                    label_col = 'label' if 'label' in city_pois.columns else ('name' if 'name' in city_pois.columns else None)
                    
                    if type_col and label_col:
                        grouped = city_pois.groupby(type_col, observed=True)[label_col].apply(lambda x: sorted(list(set(x)))).to_dict()
                        data_obj.facility_details = grouped

        # 6. Inclusion (Grouped by Thematic)
        incl_data = InclusionMetrics()
        incl_data.cat_score = float(row.get('inclusion_cat_score', 0.0))
        
        if codgeo_str and not self.annuaire_inclusion.empty:
            city_incl = self.annuaire_inclusion[self.annuaire_inclusion['codgeo'] == codgeo_str]
            if not city_incl.empty:
                # Group by 'thematiques'
                if 'thematiques' in city_incl.columns:
                    label_col = 'label' if 'label' in city_incl.columns else ('name' if 'name' in city_incl.columns else None)
                    if label_col:
                        # Group by thematic codes first
                        grouped_incl_raw = city_incl.groupby('thematiques', observed=True)[label_col].apply(list).to_dict()
                        
                        # Map codes to labels using inclusion_services_index (safely)
                        grouped_incl = {}
                        for code, names in grouped_incl_raw.items():
                            label = code
                            try:
                                if hasattr(self, 'inclusion_services_index') and self.inclusion_services_index is not None and code in self.inclusion_services_index.index:
                                    val = self.inclusion_services_index.loc[code, 'label']
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
        elif self.rna_rag_service and codgeo_str:
            # Fallback (Sync) - only if we don't use the background enrichment or for single-city tests
            # For now, we keep it empty to allow background enrichment to fill it.
            pass

        incl_data.asso_refugee_list = refugee_list
        incl_data.asso_refugee_count = len(refugee_list)
        incl_data.asso_inclusion_list_by_cat = inclusion_list_by_cat
        incl_data.asso_inclusion_count = total_incl_count
        # 8. Calculate dynamic category scores (weighted average of active criteria)
        # This ensures the radar chart is populated even for reference cities not in the search pool.
        cat_final_scores = {}
        for norm_cat in active_norm_cats:
            cat_items = [it for it in displayed_items if it['norm_cat'] == norm_cat]
            if cat_items and cat_internal_weights.get(norm_cat, 0) > 0:
                cat_final_scores[norm_cat] = sum(it['val_scaled'] * it['w_crit'] for it in cat_items) / cat_internal_weights[norm_cat]
            else:
                cat_final_scores[norm_cat] = 0.0

        emploi_data.cat_score = float(cat_final_scores.get('emploi', 0.0))
        logement_data.cat_score = float(cat_final_scores.get('logement', 0.0))
        edu_data.cat_score = float(cat_final_scores.get('education', 0.0))
        sante_data.cat_score = float(cat_final_scores.get('sante', 0.0))
        incl_data.cat_score = float(cat_final_scores.get('inclusion', 0.0))
        mob_data.cat_score = float(cat_final_scores.get('mobilite', 0.0))

        return CommuneResult(
            codgeo=str(row.name),
            name=static_row.get('libgeo', 'Inconnu'),
            population=int(static_row.get('population', 0)),
            codgeo_bdv=str(static_row.get('bassin_de_vie', 'Inconnu')),
            name_bdv=static_row.get('libelle_bassin_de_vie', 'Inconnu'),
            global_score=float(row.get('weighted_score', 0.0)),
            scores=structured_scores,
            employment=emploi_data,
            housing=logement_data,
            education=edu_data,
            health=sante_data,
            inclusion=incl_data,
            mobility=mob_data
        )

    def create_search_results(self, processed_gdf: gpd.GeoDataFrame, config: SearchCriterias) -> SearchResultsData:
        """Helper to create a SearchResultsData object from the scoring results."""
        
        # 1. Identify the current city
        c_code_raw = config.commune_actuelle
        c_code = c_code_raw.code if hasattr(c_code_raw, 'code') else c_code_raw
        
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
             current_geo = self.format_city_details(self.df_all_communes.loc[c_code], config)
             
        # 3. Filter out current city and its PLM family from the results list
        display_gdf = processed_gdf.copy()
        
        # Drop the current code itself
        if c_code in display_gdf.index:
            display_gdf = display_gdf.drop(c_code)
            
        # Detect PLM family (either parent or arrondissement)
        plm_prefix = None
        if c_code in cfg.PLM_MAPPING:
            plm_prefix = cfg.PLM_MAPPING[c_code]
        else:
            # Check if c_code is an arrondissement (e.g. '13201' starts with '132')
            for parent_code, prefix in cfg.PLM_MAPPING.items():
                if str(c_code).startswith(prefix):
                    plm_prefix = prefix
                    # Also explicitly drop the parent code if it's in the results
                    if parent_code in display_gdf.index:
                        display_gdf = display_gdf.drop(parent_code)
                    break
        
        if plm_prefix:
            # Drop all members of this PLM family (starts with prefix)
            display_gdf = display_gdf[~display_gdf.index.astype(str).str.startswith(plm_prefix)]

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
            current_geo=current_geo
        )

    def get_city_details(self, codgeo: str) -> CommuneResult:
        """Retrieves detailed information using static data."""
        if codgeo not in self.df_all_communes.index:
            raise KeyError(f"Commune code {codgeo} not found.")

        return self.format_city_details(self.df_all_communes.loc[codgeo])

    def run_optimized(self, config: SearchCriterias, log_prefix: str = "search_results") -> Tuple[SearchResultsData, pd.DataFrame]:
        """
        Orchestrates the full scoring pipeline with optimized memory management.
        Returns a tuple (SearchResultsData model, pruned DataFrame for map).
        """
        # 1. Compute full scores
        results_raw = self.run(config)
        
        # 2. Extract into Pydantic model while we still have all columns
        # (Hydration from shared static_row happens inside format_city_details)
        model = self.create_search_results(results_raw, config)

        # 🧪 SOTA: Local markdown logging for development audit
        try:
            log_search_results(config, model, prefix=log_prefix)
        except Exception as e:
            logger.warning(f"⚠️ [SCORING] Search result logging failed: {e}")
        
        # 3. Aggressively prune the DataFrame to only what's needed for the map
        # Now dropping polygons as they are hydrated JIT during rendering
        self._prune_irrelevant_metrics(results_raw, config, aggressive=True)
        
        # Convert to standard DataFrame to remove GeoPandas overhead in session state
        if isinstance(results_raw, gpd.GeoDataFrame):
            results_raw = pd.DataFrame(results_raw.drop(columns='geometry', errors='ignore'))
            
        return model, results_raw

    def run(self, config: SearchCriterias, log_prefix: Optional[str] = None) -> pd.DataFrame:
        """Orchestrates the full scoring pipeline."""
        logger.debug(f"⚙️ [ENGINE] Starting run with Profile: {config.weight_profile}")
        logger.debug(f"⚙️ [ENGINE] Config: {config}")
        if not config.active_criteria:
            config.active_criteria = self._get_active_criteria(config)
            
        # Derive active categories from scores_cat mapping
        if config.active_criteria:
            active_mask = self.scores_cat['score'].isin(config.active_criteria)
            cats = self.scores_cat[active_mask]['cat'].unique()
            normalized = set()
            for c in cats:
                nc = str(c).lower()
                if nc in ['mobilité', 'mobilite']: nc = 'mobilite'
                elif nc in ['santé', 'sante']: nc = 'sante'
                normalized.add(nc)
            config.active_categories = sorted(list(normalized))
        
        c_code_obj = getattr(config, 'commune_actuelle', None)
        c_code = c_code_obj.code if hasattr(c_code_obj, 'code') else c_code_obj
        
        # Robustness: fallback to Paris if c_code is missing
        if not c_code:
            logger.warning("⚠️ [ENGINE] commune_actuelle is None or empty. Falling back to Paris (75056).")
            c_code = '75056'

        start_commune = self.df_all_communes.loc[[c_code]]
        loc_type = config.loc_search_area # 'departement', 'region', 'france'
        loc_code = config.loc_search_code
        
        if not loc_code and loc_type != 'france':
            # Fallback to current location's area
            loc_col = 'dep_code' if loc_type == 'departement' else 'reg_code'
            loc_code = start_commune.iloc[0][loc_col]

        communes_to_score = self._filter_communes(
            df=self.df_all_communes,
            start_commune=start_commune,
            loc_type=loc_type,
            loc_code=loc_code
        )
        
        # 🧪 CRITICAL: Always include the current commune in the scoring pool
        # This ensures it gets scored with the exact same logic (normalization bounds, active criteria)
        # as the candidates, even if it falls outside the geographic filter.
        if c_code in self.df_all_communes.index and c_code not in communes_to_score.index:
            communes_to_score = pd.concat([communes_to_score, self.df_all_communes.loc[[c_code]]])
        
        # 1. Early Pruning
        # We drop any _scaled metrics that are NOT active in the request to save memory and processing time.
        communes_to_score = self._prune_irrelevant_metrics(communes_to_score, config, aggressive=False)
        
        results = self._compute_scores(communes_to_score, config)
        
        # 2. Return the results
        del communes_to_score
        gc.collect()

        # 2. Return the results
        # Note: We do NOT prune aggressively here yet because the caller (UI or MCP)
        # needs the full data to call format_city_details for the Top results.
        return results

    def _compute_scores(self, df_search: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        if df_search.empty: return df_search

        # Distance
        odis_search = df_search
        if 'dist_current_loc' not in odis_search.columns:
            odis_search = self._compute_distance_score(odis_search, config)

        # Merge BdV Data
        if self.bv_data is not None and not self.bv_data.empty and 'bassin_de_vie' in odis_search.columns:
             # Ensure type consistency for merge
             odis_search['bassin_de_vie'] = odis_search['bassin_de_vie'].astype(str)
             
             # Instead of copying the whole bv_data, we merge and then handle suffixes if needed
             # Or even better, only merge the columns that are not already in odis_search
             bv_cols = [c for c in self.bv_data.columns if c not in odis_search.columns or c == 'bassin_de_vie']
             # However, the engine logic expects '_bdv' suffix for everything.
             # So we do need the suffix. Let's do it efficiently.
             
             odis_search = pd.merge(
                 odis_search, 
                 self.bv_data.add_suffix('_bdv'), 
                 left_on='bassin_de_vie', 
                 right_index=True, 
                 how='left'
             )

        # logger.info(f"⚙️ [ENGINE] Computing criteria scores...")
        odis_scored = self._compute_criteria_scores(odis_search, config)
        # logger.info(f"⚙️ [ENGINE] Computing category scores...")
        odis_exploded = self._compute_category_scores(odis_scored, config)
        # logger.info(f"⚙️ [ENGINE] Computing final weighted scores...")
        odis_exploded['weighted_score'] = self._compute_weighted_score(odis_exploded, config)

        # Final pruning of intermediates before return
        # Note: We don't do aggressive pruning here yet because the caller might need details.
        # Aggressive pruning happens in run() if needed.
        self._prune_irrelevant_metrics(odis_exploded, config, aggressive=False)

        return odis_exploded.sort_values(by='weighted_score', ascending=False)




    def _compute_employment_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        # Operating in-place
        
        # --- Live Jobs (ROME-based) ---
        if any(config.codes_metiers) and not self.live_jobs_data.empty:
            commune_to_bdv = self.df_all_communes['bassin_de_vie'].dropna().to_dict()

            for i in range(config.nb_adultes):
                adult_key = f'adult{i+1}'
                adult_romes = set()
                
                if i < len(config.codes_metiers):
                    for c in config.codes_metiers[i]:
                        # Handle CriteriaItem or str
                        code = c.code if hasattr(c, 'code') else str(c)
                        if len(code) == 5 and code[0].isalpha() and code[1:].isdigit():
                            adult_romes.add(code)
                
                if not adult_romes:
                    df[f'met_match_{adult_key}_scaled'] = 0.0
                    if 'bassin_de_vie' in df.columns:
                        df[f'met_match_{adult_key}_bdv_scaled'] = 0.0
                    df[f'met_match_{adult_key}_tension_scaled'] = 0.0
                    continue

                target_live = self.live_jobs_data[self.live_jobs_data['romeCode'].isin(adult_romes)].copy()
                
                # City Sum
                commune_live_counts = target_live.groupby('commune')['total_postes'].sum()
                col_raw = f'met_match_{adult_key}'
                df[col_raw] = df.index.map(commune_live_counts).fillna(0)
                
                # Tension Sum
                col_tension_raw = f'met_match_{adult_key}_tension'
                if 'nb_offres_tension' in target_live.columns:
                    commune_tension_counts = target_live.groupby('commune')['nb_offres_tension'].sum()
                    df[col_tension_raw] = df.index.map(commune_tension_counts).fillna(0)
                else:
                    df[col_tension_raw] = 0.0

                # BdV Sum
                col_bdv_raw = f'met_match_{adult_key}_bdv'
                if 'bassin_de_vie' in df.columns:
                    target_live['bdv'] = target_live['commune'].map(commune_to_bdv)
                    bdv_live_counts = target_live.groupby('bdv')['total_postes'].sum()
                    df[col_bdv_raw] = df['bassin_de_vie'].map(bdv_live_counts).fillna(0)
                else:
                    df[col_bdv_raw] = 0.0

                # Scaling
                s_def = self.scores_cat[self.scores_cat['score'] == f'{col_raw}_scaled'].iloc[0] if not self.scores_cat[self.scores_cat['score'] == f'{col_raw}_scaled'].empty else {}
                min_c, max_c = self._get_bounds(f'{col_raw}_scaled')
                if pd.isna(max_c): max_c = 10.0
                df[f'{col_raw}_scaled'] = self._scale_series(
                    df[col_raw], min_c, max_c, 
                    scaling_type=s_def.get('scaling_type', 'linear'),
                    mu=s_def.get('mu'),
                    sigma=s_def.get('sigma')
                )
                
                s_def_bdv = self.scores_cat[self.scores_cat['score'] == f'{col_raw}_scaled_bdv'].iloc[0] if not self.scores_cat[self.scores_cat['score'] == f'{col_raw}_scaled_bdv'].empty else {}
                min_b, max_b = self._get_bounds(f'{col_raw}_scaled_bdv')
                if pd.isna(max_b): max_b = 50.0
                df[f'{col_raw}_scaled_bdv'] = self._scale_series(
                    df[col_bdv_raw], min_b, max_b,
                    scaling_type=s_def_bdv.get('scaling_type', 'linear'),
                    mu=s_def_bdv.get('mu'),
                    sigma=s_def_bdv.get('sigma')
                )
                
                s_def_t = self.scores_cat[self.scores_cat['score'] == f'{col_tension_raw}_scaled'].iloc[0] if not self.scores_cat[self.scores_cat['score'] == f'{col_tension_raw}_scaled'].empty else {}
                min_t, max_t = self._get_bounds(f'{col_tension_raw}_scaled')
                if pd.isna(max_t): max_t = 5.0
                df[f'{col_tension_raw}_scaled'] = self._scale_series(
                    df[col_tension_raw], min_t, max_t,
                    scaling_type=s_def_t.get('scaling_type', 'linear'),
                    mu=s_def_t.get('mu'),
                    sigma=s_def_t.get('sigma')
                )

                # --- SIAE Jobs Matching (New F-39) ---
                col_siae_raw = f'met_siae_match_{adult_key}'
                df[col_siae_raw] = 0.0
                
                if self.siae_jobs_data is not None and not self.siae_jobs_data.empty:
                    # SIAE matching uses 3rd digit prefix
                    siae_prefixes = {c[:3] for c in adult_romes if len(c) >= 3}
                    
                    # Filter SIAE jobs matching these prefixes
                    # ROME column in siae_jobs_data is named 'rome'
                    siae_match = self.siae_jobs_data[
                        self.siae_jobs_data['rome'].str[:3].isin(siae_prefixes)
                    ]
                    
                    if not siae_match.empty:
                        siae_counts = siae_match.groupby('codgeo').size()
                        df[col_siae_raw] = df.index.map(siae_counts).fillna(0)

                # Scaling SIAE
                s_def_s = self.scores_cat[self.scores_cat['score'] == f'{col_siae_raw}_scaled'].iloc[0] if not self.scores_cat[self.scores_cat['score'] == f'{col_siae_raw}_scaled'].empty else {}
                min_s, max_s = self._get_bounds(f'{col_siae_raw}_scaled')
                if pd.isna(max_s): max_s = 5.0
                df[f'{col_siae_raw}_scaled'] = self._scale_series(
                    df[col_siae_raw], min_s, max_s,
                    scaling_type=s_def_s.get('scaling_type', 'linear'),
                    mu=s_def_s.get('mu'),
                    sigma=s_def_s.get('sigma')
                )

        # --- Formations ---
        if any(config.codes_formations):
            relevant_formations = self.formations_data[self.formations_data['codgeo'].isin(df.index)]
            form_map = relevant_formations.groupby('codgeo')['formation_code'].apply(set).to_dict()
            
            commune_to_bdv = self.df_all_communes['bassin_de_vie'].dropna().to_dict()
            relevant_formations_bdv = relevant_formations.copy()
            relevant_formations_bdv['bdv'] = relevant_formations_bdv['codgeo'].map(commune_to_bdv)
            form_map_bdv = relevant_formations_bdv.groupby('bdv')['formation_code'].apply(set).to_dict()

            for i in range(config.nb_adultes):
                if i < len(config.codes_formations) and config.codes_formations[i]:
                    adult_key = f'adult{i+1}'
                    # Handle CriteriaItem
                    prefs = {c.code if hasattr(c, 'code') else str(c) for c in config.codes_formations[i]}
                    
                    col_name = f'form_match_codes_{adult_key}'
                    df[col_name] = df.index.map(lambda c: list(form_map.get(c, set()).intersection(prefs)))
                    
                    # Match Score Local
                    score_key = f'form_match_{adult_key}'
                    df[score_key] = df.index.map(lambda c: len(form_map.get(c, set()).intersection(prefs)))
                    s_def_fl = self.scores_cat[self.scores_cat['score'] == f'{score_key}_scaled'].iloc[0] if not self.scores_cat[self.scores_cat['score'] == f'{score_key}_scaled'].empty else {}
                    min_b, max_b = self._get_bounds(f'{score_key}_scaled')
                    if pd.isna(max_b): max_b = float(len(prefs))
                    df[f'{score_key}_scaled'] = self._scale_series(
                        df[score_key].fillna(0), min_b, max_b,
                        scaling_type=s_def_fl.get('scaling_type', 'linear'),
                        mu=s_def_fl.get('mu'),
                        sigma=s_def_fl.get('sigma')
                    )

                    # Match Score BdV
                    if 'bassin_de_vie' in df.columns:
                        df[f'{score_key}_bdv'] = df['bassin_de_vie'].map(lambda b: len(form_map_bdv.get(b, set()).intersection(prefs)))
                        s_def_fb = self.scores_cat[self.scores_cat['score'] == f'{score_key}_scaled_bdv'].iloc[0] if not self.scores_cat[self.scores_cat['score'] == f'{score_key}_scaled_bdv'].empty else {}
                        min_b, max_b = self._get_bounds(f'{score_key}_scaled_bdv')
                        if pd.isna(max_b): max_b = float(len(prefs))
                        df[f'{score_key}_scaled_bdv'] = self._scale_series(
                            df[f'{score_key}_bdv'].fillna(0), min_b, max_b,
                            scaling_type=s_def_fb.get('scaling_type', 'linear'),
                            mu=s_def_fb.get('mu'),
                            sigma=s_def_fb.get('sigma')
                        )

            # Aggregate formation names
            if self.codformations_index is not None and not self.codformations_index.empty:
                def get_all_labels(row):
                    codes = set()
                    for i in range(config.nb_adultes):
                        col = f'form_match_codes_adult{i+1}'
                        if col in row and isinstance(row[col], list): codes.update(row[col])
                    return [self.codformations_index.loc[c, 'label'] if c in self.codformations_index.index else c for c in codes]
                df['noms_formations'] = df.apply(get_all_labels, axis=1)
            else:
                df['noms_formations'] = [[] for _ in range(len(df))]

        return df

    def _compute_sante_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        # Operating in-place
        if config.besoin_sante != 'Aucun':
            col_map = {'Hôpital': 'sante_hopital_scaled', 'Hopital': 'sante_hopital_scaled',
                       'Maternité': 'sante_maternite_scaled', 
                       'Soutien Psychologique & Addictologie': 'sante_psy_scaled', 'Psychiatrie': 'sante_psy_scaled'}
            target = col_map.get(config.besoin_sante)
            if target and target in df.columns: 
                df['sante_structures_scaled'] = df[target]
            else: 
                df['sante_structures_scaled'] = 0.0
        return df

    def _compute_mobility_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        # Operating in-place
        
        # --- Density ---
        if 'nb_stops_total' in df.columns:
            df['mob_trans_pub_stop_density'] = (df['nb_stops_total'] / df['population'].replace(0, 1)) * 1000
            s_def_mob = self.scores_cat[self.scores_cat['score'] == 'mob_trans_pub_density_scaled'].iloc[0] if not self.scores_cat[self.scores_cat['score'] == 'mob_trans_pub_density_scaled'].empty else {}
            min_b, max_b = self._get_bounds('mob_trans_pub_density_scaled')
            if pd.isna(max_b): max_b = 10.0 
            df['mob_trans_pub_density_scaled'] = self._scale_series(
                df['mob_trans_pub_stop_density'], min_b, max_b,
                scaling_type=s_def_mob.get('scaling_type', 'linear'),
                mu=s_def_mob.get('mu'),
                sigma=s_def_mob.get('sigma')
            )

        # --- EPCI Bonus ---
        current_epci = None
        current_reg = None
        current_dep = None
        
        # Resolve current location details
        c_code_obj = getattr(config, 'commune_actuelle', None)
        if c_code_obj:
             c_code = c_code_obj.code if hasattr(c_code_obj, 'code') else c_code_obj
             if c_code in self.df_all_communes.index:
                  cur_row = self.df_all_communes.loc[c_code]
                  current_epci = cur_row['epci_code']
                  current_reg = cur_row['reg_code']
                  current_dep = cur_row['dep_code']

        if self._is_local_search(config) and current_epci:
             df['mob_epci_scaled'] = np.where(df['epci_code'] == current_epci, 1.0, 0.0)
        else:
             df['mob_epci_scaled'] = 0.0
             
        return df

    def _compute_education_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        # Placeholder for specific education logic if needed in future.
        # Currently education scores are pre-computed in DB/DF.
        return df

    def _compute_housing_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        # Placeholder for specific housing logic if needed.
        # Currently housing pruning handles most variation.
        return df

    def _compute_inclusion_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        # Operating in-place
        
        # Population Score (F-50) - Dynamic re-calculation
        # This overrides the precomputed 'inc_population_scaled' if mu/sigma are provided in config
        if 'inc_population_scaled' in self._get_active_criteria(config) and 'population' in df.columns:
            mu = getattr(config, 'target_population', 50000)
            sigma = getattr(config, 'target_population_sigma', 25000)
            
            # Recompute gaussian score based on raw population
            # We use _scale_series which handles 'gaussian' type
            df['inc_population_scaled'] = self._scale_series(
                df['population'], 0, 0, 
                scaling_type='gaussian', mu=mu, sigma=sigma
            )

        if 'inc_asso_core_scaled' not in df.columns: df['inc_asso_core_scaled'] = 0.0

        # Affinities
        inc_asso_add = getattr(config, 'inc_asso_add_selection', [])
        if inc_asso_add:
            interest_codes = set()
            for i in inc_asso_add:
                  # Logic to handle CriteriaItem or str
                  code = i.code if hasattr(i, 'code') else str(i)
                  interest_codes.add(code)
            
            if interest_codes:
                # Normalization logic
                expanded_interests = set()
                for c in interest_codes:
                     expanded_interests.add(c)
                     if c.startswith('0'): expanded_interests.add(c.lstrip('0'))
                     else: expanded_interests.add('0' + c)
                
                affinite_assos = self.associations_data[self.associations_data['id_waldec'].astype(str).str.startswith(tuple(expanded_interests), na=False)]
                affinite_counts = affinite_assos.groupby('codgeo')['count'].sum().reindex(df.index, fill_value=0)
                
                # Safety check for population column
                if 'population' in df.columns:
                    df['affinite_density'] = (affinite_counts * 1000) / df['population']
                else:
                    df['affinite_density'] = 0.0
                min_b, max_b = self._get_bounds('inc_asso_add_scaled')
                s_def_inc = self.scores_cat[self.scores_cat['score'] == 'inc_asso_add_scaled'].iloc[0] if not self.scores_cat[self.scores_cat['score'] == 'inc_asso_add_scaled'].empty else {}
                min_b, max_b = self._get_bounds('inc_asso_add_scaled')
                df['inc_asso_add_scaled'] = self._scale_series(
                    df['affinite_density'], min_b, max_b,
                    scaling_type=s_def_inc.get('scaling_type', 'linear'),
                    mu=s_def_inc.get('mu'),
                    sigma=s_def_inc.get('sigma')
                )
            else: 
                if 'inc_asso_add_scaled' in df.columns: df.drop(columns=['inc_asso_add_scaled'], inplace=True)
        else: 
            if 'inc_asso_add_scaled' in df.columns: df.drop(columns=['inc_asso_add_scaled'], inplace=True)

        # Inclusion Services (F-48: Merged Selection)
        needed = set()
        for i in getattr(config, 'inc_services_core_selection', []):
            needed.add(i.code if hasattr(i, 'code') else str(i))
        for i in getattr(config, 'inc_services_add_selection', []):
            needed.add(i.code if hasattr(i, 'code') else str(i))
            
        if needed:
             # Optimize lookup
             def count_matches(available):
                 if not isinstance(available, set): return 0
                 return sum(1 for n in needed if any(n in a for a in available))

             if 'key' not in df.columns: 
                 df = df.join(self.incl_index, how='left')
             
             # Direct calculation without intermediate column
             df['inc_services_incl_scaled'] = df['key'].apply(count_matches) / len(needed)
        else:
             if 'inc_services_incl_scaled' in df.columns: 
                 df.drop(columns=['inc_services_incl_scaled'], inplace=True)

        return df

    def _compute_criteria_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        # Orchestrator
        df = self._compute_employment_scores(df, config)
        df = self._compute_mobility_scores(df, config)
        df = self._compute_sante_scores(df, config)
        df = self._compute_inclusion_scores(df, config)
        df = self._compute_housing_scores(df, config)
        df = self._compute_education_scores(df, config)
        
        # Pruning
        df = self._prune_irrelevant_metrics(df, config)
        
        return df


    def _is_local_search(self, config: SearchCriterias) -> bool:
        """Determines if the search is happening within the user's current area."""
        c_code_raw = getattr(config, 'commune_actuelle', None)
        if not c_code_raw:
            return False
            
        c_code = c_code_raw.code if hasattr(c_code_raw, 'code') else c_code_raw
        
        if c_code not in self.df_all_communes.index:
            return False
            
        cur_row = self.df_all_communes.loc[c_code]
        current_dep = cur_row['dep_code']
        current_reg = cur_row['reg_code']
        
        # Search area must either be the same department or same region
        if config.loc_search_area == 'region':
            return config.loc_search_code == current_reg
            
        if config.loc_search_area == 'departement':
            return config.loc_search_code == current_dep
            
        # If searching France or another area, proximity is not a local search factor
        return False

    def prefetch_associations(self, codgeos: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Fetches association details for multiple communes in a single BigQuery call.
        Updates self._associations_cache.
        """
        if not self.rna_rag_service or not codgeos:
            return {}
            
        try:
            logger.info(f"📊 [PREFETCH] Fetching associations for {len(codgeos)} communes")
            all_assos = self.rna_rag_service.get_associations_by_codgeo(codgeos)
            
            # Reset cache for these specific codgeos
            temp_results = {cg: {"refugee": [], "inclusion": {}} for cg in codgeos}
            
            for asso in all_assos:
                codgeo = asso.get('codgeo')
                if not codgeo or codgeo not in temp_results:
                    continue
                
                # Mapping logic (same as in format_city_details)
                raw_code = str(asso.get('code_waldec', '')).strip()
                desc = str(asso.get('description', '')).strip()
                if desc.lower() in ["nan", "none"]: desc = ""
                if len(desc) > 250: desc = desc[:250] + "..."
                
                name = string.capwords(str(asso.get('name', 'Inconnu')).lower())
                
                asso_data = {
                    "id": asso.get('id', ''),
                    "name": name,
                    "description": desc,
                    "waldec_code": raw_code,
                    "waldec_label": asso.get('categorie', 'Action Sociale'),
                    "categorie_odis": asso.get('primary_category', ''),
                    "codgeo": codgeo,
                    "is_refugee_focused": bool(asso.get('is_refugee_focused', False))
                }
                
                if asso_data["is_refugee_focused"]:
                    temp_results[codgeo]["refugee"].append(asso_data)
                else:
                    cat = asso_data["categorie_odis"] or "Inclusion"
                    if cat not in temp_results[codgeo]["inclusion"]:
                        temp_results[codgeo]["inclusion"][cat] = []
                    
                    if len(temp_results[codgeo]["inclusion"][cat]) < 20:
                        temp_results[codgeo]["inclusion"][cat].append(asso_data)
            
            # Bulk update cache
            self._associations_cache.update(temp_results)
            return temp_results
            
        except Exception as e:
            logger.error(f"❌ [PREFETCH] Failed associations fetch: {e}")
            return {}
