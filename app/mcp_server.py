
from fastmcp import FastMCP
from typing import Dict, Any, List
import json
import pandas as pd
import geopandas as gpd
import numpy as np
from data_loader import load_all_data_raw
from scoring import ScoringEngine
from config import ScoringConfig
import config as cfg
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

    # 2. Standard Dataframe Search
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
        
        # B. Token Overlap
        row_tokens = set(row_label.split())
        query_tokens = set(query_norm.split()) - STOP_WORDS
        
        if not query_tokens: # Check just in case query was only stop words
             query_tokens = set(query_norm.split())

        overlap = len(query_tokens.intersection(row_tokens))
        score += overlap * 20
        
        return score

    work_df['score'] = work_df['label_lower'].apply(calculate_relevance)
    
    
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

    # 2. Map Inputs to ScoringConfig
    # Robustness: Handle aliasing
    socle_sel = filters.get('socle_admin_selection', [])
    if not socle_sel and 'codes_inclusion' in filters:
        socle_sel = filters.get('codes_inclusion')
        logger.info(f"   [MCP] Mapped alias 'codes_inclusion' -> 'socle_admin_selection': {socle_sel}")

    # Robustness: Coerce codes_metiers to List[List]
    raw_metiers = filters.get('codes_metiers', [])
    c_metiers = []
    nb_adultes = int(filters.get('nb_adultes', 1))

    if isinstance(raw_metiers, list):
         if raw_metiers and isinstance(raw_metiers[0], str):
             # Detected flat list ["A", "B"] -> Assume one code per adult, or all for first?
             # Heuristic: Assign to first adult.
             c_metiers = [raw_metiers] + [[]]*(nb_adultes-1)
         else:
             c_metiers = raw_metiers
    
    # Pad to nb_adultes
    if len(c_metiers) < nb_adultes:
        c_metiers += [[]] * (nb_adultes - len(c_metiers))

    # Same for Formations
    raw_formations = filters.get('codes_formations', [])
    c_formations = []
    if isinstance(raw_formations, list):
         if raw_formations and isinstance(raw_formations[0], str):
             c_formations = [raw_formations] + [[]]*(nb_adultes-1)
         else:
             c_formations = raw_formations
    
    if len(c_formations) < nb_adultes:
        c_formations += [[]] * (nb_adultes - len(c_formations))

    # Provide safe defaults for missing fields
    # Fix: Agent sends 'poids_education' but config expects int values.
    # The keys in `weights` dict from Agent are like 'poids_education'.
    # We map them correctly.
    
    # Robustness: Handle aliasing for Inclusion Services
    # User feedback: Specific needs (FLE, etc.) should map to 'besoins_autres', not 'socle_admin_selection'.
    # 'socle_admin_selection' should ideally keep defaults (base services).
    
    specific_needs = filters.get('besoins_autres', [])
    if not specific_needs and 'codes_inclusion' in filters:
        specific_needs = filters.get('codes_inclusion')
        logger.info(f"   [MCP] Mapped alias 'codes_inclusion' -> 'besoins_autres': {specific_needs}")
    
    # Ensure default socle is present if not strictly overridden?
    # For now, we trust the defaults of ScoringConfig logic or defaults defined in config.py
    # But ScoringConfig dataclass doesn't have defaults. 
    # We should use cfg.DEFAULT_SOCLE_ADMIN if agent doesn't specify (which it doesn't usually).
    socle_sel = filters.get('socle_admin_selection', cfg.DEFAULT_SOCLE_ADMIN)

    # Robustness: Check for codgeo_voisins in loaded data
    if 'codgeo_voisins' not in DATA_CONTEXT['odis'].columns:
        logger.warning("⚠️ 'codgeo_voisins' missing from ODIS data. Disabling Binome logic (adding empty col).")
        DATA_CONTEXT['odis']['codgeo_voisins'] = [np.array([], dtype=object) for _ in range(len(DATA_CONTEXT['odis']))]

    def get_weight(key_suffix, default=50):
        # Try exact match (e.g. 'emploi')
        if key_suffix in weights:
             return int(weights[key_suffix])
        # Try with prefix (e.g. 'poids_emploi')
        if f"poids_{key_suffix}" in weights:
             return int(weights[f"poids_{key_suffix}"])
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
        loc_distance_km=filters.get('loc_distance_km', 'departement'),
        nb_adultes=nb_adultes,
        nb_enfants=int(filters.get('nb_enfants', 0)),
        hebergement=filters.get('hebergement', 'Location'),
        logement=filters.get('logement', 'Location'),
        codes_metiers=c_metiers,
        codes_formations=c_formations,
        classe_enfants=filters.get('classe_enfants', []),
        besoin_sante=filters.get('besoin_sante', 'Aucun'),
        besoins_autres=specific_needs, # Mapped from codes_inclusion
        socle_admin_selection=socle_sel,
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
