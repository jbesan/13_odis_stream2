# coding: utf-8
"""
Scoring module for the ODIS application.

This module contains functions to calculate scores for communes based on various criteria
such as employment, housing, education, and mobility.
"""
from typing import List, Dict, Set, Any, Optional, Union, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
from sklearn import preprocessing
from pyproj import Transformer
from shapely.ops import transform

import config as cfg
from config import ScoringConfig


# --- Helper Functions ---

def get_bounds(score_id: str, scores_cat: pd.DataFrame, global_stats: Dict[str, Dict[str, float]]) -> Tuple[float, float]:
    """Retrieves the min and max bounds for a given score."""
    # 1. Check global computed stats
    if score_id in global_stats:
        return global_stats[score_id]['min'], global_stats[score_id]['max']
    
    # 2. Check hardcoded bounds in scores_cat
    row = scores_cat[scores_cat['score'] == score_id]
    if not row.empty:
        min_b = row.iloc[0]['min_bound']
        max_b = row.iloc[0]['max_bound']
        # Return values directly, converting to float (NaN stays NaN)
        # We use float() but handle None/NaN safely if needed, though pandas usually handles it.
        # If min_b is None/NaN, we want to return np.nan
        val_min = float(min_b) if pd.notna(min_b) else np.nan
        val_max = float(max_b) if pd.notna(max_b) else np.nan
        return val_min, val_max
            
    # 3. Default fallback (should not happen if config is correct)
    return 0.0, 1.0

def min_max_scale(series: pd.Series, min_val: float, max_val: float) -> pd.Series:
    """Scales a series to [0, 1] using min-max scaling."""
    if max_val == min_val:
        return pd.Series(0.0, index=series.index)
    return ((series - min_val) / (max_val - min_val)).clip(0, 1)


# --- Scoring Pipeline Functions ---

def add_distance_to_current_loc(df: gpd.GeoDataFrame, current_codgeo: str, df_all: Optional[gpd.GeoDataFrame] = None) -> gpd.GeoDataFrame:
    """Computes the distance from each commune in the dataframe to a reference commune."""
    
    # Optimization: Use pre-calculated centroids if available
    if 'centroid' in df.columns:
        centroids_proj = df['centroid'].to_crs(cfg.PROJECTED_CRS)
        
        target_centroid = None
        if current_codgeo in df.index:
            target_centroid = centroids_proj.loc[current_codgeo]
        elif df_all is not None and current_codgeo in df_all.index and 'centroid' in df_all.columns:
             target_centroid = df_all.loc[current_codgeo, 'centroid']
             # Ensure it's projected
             # We assume df_all has centroid in same CRS as df (usually 4326)
             # So we need to project it
             target_centroid = _transform_point(target_centroid, df_all.crs, cfg.PROJECTED_CRS)
        
        if target_centroid is None:
             # Fallback or error?
             # If we can't find the start point, we can't calculate distance.
             # But maybe we should fall back to the non-centroid method?
             pass
        else:
            if isinstance(target_centroid, (pd.Series, gpd.GeoSeries)):
                target_centroid = target_centroid.iloc[0]

            distances = centroids_proj.distance(target_centroid)
            
            df_result = df.copy()
            df_result['dist_current_loc'] = distances
            return df_result

    # Fallback to original method if centroid is missing or we fell through
    df_projected = df.to_crs(cfg.PROJECTED_CRS)
    
    zone_recherche = None
    if current_codgeo in df_projected.index:
        zone_recherche = df_projected.loc[[current_codgeo]].copy()
    elif df_all is not None and current_codgeo in df_all.index:
        zone_recherche = df_all.loc[[current_codgeo]].to_crs(cfg.PROJECTED_CRS).copy()
        
    if zone_recherche is not None:
        zone_recherche['geometry'] = zone_recherche.centroid

        df_with_dist = df_projected.sjoin_nearest(
            zone_recherche, distance_col="dist_current_loc"
        )[['dist_current_loc']]

        return df.merge(df_with_dist, left_index=True, right_index=True, how='left')
    
    return df # Should probably raise error if we can't calculate distance


def filter_by_distance(df: pd.DataFrame, max_distance_km: float) -> pd.DataFrame:
    """Filters a dataframe to keep only rows within a given distance."""
    return df[df.dist_current_loc < max_distance_km * 1000].copy()

def _transform_point(point, src_crs, dst_crs):
    """Helper to transform a single shapely point."""
    project = Transformer.from_crs(src_crs, dst_crs, always_xy=True).transform
    return transform(project, point)

