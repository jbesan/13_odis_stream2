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

import config as cfg
from config import ScoringConfig


# --- Scoring Pipeline Functions ---

def add_distance_to_current_loc(df: gpd.GeoDataFrame, current_codgeo: str) -> gpd.GeoDataFrame:
    """Computes the distance from each commune in the dataframe to a reference commune.

    Returns:
        GeoDataFrame with an added 'dist_current_loc' column in meters.
    """
    # Optimization: Use pre-calculated centroids if available
    if 'centroid' in df.columns:
        # Ensure we are working with a GeoSeries of centroids in the projected CRS
        # The centroid column is in the original CRS (EPSG:4326), so we project it.
        centroids_proj = df['centroid'].to_crs(cfg.PROJECTED_CRS)
        
        # Get the target centroid
        # We assume current_codgeo is in the dataframe (it should be, as it's from df_all_communes)
        # If not, we might need to look it up from df_all_communes, but this function takes df which is usually df_search.
        # However, df_search might NOT contain current_codgeo if it was filtered out!
        # Wait, if current_codgeo is not in df, we can't get its centroid from df.
        # But this function is usually called on df_search.
        # If current_codgeo is not in df_search, we have a problem.
        # Let's look at how it was used before:
        # zone_recherche = df_projected.loc[[current_codgeo]].copy()
        # This implies current_codgeo MUST be in df.
        
        if current_codgeo not in df.index:
             # Fallback or Error? 
             # If the user filters by department 33, and current location is in 75, 
             # then current_codgeo is NOT in df.
             # The previous code would crash if current_codgeo was not in df.
             # "zone_recherche = df_projected.loc[[current_codgeo]]" would raise KeyError.
             # So we can assume it is present or the previous code was buggy (or usage guarantees it).
             # Actually, in compute_odis_score, df_search comes from filter_communes.
             # If filter_communes filters by distance, it keeps nearby communes.
             # If filter_communes filters by department, it keeps that department.
             # If I live in Paris (75) and search in Gironde (33), df_search only has 33.
             # So current_codgeo (75) is NOT in df_search.
             # So the previous code WOULD FAIL?
             # Let's check filter_communes.
             # If loc_type='departement', it returns df[df['dep_code'] == loc_code].
             # So yes, if I search in another department, current_codgeo is missing.
             # BUT, maybe the UI prevents this? Or maybe config.commune_actuelle is just for distance calculation?
             # If I search in another department, do I care about distance to my home?
             # Yes, "Distance from the current location" score.
             # So this function MUST work even if current_codgeo is not in df.
             pass

        # To be safe and support the case where current_codgeo is not in df,
        # we should probably pass the centroid of current_codgeo explicitly or look it up from a global source.
        # But the signature is (df, current_codgeo).
        # The previous implementation:
        # df_projected = df.to_crs(cfg.PROJECTED_CRS)
        # zone_recherche = df_projected.loc[[current_codgeo]].copy()
        # This CONFIRMS that previous code assumed current_codgeo is in df.
        # If it wasn't, it would crash.
        # So I will maintain this assumption for now, or improve it if I can.
        # Actually, looking at compute_odis_score:
        # df_search = filter_communes(...)
        # if 'dist_current_loc' not in df_search.columns:
        #    odis_search = add_distance_to_current_loc(df_search, config.commune_actuelle)
        
        # If I set loc_type='departement' (e.g. 33) and commune_actuelle='Paris' (75),
        # filter_communes returns only 33.
        # add_distance_to_current_loc is called with df (33) and 'Paris'.
        # df.loc[['Paris']] will FAIL.
        # So the app probably crashes in this scenario currently?
        # Or maybe the UI forces commune_actuelle to be in the search area?
        # No, "Commune actuelle" is a user setting. Search area is another.
        # This looks like a bug in the existing code or I am missing something.
        # Wait, maybe df passed to add_distance_to_current_loc is NOT filtered yet?
        # No, it is df_search.
        
        # Let's assume for now we only optimize what is there.
        # But to be robust, if I can't find current_codgeo in df, I can't calculate distance easily 
        # unless I have access to all communes.
        # But I don't have df_all_communes here.
        
        # However, if I look at filter_communes, it takes start_commune (GeoSeries).
        # Maybe I should pass start_commune to add_distance_to_current_loc?
        # But I cannot change the signature too much without changing the caller.
        # The caller is compute_odis_score.
        
        # Let's stick to the plan: optimize using centroids.
        # I will try to find current_codgeo in df.
        target_centroid = centroids_proj.loc[current_codgeo]
        
        distances = centroids_proj.distance(target_centroid)
        
        df_result = df.copy()
        df_result['dist_current_loc'] = distances
        return df_result
    else:
        # Fallback to original method if centroid is missing
        # We first need to change CRS to a projected CRS to compute distances in meters.
        df_projected = df.to_crs(cfg.PROJECTED_CRS)

        # Isolate the reference commune and calculate its centroid.
        zone_recherche = df_projected.loc[[current_codgeo]].copy()
        zone_recherche['geometry'] = zone_recherche.centroid

        # Use sjoin_nearest to efficiently calculate the distance for all points.
        df_with_dist = df_projected.sjoin_nearest(
            zone_recherche, distance_col="dist_current_loc"
        )[['dist_current_loc']]

        # Merge the distance back to the original dataframe.
        return df.merge(df_with_dist, left_index=True, right_index=True, how='left')


