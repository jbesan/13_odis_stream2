
# coding: utf-8
"""
Scoring module for the ODIS application.
"""
from typing import List, Dict, Set, Any, Optional, Union, Tuple
import geopandas as gpd
import numpy as np
import pandas as pd
from core.models import ScoringConfig
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
    def _min_max_scale(series: pd.Series, min_val: float, max_val: float) -> pd.Series:
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

    def _compute_distance_score(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
        current_codgeo = config.commune_actuelle
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
             min_b, max_b = self._get_bounds('dist_current_loc_scaled')
             if pd.isna(max_b): max_b = 50000.0 # Default 50km
             # Inverse scale: closer is better
             # We use standard min_max then invert? Or just 1 - dist/max
             # Let's stick to standard pattern if bounds are set for "dist_current_loc_scaled"
             # Usually distance is "lower is better".
             # If min=0, max=50000. 
             # Val = (d - min)/(max - min). 0 -> 0. 50000 -> 1.
             # We want 0 -> 1 (Good), 50000 -> 0 (Bad).
             # So 1 - min_max_scale
             scaled = self._min_max_scale(df['dist_current_loc'], min_b, max_b)
             df['dist_current_loc_scaled'] = 1.0 - scaled
        
        return df

    def _compute_category_scores(self, df: pd.DataFrame, config: ScoringConfig) -> pd.DataFrame:
        df = df.copy()
        
        # Use cached active criteria if available
        active = config.active_criteria if config.active_criteria is not None else self._get_active_criteria(config)

        # Compute for all categories
        categories = ['emploi', 'logement', 'education', 'inclusion', 'mobilité', 'sante']
        for category in categories:
            # Skip if category totally irrelevant (logic can be improved but sticking to previous pattern)
            if category == 'education' and config.nb_enfants == 0: continue
            if category == 'sante' and config.besoin_sante == 'Aucun': continue

            # Find columns for this category that are active AND present
            cat_scores = self.scores_cat[self.scores_cat.cat == category]
            score_cols = [s for s in cat_scores['score'] if s in df.columns and s in active]
            
            if not score_cols: continue
            
            scores_val = []
            weights_val = []
            
            for col in score_cols:
                 val = df[col]
                 weight = 1.0 # Default
                 
                 # Priority: Config weight -> Catalog weight
                 if col in config.criteria_weights: weight *= config.criteria_weights[col]
                 else:
                      row = self.scores_cat[self.scores_cat['score'] == col]
                      if not row.empty: weight *= float(row.iloc[0]['weight'])

                 # Track valid weights per row
                 valid_weight = weight * val.notna().astype(float)
                 scores_val.append(val.fillna(0) * weight)
                 weights_val.append(valid_weight)
            
            if weights_val:
                 denom = sum(weights_val)
                 # Avoid division by zero
                 df[f"{category}_cat_score"] = np.where(denom > 0, sum(scores_val) / denom, 0.0)

        return df

    def _compute_weighted_score(self, df: pd.DataFrame, config: ScoringConfig) -> pd.Series:
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
        odis_asso_mini_data: pd.DataFrame = pd.DataFrame(),
        live_jobs_data: pd.DataFrame = pd.DataFrame(),
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
        self.odis_asso_mini_data = odis_asso_mini_data
        self.live_jobs_data = live_jobs_data
        self.bmo_vertical = bmo_vertical

    def _get_active_criteria(self, config: ScoringConfig) -> Set[str]:
        """Centralized logic to determine which criteria are active based on config."""
        active = set()
        
        # 1. Categories that are always active (even if partial)
        active.add('workclass_decline_scaled')
        active.add('mob_gare_scaled')
        active.add('mob_trans_pub_density_scaled')
        active.add('mob_epci_scaled')
        active.add('dist_current_loc_scaled')
        active.add('mob_dist_scaled') # Alias used in some tests/configs
        
        # 2. Employment & Formations (Only if something was searched)
        if any(config.codes_metiers):
            for i in range(config.nb_adultes):
                active.add(f'met_match_adult{i+1}_scaled')
                active.add(f'met_match_adult{i+1}_bdv_scaled')
                active.add(f'met_match_adult{i+1}_tension_scaled')
        
        if any(config.codes_formations):
            for i in range(config.nb_adultes):
                active.add(f'form_match_adult{i+1}_scaled')
                active.add(f'form_match_adult{i+1}_bdv_scaled')

        # 3. Logement
        if config.hebergement == 'Location' or config.logement == 'Location':
            active.add('log_vac_scaled')
            # Handle both formats: log_loyer_moyen_appt_all_scaled and log_loyer_moyen_scaled_appartement_toutes
            active.add(f'log_loyer_moyen_{config.type_logement}_scaled')
            if config.type_logement == 'appartement_toutes':
                active.add('log_loyer_moyen_scaled_appartement_toutes')
            elif config.type_logement == 'appt_all':
                active.add('log_loyer_moyen_appt_all_scaled')

        if config.logement == 'Logement Social':
            active.add('log_soc_inoc_scaled')
        
        if config.hebergement == "Chez l'habitant":
            active.add('log_occup_scaled')

        # 4. Education
        if config.nb_enfants > 0:
            active.add('youth_decline_scaled')
            active.add('edu_classes_ferm_scaled')
            active.add('edu_structures_scaled') # Mock indicator
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
        active.add('inc_services_core_scaled')
        active.add('inc_asso_core_scaled')
        active.add('inc_asso_refug_scaled') 
        if config.inc_services_add_selection: active.add('inc_services_add_scaled')
        if config.inc_asso_add_selection: active.add('inc_asso_add_scaled')
        
        return active

    
    def format_city_details(self, row: pd.Series, config: Optional[ScoringConfig] = None) -> Dict[str, Any]:
        """
        Formats detailed information for a city to be displayed in the UI.
        """
        codgeo = str(row['codgeo']) if 'codgeo' in row else str(row.name)
        details = {
            "identity": {
                "codgeo": codgeo,
                "nom": row.get('libgeo', 'Inconnu'),
                "population": row.get('population', 0),
                "bassin_de_vie": row.get('libelle_bassin_de_vie', 'N/A'),
                "score_global": float(row.get('weighted_score', 0.0)) if 'weighted_score' in row else None
            },
            "name": row.get('libgeo', 'N/A'),
            "codgeo": codgeo,
            "population": row.get('population', 0),
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
                "selected_type": config.type_logement if config else 'appt_all',
                "raw_euro_m2": None, "odace_all_variants": {}
            }
        }

        active_ids = self._get_active_criteria(config) if config else set()
        
        # Calculate weights for relative_weight
        # We need to replicate compute_weighted_score logic to find the global impact
        cat_weights = {
            'emploi': config.poids_emploi if config else 100,
            'logement': config.poids_logement if config else 100,
            'education': config.poids_education if config else 100,
            'inclusion': config.poids_inclusion if config else 100,
            'mobilité': config.poids_mobilité if config else 100,
            'sante': config.poids_sante if config else 100,
            'santé': config.poids_sante if config else 100 # support for mock
        }
        
        # Skip categories based on config
        if config:
            if config.nb_enfants == 0: cat_weights['education'] = 0
            if config.besoin_sante == 'Aucun': 
                cat_weights['sante'] = 0
                cat_weights['santé'] = 0
            
        total_cat_weight = sum({v for k,v in cat_weights.items() if k != 'santé'}) # Avoid double counting santé/sante
        if config and config.besoin_sante == 'Aucun': total_cat_weight = sum([v for k,v in cat_weights.items() if k not in ['education', 'sante', 'santé']])
        total_cat_weight = 0.0
        for c, w in cat_weights.items():
            if c == 'santé': continue # Skip alias
            # Engine re-weighting logic: only count if category score exists in row
            if f"{c}_cat_score" in row:
                total_cat_weight += w
            elif c == 'sante' and 'santé_cat_score' in row:
                total_cat_weight += w
        
        if total_cat_weight == 0: total_cat_weight = 1.0

        # Pre-compute category internal weight sums
        cat_internal_weights = {}
        for cat in cat_weights:
            # Normalize cat for matching weights
            norm_cat = 'sante' if cat == 'santé' else cat
            c_scores = self.scores_cat[self.scores_cat.cat == cat]
            # Only count active scores
            active_c_scores = c_scores[c_scores.score.isin(active_ids)]
            
            # Sum criteria weights
            weight_sum = 0.0
            for _, s_row in active_c_scores.iterrows():
                sid = s_row['score']
                # Check if it was actually in the row (might have been missing in data even if active)
                if sid in row:
                    w = float(s_row['weight'])
                    if config and sid in config.criteria_weights: w *= config.criteria_weights[sid]
                    weight_sum += w
            cat_internal_weights[cat] = weight_sum or 1.0

        # 1. Scores per Category
        for _, score_row in self.scores_cat.iterrows():
            cat = score_row['cat']
            score_id = score_row['score']
            
            # Central pruning: skip if not active
            if config and score_id not in active_ids: continue
            
            val_scaled = float(row[score_id]) if score_id in row and pd.notna(row[score_id]) else None
            if val_scaled is None and config: continue # Hide if inactive/uncomputed
            
            if cat not in details['scores']: details['scores'][cat] = []
            
            # Improved Valeur KPI
            val_raw = "N/A"
            raw_metric_col = score_row['metric']
            if raw_metric_col and raw_metric_col in row and pd.notna(row[raw_metric_col]):
                val = row[raw_metric_col]
                d_factor = float(score_row.get('display_factor', 1.0))
                if pd.api.types.is_number(val):
                    val_conv = val * d_factor
                    if val_conv.is_integer(): val_raw = str(int(val_conv))
                    else: val_raw = f"{val_conv:.1f}" if d_factor > 1 else f"{val_conv:.2f}"
                else:
                    val_raw = str(val)
            
            # Relative Weight Calculation
            w_crit = float(score_row['weight'])
            if config and score_id in config.criteria_weights: w_crit *= config.criteria_weights[score_id]
            
            # Impact = (w_crit / sum_weights_in_cat) * (cat_weight / total_cat_weight)
            rel_weight = (w_crit / cat_internal_weights[cat]) * (cat_weights[cat] / total_cat_weight)

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
            if config and config.type_logement == ht:
                details['logement']['raw_euro_m2'] = variant_data['raw']
            elif not config and ht == 'appt_all':
                details['logement']['raw_euro_m2'] = variant_data['raw']

        # 3. Emploi (Top 10 from Live Jobs & Formations)
        if codgeo:
            # --- Live Jobs Match (ROME) ---
            if not self.live_jobs_data.empty:
                live_city = self.live_jobs_data[self.live_jobs_data['commune'] == codgeo].copy()
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
                                    if len(c) == 5: target_romes.add(c)
                            elif isinstance(codes, str) and len(codes) == 5:
                                target_romes.add(codes)
                        
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

            
            # Formations logic remains
            if not self.formations_data.empty:
                 city_forms = self.formations_data[self.formations_data['codgeo'] == codgeo].copy()
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

        # 7. Associations
        if codgeo and not self.associations_data.empty:
            asso_city = self.associations_data[self.associations_data['codgeo'] == codgeo]
            if not asso_city.empty:
                total_assos = asso_city['count'].sum() if 'count' in asso_city.columns else len(asso_city)
                details['associations']['total'] = int(total_assos)
                
                # Refugees
                cols = asso_city.columns
                waldec_col = 'id_waldec' if 'id_waldec' in cols else ('objet_social1' if 'objet_social1' in cols else None)
                if waldec_col:
                    refugee_assos = asso_city[asso_city[waldec_col].astype(str).str.startswith('019025', na=False)]
                    details['associations']['refugee_count'] = int(refugee_assos['count'].sum()) if 'count' in refugee_assos.columns else len(refugee_assos)

            # 7b. ODIS Mini Associations
            if self.odis_asso_mini_data is not None and not self.odis_asso_mini_data.empty:
                odis_assos = self.odis_asso_mini_data[self.odis_asso_mini_data['codgeo'] == codgeo].copy()
                if not odis_assos.empty:
                    # Provide counts and grouped data
                    details['associations']['odis_mini_count'] = len(odis_assos)
                    
                    # Group by WALDEC label
                    grouped_odis = {}
                    for _, asso in odis_assos.iterrows():
                        raw_code = str(asso['waldec_code']).strip()
                        code_norm = raw_code.lstrip('0') if raw_code.startswith('0') else raw_code
                        label = "Autres associations"
                        
                        try:
                            if self.waldec_index is not None:
                                # Logic similar to refugee associations for label lookup
                                possible_codes = [raw_code, code_norm]
                                if len(raw_code) >= 3:
                                    possible_codes.append(raw_code[:3])
                                    possible_codes.append(raw_code[:3].lstrip('0'))
                                
                                for pc in possible_codes:
                                    if pc and pc in self.waldec_index.index:
                                        val = self.waldec_index.loc[pc, 'label']
                                        label = val if isinstance(val, str) else val.iloc[0]
                                        break
                        except:
                            pass
                        
                        # Format label: Capital on first letter, then lower
                        label = str(label).capitalize()
                        
                        if label not in grouped_odis:
                            grouped_odis[label] = []
                        
                        # Format name: Capital on first letter, then lower
                        name = str(asso['name']).capitalize()
                        
                        grouped_odis[label].append({
                            'id': asso['id'],
                            'name': name,
                            'description': asso['description']
                        })
                    
                    # Sort names within groups
                    for label in grouped_odis:
                        grouped_odis[label] = sorted(grouped_odis[label], key=lambda x: x['name'])
                             
                    details['inclusion']['odis_associations_grouped'] = grouped_odis
                    # Keep a small extract for compatibility if needed, but the user wants the grouped version
                    details['associations']['odis_mini'] = odis_assos.head(5).to_dict(orient='records')

        logger.info(f"⚙️ [ENGINE] city_details {details}")

        return details

    def get_city_details(self, codgeo: str) -> Dict[str, Any]:
        """Retrieves detailed information using static data."""
        if codgeo not in self.df_all_communes.index:
            return {"error": f"City code {codgeo} not found."}

        return self.format_city_details(self.df_all_communes.loc[codgeo])

    def run(self, config: ScoringConfig, log_prefix: Optional[str] = None) -> gpd.GeoDataFrame:
        """Orchestrates the full scoring pipeline."""
        logger.debug(f"⚙️ [ENGINE] Starting run with Profile: {config.weight_profile}")
        logger.debug(f"⚙️ [ENGINE] Config: {config}")
        if not config.active_criteria:
            config.active_criteria = self._get_active_criteria(config)
        
        start_commune = self.df_all_communes.loc[[config.commune_actuelle]]
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

    def _compute_scores(self, df_search: gpd.GeoDataFrame, config: ScoringConfig) -> pd.DataFrame:
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
        if config.commune_actuelle in odis_exploded.index:
            odis_exploded = odis_exploded.drop(config.commune_actuelle)
        
        if config.commune_actuelle in PLM_MAPPING:
            prefix = PLM_MAPPING[config.commune_actuelle]
            odis_exploded = odis_exploded[~odis_exploded.index.astype(str).str.startswith(prefix)]

        return odis_exploded.sort_values(by='weighted_score', ascending=False)



    def _prune_irrelevant_metrics(self, df: pd.DataFrame, config: ScoringConfig) -> pd.DataFrame:
        """Prunes columns that are not relevant based on active criteria."""
        active = config.active_criteria if config.active_criteria is not None else self._get_active_criteria(config)
        
        # Identify all scaled columns present
        scaled_cols = [c for c in df.columns if c.endswith('_scaled') or c.endswith('_scaled_binome')]
        
        # Identify columns to drop
        # Logic: Drop if base score (without _binome) is NOT in active set
        cols_to_drop = [c for c in scaled_cols if c.replace('_binome', '') not in active]
        
        # Also drop corresponding raw metrics if they are in scores_cat
        score_to_metric = dict(zip(self.scores_cat['score'], self.scores_cat['metric']))
        extra_drops = []
        for sid in cols_to_drop:
            base_sid = sid.replace('_binome', '')
            if base_sid in score_to_metric:
                metric = score_to_metric[base_sid]
                if metric and metric in df.columns:
                    extra_drops.append(metric)
        
        # Add special cases for employment/formations raw columns (if entire block unused)
        if not any(config.codes_metiers):
            for i in range(config.nb_adultes):
                prefix = f'met_match_adult{i+1}'
                extra_drops.extend([prefix, f'{prefix}_bdv', f'{prefix}_tension'])

        if not any(config.codes_formations):
             for i in range(config.nb_adultes):
                prefix = f'form_match_adult{i+1}'
                extra_drops.extend([prefix, f'{prefix}_bdv', f'form_match_codes_{prefix}'])

        if cols_to_drop or extra_drops:
            # Only drop if columns exist
            all_drops = set(cols_to_drop + extra_drops)
            df.drop(columns=[c for c in all_drops if c in df.columns], inplace=True)
            
        return df

    def _compute_employment_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
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
                min_c, max_c = self._get_bounds(f'{col_raw}_scaled')
                if pd.isna(max_c): max_c = 10.0
                df[f'{col_raw}_scaled'] = self._min_max_scale(df[col_raw], min_c, max_c)
                
                min_b, max_b = self._get_bounds(f'{col_bdv_raw}_scaled')
                if pd.isna(max_b): max_b = 50.0
                df[f'{col_bdv_raw}_scaled'] = self._min_max_scale(df[col_bdv_raw], min_b, max_b)
                
                min_t, max_t = self._get_bounds(f'{col_tension_raw}_scaled')
                if pd.isna(max_t): max_t = 5.0
                df[f'{col_tension_raw}_scaled'] = self._min_max_scale(df[col_tension_raw], min_t, max_t)

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
                    min_b, max_b = self._get_bounds(f'{score_key}_scaled')
                    if pd.isna(max_b): max_b = float(len(prefs))
                    df[f'{score_key}_scaled'] = self._min_max_scale(df[score_key].fillna(0), min_b, max_b)

                    # Match Score BdV
                    if 'bassin_de_vie' in df.columns:
                        df[f'{score_key}_bdv'] = df['bassin_de_vie'].map(lambda b: len(form_map_bdv.get(b, set()).intersection(prefs)))
                        min_b, max_b = self._get_bounds(f'{score_key}_bdv_scaled')
                        if pd.isna(max_b): max_b = float(len(prefs))
                        df[f'{score_key}_bdv_scaled'] = self._min_max_scale(df[f'{score_key}_bdv'].fillna(0), min_b, max_b)

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

    def _compute_sante_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
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

    def _compute_mobility_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
        df = df.copy()
        
        # --- Density ---
        if 'nb_stops_total' in df.columns:
            df['mob_trans_pub_stop_density'] = (df['nb_stops_total'] / df['population'].replace(0, 1)) * 1000
            min_b, max_b = self._get_bounds('mob_trans_pub_density_scaled')
            if pd.isna(max_b): max_b = 10.0 
            df['mob_trans_pub_density_scaled'] = self._min_max_scale(df['mob_trans_pub_stop_density'], min_b, max_b)

        # --- EPCI Bonus ---
        current_epci = None
        current_reg = None
        current_dep = None
        
        # Resolve current location details
        if config.commune_actuelle:
             # Handle CriteriaItem properly if it is one, but config.commune_actuelle is usually code str here
             # Wait, model definition says CriteriaItem for SearchCriterias but ScoringConfig has pure strings?
             # Let's check ScoringConfig definition. It has 'commune_actuelle: str'. Good.
             c_code = config.commune_actuelle
             if c_code in self.df_all_communes.index:
                 cur_row = self.df_all_communes.loc[c_code]
                 current_epci = cur_row['epci_code']
                 current_reg = cur_row['reg_code']
                 current_dep = cur_row['dep_code']

        apply_epci_bonus = False
        if config.loc_search_area in ['departement', 'region']:
             if config.loc_search_code:
                  if config.loc_search_area == 'departement' and config.loc_search_code == current_dep:
                       apply_epci_bonus = True
                  elif config.loc_search_area == 'region' and config.loc_search_code == current_reg:
                       apply_epci_bonus = True
        
        if apply_epci_bonus and current_epci:
             df['mob_epci_scaled'] = np.where(df['epci_code'] == current_epci, 1.0, 0.0)
        else:
             df['mob_epci_scaled'] = 0.0
             
        return df

    def _compute_education_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
        # Placeholder for specific education logic if needed in future.
        # Currently education scores are pre-computed in DB/DF.
        return df

    def _compute_housing_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
        # Placeholder for specific housing logic if needed.
        # Currently housing pruning handles most variation.
        return df

    def _compute_inclusion_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
        df = df.copy()
        for col in ['inc_services_core_scaled', 'inc_asso_core_scaled']:
            if col not in df.columns: df[col] = 0.0

        # Affinities
        if config.inc_asso_add_selection:
            interest_codes = set()
            for i in config.inc_asso_add_selection:
                 # Logic to handle CriteriaItem or str
                 code = i.code if hasattr(i, 'code') else str(i)
                 if code in cfg.WALDEC_INC_ASSO_ADD_MAPPING: interest_codes.update(cfg.WALDEC_INC_ASSO_ADD_MAPPING[code])
                 elif len(code)>=3: interest_codes.add(code)
            
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
                df['inc_asso_add_scaled'] = self._min_max_scale(df['affinite_density'], min_b, max_b)
            else: 
                if 'inc_asso_add_scaled' in df.columns: df.drop(columns=['inc_asso_add_scaled'], inplace=True)
        else: 
            if 'inc_asso_add_scaled' in df.columns: df.drop(columns=['inc_asso_add_scaled'], inplace=True)

        # Specific Services
        needed = set()
        for i in config.inc_services_add_selection:
            needed.add(i.code if hasattr(i, 'code') else str(i))
            
        if needed:
             # Optimize lookup
             def count_matches(available):
                 if not isinstance(available, set): return 0
                 return sum(1 for n in needed if any(n in a for a in available))

             if 'key' not in df.columns: df = df.join(self.incl_index, how='left')
             df['inc_services_add_scaled'] = df['key'].apply(count_matches) / len(needed)
        else:
             if 'inc_services_add_scaled' in df.columns: df.drop(columns=['inc_services_add_scaled'], inplace=True)

        return df

    def _compute_criteria_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
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