def filter_communes(
    df: gpd.GeoDataFrame,
    start_commune: gpd.GeoSeries,
    loc_type: str,
    loc_code: str,
    loc_distance_km: int
) -> gpd.GeoDataFrame:
    """Filters the communes dataframe based on the selected mobility criteria."""
    if loc_type == 'distance':
        if 'centroid' in df.columns:
            centroids_proj = df['centroid'].to_crs(cfg.PROJECTED_CRS)
            
            if 'centroid' in start_commune:
                start_centroid = start_commune['centroid'].iloc[0]
            else:
                start_centroid = start_commune.centroid.iloc[0]
            
            # Use direct transformation to avoid DeprecationWarning from pyproj/geopandas interaction on single points
            start_centroid_proj = _transform_point(start_centroid, df.crs, cfg.PROJECTED_CRS)

            distances = centroids_proj.distance(start_centroid_proj)
            mask = distances <= loc_distance_km * 1000
            filtered_df = df[mask].copy()
            filtered_df['dist_current_loc'] = distances[mask]
            return filtered_df
        else:
            df_proj = df.to_crs(cfg.PROJECTED_CRS)
            start_centroid = start_commune.centroid.iloc[0]
            start_centroid_proj = _transform_point(start_centroid, df.crs, cfg.PROJECTED_CRS)
            
            distances = df_proj.centroid.distance(start_centroid_proj)
            return df[distances <= loc_distance_km * 1000].copy()

    elif loc_type == 'departement':
        return df[df['dep_code'] == loc_code].copy()
    elif loc_type == 'region':
        return df[df['reg_code'] == loc_code].copy()

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
        if 'centroid' in bv_gdf.columns:
            centroids_proj = bv_gdf['centroid'].to_crs(cfg.PROJECTED_CRS)
            
            if 'centroid' in start_commune:
                start_centroid = start_commune['centroid'].iloc[0]
            else:
                start_centroid = start_commune.centroid.iloc[0]

            start_centroid_proj = _transform_point(start_centroid, bv_gdf.crs, cfg.PROJECTED_CRS)

            distances = centroids_proj.distance(start_centroid_proj)
            return bv_gdf[distances <= loc_distance_km * 1000].copy()
        else:
            bv_proj = bv_gdf.to_crs(cfg.PROJECTED_CRS)
            start_centroid = start_commune.centroid.iloc[0]
            start_centroid_proj = _transform_point(start_centroid, bv_gdf.crs, cfg.PROJECTED_CRS)
            
            distances = bv_proj.centroid.distance(start_centroid_proj)
            return bv_gdf[distances <= loc_distance_km * 1000].copy()

    elif loc_type in ['departement', 'region']:
        area_geometry = area_gdf.loc[(loc_type, loc_code)].polygon
        intersecting_mask = bv_gdf.intersects(area_geometry)
        return bv_gdf[intersecting_mask].copy()

    return gpd.GeoDataFrame()




