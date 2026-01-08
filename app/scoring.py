
# coding: utf-8
"""
Scoring module for the ODIS application.
"""
from typing import List, Dict, Set, Any, Optional, Union, Tuple
import geopandas as gpd
import numpy as np
import pandas as pd
from config import ScoringConfig
import config as cfg
import logging
# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Paris, Lyon, Marseille Global Codes -> Arrondissement Prefix
PLM_MAPPING = {
    '75056': '751',
    '69123': '693',
    '13055': '132'
}

class ScoringEngine:
    """
    The engine responsible for running the ODIS scoring algorithm.
    """
    def __init__(
        self,
        df_all_communes: gpd.GeoDataFrame,
        df_bv_geo: gpd.GeoDataFrame,
        df_area_geo: gpd.GeoDataFrame,
        scores_cat: pd.DataFrame,
        incl_index: pd.DataFrame,
        associations_data: pd.DataFrame,
        bmo_vertical: pd.DataFrame,
        formations_data: pd.DataFrame,
        codformations_index: pd.DataFrame,
        global_stats: Dict[str, Dict[str, float]],
        bv_data: gpd.GeoDataFrame = None,
        codfap_index: Optional[pd.DataFrame] = None,
        annuaire_ecoles: pd.DataFrame = pd.DataFrame(),
        annuaire_sante: pd.DataFrame = pd.DataFrame(),
        annuaire_inclusion: pd.DataFrame = pd.DataFrame(),
        inclusion_services_index: pd.DataFrame = pd.DataFrame()
    ):
        self.df_all_communes = df_all_communes
        self.df_bv_geo = df_bv_geo
        self.df_area_geo = df_area_geo
        self.scores_cat = scores_cat
        self.incl_index = incl_index
        self.associations_data = associations_data
        self.bmo_vertical = bmo_vertical
        self.formations_data = formations_data
        self.codformations_index = codformations_index
        self.global_stats = global_stats
        self.bv_data = bv_data if bv_data is not None else df_bv_geo
        self.codfap_index = codfap_index
        self.annuaire_ecoles = annuaire_ecoles
        self.annuaire_sante = annuaire_sante
        self.annuaire_inclusion = annuaire_inclusion
        self.inclusion_services_index = inclusion_services_index
    
    def format_city_details(self, row: pd.Series) -> Dict[str, Any]:
        """Formats a row into a detailed dictionary."""
        codgeo = row.name if isinstance(row.name, str) else row.get('codgeo')
        # Fallback if codgeo is not found
        if not codgeo and 'codgeo' in row: codgeo = row['codgeo']
        
        details = {
            "identity": {
                "codgeo": codgeo,
                "nom": row.get('libgeo', 'Unknown'),
                "population": int(row['population']) if 'population' in row else 0,
                "bassin_de_vie": row.get('libelle_bassin_de_vie', 'N/A'),
                "departement": str(row.get('dep_code', 'N/A')),
                "score_global": float(row.get('weighted_score', 0.0)) if 'weighted_score' in row else None
            },
            "scores": {},
            "emploi": {"top_metiers": [], "formations": []},
            "education": {"counts": {}, "etablissements": {}},
            "sante": {"counts": {}, "etablissements": {}},
            "inclusion": {"services_grouped": {}},
            "associations": {}
        }

        # 2. Detailed Scores (Raw & Scaled)
        if not self.scores_cat.empty:
            for _, score_row in self.scores_cat.iterrows():
                cat = score_row['cat']
                score_id = score_row['score']
                raw_metric_col = score_row['metric']
                
                if cat not in details['scores']: details['scores'][cat] = []
                
                val_scaled = float(row[score_id]) if score_id in row else None
                val_raw = "N/A"
                
                if raw_metric_col and raw_metric_col in row:
                    val = row[raw_metric_col]
                    if pd.api.types.is_number(val):
                        unit = score_row.get('description', '')
                        label = score_row.get('label', '')
                        if ('%' in unit or 'Taux' in label) and -1.5 <= val <= 1.5:
                             val_raw = f"{val * 100:.1f}"
                        else:
                             val_raw = str(int(val)) if float(val).is_integer() else f"{val:.2f}"
                    else:
                        val_raw = str(val)
                elif val_scaled is None:
                     continue # Hide if no data at all

                details['scores'][cat].append({
                    "label": score_row.get('label', score_id),
                    "score_id": score_id,
                    "valeur_kpi": val_raw,
                    "score_normalise": val_scaled,
                    "unit": score_row.get('description', '')
                })

        # 3. Emploi (Top 10 & Formations)
        if codgeo:
            if not self.bmo_vertical.empty:
                bmo_city = self.bmo_vertical[self.bmo_vertical['codgeo'] == codgeo].copy()
                if not bmo_city.empty and self.codfap_index is not None:
                    # Robust type conversion for merge keys
                    bmo_city['fap_code'] = bmo_city['fap_code'].astype(str)
                    merged = bmo_city.merge(self.codfap_index, left_on='fap_code', right_index=True, how='left')
                    merged['label'] = merged['label'].fillna(merged['fap_code'])
                    details['emploi']['top_metiers'] = sorted(merged['label'].unique().tolist())

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
                        grouped = city_pois.groupby(type_col)[label_col].apply(lambda x: sorted(list(set(x)))).to_dict()
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
                        grouped_incl_raw = city_incl.groupby('thematiques')[label_col].apply(list).to_dict()
                        
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

        return details

    def get_city_details(self, codgeo: str) -> Dict[str, Any]:
        """Retrieves detailed information using static data."""
        if codgeo not in self.df_all_communes.index:
            return {"error": f"City code {codgeo} not found."}
        return self.format_city_details(self.df_all_communes.loc[codgeo])

    def run(self, config: ScoringConfig) -> gpd.GeoDataFrame:
        """Orchestrates the full scoring pipeline."""
        start_commune = self.df_all_communes.loc[[config.commune_actuelle]]
        loc_type = config.loc_search_area
        
        # Filtering
        if config.loc_custom_code:
            loc_code = config.loc_custom_code
        else:
            loc_col = 'dep_code' if loc_type == 'departement' else 'reg_code'
            loc_code = start_commune.iloc[0][loc_col] if loc_type != 'france' else None

        communes_to_score = filter_communes(
            df=self.df_all_communes,
            start_commune=start_commune,
            loc_type=loc_type,
            loc_code=loc_code,
            loc_search_area=config.loc_search_area
        )
        
        return self._compute_scores(communes_to_score, config)

    def _compute_scores(self, df_search: gpd.GeoDataFrame, config: ScoringConfig) -> pd.DataFrame:
        if df_search.empty: return df_search.copy()

        # Distance
        odis_search = df_search.copy()
        if 'dist_current_loc' not in odis_search.columns:
            odis_search = add_distance_to_current_loc(odis_search, config.commune_actuelle, self.df_all_communes)

        # Merge BdV Data
        if self.bv_data is not None and not self.bv_data.empty and 'bassin_de_vie' in odis_search.columns:
             odis_search = pd.merge(
                 odis_search, 
                 self.bv_data.add_suffix('_bdv'), 
                 left_on='bassin_de_vie', 
                 right_index=True, 
                 how='left'
             )

        # Compute scores
        logger.info(f"⚙️ [ENGINE] Computing criteria scores...")
        odis_scored = self._compute_criteria_scores(odis_search, config)
        logger.info(f"⚙️ [ENGINE] Computing category scores...")
        odis_exploded = compute_category_scores(odis_scored, self.scores_cat, config)
        logger.info(f"⚙️ [ENGINE] Computing final weighted scores...")
        odis_exploded['weighted_score'] = compute_weighted_score(odis_exploded, config)

        # Exclusion
        if config.commune_actuelle in odis_exploded.index:
            odis_exploded = odis_exploded.drop(config.commune_actuelle)
        
        if config.commune_actuelle in PLM_MAPPING:
            prefix = PLM_MAPPING[config.commune_actuelle]
            odis_exploded = odis_exploded[~odis_exploded.index.astype(str).str.startswith(prefix)]

        return odis_exploded.sort_values(by='weighted_score', ascending=False)

    def _compute_criteria_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
        df = df.copy()

        # --- EMPLOI (Generic Matching Helper) ---
        relevant_bmo = self.bmo_vertical[self.bmo_vertical['codgeo'].isin(df.index)]
        commune_fap_map = relevant_bmo.groupby('codgeo')['fap_code'].apply(set).to_dict()

        for i in range(config.nb_adultes):
            if config.codes_metiers[i]:
                 self._score_matching(df, f'met_match_adult{i+1}', set(config.codes_metiers[i]), commune_fap_map)

        # Formations
        relevant_formations = self.formations_data[self.formations_data['codgeo'].isin(df.index)]
        form_map = relevant_formations.groupby('codgeo')['formation_code'].apply(set).to_dict()

        for i in range(config.nb_adultes):
            if config.codes_formations[i]:
                 # For formations we also store the codes for display
                 adult_key = f'adult{i+1}'
                 prefs = set(config.codes_formations[i])
                 col_name = f'form_match_codes_{adult_key}'
                 df[col_name] = df.index.map(lambda c: list(form_map.get(c, set()).intersection(prefs)))
                 # Then score count
                 self._score_matching(df, f'form_match_{adult_key}', prefs, form_map)

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

        # --- HOUSING ---
        self._prune_irrelevant_scores(df, config)

        # --- EDUCATION ---
        if config.nb_enfants > 0 and config.classe_enfants:
             min_b, max_b = get_bounds('edu_classes_ferm_scaled', self.scores_cat, self.global_stats)
             edu_map = {'Crèche / Assistante Maternelle': 'edu_petite_enfance_scaled', 'Maternelle': 'edu_maternelle_scaled', 'Elémentaire': 'edu_elementaire_scaled', 'Collège': 'edu_college_scaled', 'Lycée': 'edu_lycee_scaled'}
             
             cols_to_drop = [sco for opt, sco in edu_map.items() if opt not in config.classe_enfants and sco in df.columns]
             if cols_to_drop: df.drop(columns=cols_to_drop, inplace=True)
             if 'edu_structures_scaled' in df.columns: df.drop(columns=['edu_structures_scaled'], inplace=True)
        else:
             df['edu_classes_ferm_scaled'] = 0.0

        # --- SANTE ---
        if config.besoin_sante != 'Aucun':
            col_map = {'Hopital': 'sante_hopital_scaled', 'Maternité': 'sante_maternite_scaled', 'Soutien Psychologique & Addictologie': 'sante_psy_scaled'}
            target = col_map.get(config.besoin_sante)
            if target in df.columns: df['sante_structures_scaled'] = df[target]
            else: df['sante_structures_scaled'] = 0.0



        # EPCI Bonus
        current_epci = None
        if config.commune_actuelle:
             if isinstance(config.commune_actuelle, str) and config.commune_actuelle in self.df_all_communes.index:
                 current_epci = self.df_all_communes.loc[config.commune_actuelle]['epci_code']
             elif isinstance(config.commune_actuelle, (pd.Series, pd.DataFrame)) and 'epci_code' in config.commune_actuelle:
                  current_epci = config.commune_actuelle['epci_code'].iloc[0]

        df['mob_epci_scaled'] = np.where(df['epci_code'] == current_epci, 1, 0) if current_epci else 0.0

        # --- INCLUSION ---
        df = compute_inclusion_score(df, config, self.incl_index, self.associations_data, self.scores_cat, self.global_stats)
        
        logger.info(f"📈 [ENGINE] Scored columns: {[c for c in df.columns if 'scaled' in c]}")
        return df

    def _score_matching(self, df: pd.DataFrame, score_key: str, prefs: set, data_map: dict):
        """Helper to calculate match count and scale it."""
        df[score_key.replace('_scaled', '')] = df.index.map(lambda c: len(data_map.get(c, set()).intersection(prefs)))
        min_b, max_b = get_bounds(f'{score_key}_scaled', self.scores_cat, self.global_stats)
        if pd.isna(max_b): max_b = float(len(prefs))
        df[f'{score_key}_scaled'] = min_max_scale(df[score_key.replace('_scaled', '')].fillna(0), min_b, max_b)

    def _prune_irrelevant_scores(self, df: pd.DataFrame, config: ScoringConfig):
        """Removes scores not relevant to the current user selection."""
        if config.hebergement != 'Location' and config.logement != 'Location':
            df.drop(columns=[c for c in ['log_vac_scaled', 'loyer_abordable_scaled'] if c in df.columns], inplace=True)
        if config.logement != 'Logement Social':
             if 'log_soc_inoc_scaled' in df.columns: df.drop(columns=['log_soc_inoc_scaled'], inplace=True)
        if config.hebergement != "Chez l'habitant":
             if 'log_occup_scaled' in df.columns: df.drop(columns=['log_occup_scaled'], inplace=True)

