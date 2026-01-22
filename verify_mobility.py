import pandas as pd
import os
import sys
sys.path.append(os.getcwd())
try:
    from app.core.scoring import ScoringEngine
    from app.core.models import ScoringConfig
except ImportError:
    # Handle if run from different CWD
    sys.path.append(os.path.join(os.getcwd(), 'app'))
    from core.scoring import ScoringEngine
    from core.models import ScoringConfig

def verify():
    print("--- Checking odis_communes_pre.parquet ---")
    df = pd.read_parquet("data/odis_communes_pre.parquet")
    cols = ['nb_stops_bus', 'nb_stops_tram', 'nb_stops_metro', 'nb_stops_train', 'nb_stops_total']
    for col in cols:
        if col in df.columns:
            print(f"✅ Column {col} found. Sum: {df[col].sum()}")
        else:
            print(f"❌ Column {col} MISSING")

    print("\n--- Testing ScoringEngine ---")
    # Load engine
    try:
        from app.utils.data_loader import load_all_data_raw
        from app.utils.data_loader import load_scores_config_as_df
    except ImportError:
        sys.path.append(os.path.join(os.getcwd(), 'app'))
        from utils.data_loader import load_all_data_raw
        from utils.data_loader import load_scores_config_as_df
    
    data = load_all_data_raw()
    engine = ScoringEngine(
        df_all_communes=data['odis'],
        df_bv_geo=data['bv_geo'],
        df_area_geo=data['area_geo'],
        scores_cat=data['scores_cat'],
        incl_index=data['incl_index'],
        # ... and so on, but easier to use a helper if available, 
        # but let's just use the data dict directly for the engine init
        associations_data=data['associations_data'],
        formations_data=data['formations_data'],
        codformations_index=data['codformations_index'],
        waldec_index=data['waldec_index'],
        global_stats={},
        bv_data=data.get('bv_data'),
        annuaire_ecoles=data['annuaire_ecoles'],
        annuaire_sante=data['annuaire_sante'],
        annuaire_inclusion=data['annuaire_inclusion'],
        inclusion_services_index=data['inclusion_services_index'],
        rome_index=data['rome_index'],
        refugee_associations_data=data['refugee_associations_data'],
        odis_asso_mini_data=data['odis_asso_mini_data'],
        live_jobs_data=data['live_jobs_data']
    )

    # Pick a commune with transport (e.g. Bordeaux 33063 if in data)
    test_codgeo = '33063' 
    if test_codgeo not in data['odis'].index:
        test_codgeo = data['odis'][data['odis']['nb_stops_total'] > 0].index[0]
    
    print(f"Testing with codgeo: {test_codgeo}")
    row = data['odis'].loc[test_codgeo]
    details = engine.format_city_details(row)
    
    if 'mobilité' in details:
        print("✅ Mobility details found in city_details")
        print(f"   Details: {details['mobilité']}")
    else:
        print("❌ Mobility details MISSING in city_details")

    # Test scoring
    config = ScoringConfig(
        poids_emploi=1, poids_logement=1, poids_education=1, poids_inclusion=1, poids_mobilité=11, poids_sante=1,
        criteria_weights={}, commune_actuelle='75056', loc_search_area='france', 
        nb_adultes=1, nb_enfants=0, hebergement='Location', logement='Social',
        codes_metiers=[], codes_formations=[], classe_enfants=[], besoin_sante='Aucun',
        inc_services_add_selection=[], inc_services_core_selection=[], inc_asso_add_selection=[]
    )
    
    results = engine.run(config)
    if 'mob_trans_pub_density_scaled' in results.columns:
        print("✅ mob_trans_pub_density_scaled column found in results")
        print(f"   Top 5 mobility density scores:\n{results['mob_trans_pub_density_scaled'].head()}")
    else:
        print("❌ mob_trans_pub_density_scaled MISSING in results")

if __name__ == "__main__":
    verify()