def compute_inclusion_score(
    df: gpd.GeoDataFrame,
    config: ScoringConfig,
    incl_index: pd.DataFrame,
    associations_data: pd.DataFrame,
    scores_cat: pd.DataFrame, # Added
    global_stats: Dict[str, Dict[str, float]] # Added
) -> gpd.GeoDataFrame:
    """Computes the new Inclusion score based on 3 components."""
    
    df = df.copy()
    
    # --- 1. Socle Administratif ---
    # Pre-calculated in prescoring.py
    if 'inc_socle_admin_score' not in df.columns:
         # Fallback if prescoring didn't run or failed (should be 0.0 from prescoring)
         df['inc_socle_admin_score'] = 0.0

    # --- 2. Lien Social ---
    # Calculate density of associations in CORE categories
    # associations_data has MultiIndex (codgeo, id_waldec) -> count
    
    # Filter for core codes (handling prefixes)
    # Note: associations_data['id_waldec'] should be strings.

    # Normalize
    # We use the pre-calculated score from prescoring
    if 'inc_lien_social_score' not in df.columns:
         raise ValueError("Missing pre-calculated score: inc_lien_social_score")
    
    # --- 3. Affinité ---
    selected_interests = config.affinite_selection
    if selected_interests:
        # Gather all relevant WALDEC codes
        interest_codes = set()
        for interest in selected_interests:
            if interest in cfg.WALDEC_INTERESTS_MAPPING:
                interest_codes.update(cfg.WALDEC_INTERESTS_MAPPING[interest])
        
        if interest_codes:
            interest_prefixes = tuple(interest_codes)
            affinite_assos = associations_data[associations_data['id_waldec'].astype(str).str.startswith(interest_prefixes, na=False)]
            affinite_counts = affinite_assos.groupby('codgeo')['count'].sum()
            
            df = df.join(affinite_counts.rename('affinite_count'), how='left')
            df['affinite_count'] = df['affinite_count'].fillna(0)
            
            df['affinite_density'] = (df['affinite_count'] * 1000) / df['population']
            
            min_b, max_b = get_bounds('inc_affinite_score', scores_cat, global_stats)
            df['inc_affinite_score'] = min_max_scale(df['affinite_density'].fillna(0), min_b, max_b)
        else:
            df['inc_affinite_score'] = 0.0
    else:
        df['inc_affinite_score'] = 0.0

    # --- 4. Services Spécifiques ---
    # Uses 'besoins_autres' which is a list of slugs [category--service, ...]
    besoins_autres = config.besoins_autres
    # Flatten the needed services into a set of "category--service" keys
    needed_extra_services = set()
    for slug in besoins_autres:
        needed_extra_services.add(slug)
            
    if needed_extra_services:
        # We need to join with incl_index again or reuse the previous join if possible.
        # incl_index has 'key' = "category_service"
        # We can reuse the logic from Socle Admin but with different keys
        # Helper to count matches with substring support
        def count_extra_matches(available_set):
            if not isinstance(available_set, set): return 0
            matches = 0
            for needed in needed_extra_services:
                if any(needed in av for av in available_set):
                    matches += 1
            return matches

        if 'key' not in df.columns: # Should be there if Socle Admin ran, but let's be safe
             df_merged = df.join(incl_index, how='left')
             df['extra_match_count'] = df_merged['key'].apply(count_extra_matches)
        else:
            # If df already has 'key' from previous join (unlikely as join adds columns to left, not right)
            # Actually df.join(incl_index) adds columns from incl_index to df.
            # If we did it in step 1, df has 'key'.
            # Let's check if we did step 1.
            if config.socle_admin_selection:
                 # df already has 'key' column from the join in step 1
                 df['extra_match_count'] = df['key'].apply(count_extra_matches)
            else:
                 # Need to join
                 df_merged = df.join(incl_index, how='left')
                 df['extra_match_count'] = df_merged['key'].apply(count_extra_matches)
        
        df['inc_extra_services_score'] = df['extra_match_count'] / len(needed_extra_services)
    else:
        df['inc_extra_services_score'] = 0.0

    # --- Global Inclusion Score ---
    # Removed pre-aggregation. Components are now aggregated by category in compute_category_scores.
    
    return df


