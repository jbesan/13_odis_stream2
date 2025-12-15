
from fastmcp import FastMCP
from typing import Dict, Any, List
import json
import pandas as pd
import geopandas as gpd
from data_loader import load_all_data_raw
from scoring import ScoringEngine
from config import ScoringConfig
import logging

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
        global_stats={} # TODO: Compute or load global stats if needed for scaling
    )

def _search_referentiels_logic(query: str, domain: str = None) -> List[Dict[str, str]]:
    """
    Searches for codes in the ODIS referentials (Jobs, Formations, Inclusion).
    
    Args:
        query: The search term (e.g. "Boulanger", "Comptabilité").
        domain: Optional filter ('fap_codes', 'formation_codes', 'waldec_codes', 'inclusion_services').
    """
    logger.info(f"👉 [MCP] Request: search_referentiels")
    logger.info(f"   Query: '{query}', Domain: '{domain}'")
    
    if 'referentiels_raw' not in DATA_CONTEXT:
        logger.warning("   ⚠️ Referentiels data not available.")
        return []

    df = DATA_CONTEXT['referentiels_raw']
    if df.empty:
        return []

    # 1. Filter by domain if provided
    if domain:
        df = df[df['key'] == domain]
    
    
    # 2. Robust Search Logic
    STOP_WORDS = {
        "le", "la", "les", "l", "d", "de", "du", "des", 
        "un", "une", "et", "ou", "au", "aux", "en", 
        "par", "pour", "sur", "dans"
    }
    
    query_norm = query.lower()
    if 'label' not in df.columns:
        return []

    # Prepare DataFrame for scoring
    work_df = df.copy()
    work_df['label_lower'] = work_df['label'].astype(str).str.lower()
    
    # 2.1 Calculate Score
    def calculate_relevance(row_label: str) -> int:
        score = 0
        # A. Phrase Match
        if query_norm in row_label:
            score += 100
        
        # B. Token Match
        # Split query, remove stopwords and short words
        raw_tokens = query_norm.replace("'", " ").split()
        query_tokens = [
            t for t in raw_tokens 
            if len(t) > 1 and t not in STOP_WORDS
        ]
        
        if not query_tokens:
             return score
             
        matches = 0
        for token in query_tokens:
            if token in row_label:
                matches += 1
        
        # Add score based on coverage (boosted multiplier)
        if matches > 0:
            score += (matches * 20)
            
        return score

    # Apply scoring (vectorized-ish or apply)
    # Since dataset is small (<1000 rows usually), apply is fine.
    work_df['score'] = work_df['label_lower'].apply(calculate_relevance)
    
    # Filter non-zero scores
    results_df = work_df[work_df['score'] > 0]
    
    # Sort by score descending
    results_df = results_df.sort_values(by='score', ascending=False)
    
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

def _compute_top_cities_logic(weights: Dict[str, float], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Computes the top 10 cities (communes) based on user criteria.
    
    Args:
        weights: Dictionary of weights (0-100) for categories (emploi, logement, education, inclusion, mobilité, sante).
        filters: Dictionary of filter criteria (commune_actuelle, loc_distance_km, nb_adultes, etc.).
        
    Returns:
        List of top 10 cities with their detailed scores.
    """
    logger.info(f"👉 [MCP] Request: compute_top_cities")
    logger.info(f"   Weights: {json.dumps(weights, default=str)}")
    logger.info(f"   Filters: {json.dumps(filters, default=str)}")
    
    engine = get_scoring_engine()
    
    # ... resolution logic ...
    
    # [Lines 119-186 unchanged logic, just logging updates at start]
    # For constraints of replace_file_content, I need to be careful not to rewrite the whole function if possible or just rewrite the logging parts.
    # But the function is large. I will just rewrite the logging at the start and end.
    
    # Actually, I will rewrite the whole function to be safe and clean.
    
    # 1. Resolve Commune Name to Code if necessary
    commune_input = filters.get('commune_actuelle', 'Paris')
    # ... (Keep existing resolution logic) ...
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

    # 2. Map Inputs (Keep existing)
    config = ScoringConfig(
        poids_emploi=int(weights.get('emploi', 50)),
        poids_logement=int(weights.get('logement', 50)),
        poids_education=int(weights.get('education', 50)),
        poids_inclusion=int(weights.get('inclusion', 50)),
        poids_mobilité=int(weights.get('mobilité', 50)),
        poids_sante=int(weights.get('sante', 50)),
        criteria_weights=filters.get('criteria_weights', {}),
        commune_actuelle=resolved_commune,
        loc_distance_km=filters.get('loc_distance_km', 'departement'),
        nb_adultes=int(filters.get('nb_adultes', 1)),
        nb_enfants=int(filters.get('nb_enfants', 0)),
        hebergement=filters.get('hebergement', 'Location'),
        logement=filters.get('logement', 'Location'),
        codes_metiers=filters.get('codes_metiers', []) + [[]] * (int(filters.get('nb_adultes', 1)) - len(filters.get('codes_metiers', []))),
        codes_formations=filters.get('codes_formations', []) + [[]] * (int(filters.get('nb_adultes', 1)) - len(filters.get('codes_formations', []))),
        classe_enfants=filters.get('classe_enfants', []),
        besoin_sante=filters.get('besoin_sante', 'Aucun'),
        besoins_autres=filters.get('besoins_autres', []),
        socle_admin_selection=filters.get('socle_admin_selection', []),
        affinite_selection=filters.get('affinite_selection', []),
        binome_penalty=float(filters.get('binome_penalty', 0.1)),
        pop_min=int(filters.get('pop_min', 0))
    )
    
    # 2. Run Engine
    view_level = filters.get('view_level', 'Communes')
    try:
        processed_gdf, _ = engine.run(config, view_level=view_level)
    except Exception as e:
        logger.error(f"❌ [MCP] Error: {e}")
        return [{"error": str(e)}]
    
    # 3. Format Output
    if processed_gdf.empty:
        logger.info("   [MCP] No results found.")
        return []
        
    # Take top 10
    top_10 = processed_gdf.head(10).copy()
    
    results = []
    for codgeo, row in top_10.iterrows():
        cat_scores = {col: float(row[col]) for col in row.index if col.endswith('_cat_score')}
        item = {
            "codgeo": str(codgeo),
            "name": row['libgeo'],
            "score": float(row['weighted_score']) if 'weighted_score' in row else 0.0,
            "population": int(row['population']) if 'population' in row else 0,
            "category_scores": cat_scores,
        }
        results.append(item)
        
    # Log Response Summary
    top_names = [r['name'] for r in results[:5]]
    logger.info(f"✅ [MCP] Response: Found {len(results)} cities. Top 5: {top_names}")
    return results

@mcp.tool()
def compute_top_cities(weights: Dict[str, float], filters: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Computes the top 10 cities (communes) based on user criteria.
    
    Args:
        weights: Dictionary of weights (0-100) for categories (emploi, logement, education, inclusion, mobilité, sante).
        filters: Dictionary of filter criteria (commune_actuelle, loc_distance_km, nb_adultes, etc.).
        
    Returns:
        List of top 10 cities with their detailed scores.
    """
    return _compute_top_cities_logic(weights, filters)

if __name__ == "__main__":
    # For testing or running standalone
    mcp.run()
