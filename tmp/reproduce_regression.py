
import pandas as pd
import geopandas as gpd
import numpy as np
from shapely.geometry import Polygon
import sys
import os

# Add app directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from core import scoring
from core.models import SearchCriterias, CriteriaItem
import config as cfg

def reproduce():
    # 1. Setup Sample Data (similar to conftest.py)
    data = {
        'codgeo': ['75056', '69123', '13055', '33063', '64445'],
        'libgeo': ['Paris', 'Lyon', 'Marseille', 'Bordeaux', 'Pau'],
        'dep_code': ['75', '69', '13', '33', '64'],
        'reg_code': ['11', '84', '93', '75', '75'],
        'bassin_de_vie': ['1', '2', '3', '4', '4'],
        'population': [2148271, 513275, 861635, 257068, 77130],
        'geometry': [
            Polygon([(2.224, 48.816), (2.469, 48.816), (2.469, 48.902), (2.224, 48.902)]),
            Polygon([(4.8, 45.7), (4.9, 45.7), (4.9, 45.8), (4.8, 45.8)]),
            Polygon([(5.3, 43.2), (5.4, 43.2), (5.4, 43.3), (5.3, 43.3)]),
            Polygon([(-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838), (-0.579, 44.838)]),
            Polygon([(-0.333, 43.3), (-0.333, 43.3), (-0.333, 43.3), (-0.333, 43.3)])
        ],
        'epci_code': ['200054781', '200046977', '200054807', '200023384', '246401722'],
        'centroid_lon': [2.3488, 4.8357, 5.3698, -0.5792, -0.3708],
        'centroid_lat': [48.8534, 45.7640, 43.2965, 44.8378, 43.2951],
        'log_vac_scaled': [0.1, 0.2, 0.3, 0.4, 0.5],
        'log_soc_inoc_scaled': [0.1, 0.2, 0.3, 0.4, 0.5],
        'inc_population_scaled': [0.1, 0.2, 0.3, 0.4, 0.5],
        'inc_asso_core_scaled': [0.1, 0.2, 0.3, 0.4, 0.5],
    }
    gdf = gpd.GeoDataFrame(data, crs="EPSG:4326").set_index('codgeo')

    scores_cat = pd.DataFrame({
        'score': ['log_vac_scaled', 'log_soc_inoc_scaled', 'inc_population_scaled', 'inc_asso_core_scaled'],
        'cat': ['logement', 'logement', 'inclusion', 'inclusion'],
        'metric': ['log_vac_ratio', 'log_soc_inoc_ratio', 'population', 'lien_social_density'],
        'weight': [1.0, 1.0, 1.0, 1.0],
        'min_bound': [0.0, 0.0, 0.0, 0.0],
        'max_bound': [1.0, 1.0, 1.0, 1.0]
    })

    config = SearchCriterias(
        poids_emploi=0, poids_logement=100, poids_education=0, poids_inclusion=100, poids_sante=0, poids_mobilite=0,
        commune_actuelle=CriteriaItem(code='33063', label='Bordeaux'),
        loc_search_area='france',
        nb_adultes=1,
        nb_enfants=0,
        hebergement_cible=["Location avec Intermédiation"],
        logement='Location'
    )

    engine = scoring.ScoringEngine(
        df_all_communes=gdf,
        df_bv_geo=gpd.GeoDataFrame(),
        scores_cat=scores_cat,
        incl_index=pd.DataFrame(index=gdf.index),
        associations_data=pd.DataFrame(columns=['codgeo', 'id_waldec', 'count']),
        formations_data=pd.DataFrame(columns=['codgeo', 'formation_code']),
        global_stats={}
    )

    # 2. Run Scoring
    search_results, processed_gdf = engine.run_optimized(config)

    # 3. Check Results
    print(f"Number of results: {len(search_results.results)}")
    for i, res in enumerate(search_results.results):
        print(f"Top {i+1}: {res.name} ({res.codgeo})")
        print(f"  Housing Cat Score: {res.housing.cat_score}")
        print(f"  Inclusion Cat Score: {res.inclusion.cat_score}")
        for cat, details in res.scores.items():
             print(f"    {cat} breakdown:")
             for d in details:
                 print(f"      {d.label}: {d.score_normalise}")

if __name__ == "__main__":
    reproduce()