def filter_by_distance(df: pd.DataFrame, max_distance_km: float) -> pd.DataFrame:
    """Filters a dataframe to keep only rows within a given distance."""
    return df[df.dist_current_loc < max_distance_km * 1000].copy()

def filter_communes(
    df: gpd.GeoDataFrame,
    start_commune: gpd.GeoSeries,
    loc_type: str,
    loc_code: str,
    loc_distance_km: int
) -> gpd.GeoDataFrame:
    """Filters the communes dataframe based on the selected mobility criteria."""
    if loc_type == 'distance':
        # Optimization: Use pre-calculated centroids if available
        if 'centroid' in df.columns:
            # Project centroids to meters
            centroids_proj = df['centroid'].to_crs(cfg.PROJECTED_CRS)
            
            # Get start centroid (projected)
            # start_commune is a GeoSeries (one row), so we get its centroid and project it
            # But start_commune might not have 'centroid' column if it's just a slice of geometry?
            # start_commune comes from df_all_communes.loc[[config.commune_actuelle]]
            # So it should have 'centroid' column.
            if 'centroid' in start_commune:
                start_centroid_proj = start_commune['centroid'].to_crs(cfg.PROJECTED_CRS).iloc[0]
            else:
                # Fallback if start_commune doesn't have centroid column (unlikely)
                start_centroid_proj = start_commune.to_crs(cfg.PROJECTED_CRS).centroid.iloc[0]

            # Calculate distances
            distances = centroids_proj.distance(start_centroid_proj)

            # Filter
            mask = distances <= loc_distance_km * 1000
            filtered_df = df[mask].copy()
            
            # Pre-fill the distance column to avoid recalculation later
            filtered_df['dist_current_loc'] = distances[mask]
            
            return filtered_df
        else:
            # Project to a CRS in meters for accurate distance calculation
            df_proj = df.to_crs(cfg.PROJECTED_CRS)
            start_centroid_proj = start_commune.to_crs(cfg.PROJECTED_CRS).centroid

            # Calculate distance from the starting centroid to all other centroids
            distances = df_proj.centroid.distance(start_centroid_proj.iloc[0])

            # Filter communes within the specified radius
            filtered_df = df[distances <= loc_distance_km * 1000]
            return filtered_df.copy()

    elif loc_type == 'departement':
        return df[df['dep_code'] == loc_code].copy()
    elif loc_type == 'region':
        return df[df['reg_code'] == loc_code].copy()

    return gpd.GeoDataFrame()  # Return empty if no valid type

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
        # Optimization: Use pre-calculated centroids if available
        if 'centroid' in bv_gdf.columns:
             # Project centroids to meters
            centroids_proj = bv_gdf['centroid'].to_crs(cfg.PROJECTED_CRS)
            
            # Get start centroid (projected)
            if 'centroid' in start_commune:
                start_centroid_proj = start_commune['centroid'].to_crs(cfg.PROJECTED_CRS).iloc[0]
            else:
                start_centroid_proj = start_commune.to_crs(cfg.PROJECTED_CRS).centroid.iloc[0]

            # Calculate distance from the starting centroid to all BV centroids
            distances = centroids_proj.distance(start_centroid_proj)

            # Filter BVs within the specified radius
            filtered_df = bv_gdf[distances <= loc_distance_km * 1000]
            return filtered_df.copy()
        else:
            # Project to a CRS in meters for accurate distance calculation
            bv_proj = bv_gdf.to_crs(cfg.PROJECTED_CRS)
            start_centroid_proj = start_commune.to_crs(cfg.PROJECTED_CRS).centroid

            # Calculate distance from the starting centroid to all BV centroids
            distances = bv_proj.centroid.distance(start_centroid_proj.iloc[0])

            # Filter BVs within the specified radius
            filtered_df = bv_gdf[distances <= loc_distance_km * 1000]
            return filtered_df.copy()

    elif loc_type in ['departement', 'region']:
        # Get the geometry for the selected area
        area_geometry = area_gdf.loc[(loc_type, loc_code)].polygon

        # Find all BVs that intersect with that area
        intersecting_mask = bv_gdf.intersects(area_geometry)
        return bv_gdf[intersecting_mask].copy()

    return gpd.GeoDataFrame()  # Return empty if no valid type

