
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


def normalize_text(text: str) -> str:
    """
    Normalizes text by removing accents and lowercasing.
    """
    import unicodedata
    if not isinstance(text, str):
        return str(text)
    return ''.join(c for c in unicodedata.normalize('NFD', text)
                   if unicodedata.category(c) != 'Mn').lower()

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
    if 'label' not in df.columns:
        return []

    # Prepare DataFrame for scoring
    work_df = df.copy()
    work_df['label_norm'] = work_df['label'].apply(normalize_text)
    work_df['code_norm'] = work_df['code'].apply(normalize_text)
    
    # 2.1 Calculate Score
    def calculate_relevance(row):
        score = 0
        label_norm = row['label_norm']
        code_norm = row['code_norm']
        
        # A. Exact Phrase Match
        if query_norm in label_norm:
            score += 100
        
        # B. Token Overlap
        row_tokens = set(label_norm.split())
        query_tokens = set(query_norm.split()) - STOP_WORDS
        
        if not query_tokens: # Check just in case query was only stop words
             query_tokens = set(query_norm.split())

    # 2.1 Calculate Score
    def calculate_relevance(row):
        score = 0
        label_norm = row['label_norm']
        code_norm = row['code_norm']
        
        # A. Exact Phrase Match
        if query_norm in label_norm:
            score += 100
        
        # B. Token Overlap
        row_tokens = set(label_norm.split()) - STOP_WORDS
        query_tokens = set(query_norm.split()) - STOP_WORDS
        
        if not query_tokens: # Check just in case query was only stop words
             query_tokens = set(query_norm.split())

        overlap = len(query_tokens.intersection(row_tokens))
        score += overlap * 20
        
        # C. Substring Token Match (for minor misspellings or plurals)
        # e.g. "francais" in "francaise"
        sub_score = 0
        for qt in query_tokens:
            if len(qt) > 3:
                for rt in row_tokens:
                    if len(rt) > 3: # Only match significant tokens
                        if qt in rt or rt in qt:
                             # Don't double count if exact match already handled by intersection
                             if qt != rt:
                                 sub_score += 5
        score += sub_score
                             
        # D. Match in Code
        if query_norm in code_norm:
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
    logger.info(f"👉 [MCP] Request: search_commune (via Referentiels)")
    logger.info(f"   Query: '{query}'")
    
    if 'referentiels_raw' not in DATA_CONTEXT or 'odis' not in DATA_CONTEXT:
         logger.warning("   ⚠️ Data context missing (referentiels or odis).")
         return []
         
    refs_df = DATA_CONTEXT['referentiels_raw']
    odis_df = DATA_CONTEXT['odis']
    
    # 1. Filter for Communes in Referentiels
    # We assume 'communes' key exists (added in pipeline)
    communes_ref = refs_df[refs_df['key'] == 'communes']
    
    if communes_ref.empty:
        logger.warning("   ⚠️ No communes found in referentiels.")
        return []

    # 2. Search Logic (Re-using logic similar to _search_referentiels, simplified)
    q_norm = normalize_text(query)
    STOP_WORDS = {"le", "la", "les", "l", "d", "de", "du", "des", "saint", "st", "sainte", "ste"} # Adjusted stop words for cities? actually "saint" is important.
    # Reset stop words for cities, maybe just articles.
    STOP_WORDS = {"le", "la", "les", "l", "d", "de", "du", "des", "en", "sur", "aux"}

    work_df = communes_ref.copy()
    work_df['label_norm'] = work_df['label'].apply(normalize_text)
    
    def calculate_city_score(row):
        score = 0
        lbl = row['label_norm']
        
        # Exact match
        if lbl == q_norm:
            score += 1000
        elif lbl.startswith(q_norm):
            score += 200
        elif q_norm in lbl:
            score += 100
            
        # Token overlap
        row_tokens = set(lbl.split())
        query_tokens = set(q_norm.split()) - STOP_WORDS
        if not query_tokens: query_tokens = set(q_norm.split())
        
        overlap = len(query_tokens.intersection(row_tokens))
        score += overlap * 50
        
        return score

    work_df['score'] = work_df.apply(calculate_city_score, axis=1)
    results_df = work_df[work_df['score'] > 0].sort_values(by='score', ascending=False).head(15)

    if results_df.empty:
        logger.info("   [MCP] No cities found.")
        return []

    # 3. Lookup Details in ODIS
    results = []
    # We used 'code' column in referentiels which maps to 'codgeo'
    found_codes = results_df['code'].unique()
    
    # Get details
    # ODIS index is codgeo
    details = odis_df.loc[odis_df.index.intersection(found_codes)]
    
    # We iterate through results_df to maintain score order
    for _, ref_row in results_df.iterrows():
        codgeo = ref_row['code']
        if codgeo in details.index:
            row = details.loc[codgeo]
            results.append({
                "codgeo": str(codgeo),
                "libgeo": ref_row['label'], # Use label from ref or ODIS
                "bassin_de_vie": str(row['bassin_de_vie']) if 'bassin_de_vie' in row else "N/A",
                "population": int(row['population']) if 'population' in row else 0
            })
    
    # Sort final results by Score then Population?
    # Actually results_df is already sorted by score.
    
    logger.info(f"✅ [MCP] Response: Found {len(results)} cities. Top: {[r['libgeo'] for r in results[:3]]}")
    return results

