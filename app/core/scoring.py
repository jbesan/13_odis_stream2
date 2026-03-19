
# coding: utf-8
"""
Scoring module for the ODIS application.
"""
from typing import List, Dict, Set, Any, Optional, Union, Tuple
import geopandas as gpd
import numpy as np
import pandas as pd
from core.models import SearchCriterias
import config as cfg
import logging
from utils.logger import log_search_results
# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ScoringEngine:
    """
    The engine responsible for running the ODIS scoring algorithm.
    """
    @staticmethod
    def _filter_communes(df: gpd.GeoDataFrame, start_commune: pd.DataFrame, loc_type: str, loc_code: Optional[str]) -> gpd.GeoDataFrame:
        if loc_type == 'departement': return df[df['dep_code'] == loc_code].copy()
        elif loc_type == 'region': return df[df['reg_code'] == loc_code].copy()
        elif loc_type == 'france': return df[~df['dep_code'].astype(str).str.startswith(('97', '98'))].copy()
        return gpd.GeoDataFrame()
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

    def _compute_distance_score(self, df: gpd.GeoDataFrame, config: SearchCriterias) -> gpd.GeoDataFrame:
        current_codgeo_raw = config.commune_actuelle
        current_codgeo = current_codgeo_raw.code if hasattr(current_codgeo_raw, 'code') else current_codgeo_raw
        target_geom = None
        if current_codgeo in df.index:
             target_geom = df.loc[current_codgeo, 'centroid'] if 'centroid' in df.columns else df.loc[current_codgeo].geometry.centroid
        elif self.df_all_communes is not None and current_codgeo in self.df_all_communes.index: # Use self.df_all_communes
             target_geom = self.df_all_communes.loc[current_codgeo, 'centroid'] if 'centroid' in self.df_all_communes.columns else self.df_all_communes.loc[current_codgeo].geometry.centroid
        
        if target_geom is not None:
             # Use projected centroids
             centroids = df['centroid'] if 'centroid' in df.columns else df.centroid
             df.loc[:, 'dist_current_loc'] = centroids.distance(target_geom)
        
        # Scale if computed
        if 'dist_current_loc' in df.columns:
             min_b, max_b = self._get_bounds('mob_dist_current_loc_scaled')
             if pd.isna(max_b): max_b = 50000.0 # Default 50km
             # Inverse scale: closer is better
             scaled = self._scale_series(df['dist_current_loc'], min_b, max_b)
             df['mob_dist_current_loc_scaled'] = 1.0 - scaled
        
        return df

    def _compute_category_scores(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        df = df.copy()
        
        # Use cached active criteria if available
        active = config.active_criteria if config.active_criteria is not None else self._get_active_criteria(config)

        # Compute for all categories
        categories = ['emploi', 'logement', 'education', 'inclusion', 'mobilité', 'sante']
        for category in categories:
            # Skip if category totally irrelevant
            if category == 'education' and config.nb_enfants == 0: continue
            if category == 'sante' and config.besoin_sante == 'Aucun': continue

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
            'mobilité': config.poids_mobilité,
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

    def _prune_irrelevant_metrics(self, df: pd.DataFrame, config: SearchCriterias) -> pd.DataFrame:
        """
        Prunes redundant columns to optimize memory usage.
        Conservative approach: 
        1. Always drop the 3 requested BdV columns.
        2. Selectively drop unselected '_scaled' scores to keep the DataFrame lean.
        """
        if config is None:
            return df
            
        # 1. Deny-list: Explicitly requested redundant BdV columns
        to_drop = ['polygon_bdv', 'libgeo_bdv', 'centroid_bdv']
        
        # 2. Selective Pruning: Drop unselected high-level scores
        # This keeps the dataframe lean as expected by tests, but avoids touching identity/raw columns.
        active_ids = None
        if hasattr(config, 'active_criteria') and config.active_criteria is not None:
            active_ids = set(config.active_criteria)
        else:
            try:
                # Fallback: compute active criteria if possible (e.g. in tests)
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
            df = df.drop(columns=actual_drops)
            
        return df

    def __init__(
        self,
        df_all_communes: gpd.GeoDataFrame,
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
        bmo_vertical: pd.DataFrame = pd.DataFrame() # Deprecated
    ):
        self.df_all_communes = df_all_communes
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
        for i in range(config.nb_adultes):
            adult_idx = i + 1
            # Employment
            if i < len(config.codes_metiers) and config.codes_metiers[i]:
                active.add(f'met_match_adult{adult_idx}_scaled')
                active.add(f'met_match_adult{adult_idx}_tension_scaled')
                active.add(f'met_siae_match_adult{adult_idx}_scaled') 

            # Formations
            if i < len(config.codes_formations) and config.codes_formations[i]:
                active.add(f'form_match_adult{adult_idx}_scaled')

        # 3. Logement
        # F-42: Hebergement Refinements
        heb_sel = config.hebergement_cible
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
        if config.logement == 'Location' or "Location avec Intermédiation" in heb_sel:
             active.add('log_vac_scaled')
         # Handle both formats: log_loyer_moyen_appt_all_scaled and log_loyer_moyen_scaled_appartement_toutes
             type_log = config.type_logement.code if hasattr(config.type_logement, 'code') else config.type_logement
             active.add(f'log_loyer_moyen_{type_log}_scaled')
             if type_log == 'appartement_toutes':
                 active.add('log_loyer_moyen_scaled_appartement_toutes')
             elif type_log == 'appt_all':
                 active.add('log_loyer_moyen_appt_all_scaled')

        if config.logement == 'Logement Social':
            active.add('log_soc_inoc_scaled')
            active.add('log_soc_dem_scaled')

        # 4. Education
        if config.nb_enfants > 0:
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
            if config.classe_enfants:
                for opt in config.classe_enfants:
                    if opt in edu_map: active.add(edu_map[opt])

        # 5. Sante
        if config.besoin_sante != 'Aucun':
             sante_map = {
                 'Hôpital': 'sante_hopital_scaled',
                 'Maternité': 'sante_maternite_scaled',
                 'Soutien Psychologique & Addictologie': 'sante_psy_scaled',
                 'Psychiatrie': 'sante_psy_scaled'
             }
             if config.besoin_sante in sante_map:
                 active.add(sante_map[config.besoin_sante])
             active.add('sante_structures_scaled')

        # 6. Inclusion
        active.add('inc_pol_scaled')
        active.add('inc_population_scaled')
        active.add('inc_asso_core_scaled')
        # F-26: Refugee Associations
        active.add('inc_asso_refug_scaled') 
        active.add('inc_siae_density_scaled') # New F-39: SIAE Density
        if config.inc_services_add_selection: active.add('inc_services_incl_scaled')
        if config.inc_asso_add_selection: active.add('inc_asso_add_scaled')
        
        return active

    
    def format_city_details(self, row: pd.Series, config: Optional[SearchCriterias] = None) -> Dict[str, Any]:
        """
        Formats detailed information for a city to be displayed in the UI.
        """
        codgeo = str(row['codgeo']) if 'codgeo' in row else str(row.name)
        details = {
            "identity": {
                "codgeo": codgeo,
                "nom": row.get('libgeo', 'Inconnu'),
                "population": int(round(row.get('population', 0) / 1000) * 1000),
                "bassin_de_vie": row.get('libelle_bassin_de_vie', 'N/A'),
                "score_global": float(row.get('weighted_score', 0.0)) if 'weighted_score' in row else None
            },
            "name": row.get('libgeo', 'N/A'),
            "codgeo": codgeo,
            "population": int(round(row.get('population', 0) / 1000) * 1000),
            "bassin_de_vie": row.get('libelle_bassin_de_vie', 'N/A'),
            "scores": {},
            "emploi": {
                "live_total": 0, "matching_total": 0, "live_jobs_summary": {}, "matching_jobs_summary": {},
                "top_metiers": [], "formations": []
            },
            "education": {"counts": {}, "etablissements": {}},
            "sante": {"counts": {}, "etablissements": {}},
            "inclusion": {"services_grouped": {}},
            "associations": {},
            "mobilité": {
                "nb_stops_bus": int(row.get('nb_stops_bus', 0)),
                "nb_stops_tram": int(row.get('nb_stops_tram', 0)),
                "nb_stops_metro": int(row.get('nb_stops_metro', 0)),
                "nb_stops_train": int(row.get('nb_stops_train', 0)),
                "nb_stops_total": int(row.get('nb_stops_total', 0)),
                "mob_trans_pub_stop_density": float(row.get('mob_trans_pub_stop_density', 0.0))
            },
            "logement": {
                "selected_type": (config.type_logement.code if hasattr(config.type_logement, 'code') else config.type_logement) if config else 'appt_all',
                "raw_euro_m2": None, "odace_all_variants": {},
                "jaccueille_count": float(row.get('heb_accueillants_count', 0.0))
            }
        }

        # F-26: Refugee Associations - If the score is active and present, update jaccueille_count
        # This is a specific override/addition based on the presence of the refugee association score.
        if 'inc_asso_refug_scaled' in row and pd.notna(row['inc_asso_refug_scaled']):
            # The instruction implies that jaccueille_count might be related to this.
            # Assuming the original line for jaccueille_count is kept, this might be an additional check
            # or a way to ensure it's only shown if the refugee association score is relevant.
            # For now, we'll keep the original jaccueille_count and add this check if it's meant to influence it.
            # If the intent was to *replace* the default jaccueille_count, the instruction was ambiguous.
            # Sticking to the most faithful interpretation of "rename" and "add this block".
            pass # The original jaccueille_count is already set above. No change needed here based on the instruction.

        # Use cached active criteria if available
        active_ids = config.active_criteria if config and config.active_criteria is not None else self._get_active_criteria(config)

        # 1. Normalize and aggregate category weights
        cat_weights = {
            'emploi': config.poids_emploi if config else 100,
            'logement': config.poids_logement if config else 100,
            'education': config.poids_education if config else 100,
            'inclusion': config.poids_inclusion if config else 100,
            'mobilité': config.poids_mobilité if config else 100,
            'sante': config.poids_sante if config else 100
        }
        
        # Skip categories based on config
        if config:
            if config.nb_enfants == 0: cat_weights['education'] = 0
            if config.besoin_sante == 'Aucun': 
                cat_weights['sante'] = 0
        
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
            norm_cat = 'sante' if cat == 'santé' else cat
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

        # 4. Populate details with correctly weighted items
        for item in displayed_items:
            score_row = item['score_row']
            cat = score_row['cat']
            norm_cat = item['norm_cat']
            val_scaled = item['val_scaled']
            w_crit = item['w_crit']
            score_id = score_row['score']

            if cat not in details['scores']: details['scores'][cat] = []
            
            # Improved Valeur KPI (Return Float as requested)
            val_raw = None
            raw_metric_col = score_row['metric']
            if raw_metric_col and raw_metric_col in row and pd.notna(row[raw_metric_col]):
                val = row[raw_metric_col]
                d_factor = float(score_row.get('display_factor', 1.0))
                if pd.api.types.is_number(val):
                    val_raw = float(val * d_factor)
                else:
                    try:
                         val_raw = float(val) * d_factor
                    except:
                         val_raw = val
            
            # Format val_raw for display
            unit = score_row.get('unit', score_row.get('description', ''))
            if isinstance(val_raw, (int, float)):
                if unit == "habitants":
                    # Round to nearest 1000 and ensure it's an int
                    val_raw = int(round(float(val_raw) / 1000) * 1000)
                elif unit == "%" or unit == "assos/1000 hab.":
                    val_raw = round(float(val_raw), 1)
                elif float(val_raw).is_integer():
                    val_raw = int(val_raw)
                else:
                    val_raw = round(float(val_raw), 1)
            
            # Impact = (w_crit / sum_weights_in_cat) * (cat_weight / total_cat_weight)
            rel_weight = (w_crit / cat_internal_weights[norm_cat]) * (cat_weights[norm_cat] / total_cat_weight)

            details['scores'][cat].append({
                "label": score_row.get('label', score_id),
                "score_id": score_id,
                "valeur_kpi": val_raw,
                "score_normalise": val_scaled,
                "unit": score_row.get('unit', score_row.get('description', '')),
                "relative_weight": round(rel_weight * 100, 1) # In %
            })

        # 2. Housing Details (ODACE Specifics)
        housing_types = ['appt_all', 'appt_t1_t2', 'appt_t3_p', 'house_all']
        for ht in housing_types:
            raw_col = f"loyer_m2_moy_{ht}"
            scaled_col = f"log_loyer_moyen_{ht}_scaled"
            
            variant_data = {
                "raw": float(row[raw_col]) if raw_col in row and pd.notna(row[raw_col]) else None,
                "scaled": float(row[scaled_col]) if scaled_col in row and pd.notna(row[scaled_col]) else None
            }
            details['logement']['odace_all_variants'][ht] = variant_data
            
            # Set top-level raw value if it's the selected type
            type_log = None
            if config and config.type_logement:
                type_log = config.type_logement.code if hasattr(config.type_logement, 'code') else config.type_logement
            
            if config and type_log == ht:
                details['logement']['raw_euro_m2'] = variant_data['raw']
            elif not config and ht == 'appt_all':
                details['logement']['raw_euro_m2'] = variant_data['raw']

        # 3. Emploi (Top 10 from Live Jobs & Formations)
        c_code = codgeo.code if hasattr(codgeo, "code") else codgeo
        if c_code:
            # --- Live Jobs Match (ROME) ---
            if not self.live_jobs_data.empty:
                live_city = self.live_jobs_data[self.live_jobs_data['commune'] == c_code].copy()
                if not live_city.empty:
                    # Global Summary
                    live_summary = live_city.groupby('romeLibelle')['total_postes'].sum().to_dict()
                    details['emploi']['live_jobs_summary'] = live_summary
                    details['emploi']['live_total'] = int(live_city['total_postes'].sum())
                    
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
                            details['emploi']['matching_jobs_summary'] = matching_city.groupby('romeLibelle')['total_postes'].sum().to_dict()
                            details['emploi']['matching_total'] = int(matching_city['total_postes'].sum())

                    # Top 10 unique labels by volume with postes count
                    top_live = live_city.groupby('romeLibelle')['total_postes'].sum().sort_values(ascending=False).head(10)
                    details['emploi']['top_metiers'] = [f"{label} ({int(vol)} postes)" for label, vol in top_live.items()]
                else:
                    details['emploi']['live_total'] = 0
                    details['emploi']['matching_total'] = 0
                    details['emploi']['top_metiers'] = []

            # --- SIAE Jobs Match (New F-39) ---
            if not self.siae_jobs_data.empty:
                siae_city = self.siae_jobs_data[self.siae_jobs_data['codgeo'] == codgeo].copy()
                if not siae_city.empty:
                    # Map rome to label using rome_index if rome_label is missing
                    if 'rome_label' not in siae_city.columns and not self.rome_index.empty:
                        siae_city['rome_label'] = siae_city['rome'].map(self.rome_index['label']).fillna(siae_city['rome'])
                    
                    # Fallback for display if no label at all
                    label_col = 'rome_label' if 'rome_label' in siae_city.columns else 'rome'
                    
                    details['emploi']['siae'] = {
                        "total": int(len(siae_city)),
                        "summary": siae_city.groupby(label_col).size().to_dict(),
                        "matching_summary": {}
                    }
                    
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
                            details['emploi']['siae']['matching_summary'] = siae_matching.groupby(label_col).size().to_dict()
                else:
                    details['emploi']['siae'] = {"total": 0, "summary": {}, "matching_summary": {}}
            
            # Formations logic remains
            if not self.formations_data.empty:
                 city_forms = self.formations_data[self.formations_data['codgeo'] == c_code].copy()
                 if not city_forms.empty:
                     if self.codformations_index is not None and not self.codformations_index.empty:
                         # Robust type conversion for merge keys
                         city_forms['formation_code'] = city_forms['formation_code'].astype(str)
                         merged_f = city_forms.merge(self.codformations_index, left_on='formation_code', right_index=True, how='left')
                         merged_f['label'] = merged_f['label'].fillna(merged_f['formation_code'])
                         details['emploi']['formations'] = sorted(merged_f['label'].unique().tolist())
                     else:
                         details['emploi']['formations'] = sorted(city_forms['formation_code'].unique().tolist())

        # 4. Education & Sante Counts & Grouped Etablissements
        for dom, mapping, annuaire in [
            ('education', {'maternelle': 'edu_maternelle_ct', 'elementaire': 'edu_elementaire_ct', 'college': 'edu_college_ct', 'lycee': 'edu_lycee_ct'}, self.annuaire_ecoles), 
            ('sante', {'hopital': 'count_hopital', 'maternite': 'count_maternite', 'psy': 'count_psy'}, self.annuaire_sante)
        ]:
            for key, col in mapping.items():
                if col in row: details[dom]['counts'][key] = int(row[col])
            
            if codgeo and not annuaire.empty:
                # Extra safety: filter by codgeo and category to avoid leaks
                city_pois = annuaire[(annuaire['codgeo'] == codgeo) & (annuaire['category'] == dom)]
                if not city_pois.empty:
                    # Group by 'type' or fallback to 'categorie'
                    type_col = 'type' if 'type' in city_pois.columns else ('categorie' if 'categorie' in city_pois.columns else None)
                    # Safely find a label column
                    label_col = 'label' if 'label' in city_pois.columns else ('name' if 'name' in city_pois.columns else None)
                    
                    if type_col and label_col:
                        grouped = city_pois.groupby(type_col, observed=True)[label_col].apply(lambda x: sorted(list(set(x)))).to_dict()
                        details[dom]['etablissements'] = grouped

        # 6. Inclusion (Grouped by Thematic)
        if codgeo and not self.annuaire_inclusion.empty:
            city_incl = self.annuaire_inclusion[self.annuaire_inclusion['codgeo'] == codgeo]
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
                        
                        details['inclusion']['services_grouped'] = grouped_incl

        # 6b. Refugee Associations (Detailed List for Inclusion Tab)
        if codgeo and not self.refugee_associations_data.empty:
            # Filter by codgeo or bassin_de_vie
            # Note: refugee_associations_data has 'codgeo' and 'bassin_de_vie' (code)
            mask = (self.refugee_associations_data['codgeo'] == codgeo)
            if 'bassin_de_vie' in row and row['bassin_de_vie']:
                mask |= (self.refugee_associations_data['bassin_de_vie'] == row['bassin_de_vie'])
            
            refug_city = self.refugee_associations_data[mask].copy()
            if not refug_city.empty:
                # Group by waldec_code and map to labels
                refugee_list = []
                for _, asso in refug_city.iterrows():
                    raw_code = str(asso['waldec_code']).strip()
                    # Normalize: strip leading zero if present for index lookup
                    code_norm = raw_code.lstrip('0') if raw_code.startswith('0') else raw_code
                    label = raw_code
                    
                    try:
                        if self.waldec_index is not None:
                            # Try exact match (original and normalized)
                            possible_codes = [raw_code, code_norm]
                            # Add prefixes (first 3 and 2 digits, normalized)
                            if len(raw_code) >= 3:
                                possible_codes.append(raw_code[:3])
                                possible_codes.append(raw_code[:3].lstrip('0'))
                            if len(raw_code) >= 2:
                                possible_codes.append(raw_code[:2])
                                possible_codes.append(raw_code[:2].lstrip('0'))
                                
                            for pc in possible_codes:
                                if pc and pc in self.waldec_index.index:
                                    val = self.waldec_index.loc[pc, 'label']
                                    label = val if isinstance(val, str) else val.iloc[0]
                                    break
                    except:
                        pass
                    
                    asso_dict = asso.to_dict()
                    # Format label: Capital on first letter, then lower
                    asso_dict['waldec_label'] = str(label).capitalize()
                    refugee_list.append(asso_dict)
                
                details['inclusion']['refugee_associations'] = refugee_list

        # 7. Associations (Updated to use RNA RAG columns)
        rna_cols = [c for c in row.index if c.startswith("inc_rna_") and c.endswith("_count")]
        if rna_cols:
            total_assos = row[rna_cols].sum()
            details['associations']['total'] = int(total_assos)
            # Grouped summary for the JSON blob
            details['associations']['summary_by_category'] = {
                c.replace("inc_rna_", "").replace("_count", ""): int(row[c]) 
                for c in rna_cols if row[c] > 0
            }
        elif codgeo and not self.associations_data.empty:
            # Fallback to legacy aggregated file if present
            asso_city = self.associations_data[self.associations_data['codgeo'] == codgeo]
            if not asso_city.empty:
                total_assos = asso_city['count'].sum() if 'count' in asso_city.columns else len(asso_city)
                details['associations']['total'] = int(total_assos)
        
        # 7b. Refugee Associations (Counts)
        if codgeo and not self.associations_data.empty and 'id_waldec' in self.associations_data.columns:
            asso_city = self.associations_data[self.associations_data['codgeo'] == codgeo]
            refugee_assos = asso_city[asso_city['id_waldec'].astype(str).str.startswith('019025', na=False)]
            details['associations']['refugee_count'] = int(refugee_assos['count'].sum()) if 'count' in refugee_assos.columns else len(refugee_assos)
        elif 'inc_asso_refug_scaled' in row:
             # If we have a scaled score, we can assume some presence, but maybe not the count
             pass

        logger.debug(f"⚙️ [ENGINE] city_details {details}")

        return details

    def get_city_details(self, codgeo: str) -> Dict[str, Any]:
        """Retrieves detailed information using static data."""
        if codgeo not in self.df_all_communes.index:
            return {"error": f"City code {codgeo} not found."}

        return self.format_city_details(self.df_all_communes.loc[codgeo])

    def run(self, config: SearchCriterias, log_prefix: Optional[str] = None) -> gpd.GeoDataFrame:
        """Orchestrates the full scoring pipeline."""
        logger.debug(f"⚙️ [ENGINE] Starting run with Profile: {config.weight_profile}")
        logger.debug(f"⚙️ [ENGINE] Config: {config}")
        if not config.active_criteria:
            config.active_criteria = self._get_active_criteria(config)
        
        c_code = config.commune_actuelle.code if hasattr(config.commune_actuelle, 'code') else config.commune_actuelle
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
        
        results = self._compute_scores(communes_to_score, config)

        if log_prefix:
            log_search_results(config, results, results, self.scores_cat, prefix=log_prefix)

        return results

    def _compute_scores(self, df_search: gpd.GeoDataFrame, config: SearchCriterias) -> pd.DataFrame:
        if df_search.empty: return df_search.copy()

        # Distance
        # Distance
        odis_search = df_search.copy()
        if 'dist_current_loc' not in odis_search.columns:
            odis_search = self._compute_distance_score(odis_search, config)

        # Merge BdV Data
        if self.bv_data is not None and not self.bv_data.empty and 'bassin_de_vie' in odis_search.columns:
             # Ensure type consistency for merge
             odis_search['bassin_de_vie'] = odis_search['bassin_de_vie'].astype(str)
             bv_data_scoped = self.bv_data.copy()
             bv_data_scoped.index = bv_data_scoped.index.astype(str)
             
             odis_search = pd.merge(
                 odis_search, 
                 bv_data_scoped.add_suffix('_bdv'), 
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

        # Exclusion
        c_code = config.commune_actuelle.code if hasattr(config.commune_actuelle, 'code') else config.commune_actuelle
        if c_code in odis_exploded.index:
            odis_exploded = odis_exploded.drop(c_code)
        
        if c_code in cfg.PLM_MAPPING:
            prefix = cfg.PLM_MAPPING[c_code]
            odis_exploded = odis_exploded[~odis_exploded.index.astype(str).str.startswith(prefix)]

        return odis_exploded.sort_values(by='weighted_score', ascending=False)




    def _compute_employment_scores(self, df: gpd.GeoDataFrame, config: SearchCriterias) -> gpd.GeoDataFrame:
        df = df.copy()
        
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

    def _compute_sante_scores(self, df: gpd.GeoDataFrame, config: SearchCriterias) -> gpd.GeoDataFrame:
        df = df.copy()
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

    def _compute_mobility_scores(self, df: gpd.GeoDataFrame, config: SearchCriterias) -> gpd.GeoDataFrame:
        df = df.copy()
        
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
        if config.commune_actuelle:
             c_code_raw = config.commune_actuelle
             c_code = c_code_raw.code if hasattr(c_code_raw, 'code') else c_code_raw
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

    def _compute_education_scores(self, df: gpd.GeoDataFrame, config: SearchCriterias) -> gpd.GeoDataFrame:
        # Placeholder for specific education logic if needed in future.
        # Currently education scores are pre-computed in DB/DF.
        return df

    def _compute_housing_scores(self, df: gpd.GeoDataFrame, config: SearchCriterias) -> gpd.GeoDataFrame:
        # Placeholder for specific housing logic if needed.
        # Currently housing pruning handles most variation.
        return df

    def _compute_inclusion_scores(self, df: gpd.GeoDataFrame, config: SearchCriterias) -> gpd.GeoDataFrame:
        df = df.copy()
        if 'inc_asso_core_scaled' not in df.columns: df['inc_asso_core_scaled'] = 0.0

        # Affinities
        if config.inc_asso_add_selection:
            interest_codes = set()
            for i in config.inc_asso_add_selection:
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
                df['affinite_density'] = (affinite_counts * 1000) / df['population']
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

        # Inclusion Services (F-48: Merged Core and Additional)
        needed = set()
        for i in config.inc_services_add_selection:
            needed.add(i.code if hasattr(i, 'code') else str(i))
            
        if needed:
             # Optimize lookup
             def count_matches(available):
                 if not isinstance(available, set): return 0
                 return sum(1 for n in needed if any(n in a for a in available))

             if 'key' not in df.columns: df = df.join(self.incl_index, how='left')
             df['inc_services_incl_scaled'] = df['key'].apply(count_matches) / len(needed)
        else:
             if 'inc_services_incl_scaled' in df.columns: df.drop(columns=['inc_services_incl_scaled'], inplace=True)

        return df

    def _compute_criteria_scores(self, df: gpd.GeoDataFrame, config: SearchCriterias) -> gpd.GeoDataFrame:
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
        if not config.commune_actuelle:
            return False
            
        c_code_raw = config.commune_actuelle
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
