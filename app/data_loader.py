import streamlit as st
import os
from typing import List, Dict, Set, Any
import pandas as pd
import numpy as np
import geopandas as gpd
import shapely as shp
import gcsfs
import yaml
import config as cfg # Import config for constants and ScoringConfig

def load_scores_config_as_df(filepath: str) -> pd.DataFrame:
    """Loads the scores configuration from a YAML file and transforms it into a DataFrame."""
    with open(filepath, 'r') as f:
        config = yaml.safe_load(f)

    records = []
    for score_data in config['scores']:
        flat_record = {
            'score': score_data['id'],
            'cat': score_data['category'],
            'metric': score_data.get('source_metric'),
            'incl_binome': score_data['include_in_binom'],
            'score_name': score_data['display']['name'],
            'score_affichage': score_data['display']['strong_point_text'],
            'high_value_adj': score_data['display']['high_value_adjective'],
            'show_metric': score_data['display']['show'],
            'unit': score_data['display'].get('unit'),
            'display_factor': score_data['display']['display_factor'],
            'tooltip': score_data['display']['tooltip'],
        }
        records.append(flat_record)

    df = pd.DataFrame(records)
    # Ensure correct data types, similar to the original loader
    df = df.astype({'score': str, 'metric': str})
    return df


@st.cache_data
def load_bassin_de_vie_data(file_path: str) -> pd.DataFrame:
    """Loads the 'bassin de vie' dataset."""
    df = pd.read_csv(file_path, dtype={'CODGEO': str, cfg.BV_CODE_COL: str})
    return df


def get_data_path():
    """
    Returns the appropriate data path based on the environment.
    Checks for the K_SERVICE environment variable to detect Cloud Run.
    """
    if 'K_SERVICE' in os.environ:
        return cfg.GCS_BUCKET_PATH
    else:
        return cfg.LOCAL_CSV_PATH

