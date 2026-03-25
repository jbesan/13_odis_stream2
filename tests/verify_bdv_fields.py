import sys
import os
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point

# Add app directory to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../app')))

from core import scoring
from core.models import SearchCriterias

def verify_bdv_fields():
    # 1. Setup Mock Data
    df_all = gpd.GeoDataFrame({
        'codgeo': ['33063'],
        'libgeo': ['Bordeaux'],
        'bassin_de_vie': ['33301'],
        'libelle_bassin_de_vie': ['Bassin de Bordeaux'],
        'population': [2148271],
        'geometry': [Point(0,0)]
    }).set_index('codgeo')
    
    # 2. Instantiate Engine
    engine = scoring.ScoringEngine(
        df_all_communes=df_all,
        df_bv_geo=None,
        df_area_geo=None,
        scores_cat=pd.DataFrame(columns=['score', 'cat', 'metric', 'weight', 'min_bound', 'max_bound']),
        incl_index=pd.DataFrame(),
        associations_data=pd.DataFrame(),
        formations_data=pd.DataFrame(),
        global_stats={}
    )
    
    # 3. Format result
    row = df_all.iloc[0]
    res = engine.format_city_details(row)
    
    print(f"Bassin de Vie (Name): {res.name_bdv}")
    print(f"Codgeo BdV: {res.codgeo_bdv}")
    print(f"Name BdV: {res.name_bdv}")
    print(f"Centroid: {res.centroid}")
    
    # Assertions
    assert res.codgeo_bdv == "33301"
    assert res.name_bdv == "Bassin de Bordeaux"
    assert not hasattr(res, 'bassin_de_vie')
    assert isinstance(res.centroid, Point)
    
    print("\n✅ BV Metadata Fields Verification Passed!")

if __name__ == "__main__":
    verify_bdv_fields()