def compute_criteria_scores(
    df: gpd.GeoDataFrame,
    config: ScoringConfig,
    incl_index: pd.DataFrame,
    df_all_communes: gpd.GeoDataFrame,
    associations_data: pd.DataFrame, # Added argument
    bmo_vertical: pd.DataFrame, # Added argument
    formations_data: pd.DataFrame, # Added argument
    codformations_index: pd.DataFrame, # Added argument
    scores_cat: pd.DataFrame, # Added
    global_stats: Dict[str, Dict[str, float]] # Added
) -> gpd.GeoDataFrame:
    """Computes individual scores for each criterion based on user preferences.

    All scores are normalized between 0 and 1 using a QuantileTransformer.
    """
    df = df.copy()

    # Determine the optimal n_quantiles for the transformer to avoid warnings.
    n_samples = len(df)
    # n_quantiles cannot be greater than the number of samples.
    n_quantiles = min(n_samples, 1000)

    # Use QuantileTransformer to normalize scores to a uniform distribution [0, 1].
    transformer = preprocessing.QuantileTransformer(
        output_distribution="uniform",
        n_quantiles=n_quantiles,
        random_state=42
    )

    # --- EMPLOI ---
    # met_ratio is pre-calculated in data_loader
    if 'met_scaled' not in df.columns:
        raise ValueError("Missing pre-calculated score: met_scaled")

    # Pre-process BMO data for the current set of communes
    # We need to know which FAP codes are available for each commune in df
    # bmo_vertical has columns: codgeo, fap_code (and maybe others)
    # Filter bmo_vertical to only include communes in df
    relevant_bmo = bmo_vertical[bmo_vertical['codgeo'].isin(df.index)]
    
    # Create a mapping: codgeo -> set of available FAP codes
    # This is much faster than applying per row
    commune_fap_map = relevant_bmo.groupby('codgeo')['fap_code'].apply(set).to_dict()

    # Job categories that match user preferences
    for i in range(config.nb_adultes):
        adult_key = f'adult{i+1}'
        if config.codes_metiers[i]:
            prefs_metiers = set(config.codes_metiers[i])
            
            # Calculate intersection size
            # We use map to get the set of available metiers for each commune
            # Then intersect with prefs
            
            def get_match_count(codgeo):
                available = commune_fap_map.get(codgeo, set())
                return len(available.intersection(prefs_metiers))
            
            df[f'met_match_{adult_key}'] = df.index.map(get_match_count)
            
            # Store matched codes for display/debug if needed (optional, might be heavy)
            # df[f'met_match_codes_{adult_key}'] = df.index.map(lambda x: list(commune_fap_map.get(x, set()).intersection(prefs_metiers)))

            
            # Dynamic max bound based on number of selected items
            min_b, max_b = get_bounds(f'met_match_{adult_key}_scaled', scores_cat, global_stats)
            if pd.isna(max_b):
                 max_b = float(len(prefs_metiers))
            
            df[f'met_match_{adult_key}_scaled'] = min_max_scale(df[f'met_match_{adult_key}'].fillna(0), min_b, max_b)

    # Training centers that match
    # We need to know which Formation codes are available for each commune in df
    # formations_data has columns: codgeo, formation_code, count
    relevant_formations = formations_data[formations_data['codgeo'].isin(df.index)]
    
    # Create a mapping: codgeo -> set of available Formation codes
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
            
            # Dynamic max bound based on number of selected items
            min_b, max_b = get_bounds(f'form_match_{adult_key}_scaled', scores_cat, global_stats)
            if pd.isna(max_b):
                 max_b = float(len(prefs_formations))

            df[f'form_match_{adult_key}_scaled'] = min_max_scale(df[f'form_match_{adult_key}'].fillna(0), min_b, max_b)
            
    # Aggregate formation names for display (union of all adults)
    # We want 'noms_formations' column containing list of labels
    if codformations_index is not None and not codformations_index.empty:
        def get_all_formation_labels(row):
            codes = set()
            for i in range(config.nb_adultes):
                adult_key = f'adult{i+1}'
                col = f'form_match_codes_{adult_key}'
                if col in row and isinstance(row[col], list):
                    codes.update(row[col])
            
            labels = []
            for c in codes:
                if c in codformations_index.index:
                    labels.append(codformations_index.loc[c, 'label'])
                else:
                    labels.append(c)
            return labels

        df['noms_formations'] = df.apply(get_all_formation_labels, axis=1)
    else:
        df['noms_formations'] = [[] for _ in range(len(df))]

    # --- HEBERGEMENT / LOGEMENT ---

    def drop_score_cols(df, col_name):
        # Build list of cols to drop including potential binome cols
        cols_to_drop = [col_name, f"{col_name}_binome"]
        # Drop only those present to avoid errors, although errors='ignore' handles it.
        # We use strict list for clarity.
        existing_cols = [c for c in cols_to_drop if c in df.columns]
        if existing_cols:
            df.drop(columns=existing_cols, inplace=True)

    # 1. Taux de Vacance (log_vac_scaled)
    # Used if "Location" is selected in EITHER Hébergement OR Logement
    if config.hebergement == 'Location' or config.logement == 'Location':
        pass
    else:
        drop_score_cols(df, 'log_vac_scaled')

    # 2. Logement Social (log_soc_inoc_scaled)
    # Used ONLY if "Logement Social" is selected in Logement
    if config.logement == 'Logement Social':
        pass
    else:
        drop_score_cols(df, 'log_soc_inoc_scaled')

    # 3. Occupation (log_occup_scaled)
    # Used ONLY if "Chez l'habitant" is selected in Hébergement
    if config.hebergement == "Chez l'habitant":
        pass
    else:
        drop_score_cols(df, 'log_occup_scaled')

    # --- EDUCATION ---
    if config.nb_enfants > 0:
        if config.classe_enfants:
            # New Education Score: Average School Size (Proxy for Closure Risk)
            # We want to minimize risk, so we want larger schools/classes.
            # Score = Normalized(Avg Size).
                
            min_b, max_b = get_bounds('edu_classes_ferm_scaled', scores_cat, global_stats)
            # If max_b is not defined, we might need a reasonable max (e.g. 300 students per school?)
            # min_max_scale handles it if we have global stats.
            if 'edu_classes_ferm_scaled' not in df.columns:
                raise ValueError("Missing pre-calculated score: edu_classes_ferm_scaled")
            
            # --- Granular Scoring ---
            
            # 1. Petite Enfance (Crèche / Assistante Maternelle)
            if 'Crêche / Assistante Maternelle' in config.classe_enfants:
                # Use pre-calculated scaled score
                if 'edu_petite_enfance_scaled' in df.columns:
                    # Do NOT fillna(0). Keep NaNs to exclude them later.
                    pass
                else:
                    df['edu_petite_enfance_scaled'] = np.nan

            # 2. Schools (Maternelle, Elementaire, College, Lycee)
            # We check presence (count > 0) for each type if selected
            
            # Maternelle
            if 'Maternelle' in config.classe_enfants:
                if 'edu_maternelle_scaled' not in df.columns:
                    raise ValueError("Missing pre-calculated score: edu_maternelle_scaled")
                
            # Elementaire
            if 'Elémentaire' in config.classe_enfants:
                if 'edu_elementaire_scaled' not in df.columns:
                    raise ValueError("Missing pre-calculated score: edu_elementaire_scaled")
                
            # College
            if 'Collège' in config.classe_enfants:
                if 'edu_college_scaled' not in df.columns:
                    raise ValueError("Missing pre-calculated score: edu_college_scaled")
                
            # Lycee
            if 'Lycée' in config.classe_enfants:
                if 'edu_lycee_scaled' not in df.columns:
                    raise ValueError("Missing pre-calculated score: edu_lycee_scaled")

            # Remove old score column if it exists to avoid confusion
            if 'edu_structures_scaled' in df.columns:
                df.drop(columns=['edu_structures_scaled'], inplace=True)
                
        else:
            df['edu_classes_ferm_scaled'] = 0.0
            # If no kids, we don't calculate any education scores.
            # They won't be in the df, so they won't be averaged.
    # Else: Education criteria are not calculated/added to df

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
            # Already scaled (0 or 1)
            df['sante_structures_scaled'] = df[target_col]
        elif target_col:
             raise ValueError(f"Missing pre-calculated score: {target_col}")
        else:
            df['sante_structures_scaled'] = 0.0
    # Else: Sante criteria are not calculated/added to df

    # --- MOBILITE ---
    # 1. Distance from the current location
    if isinstance(config.loc_distance_km, int):
        df['mob_dist_scaled'] = (1 - df['dist_current_loc'] / (config.loc_distance_km * 1000))
    # 2. Is the commune in the same EPCI as the current one?
    # We get the EPCI from the original, unfiltered dataframe to avoid KeyErrors
    current_epci = df_all_communes.loc[config.commune_actuelle]['epci_code']
    df['mob_epci_scaled'] = np.where(df['epci_code'] == current_epci, 1, 0)

    # --- INCLUSION ---
    df = compute_inclusion_score(df, config, incl_index, associations_data, scores_cat, global_stats)

    # Population as a direct score for inclusion
    if 'inc_population_scaled' not in df.columns:
        raise ValueError("Missing pre-calculated score: inc_population_scaled")

    # Political orientation score
    if 'inc_pol_scaled' not in df.columns:
        raise ValueError("Missing pre-calculated score: inc_pol_scaled")

    return df