# --- Stateless Helpers ---

def get_bounds(score_id: str, scores_cat: pd.DataFrame, global_stats: Dict) -> Tuple[float, float]:
    if score_id in global_stats: return global_stats[score_id]['min'], global_stats[score_id]['max']
    row = scores_cat[scores_cat['score'] == score_id]
    if not row.empty:
        return (float(row.iloc[0]['min_bound']) if pd.notna(row.iloc[0]['min_bound']) else 0.0,
                float(row.iloc[0]['max_bound']) if pd.notna(row.iloc[0]['max_bound']) else 1.0)
    return 0.0, 1.0

def min_max_scale(series: pd.Series, min_val: float, max_val: float) -> pd.Series:
    if max_val == min_val: return pd.Series(0.0, index=series.index)
    return ((series - min_val) / (max_val - min_val)).clip(0, 1)

def add_distance_to_current_loc(df: gpd.GeoDataFrame, current_codgeo: Optional[str], df_all: Optional[gpd.GeoDataFrame] = None) -> gpd.GeoDataFrame:
    target_geom = None
    if current_codgeo in df.index:
         target_geom = df.loc[current_codgeo, 'centroid'] if 'centroid' in df.columns else df.loc[current_codgeo].geometry.centroid
    elif df_all is not None and current_codgeo in df_all.index:
         target_geom = df_all.loc[current_codgeo, 'centroid'] if 'centroid' in df_all.columns else df_all.loc[current_codgeo].geometry.centroid
    
    if target_geom is not None:
         # Use projected centroids
         centroids = df['centroid'] if 'centroid' in df.columns else df.centroid
         df.loc[:, 'dist_current_loc'] = centroids.distance(target_geom)
    return df

