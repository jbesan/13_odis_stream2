
from fastmcp import FastMCP
from typing import Dict, Any, List, Optional
import json
import pandas as pd
import geopandas as gpd
import numpy as np
from data_loader import load_all_data_raw
from scoring import ScoringEngine
from config import ScoringConfig
import config as cfg
import logging
from utils import normalize_text, calculate_fuzzy_match_score, sanitize_for_json

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")

# Initialize FastMCP Server
mcp = FastMCP("ODIS-Core")

# Global State for Data (Loaded on startup)
DATA_CONTEXT = {}

def set_data_context(context: Dict[str, Any]):
    """Allows external injection of data context (e.g. from Streamlit cache)"""
    global DATA_CONTEXT
    DATA_CONTEXT = context
    logger.info("Data Context injected externally.")

def get_scoring_engine() -> ScoringEngine:
    """
    Lazy loads the data and returns the ScoringEngine instance.
    """
    global DATA_CONTEXT
    if not DATA_CONTEXT:
        logger.info("Initializing Data Context for MCP...")
        try:
            DATA_CONTEXT = load_all_data_raw()
            logger.info("Data Context Loaded Successfully.")
        except Exception as e:
            logger.error(f"Failed to load data context: {e}")
            raise RuntimeError(f"Failed to load ODIS data: {e}")
            
    return ScoringEngine(
        df_all_communes=DATA_CONTEXT['odis'],
        df_bv_geo=DATA_CONTEXT['bv_geo'],
        df_area_geo=DATA_CONTEXT['area_geo'],
        scores_cat=DATA_CONTEXT['scores_cat'],
        incl_index=DATA_CONTEXT['incl_index'],
        associations_data=DATA_CONTEXT['associations_data'],
        bmo_vertical=DATA_CONTEXT['bmo_vertical'],
        formations_data=DATA_CONTEXT['formations_data'],
        codformations_index=DATA_CONTEXT['codformations_index'],
        global_stats={}, # TODO: Compute or load global stats if needed for scaling
        codfap_index=DATA_CONTEXT.get('codfap_index')
    )

def _search_referentiels_logic(query: str, domain: str = None) -> List[Dict[str, str]]:
    """
    Searches for codes in the ODIS referentials (Jobs, Formations, Inclusion).
    """
    logger.info(f"👉 [MCP] Request: search_referentiels")
    logger.info(f"   Query: '{query}', Domain: '{domain}'")
    
    if 'referentiels_raw' not in DATA_CONTEXT:
        logger.warning("   ⚠️ Referentiels data not available.")
        return []

    df = DATA_CONTEXT['referentiels_raw']
    if df.empty:
        return []

    # 1. Filter by Domain
    if domain:
        df = df[df['key'] == domain]
    
    # 2. Robust Search Logic
    STOP_WORDS = {
        "le", "la", "les", "l", "d", "de", "du", "des", 
        "un", "une", "et", "ou", "au", "aux", "en", 
        "par", "pour", "sur", "dans", "a", "à"
    }
    
    query_norm = normalize_text(query)
    # Filter early to avoid processing huge DF? No, we scan all
    if 'label' not in df.columns:
        return []

    # Prepare DataFrame for scoring
    work_df = df.copy()
    work_df['label_norm'] = work_df['label'].apply(normalize_text)
    work_df['code_norm'] = work_df['code'].apply(normalize_text)
    
    query_tokens = set(query_norm.split())
    
    weights = {'exact': 100, 'token_overlap': 20, 'contains': 20, 'starts_with': 50}

    def calculate_relevance(row):
        score = calculate_fuzzy_match_score(
            query_norm, 
            row['label_norm'], 
            query_tokens, 
            set(row['label_norm'].split()), 
            STOP_WORDS,
            weights
        )
        # Match in Code (Specific)
        if query_norm in row['code_norm']:
            score += 50
        
        return score

    work_df['score'] = work_df.apply(calculate_relevance, axis=1)
    
    # Sort by score descending
    results_df = work_df[work_df['score'] > 0].sort_values(by='score', ascending=False)
    
    # 3. Format Output
    results_df = results_df.head(20)
    
    results = []
    for _, row in results_df.iterrows():
        results.append({
            "code": row['code'],
            "label": row['label'],
            "type": row['key'],
            "relevance": row['score'] # Debug info
        })
        
    logger.info(f"✅ [MCP] Response: Found {len(results)} matches.")
    if results:
         top_summary = [f"{r['label']} ({r['relevance']})" for r in results[:3]]
         logger.info(f"   Top matches: {top_summary}")
         
    return results


