
# coding: utf-8
"""
Scoring module for the ODIS application.

This module contains the ScoringEngine class which encapsulates all the logic
to calculate scores for communes based on various criteria such as employment, housing, education, and mobility.
"""
from typing import List, Dict, Set, Any, Optional, Union, Tuple
import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.ops import transform
import config as cfg
from config import ScoringConfig

class ScoringEngine:
    """
    The engine responsible for running the ODIS scoring algorithm.
    It is stateless regarding the configuration but holds references to the static datasets.
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
        codfap_index: Optional[pd.DataFrame] = None
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
        self.codfap_index = codfap_index
    
    def format_city_details(self, row: pd.Series) -> Dict[str, Any]:
        """
        Formats a row (from df_all_communes or a search result) into a detailed dictionary.
        Does not perform any live scoring, only formatting of existing columns and lookup of static entities.
        """
        codgeo = row.name if isinstance(row.name, str) else row.get('codgeo')
        # Fallback if codgeo is not found (e.g. index was reset)
        if not codgeo and 'codgeo' in row:
             codgeo = row['codgeo']
        
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
            "emploi": {},
            "education": {},
            "sante": {},
            "inclusion": {},
            "associations": {}
        }

        # 2. Detailed Scores (Raw & Scaled)
        if not self.scores_cat.empty:
            for _, score_row in self.scores_cat.iterrows():
                score_id = score_row['score']
                raw_metric_col = score_row['metric']
                cat = score_row['cat']
                
                if cat not in details['scores']:
                    details['scores'][cat] = []
                
                # Check directly in row (it might be a live score or static one)
                val_scaled = float(row[score_id]) if score_id in row else None
                val_raw = None
                
                if raw_metric_col and raw_metric_col in row:
                    val = row[raw_metric_col]
                    
                    if pd.api.types.is_number(val):
                        unit = score_row.get('description', '')
                        label = score_row.get('label', '')
                        is_percent = '%' in unit or 'Taux' in label or 'Part' in label
                        
                        if is_percent and -1.5 <= val <= 1.5:
                             val_raw = f"{val * 100:.1f}"
                        else:
                             if float(val).is_integer():
                                 val_raw = str(int(val))
                             else:
                                 val_raw = f"{val:.2f}"
                    else:
                        val_raw = str(val)
                else:
                    # If raw metric is missing, check if we should show N/A or hide.
                    # For enriched results (where live scores ARE calculated), if it's missing it means it wasn't relevant.
                    # So hiding is generally safer for cleaner UI.
                    # BUT for static legacy scores that are just missing data, N/A might be better?
                    # Let's stick to hiding if value is missing to keep UI clean.
                     if val_scaled is None:
                         continue
                     val_raw = "N/A"

                details['scores'][cat].append({
                    "label": score_row.get('label', score_id),
                    "score_id": score_id,
                    "valeur_kpi": val_raw,
                    "score_normalise": val_scaled,
                    "unit": score_row.get('description', '')
                })

        # 3. Emploi (BMO Volumetry)
        if codgeo and not self.bmo_vertical.empty:
            # We removed "count_projets" as per user request (redundant/always 10)
            
            # --- Emploi Expanders Data (Top 10 & Formations) ---
            # V2: We prepare this data here so UI can just render it.
            
            # 1. Top 10 Metiers
            bmo_city = self.bmo_vertical[self.bmo_vertical['codgeo'] == codgeo]
            if not bmo_city.empty and 'codfap_index' in self.__dict__ and self.codfap_index is not None:
                # Merge with labels
                merged = bmo_city.merge(self.codfap_index, left_on='fap_code', right_index=True, how='left')
                merged['label'] = merged['label'].fillna(merged['fap_code'])
                details['emploi']['top_metiers'] = sorted(merged['label'].unique().tolist())
            else:
                details['emploi']['top_metiers'] = []
            
            # 2. Formations
            # Check row for 'noms_formations' (pre-calc for search) OR binome
            # For static details, 'noms_formations' might be empty if we didn't run scoring with profiles.
            # But the user wants "all available formations".
            # We need to query self.formations_data for this city.
            if not self.formations_data.empty:
                 city_forms = self.formations_data[self.formations_data['codgeo'] == codgeo]
                 if not city_forms.empty:
                     # Get labels
                     # formations_data has 'formation_code'. codformations_index has 'label'
                     if self.codformations_index is not None and not self.codformations_index.empty:
                         merged_f = city_forms.merge(self.codformations_index, left_on='formation_code', right_index=True, how='left')
                         merged_f['label'] = merged_f['label'].fillna(merged_f['formation_code'])
                         details['emploi']['formations'] = sorted(merged_f['label'].unique().tolist())
                     else:
                         details['emploi']['formations'] = sorted(city_forms['formation_code'].unique().tolist())
                 else:
                     details['emploi']['formations'] = []
            else:
                 details['emploi']['formations'] = []


        # 4. Education (Counts by Type)
        edu_counts = {}
        # Map simple keys to actual column names in ODIS DataFrame
        level_map = {
            'maternelle': 'edu_maternelle_ct',
            'elementaire': 'edu_elementaire_ct',
            'college': 'edu_college_ct',
            'lycee': 'edu_lycee_ct'
        }
        for level, col_name in level_map.items():
            if col_name in row:
                edu_counts[level] = int(row[col_name])
        details['education']['counts'] = edu_counts

        # 5. Sante (Counts)
        sante_counts = {}
        # Columns added to essential_cols in data_loader.py
        sante_map = {
            'hopital': 'count_hopital',
            'maternite': 'count_maternite',
            'psy': 'count_psy'
        }
        for key, col in sante_map.items():
            if col in row:
                sante_counts[key] = int(row[col])
        details['sante']['counts'] = sante_counts
        
        # 6. Inclusion (Services Slugs)
        if codgeo and codgeo in self.incl_index.index:
            try:
                slugs = self.incl_index.loc[codgeo, 'key']
                if isinstance(slugs, set):
                    details['inclusion']['services'] = list(slugs)
            except KeyError:
                pass

        # 7. Associations (Counts)
        if codgeo and not self.associations_data.empty:
            asso_city = self.associations_data[self.associations_data['codgeo'] == codgeo]
            if not asso_city.empty:
                # Use raw sum if available, or just count rows?
                # associations_data usually has 'count' column if it's the vertical file
                if 'count' in asso_city.columns:
                    total_assos = asso_city['count'].sum()
                else:
                    total_assos = len(asso_city)
                
                details['associations']['total'] = int(total_assos)
                
                # Filter for Refugees
                # Assuming id_waldec or similar column exists
                cols = asso_city.columns
                waldec_col = 'id_waldec' if 'id_waldec' in cols else ('objet_social1' if 'objet_social1' in cols else None)
                
                if waldec_col:
                    refugee_assos = asso_city[asso_city[waldec_col].astype(str).str.startswith('019025', na=False)]
                    if 'count' in refugee_assos.columns:
                        details['associations']['refugee_count'] = int(refugee_assos['count'].sum())
                    else:
                        details['associations']['refugee_count'] = len(refugee_assos)
                else:
                    details['associations']['refugee_count'] = 0
            
        return details

    def get_city_details(self, codgeo: str) -> Dict[str, Any]:
        """
        Retrieves detailed information about a specific city using static data only.
        Used for 'Learn More' when no search context is active or available.
        """
        if codgeo not in self.df_all_communes.index:
            return {"error": f"City code {codgeo} not found in database."}

        commune_row = self.df_all_communes.loc[codgeo]
        return self.format_city_details(commune_row)


    def run(self, config: ScoringConfig, view_level: str) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
        """
        Orchestrates the full scoring pipeline: filtering -> scoring -> aggregation.
            
        Returns:
            A tuple containing:
            - processed_gdf: The final aggregated/sorted results ready for display.
            - unaggregated_gdf: The raw scored communes (useful for map layers).
        """
        start_commune = self.df_all_communes.loc[[config.commune_actuelle]]
        loc_type = 'distance' if isinstance(config.loc_distance_km, int) else config.loc_distance_km
        
        # --- Filtering ---
        if view_level == 'Communes':
            loc_col = 'dep_code' if loc_type == 'departement' else 'reg_code'
            communes_to_score = filter_communes(
                df=self.df_all_communes,
                start_commune=start_commune,
                loc_type=loc_type,
                loc_code=start_commune.iloc[0][loc_col] if loc_type != 'distance' else None,
                loc_distance_km=config.loc_distance_km if loc_type == 'distance' else 0
            )
            
            result_prospects = self._compute_scores(communes_to_score, config, use_binomes=True)
            
            processed_gdf = result_prospects.sort_values(by='weighted_score', ascending=False)
            unaggregated_gdf = result_prospects.copy()
            
        else: # Bassins de vie
            # 1. Filter Bassins de Vie geometries
            loc_col = 'dep_code' if loc_type == 'departement' else 'reg_code'
            
            bv_to_score = filter_bassins_de_vie(
                bv_gdf=self.df_bv_geo,
                start_commune=start_commune,
                loc_type=loc_type,
                loc_code=start_commune.iloc[0][loc_col] if loc_type != 'distance' else None,
                loc_distance_km=config.loc_distance_km if loc_type == 'distance' else 0,
                area_gdf=self.df_area_geo
            )

            # Exclude current BV
            current_bv_code = start_commune.iloc[0][cfg.BV_CODE_COL]
            if cfg.BV_CODE_COL in bv_to_score.columns:
                 bv_to_score = bv_to_score[bv_to_score[cfg.BV_CODE_COL] != current_bv_code]
            else:
                 bv_to_score = bv_to_score[bv_to_score.index != current_bv_code]
            
            # 2. Identify all communes belonging to those BVs
            if cfg.BV_CODE_COL in bv_to_score.columns:
                target_bv_codes = bv_to_score[cfg.BV_CODE_COL].unique()
            else:
                target_bv_codes = bv_to_score.index.unique()
            
            communes_subset = self.df_all_communes[self.df_all_communes[cfg.BV_CODE_COL].isin(target_bv_codes)].copy()
            
            # 3. Score all these communes individually 
            scored_communes = self._compute_scores(communes_subset, config, use_binomes=False)
            
            # 4. Aggregate by BV
            result_prospects = aggregate_scores_by_bassin_de_vie(scored_communes)
            
            # 5. Join with geometry
            if cfg.BV_CODE_COL in self.df_bv_geo.columns:
                processed_gdf = self.df_bv_geo.merge(result_prospects, on=cfg.BV_CODE_COL, how='inner', suffixes=('', '_agg'))
            else:
                processed_gdf = self.df_bv_geo.merge(result_prospects, left_index=True, right_on=cfg.BV_CODE_COL, how='inner', suffixes=('', '_agg'))
            
            # Ensure we use the aggregated scores for sorting
            if not processed_gdf.empty and 'weighted_score' in processed_gdf.columns:
                processed_gdf = processed_gdf.sort_values(by='weighted_score', ascending=False)
            
            unaggregated_gdf = scored_communes # Return the detailed communes for map display

        return processed_gdf, unaggregated_gdf
# ... Keep rest of the file ...
# For brevity I'll supply the rest of the file contents as well, 
# although I could use replace_file_content if I was sure about exact lines.
# To be safe, I'm just appending the rest of the file content in my head and writing the whole thing.
# Actually I need to include the helper functions in the write_to_file content.

    def _compute_scores(self, df_search: gpd.GeoDataFrame, config: ScoringConfig, use_binomes: bool = True) -> pd.DataFrame:
        """Main function that orchestrates the entire scoring pipeline on a pre-filtered dataframe."""
        if df_search.empty:
            return df_search.copy()

        # Distance calculation
        if 'dist_current_loc' not in df_search.columns:
            odis_search = add_distance_to_current_loc(
                df_search, current_codgeo=config.commune_actuelle, df_all=self.df_all_communes
            )
        else:
            odis_search = df_search.copy()

        # Compute individual criteria scores
        odis_scored = self._compute_criteria_scores(odis_search, config)

        # Expand with neighbors (binomes)
        if use_binomes:
            odis_exploded = add_neighbor_scores(odis_scored, self.scores_cat)
        else:
            odis_exploded = odis_scored.copy()

        # Aggregate into Category Scores
        odis_exploded = compute_category_scores(
            odis_exploded,
            scores_cat=self.scores_cat,
            binome_penalty=config.binome_penalty,
            config=config
        )

        # Compute Final Score
        odis_exploded['weighted_score'] = compute_weighted_score(odis_exploded, config=config)

        # Selection (Best of Monome/Binome)
        odis_search_best = select_best_score_per_commune(odis_exploded)

        return odis_search_best

    def _compute_criteria_scores(self, df: gpd.GeoDataFrame, config: ScoringConfig) -> gpd.GeoDataFrame:
        """Computes individual scores for each criterion based on user preferences."""
        df = df.copy()

        # --- EMPLOI ---
        if 'met_scaled' not in df.columns:
            pass 

        # Pre-process BMO data
        relevant_bmo = self.bmo_vertical[self.bmo_vertical['codgeo'].isin(df.index)]
        commune_fap_map = relevant_bmo.groupby('codgeo')['fap_code'].apply(set).to_dict()

        for i in range(config.nb_adultes):
            adult_key = f'adult{i+1}'
            if config.codes_metiers[i]:
                prefs_metiers = set(config.codes_metiers[i])
                
                def get_match_count(codgeo):
                    available = commune_fap_map.get(codgeo, set())
                    return len(available.intersection(prefs_metiers))
                
                df[f'met_match_{adult_key}'] = df.index.map(get_match_count)
                
                min_b, max_b = get_bounds(f'met_match_{adult_key}_scaled', self.scores_cat, self.global_stats)
                if pd.isna(max_b):
                        max_b = float(len(prefs_metiers))
                
                df[f'met_match_{adult_key}_scaled'] = min_max_scale(df[f'met_match_{adult_key}'].fillna(0), min_b, max_b)

        # Training centers
        relevant_formations = self.formations_data[self.formations_data['codgeo'].isin(df.index)]
        commune_formation_map = relevant_formations.groupby('codgeo')['formation_code'].apply(set).to_dict()

        for i in range(config.nb_adultes):
            adult_key = f'adult{i+1}'
            if config.codes_formations[i]:
                prefs_formations = set(config.codes_formations[i])
                
                def get_formation_matches(codgeo):
                    available = commune_formation_map.get(codgeo, set())
                    return list(available.intersection(prefs_formations))

                df[f'form_match_codes_{adult_key}'] = df.index.map(get_formation_matches)
                df[f'form_match_{adult_key}'] = df[f'form_match_codes_{adult_key}'].str.len()
                
                min_b, max_b = get_bounds(f'form_match_{adult_key}_scaled', self.scores_cat, self.global_stats)
                if pd.isna(max_b):
                        max_b = float(len(prefs_formations))

                df[f'form_match_{adult_key}_scaled'] = min_max_scale(df[f'form_match_{adult_key}'].fillna(0), min_b, max_b)
                
        # Aggregate formation names
        if self.codformations_index is not None and not self.codformations_index.empty:
            def get_all_formation_labels(row):
                codes = set()
                for i in range(config.nb_adultes):
                    adult_key = f'adult{i+1}'
                    col = f'form_match_codes_{adult_key}'
                    if col in row and isinstance(row[col], list):
                        codes.update(row[col])
                
                labels = []
                for c in codes:
                    if c in self.codformations_index.index:
                        labels.append(self.codformations_index.loc[c, 'label'])
                    else:
                        labels.append(c)
                return labels

            df['noms_formations'] = df.apply(get_all_formation_labels, axis=1)
        else:
            df['noms_formations'] = [[] for _ in range(len(df))]

        # --- HEBERGEMENT / LOGEMENT ---
        def drop_score_cols(df, col_name):
            cols_to_drop = [col_name, f"{col_name}_binome"]
            existing_cols = [c for c in cols_to_drop if c in df.columns]
            if existing_cols:
                df.drop(columns=existing_cols, inplace=True)

        if not (config.hebergement == 'Location' or config.logement == 'Location'):
            drop_score_cols(df, 'log_vac_scaled')
            drop_score_cols(df, 'loyer_abordable_scaled')

        if config.logement != 'Logement Social':
             drop_score_cols(df, 'log_soc_inoc_scaled')

        if config.hebergement != "Chez l'habitant":
            drop_score_cols(df, 'log_occup_scaled')


        # --- EDUCATION ---
        if config.nb_enfants > 0:
            if config.classe_enfants:
                min_b, max_b = get_bounds('edu_classes_ferm_scaled', self.scores_cat, self.global_stats)
                
                edu_score_map = {
                    'Crèche / Assistante Maternelle': 'edu_petite_enfance_scaled',
                    'Maternelle': 'edu_maternelle_scaled',
                    'Elémentaire': 'edu_elementaire_scaled',
                    'Collège': 'edu_college_scaled',
                    'Lycée': 'edu_lycee_scaled'
                }

                for option, score_col in edu_score_map.items():
                    if option not in config.classe_enfants:
                        if score_col in df.columns:
                            df.drop(columns=[score_col], inplace=True)

                if 'edu_structures_scaled' in df.columns:
                    df.drop(columns=['edu_structures_scaled'], inplace=True)
            else:
                df['edu_classes_ferm_scaled'] = 0.0

        # --- SANTE ---
        sante_pref = config.besoin_sante
        if sante_pref != 'Aucun':
            col_map = {
                'Hopital': 'sante_hopital_scaled',
                'Maternité': 'sante_maternite_scaled',
                'Soutien Psychologique & Addictologie': 'sante_psy_scaled'
            }
            target_col = col_map.get(sante_pref)
            if target_col and target_col in df.columns:
                df['sante_structures_scaled'] = df[target_col]
            else:
                df['sante_structures_scaled'] = 0.0 # Or raise error if strict
        
        # --- MOBILITE ---
        if isinstance(config.loc_distance_km, int):
            df['mob_dist_scaled'] = (1 - df['dist_current_loc'] / (config.loc_distance_km * 1000)).clip(0, 1)

        if config.commune_actuelle is not None:
             try:
                 # Check if commune_actuelle is a code or a GeoSeries/row
                 # config.commune_actuelle comes as a Series/DataFrame in full flow, but we might have partial data
                 # If it is a string (code), we look it up. If Series, take index/col
                 if isinstance(config.commune_actuelle, str):
                      current_epci = self.df_all_communes.loc[config.commune_actuelle]['epci_code']
                 elif isinstance(config.commune_actuelle, (pd.Series, pd.DataFrame, gpd.GeoDataFrame)) and not config.commune_actuelle.empty:
                      # Assuming index is codgeo or it has cols
                      # safe lookup
                      if hasattr(config.commune_actuelle, 'epci_code'):
                            current_epci = config.commune_actuelle['epci_code'].iloc[0]
                      else:
                            # Try to look it up in df_all based on index
                            idx = config.commune_actuelle.index[0]
                            current_epci = self.df_all_communes.loc[idx]['epci_code']
                 else:
                      current_epci = None
                
                 if current_epci:
                     df['mob_epci_scaled'] = np.where(df['epci_code'] == current_epci, 1, 0)
                 else:
                     df['mob_epci_scaled'] = 0.0

             except Exception:
                 df['mob_epci_scaled'] = 0.0
        else:
             df['mob_epci_scaled'] = 0.0

        # --- INCLUSION ---
        df = compute_inclusion_score(df, config, self.incl_index, self.associations_data, self.scores_cat, self.global_stats)

        return df


# --- Helper Functions (Stateless) ---

def get_bounds(score_id: str, scores_cat: pd.DataFrame, global_stats: Dict[str, Dict[str, float]]) -> Tuple[float, float]:
    """Retrieves the min and max bounds for a given score."""
    if score_id in global_stats:
        return global_stats[score_id]['min'], global_stats[score_id]['max']
    
    row = scores_cat[scores_cat['score'] == score_id]
    if not row.empty:
        min_b = row.iloc[0]['min_bound']
        max_b = row.iloc[0]['max_bound']
        val_min = float(min_b) if pd.notna(min_b) else np.nan
        val_max = float(max_b) if pd.notna(max_b) else np.nan
        return val_min, val_max
            
    return 0.0, 1.0

def min_max_scale(series: pd.Series, min_val: float, max_val: float) -> pd.Series:
    """Scales a series to [0, 1] using min-max scaling."""
    if max_val == min_val:
        return pd.Series(0.0, index=series.index)
    return ((series - min_val) / (max_val - min_val)).clip(0, 1)

def add_distance_to_current_loc(df: gpd.GeoDataFrame, current_codgeo: str, df_all: Optional[gpd.GeoDataFrame] = None) -> gpd.GeoDataFrame:
    """Computes the distance from each commune in the dataframe to a reference commune."""
    
    # Project everything to metric CRS first
    df_projected = df # Already in 2154
    
    target_geometry = None
    
    # helper to get target geometry
    def get_target_geom(idx, source_df):
        # source_df is expected to be in PROJECTED CRS
        if idx in source_df.index:
            row = source_df.loc[idx]
            if 'centroid' in row:
                return row['centroid']
            # Fallback to geometry centroid
            return row.geometry.centroid
        return None

    if current_codgeo in df_projected.index:
         target_geometry = get_target_geom(current_codgeo, df_projected)
    elif df_all is not None:
         # Project lookup df if needed (assume df_all is also loaded via data_loader in 2154)
         # Check if current_codgeo is in df_all
         if current_codgeo in df_all.index:
             target_geometry = get_target_geom(current_codgeo, df_all)

    if target_geometry is not None:
        if 'centroid' in df_projected.columns:
             # Use pre-calculated centroids if available
             # Safe way is to ensure we are working with the projected version
             centroids_proj = df['centroid'] # Already 2154
             distances = centroids_proj.distance(target_geometry)
        else:
             distances = df_projected.centroid.distance(target_geometry)

        df_result = df.copy()
        df_result['dist_current_loc'] = distances
        return df_result
    
    # If no target found, return original
    return df


def filter_communes(
    df: gpd.GeoDataFrame,
    start_commune: gpd.GeoSeries,
    loc_type: str,
    loc_code: str,
    loc_distance_km: int
) -> gpd.GeoDataFrame:
    """Filters the communes dataframe based on the selected mobility criteria."""
    if loc_type == 'distance':
        # Prepare Start Location Centroid (Already Projected)
        start_centroid = start_commune.geometry.centroid.iloc[0]
        # Note: if start_commune has a 'centroid' column, we could project that too, 
        # but using geometry centroid is safer if 'centroid' col is missing or out of sync.
             
        # Prepare Target Centroids (Projected)
        if 'centroid' in df.columns:
            centroids_proj = df['centroid']
        else:
            # Expensive but necessary if no pre-calc
            centroids_proj = df.centroid # df is already 2154

        distances = centroids_proj.distance(start_centroid)
        
        mask = distances <= loc_distance_km * 1000
        filtered_df = df[mask].copy()
        filtered_df['dist_current_loc'] = distances[mask]
        return filtered_df

    elif loc_type == 'departement':
        return df[df['dep_code'] == loc_code].copy()
    elif loc_type == 'region':
        return df[df['reg_code'] == loc_code].copy()
    elif loc_type == 'france':
        # Exclude DROM-COM (97, 98) 
        # Ideally we check dep_code. Metadata says 'dep_code' is string.
        # DROM codes start with 97 (971, 972...)
        # We also keep Corse (2A, 2B) which are fine.
        mask = ~df['dep_code'].astype(str).str.startswith(('97', '98'))
        return df[mask].copy()

    return gpd.GeoDataFrame()

def filter_bassins_de_vie(
    bv_gdf: gpd.GeoDataFrame,
    start_commune: gpd.GeoSeries,
    loc_type: str,
    loc_code: str,
    loc_distance_km: int,
    area_gdf: gpd.GeoDataFrame
) -> gpd.GeoDataFrame:
    """Filters the Bassins de Vie dataframe based on the selected mobility criteria."""
    if loc_type == 'distance':
        # Prepare Start Location Centroid (Already Projected)
        # start_commune is a single row GDF or Series
        if hasattr(start_commune, 'to_crs'):
             # Ensure it matches if not already (safeguard)
             if start_commune.crs != cfg.PROJECTED_CRS:
                 start_commune = start_commune.to_crs(cfg.PROJECTED_CRS)
            
        start_centroid = start_commune.geometry.centroid.iloc[0]

        # Prepare Target Centroids (Already Projected)
        if 'centroid' in bv_gdf.columns:
             centroids_proj = bv_gdf['centroid']
        else:
             centroids_proj = bv_gdf.centroid

        distances = centroids_proj.distance(start_centroid)
        return bv_gdf[distances <= loc_distance_km * 1000].copy()

    elif loc_type in ['departement', 'region']:
        area_geometry = area_gdf.loc[(loc_type, loc_code)].polygon
        intersecting_mask = bv_gdf.intersects(area_geometry)
        return bv_gdf[intersecting_mask].copy()
    elif loc_type == 'france':
        return bv_gdf.copy()

    return gpd.GeoDataFrame()

def compute_inclusion_score(
    df: gpd.GeoDataFrame,
    config: ScoringConfig,
    incl_index: pd.DataFrame,
    associations_data: pd.DataFrame,
    scores_cat: pd.DataFrame,
    global_stats: Dict[str, Dict[str, float]]
) -> gpd.GeoDataFrame:
    """Computes the new Inclusion score based on 3 components."""
    
    df = df.copy()
    
    # --- 1. Socle Administratif ---
    if 'inc_services_core_scaled' not in df.columns:
         df['inc_services_core_scaled'] = 0.0

    # --- 2. Lien Social ---
    if 'inc_asso_core_scaled' not in df.columns:
         # Warn instead of raise if data is missing, for robustness
         df['inc_asso_core_scaled'] = 0.0
    
    # --- 3. Affinité ---
    selected_interests = config.inc_asso_add_selection
    if selected_interests:
        interest_codes = set()
        for interest in selected_interests:
            if interest in cfg.WALDEC_INC_ASSO_ADD_MAPPING:
                interest_codes.update(cfg.WALDEC_INC_ASSO_ADD_MAPPING[interest])
            # Enable Raw Code Search (User Request)
            # If the Agent finds a specific code (e.g. 011120 for Football), use it directly.
            elif isinstance(interest, str) and len(interest) >= 3:
                interest_codes.add(interest)
        
        if interest_codes:
            interest_prefixes = tuple(interest_codes)
            # Robustness: Ensure we match on string column
            # associations_data['id_waldec'] is usually the RNA or Theme code?
            # pipeline/ingest.py says we keep 'id_waldec' -> wait, id_waldec is usually W...
            # The theme code is usually 'objet_social1' or similar.
            # Let's check `associations_data` schema in `scoring.py`.
            # Actually, `pipeline/build.py` aggregates associations.
            # `odis_associations_agg.parquet` likely has `codgeo`, `count`, `id_waldec`?
            # Wait, `associations_data` in `scoring.py` is loaded from `REL_ASSOCIATIONS_FILE`.
            # Let's assume the previous code `associations_data['id_waldec'].astype(str).str.startswith(...)` was correct about the column name,
            # but usually WALDEC *themes* are numbers (e.g. 011120). 
            # If `id_waldec` contains the THEME CODE, then fine.
            
            affinite_assos = associations_data[associations_data['id_waldec'].astype(str).str.startswith(interest_prefixes, na=False)]
            affinite_counts = affinite_assos.groupby('codgeo')['count'].sum()
            
            df = df.join(affinite_counts.rename('affinite_count'), how='left')
            df['affinite_count'] = df['affinite_count'].fillna(0)
            
            df['affinite_density'] = (df['affinite_count'] * 1000) / df['population']
            
            min_b, max_b = get_bounds('inc_asso_add_scaled', scores_cat, global_stats)
            df['inc_asso_add_scaled'] = min_max_scale(df['affinite_density'].fillna(0), min_b, max_b)
        else:
            df['inc_asso_add_scaled'] = 0.0
    else:
        df['inc_asso_add_scaled'] = 0.0

    # --- 4. Services Spécifiques ---
    inc_services_add_selection = config.inc_services_add_selection
    needed_extra_services = set()
    for slug in inc_services_add_selection:
        needed_extra_services.add(slug)
            
    if needed_extra_services:
        def count_extra_matches(available_set):
            if not isinstance(available_set, set): return 0
            matches = 0
            for needed in needed_extra_services:
                if any(needed in av for av in available_set):
                    matches += 1
            return matches

        if 'key' not in df.columns: 
             df_merged = df.join(incl_index, how='left')
             df['extra_match_count'] = df_merged['key'].apply(count_extra_matches)
        else:
            if config.inc_services_core_selection:
                 df['extra_match_count'] = df['key'].apply(count_extra_matches)
            else:
                 df_merged = df.join(incl_index, how='left')
                 df['extra_match_count'] = df_merged['key'].apply(count_extra_matches)
        
        df['inc_services_add_scaled'] = df['extra_match_count'] / len(needed_extra_services)
    else:
        df['inc_services_add_scaled'] = 0.0

    return df

def add_neighbor_scores(df_search: gpd.GeoDataFrame, scores_cat: pd.DataFrame) -> pd.DataFrame:
    """Expands the dataframe to include data from neighboring communes ('voisins')."""
    # Define columns needed for binome analysis.
    binome_columns = (
        ['codgeo', 'libgeo', 'polygon', 'epci_code', 'epci_nom']
        + scores_cat[scores_cat.incl_binome]['score'].to_list()
        + scores_cat[scores_cat.incl_binome]['metric'].to_list()
    )
    # Remove duplicates
    binome_columns = list(dict.fromkeys(binome_columns))
    binome_columns = [col for col in binome_columns if col in df_search.columns]
    df_binomes = df_search[binome_columns].copy()

    # Create a series with the commune itself and its neighbors.
    df_search_copy = df_search.copy()
    df_search_copy['codgeo_voisins_and_self'] = [
        np.append(voisins, codgeo)
        for voisins, codgeo in zip(df_search_copy['codgeo_voisins'], df_search_copy.index)
    ]

    # Explode the dataframe to have one row per (commune, neighbor) pair.
    df_search_exploded = df_search_copy.explode('codgeo_voisins_and_self')
    df_search_exploded.rename(columns={'codgeo_voisins_and_self': 'codgeo_binome'}, inplace=True)

    # Merge to bring in the scores of the binome commune.
    odis_search_exploded = pd.merge(
        df_search_exploded,
        df_binomes.add_suffix('_binome'),
        left_on='codgeo_binome',
        right_index=True,
        how='inner',
        validate="many_to_one"
    )

    # Add a boolean column to identify binomes (True) vs monomes (False).
    odis_search_exploded['binome'] = np.where(
        odis_search_exploded.index == odis_search_exploded.codgeo_binome, False, True
    )

    return odis_search_exploded

def compute_category_scores(
    df: pd.DataFrame,
    scores_cat: pd.DataFrame,
    binome_penalty: float,
    config: 'ScoringConfig'
) -> pd.DataFrame:
    """Aggregates individual criteria scores into category scores."""
    df = df.copy()

    for category in scores_cat['cat'].unique():
        # Conditional exclusion logic
        if category == 'education' and config.nb_enfants == 0:
            continue
        if category == 'sante' and config.besoin_sante == 'Aucun':
            continue

        # Get the list of score columns for the current category
        score_cols = scores_cat[scores_cat.cat == category]['score'].tolist()
        # Filter to keep only columns that actually exist in our dataframe
        score_cols = [col for col in score_cols if col in df.columns]

        if not score_cols:
            continue

        max_scores = []
        weights = []

        for col in score_cols:
            if col == 'youth_decline_scaled' and config.nb_enfants == 0:
                continue

            # --- Granular Education Logic ---
            if category == 'education':
                # Map scores to required class levels
                # "Crèche / Assistante Maternelle", "Maternelle", "Elémentaire", "Collège", "Lycée"
                
                # Crèche / Petite Enfance
                if col in ['edu_petite_enfance_scaled', 'edu_creches_scaled']:
                    if 'Crèche / Assistante Maternelle' not in config.classe_enfants:
                        continue
                
                # Maternelle
                elif col == 'edu_maternelle_scaled':
                    if 'Maternelle' not in config.classe_enfants:
                        continue
                
                # Elémentaire
                elif col == 'edu_elementaire_scaled':
                    if 'Elémentaire' not in config.classe_enfants:
                        continue
                
                # Collège
                elif col == 'edu_college_scaled':
                    if 'Collège' not in config.classe_enfants:
                        continue
                
                # Lycée
                elif col == 'edu_lycee_scaled':
                    if 'Lycée' not in config.classe_enfants:
                        continue

                # Class Closure Risk (Fermetures) - Relevant for Primary School (Maternelle + Elem)
                elif col == 'edu_classes_ferm_scaled':
                    if not any(lvl in config.classe_enfants for lvl in ['Maternelle', 'Elémentaire']):
                        continue

            score_commune = df[col]
            # Check if a corresponding binome score exists
            if f'{col}_binome' in df.columns:
                score_voisin = df[f'{col}_binome'] * (1 - binome_penalty)
                
                s_commune = score_commune.fillna(0)
                s_voisin = score_voisin.fillna(0)

                effective_score = np.maximum(s_commune, s_voisin)
                max_scores.append(effective_score)
            else:  # This criterion is not applicable to binomes
                max_scores.append(score_commune)

            # Get Weight
            base_weight = scores_cat[scores_cat.score == col]['weight'].iloc[0]
            dynamic_multiplier = config.criteria_weights.get(col, 1.0)
            weights.append(base_weight * dynamic_multiplier)

        # Weighted Average Calculation
        scores_df = pd.concat(max_scores, axis=1)
        weights_array = np.array(weights)
        
        mask = scores_df.notna()
        weighted_sum = (scores_df.fillna(0) * weights_array).sum(axis=1)
        weights_sum = (mask * weights_array).sum(axis=1)
        
        category_score = weighted_sum / weights_sum.replace(0, np.nan)
        
        df[f'{category}_cat_score'] = category_score

    return df

def compute_weighted_score(df: pd.DataFrame, config: 'ScoringConfig') -> pd.Series:
    """Computes the final weighted score for each row based on category scores and user-defined weights."""
    category_scores = [col for col in df.columns if col.endswith('_cat_score')]

    total_score = pd.Series(0.0, index=df.index)
    total_weight = pd.Series(0.0, index=df.index)

    for cat_score_col in category_scores:
        category_name = cat_score_col.split('_')[0]

        if category_name == 'education' and config.nb_enfants == 0:
            continue
        if category_name == 'sante' and config.besoin_sante == 'Aucun':
            continue

        weight_key = f'poids_{category_name}'
        weight = getattr(config, weight_key, 0)

        if weight > 0:
            score_series = df[cat_score_col]
            valid_mask = score_series.notna()
            
            total_score += score_series.fillna(0) * weight * valid_mask.astype(float)
            total_weight += weight * valid_mask.astype(float)

    final_score = total_score / total_weight
    return final_score.fillna(0)

def select_best_score_per_commune(df: pd.DataFrame) -> pd.DataFrame:
    """For each commune, keeps only the best scoring result.
    In case of ties (common with 100% penalty), prefer the Monome (binome=False).
    """
    if 'weighted_score' in df.columns:
        # Sort by Score (Desc) then by Binome (Asc -> False first)
        sort_cols = ['weighted_score']
        ascending = [False]
        
        if 'binome' in df.columns:
            sort_cols.append('binome')
            ascending.append(True)
            
        return df.sort_values(sort_cols, ascending=ascending).groupby('codgeo').head(1)
    return df

def aggregate_scores_by_bassin_de_vie(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregates commune scores at the 'bassin de vie' level."""
    df_agg = df.copy()

    if 'polygon' in df_agg.columns:
        df_agg = df_agg.drop(columns='polygon')

    def weighted_avg(group, score_col, weight_col='population'):
        weight_sum = group[weight_col].sum()
        if weight_sum == 0:
            return 0
        try:
             # Ensure numeric types
             s = pd.to_numeric(group[score_col], errors='coerce').fillna(0)
             w = pd.to_numeric(group[weight_col], errors='coerce').fillna(0)
             return (s * w).sum() / weight_sum
        except:
             return 0

    score_cols = [col for col in df_agg.columns if '_score' in col or '_scaled' in col]

    agg_dict = {
        col: lambda x, col=col: weighted_avg(df_agg.loc[x.index], col) for col in score_cols
    }

    # Basic Aggregations
    agg_dict['population'] = 'sum'
    if 'epci_nom' in df_agg.columns:
        agg_dict['epci_nom'] = lambda x: ', '.join(x.dropna().unique())

    # Complex Aggregations
    def get_url_from_most_populous(series):
        group_df = df_agg.loc[series.index]
        if not group_df.empty and 'population' in group_df.columns:
            most_populous_codgeo = group_df['population'].idxmax()
            return group_df.loc[most_populous_codgeo, series.name]
        return None

    if 'url_odis' in df_agg.columns:
        agg_dict['url_odis'] = get_url_from_most_populous
    if 'url_wikipedia' in df_agg.columns:
        agg_dict['url_wikipedia'] = get_url_from_most_populous

    def aggregate_unique_list(series):
        all_items = [item for sublist in series.dropna() for item in sublist]
        return sorted(list(set(all_items)))

    if 'be_libfap_top' in df_agg.columns:
        agg_dict['be_libfap_top'] = aggregate_unique_list
    if 'noms_formations' in df_agg.columns:
        agg_dict['noms_formations'] = aggregate_unique_list

    # Perform Grouping
    df_bv = df_agg.groupby([cfg.BV_CODE_COL, cfg.BV_NAME_COL]).agg(agg_dict)

    # Communes List - Semantic aggregation
    # We want a list of codgeo (which is the index of df_agg) per BV.
    # We reset index to access codgeo as a column, then groupby and list.
    communes_agg = df_agg.reset_index().groupby([cfg.BV_CODE_COL, cfg.BV_NAME_COL])['codgeo'].agg(list).rename('communes')

    # Join this series to the main BV dataframe
    # The indices [BV_CODE, BV_NAME] match perfectly.
    df_bv = df_bv.join(communes_agg)

    df_bv.reset_index(inplace=True)
    df_bv.rename(columns={cfg.BV_NAME_COL: 'libgeo'}, inplace=True)

    # Schema Consistency
    df_bv['binome'] = False
    df_bv['libgeo_binome'] = None
    df_bv['polygon_binome'] = None

    return df_bv