def filter_communes(df: gpd.GeoDataFrame, start_commune: pd.DataFrame, loc_type: str, loc_code: Optional[str], loc_search_area: str) -> gpd.GeoDataFrame:
    if loc_type == 'departement': return df[df['dep_code'] == loc_code].copy()
    elif loc_type == 'region': return df[df['reg_code'] == loc_code].copy()
    elif loc_type == 'france': return df[~df['dep_code'].astype(str).str.startswith(('97', '98'))].copy()
    return gpd.GeoDataFrame()

def compute_inclusion_score(df: gpd.GeoDataFrame, config: ScoringConfig, incl_index: pd.DataFrame, associations_data: pd.DataFrame, scores_cat: pd.DataFrame, global_stats: Dict) -> gpd.GeoDataFrame:
    df = df.copy()
    for col in ['inc_services_core_scaled', 'inc_asso_core_scaled']:
        if col not in df.columns: df[col] = 0.0

    # Affinities
    if config.inc_asso_add_selection:
        interest_codes = set()
        for i in config.inc_asso_add_selection:
             if i in cfg.WALDEC_INC_ASSO_ADD_MAPPING: interest_codes.update(cfg.WALDEC_INC_ASSO_ADD_MAPPING[i])
             elif isinstance(i, str) and len(i)>=3: interest_codes.add(i)
        
        if interest_codes:
            start_tuple = tuple(interest_codes)
            # Assuming id_waldec has the code
            affinite_assos = associations_data[associations_data['id_waldec'].astype(str).str.startswith(start_tuple, na=False)]
            affinite_counts = affinite_assos.groupby('codgeo')['count'].sum().reindex(df.index, fill_value=0)
            df['affinite_density'] = (affinite_counts * 1000) / df['population']
            min_b, max_b = get_bounds('inc_asso_add_scaled', scores_cat, global_stats)
            df['inc_asso_add_scaled'] = min_max_scale(df['affinite_density'], min_b, max_b)
        else: df['inc_asso_add_scaled'] = 0.0
    else: df['inc_asso_add_scaled'] = 0.0

    # Specific Services
    needed = set(config.inc_services_add_selection)
    if needed:
         # Optimize lookup
         def count_matches(available):
             if not isinstance(available, set): return 0
             return sum(1 for n in needed if any(n in a for a in available))

         if 'key' not in df.columns: df = df.join(incl_index, how='left')
         df['inc_services_add_scaled'] = df['key'].apply(count_matches) / len(needed)
    else:
         df['inc_services_add_scaled'] = 0.0

    return df