def _search_commune_logic(query: str) -> List[Dict[str, str]]:
    """
    Searches for French cities using Referentiels first, then ODIS for details.
    """
    logger.info(f"👉 [MCP] Request: search_commune")
    logger.info(f"   Query: '{query}'")
    
    if 'referentiels_raw' not in DATA_CONTEXT or 'odis' not in DATA_CONTEXT:
         logger.warning("   ⚠️ Data context missing (referentiels or odis).")
         return []
         
    refs_df = DATA_CONTEXT['referentiels_raw']
    odis_df = DATA_CONTEXT['odis']
    
    # 1. Filter for Communes
    communes_ref = refs_df[refs_df['key'] == 'communes']
    if communes_ref.empty:
        logger.warning("   ⚠️ No communes found in referentiels.")
        return []

    # 2. Search Logic
    q_norm = normalize_text(query)
    STOP_WORDS_CITIES = {"le", "la", "les", "l", "d", "de", "du", "des", "en", "sur", "aux"}

    work_df = communes_ref.copy()
    work_df['label_norm'] = work_df['label'].apply(normalize_text)
    
    query_tokens = set(q_norm.split())
    
    weights = {'exact': 1000, 'starts_with': 200, 'contains': 100, 'token_overlap': 50}

    def calculate_city_score(row):
        return calculate_fuzzy_match_score(
            q_norm,
            row['label_norm'],
            query_tokens,
            set(row['label_norm'].split()),
            STOP_WORDS_CITIES,
            weights
        )

    work_df['score'] = work_df.apply(calculate_city_score, axis=1)
    results_df = work_df[work_df['score'] > 0].sort_values(by='score', ascending=False).head(15)

    if results_df.empty:
        logger.info("   [MCP] No cities found.")
        return []

    # 3. Lookup Details
    found_codes = results_df['code'].unique()
    details = odis_df.loc[odis_df.index.intersection(found_codes)]
    
    results = []
    for _, ref_row in results_df.iterrows():
        codgeo = ref_row['code']
        if codgeo in details.index:
            row = details.loc[codgeo]
            results.append({
                "codgeo": str(codgeo),
                "libgeo": ref_row['label'],
                "bassin_de_vie": str(row['bassin_de_vie']) if 'bassin_de_vie' in row else "N/A",
                "population": int(row['population']) if 'population' in row else 0
            })
    
    logger.info(f"✅ [MCP] Response: Found {len(results)} cities. Top: {[r['libgeo'] for r in results[:3]]}")
    return results