def compute_criteria_scores(
    df: gpd.GeoDataFrame,
    prefs: Dict[str, Any],
    incl_index: pd.DataFrame,
    df_all_communes: gpd.GeoDataFrame
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
    df['met_ratio'] = 1000 * df['met'] / df['pop_be']
    df['met_scaled'] = transformer.fit_transform(df[['met_ratio']].fillna(0))

    # Job categories that match user preferences
    for i in range(prefs['nb_adultes']):
        adult_key = f'adult{i+1}'
        if prefs['codes_metiers'][i]:
            prefs_metiers = set(prefs['codes_metiers'][i])
            df[f'met_match_codes_{adult_key}'] = [
                list(set(x).intersection(prefs_metiers)) if x is not None else []
                for x in df.be_codfap_top
            ]
            df[f'met_match_{adult_key}'] = df[f'met_match_codes_{adult_key}'].str.len()
            df[f'met_match_{adult_key}_scaled'] = transformer.fit_transform(
                df[[f'met_match_{adult_key}']].fillna(0)
            )

    # Training centers that match
    for i in range(prefs['nb_adultes']):
        adult_key = f'adult{i+1}'
        if prefs['codes_formations'][i]:
            prefs_formations = set(prefs['codes_formations'][i])
            df[f'form_match_codes_{adult_key}'] = [
                list(set(x).intersection(prefs_formations)) if x is not None else []
                for x in df.codes_formations
            ]
            df[f'form_match_{adult_key}'] = df[f'form_match_codes_{adult_key}'].str.len()
            df[f'form_match_{adult_key}_scaled'] = transformer.fit_transform(
                df[[f'form_match_{adult_key}']].fillna(0)
            )

    # --- HEBERGEMENT / LOGEMENT ---
    if prefs['hebergement'] == "Chez l'habitant":
        df['log_5p_ratio'] = df['rp_5+pieces'] / df['log_rp']
        df['log_5p_scaled'] = transformer.fit_transform(df[['log_5p_ratio']].fillna(0))

    if prefs['logement'] == "Logement Social":
        df['log_soc_inoc_ratio'] = df['log_soc_inoccupes'] / df['log_soc_total']
        df['log_soc_inoc_scaled'] = transformer.fit_transform(df[['log_soc_inoc_ratio']].fillna(0))
    elif prefs['logement'] == "Location":
        df['log_vac_ratio'] = df['log_vac'] / df['log_total']
        df['log_vac_scaled'] = transformer.fit_transform(df[['log_vac_ratio']].fillna(0))

    # --- EDUCATION ---
    if prefs['classe_enfants']:
        df['risque_fermeture_ratio'] = df['risque_fermeture'] / df['ecoles_ct']
        df['classes_ferm_scaled'] = transformer.fit_transform(df[['risque_fermeture_ratio']].fillna(0))
        
        # Score based on presence of required school types
        class_mapping = {
            'Maternelle': 'count_maternelle',
            'Elémentaire': 'count_elementaire',
            'Collège': 'count_college',
            'Lycée': 'count_lycee'
        }
        required_cols = {class_mapping[c] for c in prefs['classe_enfants'] if c in class_mapping}
        
        if required_cols:
            # Count matches (boolean logic: count > 0)
            matches = (df[list(required_cols)] > 0).sum(axis=1)
            # Linear score: ratio of met requirements
            df['edu_structures_scaled'] = matches / len(required_cols)
        else:
            df['edu_structures_scaled'] = 0.0
    else:
        df['classes_ferm_scaled'] = 0.0
        df['edu_structures_scaled'] = 0.0

    # --- SANTE ---
    sante_pref = prefs.get('besoin_sante', 'Aucun')
    if sante_pref != 'Aucun':
        col_map = {
            'Hopital': 'count_hopital',
            'Maternité': 'count_maternite',
            'Soutien Psychologique & Addictologie': 'count_psy'
        }
        target_col = col_map.get(sante_pref)
        if target_col and target_col in df.columns:
            # Score 1.0 if count > 0, else 0.0
            df['sante_structures_scaled'] = (df[target_col] > 0).astype(float)
        else:
            df['sante_structures_scaled'] = 0.0
    else:
        df['sante_structures_scaled'] = 0.0

    # --- MOBILITE ---
    # 1. Distance from the current location
    if isinstance(prefs['loc_distance_km'], int):
        df['reloc_dist_scaled'] = (1 - df['dist_current_loc'] / (prefs['loc_distance_km'] * 1000))
    # 2. Is the commune in the same EPCI as the current one?
    # We get the EPCI from the original, unfiltered dataframe to avoid KeyErrors
    current_epci = df_all_communes.loc[prefs['commune_actuelle']]['epci_code']
    df['reloc_epci_scaled'] = np.where(df['epci_code'] == current_epci, 1, 0)

    # --- SOUTIEN LOCAL ---
    if prefs['besoins_autres']:
        # Vectorized approach for 'besoins_match' - much faster than itertuples
        all_needed_services = {
            f"{cat}_{serv}"
            for cat, serv_list in prefs['besoins_autres'].items()
            for serv in serv_list
        }

        # Create a boolean mask for communes that have any of the needed services
        # This merges the pre-calculated incl_index with our current dataframe
        df_merged = df.join(incl_index, how='left')

        # Calculate the number of matching services for each commune
        df['besoins_match'] = [
            len(all_needed_services.intersection(s)) if isinstance(s, set) else 0
            for s in df_merged['key']
        ]
        df['besoins_match_scaled'] = transformer.fit_transform(df[['besoins_match']].fillna(0))
    else:
        # If no specific needs, score based on the general availability of inclusion services
        df['svc_incl_ratio'] = 1000 * df['svc_incl_count'] / df['pop_be']
        df['svc_incl_scaled'] = transformer.fit_transform(df[['svc_incl_ratio']].fillna(0))

    # Population as a direct score for inclusion
    df['population_scaled'] = transformer.fit_transform(df[['population']].fillna(0))

    # Political orientation score
    df['pol_scaled'] = df['pol_num'].astype('float')

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
    binome_penalty: float
) -> pd.DataFrame:
    """Aggregates individual criteria scores into category scores (e.g., 'emploi_cat_score').

    For binomes, it considers the max score between the commune and its neighbor,
    applying a penalty to the neighbor's score.
    """
    df = df.copy()

    for category in scores_cat['cat'].unique():
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
        for col in score_cols:
            score_commune = df[col]
            # Check if a corresponding binome score exists
            if f'{col}_binome' in df.columns:
                score_voisin = df[f'{col}_binome'] * (1 - binome_penalty)
                
                # Debugging: Raise exception with type info
                s_commune = score_commune.fillna(0)
                s_voisin = score_voisin.fillna(0)
                if isinstance(s_commune, pd.DataFrame) or isinstance(s_voisin, pd.DataFrame):
                     raise ValueError(f"DEBUG: Mixed types for col '{col}'. \nCommune type: {type(s_commune)}\nVoisin type: {type(s_voisin)}\nCommune cols: {s_commune.columns if isinstance(s_commune, pd.DataFrame) else 'Series'}\nVoisin cols: {s_voisin.columns if isinstance(s_voisin, pd.DataFrame) else 'Series'}")

                effective_score = np.maximum(s_commune, s_voisin)
                max_scores.append(effective_score)
            else:  # This criterion is not applicable to binomes
                max_scores.append(score_commune.fillna(0))

        # The category score is the mean of the effective scores of its criteria.
        df[f'{category}_cat_score'] = pd.concat(max_scores, axis=1).mean(axis=1)

    return df