def compute_category_scores(df: pd.DataFrame, scores_cat: pd.DataFrame, config: ScoringConfig) -> pd.DataFrame:
    df = df.copy()
    for category in scores_cat['cat'].unique():
        if category == 'education' and config.nb_enfants == 0: continue
        if category == 'sante' and config.besoin_sante == 'Aucun': continue

        score_cols = [c for c in scores_cat[scores_cat.cat == category]['score'] if c in df.columns]
        if not score_cols: continue
        
        scores_val = []
        weights_val = []
        
        for col in score_cols:
             if col == 'youth_decline_scaled' and config.nb_enfants == 0: continue
             # Ed filtering handled in criteria
             val = df[col].fillna(0).copy() # Use Copy to avoid SettingWithCopy

             weight = 1.0 # Default
             # Lookup dynamic weight
             # Priority
             if col in config.criteria_weights: weight *= config.criteria_weights[col]
             else:
                  # Look up catalog weight
                  row = scores_cat[scores_cat['score'] == col]
                  if not row.empty: weight *= float(row.iloc[0]['weight'])

             scores_val.append(val * weight)
             weights_val.append(weight)
        
        if weights_val:
             cat_score = sum(scores_val) / sum(weights_val)
             df[f"{category}_cat_score"] = cat_score

    return df