def _compute_top_cities_logic(weights: Dict[str, float], filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes the top 10 cities (communes) based on user criteria.
    """
    logger.info(f"👉 [MCP] Request: compute_top_cities")
    logger.info(f"   Weights: {json.dumps(weights, indent=2, default=str)}")
    logger.info(f"   Filters: {json.dumps(filters, indent=2, default=str)}")
    
    engine = get_scoring_engine()
    
    # 1. Resolve Commune
    commune_input = filters.get('commune_actuelle', 'Paris')
    resolved_commune = commune_input
    if engine and isinstance(commune_input, str):
         if commune_input in engine.df_all_communes.index:
             resolved_commune = commune_input
         else:
             matches = engine.df_all_communes[engine.df_all_communes['libgeo'].str.lower() == commune_input.lower()]
             if not matches.empty:
                 resolved_commune = matches.index[0]
                 logger.info(f"   Resolved city '{commune_input}' -> '{resolved_commune}'")
             else:
                 logger.warning(f"   ⚠️ City '{commune_input}' not found.")

    # 2. Map Inputs
    socle_sel = filters.get('inc_services_core_selection', filters.get('codes_inclusion', []))
    if not socle_sel: socle_sel = cfg.DEFAULT_INC_SERVICES_CORE

    nb_adultes = int(filters.get('nb_adultes', 1))

    # Helper to pad lists
    def pad_list(l, size):
        if not isinstance(l, list): return [[]]*size
        if l and isinstance(l[0], str): return [l] + [[]]*(size-1)
        if len(l) < size: return l + [[]]*(size-len(l))
        return l
        
    c_metiers = pad_list(filters.get('codes_metiers', []), nb_adultes)
    c_formations = pad_list(filters.get('codes_formations', []), nb_adultes)

    specific_needs = filters.get('inc_services_add_selection', filters.get('besoins_autres', []))
    # Alias Fallback
    if not specific_needs and 'codes_inclusion' in filters and filters.get('codes_inclusion') != socle_sel:
        specific_needs = filters.get('codes_inclusion')

    # Binome Protection
    if 'codgeo_voisins' not in DATA_CONTEXT['odis'].columns:
        DATA_CONTEXT['odis']['codgeo_voisins'] = [np.array([], dtype=object) for _ in range(len(DATA_CONTEXT['odis']))]

    def get_weight(key_suffix, default=50):
        if key_suffix in weights: return int(weights[key_suffix])
        if f"poids_{key_suffix}" in weights: return int(weights[f"poids_{key_suffix}"])
        return default

    config = ScoringConfig(
        poids_emploi=get_weight('emploi'),
        poids_logement=get_weight('logement'),
        poids_education=get_weight('education'),
        poids_inclusion=get_weight('inclusion'),
        poids_mobilité=get_weight('mobilité'),
        poids_sante=get_weight('sante'),
        criteria_weights=filters.get('criteria_weights', {}),
        commune_actuelle=resolved_commune,
        loc_search_area=filters.get('loc_search_area', 'departement'),
        loc_custom_code=filters.get('loc_custom_code'),
        loc_custom_type=filters.get('loc_custom_type'),
        nb_adultes=nb_adultes,
        nb_enfants=int(filters.get('nb_enfants', 0)),
        hebergement=filters.get('hebergement', 'Location'),
        logement=filters.get('logement', 'Location'),
        codes_metiers=c_metiers,
        codes_formations=c_formations,
        classe_enfants=filters.get('classe_enfants', []),
        besoin_sante=filters.get('besoin_sante', 'Aucun'),
        inc_services_add_selection=specific_needs,
        inc_services_core_selection=socle_sel,
        inc_asso_add_selection=filters.get('inc_asso_add_selection', [])
    )
    
    # 3. Run Engine
    try:
        processed_gdf = engine.run(config)
    except Exception as e:
        logger.error(f"❌ [MCP] Error: {e}")
        return [{"error": str(e)}]
    
    if processed_gdf.empty:
        logger.info("   [MCP] No results found.")
        return {"cities": [], "criteria_definitions": {}}
        
    top_10 = processed_gdf.head(10).copy()
    
    # 4. Criteria Definitions
    criteria_definitions = {}
    if 'scores_cat' in DATA_CONTEXT:
        scores_cat = DATA_CONTEXT['scores_cat']
        for idx, row in scores_cat.iterrows():
            score_id = row['score']
            cat = row['cat']
            if cat not in criteria_definitions: criteria_definitions[cat] = {}
            
            criteria_definitions[cat][score_id] = {
                "label": row.get('label', score_id),
                "description": row.get('score_affichage', ''),
                "tooltip": row.get('description', '')
            }

    # 5. Build Results
    results = []
    for codgeo, row in top_10.iterrows():
        detailed_scores = {}
        for col in row.index:
            if col.endswith('_cat_score'):
                 cat_name = col.replace('_cat_score', '')
                 if cat_name not in detailed_scores: detailed_scores[cat_name] = {}
                 detailed_scores[cat_name]['score_global'] = float(row[col])
            
            if col.endswith('_scaled') or col.endswith('_scaled_binome'):
                base_col = col.replace('_binome', '')
                found_cat = "autre"
                for cat, items in criteria_definitions.items():
                    if base_col in items:
                        found_cat = cat
                        break
                if found_cat not in detailed_scores: detailed_scores[found_cat] = {}
                detailed_scores[found_cat][col] = float(row[col])

        bdv = row.get('libelle_bassin_de_vie', "N/A")
        details_full = engine.format_city_details(row)
        
        results.append({
            "codgeo": str(codgeo),
            "name": row['libgeo'],
            "bassin_de_vie": bdv,
            "population": int(row.get('population', 0)),
            "score": float(row.get('weighted_score', 0)),
            "detailed_scores": detailed_scores, 
            "details": details_full
        })
        
    logger.info(f"✅ [MCP] Response: Found {len(results)} cities. Top: {[r['name'] for r in results[:3]]}")
    
    return sanitize_for_json({
        "cities": results,
        "criteria_definitions": criteria_definitions
    })

@mcp.tool()
def compute_top_cities(weights: Dict[str, float], filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes the top 10 cities (communes) based on user criteria.
    
    Args:
        weights: Dictionary of weights (0-100) for categories (emploi, logement, education, inclusion, mobilité, sante).
        filters: Dictionary of filter criteria (commune_actuelle, loc_search_area, nb_adultes, etc.).
    """
    return _compute_top_cities_logic(weights, filters)

def _get_city_details_logic(codgeo: str) -> Dict[str, Any]:
    logger.info(f"👉 [MCP] Request: get_city_details")
    logger.info(f"   Codgeo: '{codgeo}'")
    
    engine = get_scoring_engine()
    try:
        details = engine.get_city_details(codgeo)
    except Exception as e:
        logger.error(f"❌ [MCP] Error in get_city_details: {e}")
        return {"error": str(e)}
        
    logger.info(f"✅ [MCP] Response: Found details for {details.get('identity', {}).get('nom', 'Unknown')}")
    return sanitize_for_json(details)

@mcp.tool()
def get_city_details(codgeo: str) -> Dict[str, Any]:
    """
    Retrieves detailed information about a specific city.
    Useful for "Learn More" or answering specific questions about a town.
    """
    return _get_city_details_logic(codgeo)

if __name__ == "__main__":
    mcp.run()