def add_neighbor_scores(df_search: gpd.GeoDataFrame, scores_cat: pd.DataFrame) -> pd.DataFrame:
    """Expands the dataframe to include data from neighboring communes ('voisins').

    This creates 'binome' pairs (commune + neighbor) and 'monome' cases (commune itself).
    """
    # Define columns needed for binome analysis.
    binome_columns = (
        ['codgeo', 'libgeo', 'polygon', 'epci_code', 'epci_nom']
        + scores_cat[scores_cat.incl_binome]['score'].to_list()
        + scores_cat[scores_cat.incl_binome]['metric'].to_list()
    )
    # Remove duplicates to avoid DataFrame creation on merge for same-named columns
    binome_columns = list(dict.fromkeys(binome_columns))
    binome_columns = [col for col in binome_columns if col in df_search.columns]
    df_binomes = df_search[binome_columns].copy()

    # Create a series with the commune itself and its neighbors.
    # Using .copy() on df_search prevents SettingWithCopyWarning.
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
    """Aggregates individual criteria scores into category scores (e.g., 'emploi_cat_score').

    For binomes, it considers the max score between the commune and its neighbor,
    applying a penalty to the neighbor's score.
    """
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

        # For each score, calculate the effective score, which is the max of
        # (score_commune, score_voisin * (1 - penalty)).
        # This is done for all criteria in the category.
        max_scores = []
        weights = []

        for col in score_cols:
            score_commune = df[col]
            # Check if a corresponding binome score exists
            if f'{col}_binome' in df.columns:
                score_voisin = df[f'{col}_binome'] * (1 - binome_penalty)
                
                s_commune = score_commune.fillna(0)
                s_voisin = score_voisin.fillna(0)

                effective_score = np.maximum(s_commune, s_voisin)
                max_scores.append(effective_score)
            else:  # This criterion is not applicable to binomes
                # Preserve NaNs for aggregation
                max_scores.append(score_commune)

            # Get Weight
            # We assume scores_cat has 'weight' column (added in data_loader)
            base_weight = scores_cat[scores_cat.score == col]['weight'].iloc[0]
            dynamic_multiplier = config.criteria_weights.get(col, 1.0)
            weights.append(base_weight * dynamic_multiplier)

        # Weighted Average Calculation
        scores_df = pd.concat(max_scores, axis=1)
        weights_array = np.array(weights)
        
        # 1. Mask of valid values (not NaN)
        mask = scores_df.notna()
        
        # 2. Weighted Sum of Scores (NaNs treated as 0 for sum)
        # We multiply by mask to ensure NaNs don't contribute (redundant if fillna(0) but safer)
        weighted_sum = (scores_df.fillna(0) * weights_array).sum(axis=1)
        
        # 3. Sum of Weights for valid values
        weights_sum = (mask * weights_array).sum(axis=1)
        
        # 4. Divide
        # If weights_sum is 0 (all values NaN), result will be inf or NaN. We want NaN.
        category_score = weighted_sum / weights_sum.replace(0, np.nan)
        
        df[f'{category}_cat_score'] = category_score

    return df