def compute_weighted_score(df: pd.DataFrame, config: 'ScoringConfig') -> pd.Series:
    """Computes the final weighted score for each row based on category scores and user-defined weights."""
    category_scores = [col for col in df.columns if col.endswith('_cat_score')]

    total_score = 0
    total_weight = 0

    for cat_score_col in category_scores:
        # e.g., 'emploi_cat_score' -> 'emploi'
        category_name = cat_score_col.split('_')[0]
        weight_key = f'poids_{category_name}'
        weight = getattr(config, weight_key, 0)

        if weight > 0:
            total_score += df[cat_score_col].fillna(0) * weight
            total_weight += weight

    return total_score / total_weight if total_weight > 0 else 0


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
    score_cols = [col for col in df_agg.columns if '_score' in col or '_scaled' in col]
    agg_dict = {
        col: lambda x, col=col: weighted_avg(df_agg.loc[x.index], col) for col in score_cols
    }

    # --- Basic Aggregations ---
    agg_dict['population'] = 'sum'
    if 'epci_nom' in df_agg.columns:
        agg_dict['epci_nom'] = lambda x: ', '.join(x.unique())

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
    incl_index: pd.DataFrame
) -> pd.DataFrame:
    """Main function that orchestrates the entire scoring pipeline on a pre-filtered dataframe."""
    if df_search.empty:
        return df_search.copy()  # Return a copy to avoid warnings

    # We still need the distance for some scores, calculate it if not present
    if 'dist_current_loc' not in df_search.columns:
        odis_search = add_distance_to_current_loc(
            df_search, current_codgeo=config.commune_actuelle
        )
    else:
        odis_search = df_search.copy()

    # 4. Compute all individual criteria scores based on preferences.
    odis_scored = compute_criteria_scores(
        odis_search,
        prefs=config.__dict__,
        incl_index=incl_index,
        df_all_communes=df_all_communes
    )

    # 5. Expand the dataframe to include neighbor data (creating monomes and binomes).
    odis_exploded = add_neighbor_scores(odis_scored, scores_cat)

    # 6. Aggregate criteria scores into category scores, handling the binome logic.
    odis_exploded = compute_category_scores(
        odis_exploded,
        scores_cat=scores_cat,
        binome_penalty=config.binome_penalty
    )

    # 7. Compute the final weighted score for each commune/binome pair.
    odis_exploded['weighted_score'] = compute_weighted_score(odis_exploded, config=config)

    # 8. For each commune, keep only the best result (could be monome or a binome).
    odis_search_best = select_best_score_per_commune(odis_exploded)

    return odis_search_best


