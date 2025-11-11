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


def get_data_path():
    """
    Returns the appropriate data path based on the environment.
    Checks for the K_SERVICE environment variable to detect Cloud Run.
    """
    if 'K_SERVICE' in os.environ:
        return cfg.GCS_BUCKET_PATH
    else:
        return cfg.LOCAL_CSV_PATH

def load_all_datasets(odis_file: str, scores_cat_file: str, metiers_file: str, formations_file: str, ecoles_file: str, maternites_file: str, sante_file: str, inclusion_file: str) -> tuple:
    """
    Loads all necessary datasets from specified file paths.
    This function acts as a facade, calling specific loading functions for each dataset.
    """
    base_path = get_data_path()

    # The YAML config file is located relative to the app directory, not the data directory.
    # We construct an absolute path to it to prevent FileNotFoundError.
    app_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(app_dir, scores_cat_file)

    odis = pd.read_parquet(base_path + odis_file)
    odis['polygon'] = odis.polygon.apply(shp.from_wkb)
    odis = gpd.GeoDataFrame(odis, geometry='polygon', crs='EPSG:4326')
    odis.set_geometry('polygon', inplace=True)
    odis.polygon.set_precision(10**-5)
    odis = odis[~odis.polygon.isna()]
    odis.set_index('codgeo', inplace=True)

    # Index of all scores and their explanations.
    scores_cat = load_scores_config_as_df(config_path)

    #Later we need the code FAP <-> FAP Name used to classify jobs
    codfap_index = pd.read_csv(base_path + metiers_file, delimiter=';')

    # Later we need the code formation <-> Formation Name used to classify trainings
    # source: https://www.data.gouv.fr/fr/datasets/liste-publique-des-organismes-de-formation-l-6351-7-1-du-code-du-travail/
    codformations_index = pd.read_csv(base_path + formations_file, dtype={'codformation': str}).set_index('codformation')

    # Etablissements scolaires
    annuaire_ecoles = pd.read_parquet(base_path + ecoles_file)
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
    annuaire_inclusion = pd.read_parquet(base_path + inclusion_file)
    annuaire_inclusion.geometry = annuaire_inclusion.geometry.apply(shp.from_wkb)
    annuaire_inclusion = gpd.GeoDataFrame(annuaire_inclusion, geometry='geometry', crs='EPSG:4326')
    incl_index = annuaire_inclusion[['codgeo', 'categorie', 'service']].drop_duplicates()
    incl_index['key'] = incl_index.categorie+'_'+incl_index.service
    incl_index = incl_index.groupby('codgeo').agg({'key': lambda x: set(x)})

    return odis, scores_cat, codfap_index, codformations_index, annuaire_ecoles, annuaire_sante, annuaire_inclusion, incl_index

@st.cache_resource
def init_datasets():
    """Loads all datasets and returns them in a structured dictionary."""
    print("--- Loading all datasets... ---")
    odis, scores_cat, codfap_index, codformations_index, annuaire_ecoles, annuaire_sante, annuaire_inclusion, incl_index = load_all_datasets(
        cfg.ODIS_FILE,
        cfg.SCORES_CAT_FILE,
        cfg.METIERS_FILE,
        cfg.FORMATIONS_FILE,
        cfg.ECOLES_FILE,
        cfg.MATERNITE_FILE,
        cfg.SANTE_FILE,
        cfg.INCLUSION_FILE
    )
    return {
        "odis": odis,
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