def compute_weighted_score(df: pd.DataFrame, config: ScoringConfig) -> pd.Series:
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
        if cat == 'education' and config.nb_enfants == 0: continue
        if cat == 'sante' and config.besoin_sante == 'Aucun': continue
        
        col = f"{cat}_cat_score"
        if col in df.columns:
            # Handle NaNs: treat as 0 but don't count weight if it was NaN? 
            # Actually, standard behavior is: if missing, score is 0. Weight still applies.
            # But the test expects NaNs to be IGNORED (re-weighted).
            # To support re-weighting row-wise (vectorized):
            
            val = df[col].fillna(0) # Score used
            valid_mask = df[col].notna() # Where weight applies
            
            # If we want to ignore the weight where val is NaN:
            # Add to total_score ONLY where valid * weight
            # Add to total_weight ONLY where valid * weight
            
            weighted_val = val * weight
            total_score += weighted_val
            
            # For total_weight, we add 'weight' only where valid_mask is True
            # We can use a Series for total_weight accumulation
            current_weight_series = pd.Series(0.0, index=df.index)
            current_weight_series[valid_mask] = weight
            total_weight += current_weight_series
            
            # Fallback: if we just want simple .fillna(0) approach without re-weighting
            # total_score += val * weight
            # total_weight += weight
            # But test_compute_weighted_score_nan_handling asserts 1.0 (re-weighted).
            
    # Ensure no division by zero
    if isinstance(total_weight, (int, float)):
        return total_score / total_weight if total_weight > 0 else total_score
    else:
        # It's a Series
        return (total_score / total_weight).fillna(0)