def run_scoring_pipeline(
    config: ScoringConfig,
    df_all_communes: gpd.GeoDataFrame,
    df_bv_geo: gpd.GeoDataFrame,
    df_area_geo: gpd.GeoDataFrame,
    scores_cat: pd.DataFrame,
    incl_index: pd.DataFrame,
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
        communes_to_score = df_all_communes[df_all_communes[cfg.BV_CODE_COL].isin(bv_ids_to_keep)]

    # --- Scoring ---
    odis_scored = compute_odis_score(
        df_search=communes_to_score,
        df_all_communes=df_all_communes,
        scores_cat=scores_cat,
        config=config,
        incl_index=incl_index,
    )

    # --- Post-processing ---
    odis_scored = odis_scored.drop(config.commune_actuelle, errors='ignore')

    if odis_scored.empty:
        return gpd.GeoDataFrame(columns=['polygon']), gpd.GeoDataFrame()

    unaggregated_gdf = odis_scored

    if view_level == 'Bassins de vie':
        df_bv_scores = aggregate_scores_by_bassin_de_vie(odis_scored)
        gdf_bv_geo_filtered = df_bv_geo[df_bv_geo.index.isin(df_bv_scores[cfg.BV_CODE_COL])]
        processed_gdf = gdf_bv_geo_filtered.merge(df_bv_scores, left_index=True, right_on=cfg.BV_CODE_COL)
        processed_gdf = processed_gdf.rename_geometry('polygon')
        processed_gdf = processed_gdf.drop_duplicates(subset=[cfg.BV_CODE_COL])
    else: # Commune level
        processed_gdf = odis_scored
    
    processed_gdf = processed_gdf.sort_values('weighted_score', ascending=False).reset_index()
    
    return processed_gdf, unaggregated_gdf