def compute_weighted_score(df: pd.DataFrame, config: 'ScoringConfig') -> pd.Series:
    """Computes the final weighted score for each row based on category scores and user-defined weights."""
    category_scores = [col for col in df.columns if col.endswith('_cat_score')]

    # Initialize total score and total weight vectors (Series) to handle row-specific weights
    total_score = pd.Series(0.0, index=df.index)
    total_weight = pd.Series(0.0, index=df.index)

    for cat_score_col in category_scores:
        # e.g., 'emploi_cat_score' -> 'emploi'
        category_name = cat_score_col.split('_')[0]

        # Conditional exclusion logic
        if category_name == 'education' and config.nb_enfants == 0:
            continue
        if category_name == 'sante' and config.besoin_sante == 'Aucun':
            continue

        weight_key = f'poids_{category_name}'
        weight = getattr(config, weight_key, 0)

        if weight > 0:
            # Get the score series
            score_series = df[cat_score_col]
            
            # Identify valid (non-NaN) scores
            valid_mask = score_series.notna()
            
            # Add weighted score where valid
            # fillna(0) is used for the addition, but we only add weight where valid
            total_score += score_series.fillna(0) * weight * valid_mask.astype(float)
            
            # Add weight to total_weight only where score is valid
            total_weight += weight * valid_mask.astype(float)

    # Avoid division by zero
    final_score = total_score / total_weight
    return final_score.fillna(0)


def select_best_score_per_commune(df: pd.DataFrame) -> pd.DataFrame:
    """For each commune, keeps only the best scoring result (whether it's a monome or a binome)."""
    return df.sort_values('weighted_score', ascending=False).groupby('codgeo').head(1)