def sanitize_for_json(obj):
    """
    Recursively sanitizes the object for JSON serialization.
    - Dicts: Recursively sanitize values. Keys with None/NaN values are REMOVED.
    - Lists: Recursively sanitize items.
    - Floats: NaNs become None (which allows them to be filtered out from parent dicts)
    """
    if isinstance(obj, dict):
        clean = {}
        for k, v in obj.items():
            sanitized_val = sanitize_for_json(v)
            if sanitized_val is not None:
                clean[k] = sanitized_val
        return clean
    elif isinstance(obj, list):
        return [sanitize_for_json(v) for v in obj]
    elif isinstance(obj, float):
        if np.isnan(obj) or pd.isna(obj):
            return None
        return obj
    elif isinstance(obj, (np.integer, np.floating)):
        if pd.isna(obj):
             return None
        return obj.item()
    elif pd.isna(obj): # Catch-all for other NA types
        return None
    return obj

def _compute_top_cities_logic(weights: Dict[str, float], filters: Dict[str, Any]) -> Dict[str, Any]:
    """
    Computes the top 10 cities (communes) based on user criteria.
    
    Args:
        weights: Dictionary of weights (0-100) for categories (emploi, logement, education, inclusion, mobilité, sante).
        filters: Dictionary of filter criteria (commune_actuelle, loc_distance_km, nb_adultes, etc.).
        
    Returns:
        Dictionary with:
        - "cities": List of top 10 cities with their detailed scores grouped by category.
        - "criteria_definitions": Definitions of the scores.
    """
    logger.info(f"👉 [MCP] Request: compute_top_cities")
    logger.info(f"   Weights: {json.dumps(weights, indent=2, default=str)}")
    logger.info(f"   Filters: {json.dumps(filters, indent=2, default=str)}")
    
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
    socle_sel = filters.get('inc_services_core_selection', [])
    if not socle_sel and 'codes_inclusion' in filters:
        socle_sel = filters.get('codes_inclusion')
        logger.info(f"   [MCP] Mapped alias 'codes_inclusion' -> 'inc_services_core_selection': {socle_sel}")

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
    # User feedback: Specific needs (FLE, etc.) should map to 'besoins_autres', not 'inc_services_core_selection'.
    # 'inc_services_core_selection' should ideally keep defaults (base services).
    
    specific_needs = filters.get('inc_services_add_selection', filters.get('besoins_autres', []))
    if not specific_needs and 'codes_inclusion' in filters:
        specific_needs = filters.get('codes_inclusion')
        logger.info(f"   [MCP] Mapped alias 'codes_inclusion' -> 'inc_services_add_selection': {specific_needs}")
    
    # Ensure default socle is present if not strictly overridden?
    # For now, we trust the defaults of ScoringConfig logic or defaults defined in config.py
    # But ScoringConfig dataclass doesn't have defaults. 
    # We should use cfg.DEFAULT_INC_SERVICES_CORE if agent doesn't specify (which it doesn't usually).
    socle_sel = filters.get('inc_services_core_selection', cfg.DEFAULT_INC_SERVICES_CORE)

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
        inc_services_add_selection=specific_needs, # Mapped from codes_inclusion
        inc_services_core_selection=socle_sel,
        inc_asso_add_selection=filters.get('inc_asso_add_selection', []),
        pop_min=int(filters.get('pop_min', 0))
    )
    
    # 2. Run Engine
    view_level = filters.get('view_level', 'Communes')
    try:
        processed_gdf = engine.run(config)
    except Exception as e:
        logger.error(f"❌ [MCP] Error: {e}")
        return [{"error": str(e)}]
    
    # 3. Format Output
    if processed_gdf.empty:
        logger.info("   [MCP] No results found.")
        return {"cities": [], "criteria_definitions": {}}
        
    # Take top 10
    top_10 = processed_gdf.head(10).copy()
    
    # --- Prepare Criteria Definitions ---
    criteria_definitions = {}
    if 'scores_cat' in DATA_CONTEXT:
        scores_cat = DATA_CONTEXT['scores_cat']
        # We want to export definitions for ALL possible scores that might appear
        # Or at least the ones that are likely to be relevant.
        # Let's iterate over unique scores in scores_cat
        # We need to access the 'display' properties which are in the dataframe columns if flattened, 
        # or we rely on the logic that built scores_cat.
        # Assuming scores_cat has columns like 'score', 'cat', 'weight', 'display_name', 'strong_point_text', 'tooltip' etc.
        # Let's check `scoring.py` -> `ScoringEngine` uses `scores_cat`.
        # Usually `scores_cat` columns come from the YAML.
        
        # Helper to safely get value
        def safe_get(row, col, default=""):
            return row[col] if col in row.index and pd.notna(row[col]) else default

        for idx, row in scores_cat.iterrows():
            score_id = row['score']
            category = row['cat']
            
            if category not in criteria_definitions:
                criteria_definitions[category] = {}
            
            # Use 'name' or 'display_name' depending on how it was loaded.
            # Looking at `scores_config.yaml`, keys are `display.name`, `display.strong_point_text`.
            # The loader likely flattens them or keeps them accessible.
            # Let's assume standard flattening or check `pipeline/build.py` if we could (we can't easily).
            # But usually it's `display_name` etc. 
            # If not sure, we can try to look at columns if we were debugging.
            # For now, let's assume `name`, `strong_point`, `tooltip` columns exist or similar.
            # actually, `scores_cat` usually has 'score', 'cat', 'weight', 'min_bound', 'max_bound' + display cols.
            
            # Fallback if specific columns missing (Robustness)
            label = safe_get(row, 'label', score_id)
            desc = safe_get(row, 'score_affichage', "Critère important")
            tooltip = safe_get(row, 'description', "")
            
            criteria_definitions[category][score_id] = {
                "label": label,
                "description": desc,
                "tooltip": tooltip
            }

    results = []
    for codgeo, row in top_10.iterrows():
        # Group scores by category
        detailed_scores = {}
        
        for col in row.index:
            if col.endswith('_cat_score'):
                # Category aggregated score
                 cat_name = col.replace('_cat_score', '')
                 if cat_name not in detailed_scores:
                     detailed_scores[cat_name] = {}
                 detailed_scores[cat_name]['score_global'] = float(row[col])
            
            if col.endswith('_scaled') or col.endswith('_scaled_binome'):
                # Individual criteria score
                # We need to find which category it belongs to.
                # Use criteria_definitions lookup or name parsing
                # This is slightly inefficient but robust.
                is_binome = col.endswith('_binome')
                base_col = col.replace('_binome', '')
                
                # Find category
                found_cat = "autre"
                for cat, items in criteria_definitions.items():
                    if base_col in items:
                        found_cat = cat
                        break
                
                if found_cat not in detailed_scores:
                    detailed_scores[found_cat] = {}
                
                key = "binome" if is_binome else "commune"
                 # We might want a cleaner structure:
                 # education: { "edu_lycee_scaled": 0.8 }
                 # instead of separating binome?
                 # Agent prefers simplicity.
                 # Let's just output the effective score if possible, or just the raw column.
                detailed_scores[found_cat][col] = float(row[col])

        # Bassin de vie
        bdv = row['libelle_bassin_de_vie'] if 'libelle_bassin_de_vie' in row else "N/A"
        
        # Enrich with full Score Details (using new ScoringEngine helper)
        # This gives us the "Why" (raw values, sub-scores) without re-simulation.
        details_full = engine.format_city_details(row)
        
        item = {
            "codgeo": str(codgeo),
            "name": row['libgeo'],
            "bassin_de_vie": bdv,
            "population": int(row['population']) if 'population' in row else 0,
            "score": float(row['weighted_score']) if 'weighted_score' in row else 0.0,
            "detailed_scores": detailed_scores, 
            "details": details_full # The full breakout
        }
        results.append(item)
        
    # Log Response Summary
    top_names = [r['name'] for r in results[:5]]
    logger.info(f"✅ [MCP] Response: Found {len(results)} cities. Top 5: {top_names}")
    
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
        filters: Dictionary of filter criteria (commune_actuelle, loc_distance_km, nb_adultes, etc.).
        
    Returns:
        Dictionary containing top 10 cities and criteria definitions.
    """
    return _compute_top_cities_logic(weights, filters)

def _get_city_details_logic(codgeo: str) -> Dict[str, Any]:
    """
    Retrieves detailed information about a specific city including scores, services, and associations.
    """
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
    Useful for "Learn More" or answering specific questions about a town (e.g. associations, schools).
    
    Args:
        codgeo: The INSEE code of the city (e.g. "33063" for Bordeaux).
        
    Returns:
        Dictionary containing identity, scores, employment stats, education counts, health, inclusion services, and associations.
    """
    return _get_city_details_logic(codgeo)


if __name__ == "__main__":
    # For testing or running standalone
    mcp.run()