def load_all_datasets(odis_file: str, bv_file: str, scores_cat_file: str, metiers_file: str, formations_file: str, ecoles_file: str, maternites_file: str, sante_file: str, inclusion_file: str) -> tuple:
    """
    Loads all necessary datasets from specified file paths.
    This function acts as a facade, calling specific loading functions for each dataset.
    """
    base_path = get_data_path()

    # The YAML config file is located relative to the app directory, not the data directory.
    # We construct an absolute path to it to prevent FileNotFoundError.
    app_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(app_dir, scores_cat_file)

    # Define the specific columns to load from the main dataset to save memory
    columns_to_load = [
        'codgeo', 'libgeo', 'polygon', 'dep_code', 'reg_code', 'epci_code', 'epci_nom', 'codgeo_voisins',
        'population', 'pop_be', 'met', 'be_codfap_top', 'be_libfap_top',
        'codes_formations', 'noms_formations', 'rp_5+pieces', 'log_rp', 'log_soc_inoccupes',
        'log_soc_total', 'log_vac', 'log_total', 'risque_fermeture', 'ecoles_ct',
        'svc_incl_count', 'pol_num'
    ]
    odis = pd.read_parquet(base_path + odis_file, columns=columns_to_load)
    odis['polygon'] = odis.polygon.apply(shp.from_wkb)
    odis = gpd.GeoDataFrame(odis, geometry='polygon', crs='EPSG:4326')
    odis.set_geometry('polygon', inplace=True)
    odis.polygon.set_precision(10**-5)
    odis = odis[~odis.polygon.isna()]
    
    # Add a centroid column for distance calculations
    odis['centroid'] = odis.geometry.centroid
    
    # --- Bassin de Vie Integration ---
    bassin_de_vie = load_bassin_de_vie_data(base_path + bv_file)
    odis = pd.merge(odis, bassin_de_vie[['CODGEO', cfg.BV_CODE_COL, cfg.BV_NAME_COL]], left_on='codgeo', right_on='CODGEO', how='left')
    odis.drop(columns='CODGEO', inplace=True)
    
    odis.set_index('codgeo', inplace=True)

    # --- Optimize Data Types ---
    # Downcast integer columns to save memory
    odis['population'] = odis['population'].astype('int32')
    odis['met'] = odis['met'].astype(pd.Int32Dtype())
    odis['ecoles_ct'] = odis['ecoles_ct'].astype(pd.Int16Dtype())
    odis['risque_fermeture'] = odis['risque_fermeture'].astype(pd.Int16Dtype())
    odis['svc_incl_count'] = odis['svc_incl_count'].astype(pd.Int16Dtype())

    # Downcast float columns to save memory
    float_cols = ['pop_be', 'rp_5+pieces', 'log_rp', 'log_soc_inoccupes',
                  'log_soc_total', 'log_vac', 'log_total', 'pol_num']
    for col in float_cols:
        if col in odis.columns:
            odis[col] = odis[col].astype('float32')

    # Convert object columns to category to save memory
    cat_cols = ['dep_code', 'reg_code', 'epci_code']
    for col in cat_cols:
        if col in odis.columns:
            odis[col] = odis[col].astype('category')

    # Index of all scores and their explanations.
    scores_cat = load_scores_config_as_df(config_path)

    #Later we need the code FAP <-> FAP Name used to classify jobs
    codfap_index = pd.read_csv(base_path + metiers_file, delimiter=';')

    # Later we need the code formation <-> Formation Name used to classify trainings
    # source: https://www.data.gouv.fr/fr/datasets/liste-publique-des-organismes-de-formation-l-6351-7-1-du-code-du-travail/
    codformations_index = pd.read_csv(base_path + formations_file, dtype={'codformation': str}).set_index('codformation')

    # Etablissements scolaires
    ecoles_cols_to_load = ['code_commune', 'nom_etablissement', 'type_etablissement', 'ecole_maternelle', 'ecole_elementaire', 'geometry']
    annuaire_ecoles = pd.read_parquet(base_path + ecoles_file, columns=ecoles_cols_to_load)

    # Optimize data types
    annuaire_ecoles['type_etablissement'] = annuaire_ecoles['type_etablissement'].astype('category')
    annuaire_ecoles['ecole_maternelle'] = annuaire_ecoles['ecole_maternelle'].astype(pd.Int8Dtype())
    annuaire_ecoles['ecole_elementaire'] = annuaire_ecoles['ecole_elementaire'].astype(pd.Int8Dtype())

    annuaire_ecoles.geometry = annuaire_ecoles.geometry.apply(shp.from_wkb)
    annuaire_ecoles = gpd.GeoDataFrame(annuaire_ecoles, geometry='geometry', crs='EPSG:4326')

    #Annuaire Maternités
    annuaire_maternites = pd.read_csv(base_path + maternites_file, delimiter=';')
    annuaire_maternites.drop_duplicates(subset=['FI_ET'], keep='last', inplace=True)

    # Annuaire etablissements santé
    annuaire_sante = pd.read_parquet(base_path + sante_file)
    annuaire_sante = annuaire_sante[annuaire_sante.LibelleSph == 'Etablissement public de santé']
    annuaire_sante['geometry'] = gpd.points_from_xy(annuaire_sante.coordxet, annuaire_sante.coordyet, crs=cfg.PROJECTED_CRS)
    annuaire_sante = gpd.GeoDataFrame(annuaire_sante, geometry='geometry', crs=cfg.PROJECTED_CRS)
    annuaire_sante = annuaire_sante.to_crs('EPSG:4326')
    annuaire_sante = pd.merge(annuaire_sante, annuaire_maternites[['FI_ET']], left_on='nofinesset', right_on='FI_ET', how='left', indicator="maternite")
    annuaire_sante.drop(columns=['FI_ET'], inplace=True)
    annuaire_sante.maternite = np.where(annuaire_sante.maternite == 'both', True, False)
    annuaire_sante['codgeo'] = annuaire_sante.Departement + annuaire_sante.Commune

    # Annuaire des services d'inclusion
    # Pre-process inclusion data for faster lookup
    inclusion_cols_to_load = ['nom', 'codgeo', 'categorie', 'service', 'geometry']
    annuaire_inclusion = pd.read_parquet(base_path + inclusion_file, columns=inclusion_cols_to_load)

    # Optimize data types
    annuaire_inclusion['categorie'] = annuaire_inclusion['categorie'].astype('category')
    annuaire_inclusion['service'] = annuaire_inclusion['service'].astype('category')

    annuaire_inclusion.geometry = annuaire_inclusion.geometry.apply(shp.from_wkb)
    annuaire_inclusion = gpd.GeoDataFrame(annuaire_inclusion, geometry='geometry', crs='EPSG:4326')
    incl_index = annuaire_inclusion[['codgeo', 'categorie', 'service']].drop_duplicates()
    incl_index['key'] = incl_index.categorie.astype(str) + '_' + incl_index.service.astype(str)
    incl_index = incl_index.groupby('codgeo').agg({'key': lambda x: set(x)})

    return odis, scores_cat, codfap_index, codformations_index, annuaire_ecoles, annuaire_sante, annuaire_inclusion, incl_index