def aggregate_scores_by_bassin_de_vie(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregates commune scores at the 'bassin de vie' level."""
    df_agg = df.copy()

    # Explicitly drop the old geometry column before aggregating.
    if 'polygon' in df_agg.columns:
        df_agg = df_agg.drop(columns='polygon')

    # Define a weighted average function for numerical scores
    def weighted_avg(group, score_col, weight_col='population'):
        # Ensure the weight column sum is not zero to avoid division by zero
        weight_sum = group[weight_col].sum()
        if weight_sum == 0:
            return 0
        return (group[score_col] * group[weight_col]).sum() / weight_sum

    # --- Aggregation Dictionary ---
    # 1. Scores (Weighted Average by default)
    score_cols = [col for col in df_agg.columns if '_score' in col or '_scaled' in col]
    agg_dict = {
        col: lambda x, col=col: weighted_avg(df_agg.loc[x.index], col) for col in score_cols
    }

    # --- Basic Aggregations ---
    agg_dict['population'] = 'sum'
    if 'epci_nom' in df_agg.columns:
        agg_dict['epci_nom'] = lambda x: ', '.join(x.dropna().unique())

    # --- Complex Aggregations ---
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

    # --- Perform Grouping and Aggregation ---
    # First, aggregate the main data
    df_bv = df_agg.groupby([cfg.BV_CODE_COL, cfg.BV_NAME_COL]).agg(agg_dict)

    # Then, separately aggregate the codgeo into a list
    communes_agg = df_agg.groupby([cfg.BV_CODE_COL, cfg.BV_NAME_COL]).apply(
        lambda x: list(x.index), include_groups=False
    ).rename('communes')

    # Merge the commune list back into the main aggregated dataframe
    df_bv = df_bv.merge(communes_agg, left_index=True, right_index=True)

    df_bv.reset_index(inplace=True)
    df_bv.rename(columns={cfg.BV_NAME_COL: 'libgeo'}, inplace=True)

    # Add columns for schema consistency
    df_bv['binome'] = False
    df_bv['libgeo_binome'] = None
    df_bv['polygon_binome'] = None

    return df_bv


# --- Main Orchestration Function ---

def compute_odis_score(
    df_search: gpd.GeoDataFrame,
    df_all_communes: gpd.GeoDataFrame,
    scores_cat: pd.DataFrame,
    config: 'ScoringConfig',
    incl_index: pd.DataFrame,
    associations_data: pd.DataFrame, # Added argument
    bmo_vertical: pd.DataFrame, # Added argument
    formations_data: pd.DataFrame, # Added argument
    codformations_index: pd.DataFrame, # Added argument
    global_stats: Dict[str, Dict[str, float]], # Added
    use_binomes: bool = True # New argument
) -> pd.DataFrame:
    """Main function that orchestrates the entire scoring pipeline on a pre-filtered dataframe."""
    if df_search.empty:
        return df_search.copy()  # Return a copy to avoid warnings

    # We still need the distance for some scores, calculate it if not present
    if 'dist_current_loc' not in df_search.columns:
        odis_search = add_distance_to_current_loc(
            df_search, current_codgeo=config.commune_actuelle, df_all=df_all_communes
        )
    else:
        odis_search = df_search.copy()

    # 4. Compute all individual criteria scores based on preferences.
    odis_scored = compute_criteria_scores(
        odis_search,
        config=config,
        incl_index=incl_index,
        df_all_communes=df_all_communes,
        associations_data=associations_data, # Passed down
        bmo_vertical=bmo_vertical, # Passed down
        formations_data=formations_data, # Passed down
        codformations_index=codformations_index, # Passed down
        scores_cat=scores_cat, # Passed down
        global_stats=global_stats # Passed down
    )

    # 5. Expand the dataframe to include neighbor data (creating monomes and binomes).
    if use_binomes:
        odis_exploded = add_neighbor_scores(odis_scored, scores_cat)
    else:
        # If not using binomes, we just keep the monomes (the rows themselves)
        # We need to ensure the structure matches what compute_category_scores expects
        odis_exploded = odis_scored.copy()
        # Add dummy binome columns if needed by downstream functions, or ensure downstream handles missing binome cols
        # compute_category_scores checks for f'{col}_binome' existence, so it should be fine.
        
        # However, select_best_score_per_commune expects 'weighted_score' and might group by codgeo.
        # If we don't explode, we have 1 row per codgeo.
        pass

    # 6. Aggregate criteria scores into category scores, handling the binome logic.
    odis_exploded = compute_category_scores(
        odis_exploded,
        scores_cat=scores_cat,
        binome_penalty=config.binome_penalty,
        config=config
    )

    # 7. Compute the final weighted score for each commune/binome pair.
    odis_exploded['weighted_score'] = compute_weighted_score(odis_exploded, config=config)

    # 8. For each commune, keep only the best result (could be monome or a binome).
    # If use_binomes is False, this effectively just returns the single row per commune.
    odis_search_best = select_best_score_per_commune(odis_exploded)

    return odis_search_best


def run_scoring_pipeline(
    config: ScoringConfig,
    df_all_communes: gpd.GeoDataFrame,
    df_bv_geo: gpd.GeoDataFrame,
    df_area_geo: gpd.GeoDataFrame,
    scores_cat: pd.DataFrame,
    incl_index: pd.DataFrame,
    associations_data: pd.DataFrame, # Added argument
    bmo_vertical: pd.DataFrame, # Added argument
    formations_data: pd.DataFrame, # Added argument
    codformations_index: pd.DataFrame, # Added argument
    global_stats: Dict[str, Dict[str, float]], # Added
    view_level: str
) -> Tuple[gpd.GeoDataFrame, gpd.GeoDataFrame]:
    """
    Orchestrates the full scoring pipeline: filtering -> scoring -> aggregation.
    
    Returns:
        A tuple containing:
        - processed_gdf: The final aggregated/sorted results ready for display.
        - unaggregated_gdf: The raw scored communes (useful for map layers).
    """
    start_commune = df_all_communes.loc[[config.commune_actuelle]]
    loc_type = 'distance' if isinstance(config.loc_distance_km, int) else config.loc_distance_km
    
    # --- Filtering ---
    if view_level == 'Communes':
        loc_col = 'dep_code' if loc_type == 'departement' else 'reg_code'
        communes_to_score = filter_communes(
            df=df_all_communes,
            start_commune=start_commune,
            loc_type=loc_type,
            loc_code=start_commune.iloc[0][loc_col] if loc_type != 'distance' else None,
            loc_distance_km=config.loc_distance_km if loc_type == 'distance' else None
        )
    else: # Bassins de vie
        loc_col = 'dep_code' if loc_type == 'departement' else 'reg_code'
        filtered_bvs = filter_bassins_de_vie(
            bv_gdf=df_bv_geo,
            start_commune=start_commune,
            loc_type=loc_type,
            loc_code=start_commune.iloc[0][loc_col] if loc_type != 'distance' else None,
            loc_distance_km=config.loc_distance_km if loc_type == 'distance' else None,
            area_gdf=df_area_geo
        )
        # Get all communes that belong to those BVs
        bv_ids_to_keep = filtered_bvs.index.tolist()
        
        # Exclude the BV of the current commune
        current_bv = start_commune.iloc[0][cfg.BV_CODE_COL]
        
        if current_bv in bv_ids_to_keep:
            bv_ids_to_keep.remove(current_bv)
  
        communes_to_score = df_all_communes[df_all_communes[cfg.BV_CODE_COL].isin(bv_ids_to_keep)]

    # --- Scoring ---
    # Disable binomes for 'Bassins de vie' view to ensure aggregation uses monome scores
    use_binomes = (view_level == 'Communes')
    
    odis_scored = compute_odis_score(
        df_search=communes_to_score,
        df_all_communes=df_all_communes,
        scores_cat=scores_cat,
        config=config,
        incl_index=incl_index,
        associations_data=associations_data, # Passed down
        bmo_vertical=bmo_vertical, # Passed down
        formations_data=formations_data, # Passed down
        codformations_index=codformations_index, # Passed down
        global_stats=global_stats, # Passed down
        use_binomes=use_binomes
    )

    # --- Post-processing ---
    odis_scored = odis_scored.drop(config.commune_actuelle, errors='ignore')

    if odis_scored.empty:
        return gpd.GeoDataFrame(columns=['polygon']), gpd.GeoDataFrame()

    unaggregated_gdf = odis_scored

    # 5. Aggregate by Bassin de Vie (Optional)
    if view_level == 'Bassins de vie':
        # We need to aggregate scores.
        # For simple scores, weighted average by population is good.
        # For binary/presence scores (like sante, education), we might want "max" or "union".
        df_bv_scores = aggregate_scores_by_bassin_de_vie(odis_scored)
        
        # Merge with geometry
        gdf_bv_geo_filtered = df_bv_geo[df_bv_geo.index.isin(df_bv_scores[cfg.BV_CODE_COL])]
        processed_gdf = gdf_bv_geo_filtered.merge(df_bv_scores, left_index=True, right_on=cfg.BV_CODE_COL)
        
        # Handle duplicate columns from merge (e.g. libgeo)
        if 'libgeo_x' in processed_gdf.columns and 'libgeo_y' in processed_gdf.columns:
            processed_gdf = processed_gdf.rename(columns={'libgeo_x': 'libgeo'}).drop(columns=['libgeo_y'])
        elif 'libgeo_x' in processed_gdf.columns:
             processed_gdf = processed_gdf.rename(columns={'libgeo_x': 'libgeo'})
        elif 'libgeo_y' in processed_gdf.columns:
             processed_gdf = processed_gdf.rename(columns={'libgeo_y': 'libgeo'})

        if processed_gdf.geometry.name != 'polygon':
            processed_gdf = processed_gdf.rename_geometry('polygon')
        processed_gdf = processed_gdf.drop_duplicates(subset=[cfg.BV_CODE_COL])
    else:
        processed_gdf = odis_scored.copy()
    
    processed_gdf = processed_gdf.sort_values('weighted_score', ascending=False).reset_index()
    
    return processed_gdf, unaggregated_gdf