@st.cache_data
def load_area_geodata(_communes_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Creates a GeoDataFrame of departements and regions by dissolving commune geometries.
    """
    communes_gdf = _communes_gdf.copy()
    
    # Dissolve by departement
    dep_geo = communes_gdf.dissolve(by='dep_code')
    dep_geo['area_type'] = 'departement'
    dep_geo = dep_geo.reset_index().rename(columns={'dep_code': 'area_code'})
    
    # Dissolve by region
    reg_geo = communes_gdf.dissolve(by='reg_code')
    reg_geo['area_type'] = 'region'
    reg_geo = reg_geo.reset_index().rename(columns={'reg_code': 'area_code'})
    
    # Combine and set index
    area_geo = pd.concat([dep_geo, reg_geo], ignore_index=True)
    area_geo = area_geo.set_index(['area_type', 'area_code'])
    
    return area_geo

@st.cache_data
def load_bassin_de_vie_geodata(_communes_gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Creates a GeoDataFrame of 'bassins de vie' with their dissolved geometries and centroids.
    """
    communes_gdf = _communes_gdf.copy()
    
    # Dissolve commune polygons into 'bassin de vie' polygons
    # This logic is copied from maps.dissolve_communes_to_bassins_de_vie to avoid circular imports
    bv_geo = gpd.GeoDataFrame(
        {cfg.BV_CODE_COL: communes_gdf[cfg.BV_CODE_COL]},
        geometry=communes_gdf.geometry,
        crs="EPSG:4326"
    ).dissolve(by=cfg.BV_CODE_COL)

    # Calculate centroids for the new BV polygons
    bv_geo['centroid'] = bv_geo.geometry.centroid
    
    # Get the names for each BV
    bv_names = communes_gdf[[cfg.BV_CODE_COL, cfg.BV_NAME_COL]].drop_duplicates().set_index(cfg.BV_CODE_COL)
    
    # Merge names back into the dissolved geodataframe
    bv_geo = bv_geo.merge(bv_names, left_index=True, right_index=True, how='left')
    
    return bv_geo

@st.cache_resource
def init_datasets():
    """Loads all datasets and returns them in a structured dictionary."""
    print("--- Loading all datasets... ---")
    odis, scores_cat, codfap_index, codformations_index, annuaire_ecoles, annuaire_sante, annuaire_inclusion, incl_index = load_all_datasets(
        cfg.ODIS_FILE,
        cfg.BV_FILENAME,
        cfg.SCORES_CAT_FILE,
        cfg.METIERS_FILE,
        cfg.FORMATIONS_FILE,
        cfg.ECOLES_FILE,
        cfg.MATERNITE_FILE,
        cfg.SANTE_FILE,
        cfg.INCLUSION_FILE
    )
    
    bv_geo = load_bassin_de_vie_geodata(odis)
    area_geo = load_area_geodata(odis)
    
    return {
        "odis": odis,
        "bv_geo": bv_geo,
        "area_geo": area_geo,
        "scores_cat": scores_cat,
        "codfap_index": codfap_index,
        "codformations_index": codformations_index,
        "annuaire_ecoles": annuaire_ecoles,
        "annuaire_sante": annuaire_sante,
        "annuaire_inclusion": annuaire_inclusion,
        "incl_index": incl_index,
        "coddep_set": sorted(set(odis['dep_code'])),
        "depcom_df": odis[['dep_code','libgeo']].sort_values('libgeo'),
    }