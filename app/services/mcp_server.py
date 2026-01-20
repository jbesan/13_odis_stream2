
from fastmcp import FastMCP
from typing import Dict, Any, List, Optional, Union
import json
import pandas as pd
import geopandas as gpd
import numpy as np
from utils.data_loader import load_all_data_raw
from core.scoring import ScoringEngine
from core.models import ScoringConfig, SearchCriterias
import config as cfg
import logging
from utils.common import normalize_text, calculate_fuzzy_match_score, sanitize_for_json
import os
import googlemaps

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")

# Initialize FastMCP Server
mcp = FastMCP("ODIS-Core")

# Global State for Data (Loaded on startup)
DATA_CONTEXT = {}

def set_data_context(context: Dict[str, Any]) -> None:
    """Allows external injection of data context (e.g. from Streamlit cache)"""
    global DATA_CONTEXT
    DATA_CONTEXT = context
    # logger.info("Data Context injected externally.")

def ensure_data_context() -> None:
    """Ensures data context is loaded if missing."""
    global DATA_CONTEXT
    if not DATA_CONTEXT:
        try:
            DATA_CONTEXT = load_all_data_raw()
        except Exception as e:
            logger.error(f"Failed to load data context: {e}")
            raise RuntimeError(f"Failed to load ODIS data: {e}")

def get_scoring_engine() -> ScoringEngine:
    """
    Lazy loads the data and returns the ScoringEngine instance.
    """
    ensure_data_context()
            
    return ScoringEngine(
        df_all_communes=DATA_CONTEXT['odis'],
        df_bv_geo=DATA_CONTEXT['bv_geo'],
        df_area_geo=DATA_CONTEXT['area_geo'],
        scores_cat=DATA_CONTEXT['scores_cat'],
        incl_index=DATA_CONTEXT['incl_index'],
        associations_data=DATA_CONTEXT['associations_data'],
        formations_data=DATA_CONTEXT['formations_data'],
        codformations_index=DATA_CONTEXT['codformations_index'],
        rome_index=DATA_CONTEXT.get('rome_index', pd.DataFrame()),

        global_stats={}, # TODO: Compute or load global stats if needed for scaling
        refugee_associations_data=DATA_CONTEXT.get('refugee_associations_data', pd.DataFrame()),
        odis_asso_mini_data=DATA_CONTEXT.get('odis_asso_mini_data', pd.DataFrame()),
        live_jobs_data=DATA_CONTEXT.get('live_jobs_data', pd.DataFrame())
    )

def _search_referentiels_logic(query: str, domain: str) -> List[Dict[str, Any]]:
    """Recherche des codes officiels (Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels."""

    ensure_data_context()
    print(f"Search Referentiels for Query: {query}, Domain: {domain}")

    if 'referentiels_raw' not in DATA_CONTEXT:
        logger.warning("   ⚠️ Referentiels data not available.")
        return []

    df = DATA_CONTEXT['referentiels_raw']
    if df.empty:
        return []

    # 1. Filter by Domain
    if domain:
        df = df[df['key'] == domain]
    else:
        logger.error("   ⚠️ No domain specified.")
    
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

    # 1. Calculate relevance score
    work_df['score'] = work_df.apply(calculate_relevance, axis=1)
    
    # 2. Sort by relevance score descending
    results_df = work_df[work_df['score'] > 0].sort_values(by='score', ascending=False)
    
    # 3. Format Output
    results_df = results_df.head(10)
    results = []
    for _, row in results_df.iterrows():
        results.append({
            "code": row['code'],
            "label": row['label'],
            "type": row['key']
        })
        
    # logger.info(f"✅ [MCP] Response: Found {len(results)} matches.")
    if results:
         top_summary = [f"{r['label']}" for r in results[:3]]
        #  logger.info(f"   Top matches: {top_summary}")
    print(f"✅ [MCP] Response: Found {len(results)} matches.")
    return results

def _get_labels_for_codes_logic(codes: List[str]) -> Dict[str, str]:
    """
    Returns a mapping of code -> label for a list of codes (any referential).
    """
    ensure_data_context()
    if 'referentiels_raw' not in DATA_CONTEXT:
        return {}
    
    df = DATA_CONTEXT['referentiels_raw']
    # Filter by code
    subset = df[df['code'].isin(codes)]
    return dict(zip(subset['code'].astype(str), subset['label'].astype(str)))


@mcp.tool()
def search_referentiels(query: str, domain: Optional[str] = None) -> Dict[str, Any]:
    """
    Recherche des codes officiels (Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels.
    
    Args:
        query (str): Recherche à effectuer.
        domain (str): Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements'].
    
    Returns:
       List[Dict[str, Any]]: Liste des codes officiels correspondants.
    """

    try:
        if not query:
            return {"error": "Missing 'query' parameter."}
        
        valid_domains = ['rome_codes', 'formation_codes', 'inclusion_services', 'waldec_codes', 'regions', 'departements']
        if domain and domain not in valid_domains:
            return {"error": f"Invalid domain: {domain}. Must be one of {valid_domains}"}

        results = _search_referentiels_logic(query, domain)
        return {"results": results}
    except Exception as e:
        logger.exception(f"❌ [MCP] search_referentiels failed: {e}")
        return {"error": str(e)}




def _search_commune_logic(query: str) -> List[Dict[str, str]]:
    """
    Searches for French cities using Referentiels first, then ODIS for details.
    """
    ensure_data_context()
    # logger.info(f"👉 [MCP] Request: search_commune")
    # logger.info(f"   Query: '{query}'")
    
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

    work_df['_score'] = [calculate_city_score(row) for _, row in work_df.iterrows()]
    results_df = work_df[work_df['_score'] > 0].sort_values(by='_score', ascending=False).head(15)

    if results_df.empty:
        logger.warning("   [MCP] No cities found.")
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
    
    # logger.info(f"✅ [MCP] Response: Found {len(results)} cities. Top: {[r['libgeo'] for r in results[:3]]}")
    return results


@mcp.tool()
def search_commune(query: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Searches for a French city to get its INSEE code.

    Args:
        query: City name provided by the user (e.g. 'Bordeaux').
    """
    try:
        if not query:
            return {"error": "Missing 'query' parameter."}
        return _search_commune_logic(query)
    except Exception as e:
        logger.exception(f"❌ [MCP] search_commune failed: {e}")
        return {"error": str(e)}


def _compute_top_cities_logic(criteria: Union[SearchCriterias, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Computes scores for all communes in search area and returns the top 5 cities (communes) based on user criteria.
    Weights are resolved internally based on the weight_profile.
    """
    # Robustness: Handle both Pydantic model and raw dict (from LLM tools)
    if isinstance(criteria, dict):
        criteria_obj = SearchCriterias(**criteria)
    else:
        criteria_obj = criteria

    # 0. Resolve Weights from Profile
    profile_name = criteria_obj.weight_profile or "Équilibré"
    weights = cfg.WEIGHT_PROFILES.get(profile_name, cfg.WEIGHT_PROFILES["Équilibré"])
    
    # User-requested LOUD logging
    
    # filters is the dictionary representation of criteria_obj for internal engine mapping
    filters = criteria_obj.model_dump()
    
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
                #  logger.info(f"   Resolved city '{commune_input}' -> '{resolved_commune}'")
             else:
                 logger.warning(f"   ⚠️ City '{commune_input}' not found.")

    # 2. Map Inputs (Geography)
    loc_search_area = filters.get('loc_search_area', 'departement')
    loc_search_code = filters.get('loc_search_code')

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
        criteria_weights=criteria_obj.criteria_weights,
        weight_profile=profile_name,
        commune_actuelle=resolved_commune,
        loc_search_area=loc_search_area,
        loc_search_code=loc_search_code,
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
        processed_gdf = engine.run(config, log_prefix="chatbot")
    except Exception as e:
        logger.error(f"❌ [MCP] Error: {e}")
        return {"error": str(e)}
    
    if processed_gdf.empty:
        logger.info("   [MCP] No results found.")
        return {"cities": [], "criteria_definitions": {}}
        
    top_5 = processed_gdf.head(5).copy()
    
    # 4. Criteria Definitions
    criteria_definitions: Dict[str, Any] = {}
    if not engine.scores_cat.empty:
        for idx, row in engine.scores_cat.iterrows():
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
    for codgeo, row in top_5.iterrows():
        detailed_scores: Dict[str, Any] = {}
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
        
        # AI Pruning: Streamline details but keep data not yet handled by other tools
        # Education and Formations stay here for now. Associations are handled by SCOUT.
        details_streamlined = {
            "identity": details_full.get("identity", {}),
            "scores": details_full.get("scores", {}),
            "education": details_full.get("education", {}),
            "emploi": {
                "top_metiers": details_full.get("emploi", {}).get("top_metiers", []),
                "formations": details_full.get("emploi", {}).get("formations", [])
            }
        }
        
        results.append({
            "codgeo": str(codgeo),
            "name": row['libgeo'],
            "bassin_de_vie": bdv,
            "population": int(row.get('population', 0)),
            "score": float(row.get('weighted_score', 0)),
            "detailed_scores": detailed_scores, 
            "details": details_streamlined
        })
        
    logger.debug(f"✅ [MCP] Response: Found {len(results)} cities. Top: {[r['name'] for r in results[:3]]}")
    
    return sanitize_for_json({
        "cities": results,
        "criteria_definitions": criteria_definitions
    })

@mcp.tool()
def compute_top_cities(criteria: SearchCriterias) -> Dict[str, Any]:
    """
    Computes scores for all communes in search area and returns the top 10 cities (communes) based on user criteria.
    
    Args:
        criteria: Search criteria including location, weights profile (Famille, Santé, Economique, Équilibré), 
                 and specific needs (metiers, formations, etc.).
    """

    return _compute_top_cities_logic(criteria)


def _search_places_logic(queries: List[str], location: str) -> Dict[str, Any]:
    ensure_data_context()
    # logger.info(f"🗺️ [MCP] Request: search_places '{queries}' in {location}")
    try:
        gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY")
        if not gmaps_key:
            return {"error": "Clé Maps manquante."}
        
        gmaps = googlemaps.Client(key=gmaps_key)
        results = []
        # Limit to 3 queries to avoid long wait times
        for q in queries[:3]:
            # logger.info(f"   🔎 [MCP] Google Maps Query: '{q}' near {location}")
            res = gmaps.places(query=f"{q} near {location}, France", language="fr")
            count = 0
            for p in res.get('results', [])[:3]:
                results.append({
                    "name": p.get("name"),
                    "address": p.get("formatted_address"),
                    "rating": p.get("rating"),
                    "types": p.get("types"),
                    "business_status": p.get("business_status")
                })
                count += 1
            # logger.info(f"   ✅ [MCP] Found {count} results for '{q}'")
            
        # logger.info(f"✅ [MCP] search_places finished. Total pruned results: {len(results)}")
        return sanitize_for_json({"type": "places", "data": results})
    except Exception as e:
        logger.error(f"❌ [MCP] search_places failed: {e}")
        return {"error": str(e)}

@mcp.tool()
def search_places(queries: List[str], location: str) -> Dict[str, Any]:
    """
    Recherche des lieux (POIs), commerces, associations ou services dans un secteur donné.
    Grounding Spatial (Ground 3).
    """
    try:
        if not queries or not location:
            return {"error": "Both 'queries' (list) and 'location' (string) must be provided."}
        return _search_places_logic(queries, location)
    except Exception as e:
        logger.exception(f"❌ [MCP] search_places failed: {e}")
        return {"error": str(e)}


def _search_refugee_associations_logic(codgeo: str) -> List[Dict[str, Any]]:
    """
    Internal logic for looking up refugee associations.
    Accepts INSEE code.
    """
    ensure_data_context()
    if 'refugee_associations_data' not in DATA_CONTEXT or DATA_CONTEXT['refugee_associations_data'].empty:
        logger.warning(f"⚠️ [MCP] refugee_associations_data not available or empty in DATA_CONTEXT.")
        return []
    
    df = DATA_CONTEXT['refugee_associations_data']
    odis = DATA_CONTEXT['odis']
    

    # 2. Filter by Bassin de Vie (Requirement F-26)
    # Get the BV for the target commune
    mask = pd.Series(False, index=df.index)
    if codgeo in odis.index:
        bv = odis.loc[codgeo, 'bassin_de_vie']
        if pd.notna(bv):
             bv_str = str(bv).replace('.0', '')
             # Return all associations in the same Bassin de Vie
             # Robust comparison: handle potential float/string mixture in df
             mask = (df['bassin_de_vie'].astype(str).str.replace(r'\.0$', '', regex=True) == bv_str)
        else:
             # Fallback to city-only if BV is unknown
             mask = (df['codgeo'].astype(str) == str(codgeo))
    else:
        # Last fallback: direct match on code in the vertical table
        mask = (df['codgeo'].astype(str) == str(codgeo))
    
    results = df[mask].copy()
    
    if results.empty:
        return []
    
    # Format for agent
    return results.to_dict(orient='records')

@mcp.tool()
def search_refugee_associations(codgeo: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Recherche les associations spécialisées dans l'accueil des réfugiés (RNA).
    L'outil identifie le Bassin de Vie de la commune et retourne TOUTES les associations de cette zone.
    
    Args:
        codgeo: Code INSEE de la commune (ex: '33063').
    """
    try:
        if not codgeo or not (isinstance(codgeo, str) and len(codgeo) == 5):
            return {"error": f"Invalid INSEE code (codgeo): {codgeo}. Must be 5 characters."}
        return _search_refugee_associations_logic(codgeo)
    except Exception as e:
        logger.exception(f"❌ [MCP] search_refugee_associations failed: {e}")
        return {"error": str(e)}


def _search_odis_associations_logic(codgeo: str) -> List[Dict[str, Any]]:
    """
    Internal logic for looking up ODIS associations.
    Accepts INSEE code.
    """
    ensure_data_context()
    if 'odis_asso_mini_data' not in DATA_CONTEXT or DATA_CONTEXT['odis_asso_mini_data'].empty:
        logger.warning(f"⚠️ [MCP] odis_asso_mini_data not available or empty in DATA_CONTEXT.")
        return []
    
    df = DATA_CONTEXT['odis_asso_mini_data']
    odis = DATA_CONTEXT['odis']
    

    mask = pd.Series(False, index=df.index)
    if codgeo in odis.index:
        bv = odis.loc[codgeo, 'bassin_de_vie']
        if pd.notna(bv):
             bv_str = str(bv).replace('.0', '')
             mask = (df['codgeo'].isin(odis[odis['bassin_de_vie'] == bv].index))
        else:
             mask = (df['codgeo'].astype(str) == str(codgeo))
    else:
        mask = (df['codgeo'].astype(str) == str(codgeo))
    
    results = df[mask].copy()
    
    return results.to_dict(orient='records')

@mcp.tool()
def search_odis_associations(codgeo: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Recherche les associations locales (Sports, Culture, Loisirs, Social) issues de l'annuaire ODIS.
    L'outil retourne les associations de la commune ou de son Bassin de Vie.
    
    Args:
        codgeo: Code INSEE de la commune (ex: '33063').
    """
    try:
        if not codgeo or not (isinstance(codgeo, str) and len(codgeo) == 5):
            return {"error": f"Invalid INSEE code (codgeo): {codgeo}. Must be 5 characters."}
        return _search_odis_associations_logic(codgeo)
    except Exception as e:
        logger.exception(f"❌ [MCP] search_odis_associations failed: {e}")
        return {"error": str(e)}


def _compute_routes_logic(origin: str, destination: str, mode: str = "transit") -> Dict[str, Any]:
    ensure_data_context()
    # logger.info(f"🚗 [MCP] Request: compute_routes from '{origin}' to '{destination}' (mode={mode})") 
    try:
         gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY")
         if not gmaps_key: return {"error": "Clé Maps manquante."}
         
         gmaps = googlemaps.Client(key=gmaps_key)
         
         # Attempt 1: Raw names
         try:
             directions = gmaps.directions(origin=origin, destination=destination, mode=mode, language="fr")
             if directions:
                #  logger.info(f"✅ [MCP] Route found (Attempt 1).")
                 return sanitize_for_json({"type": "directions", "data": directions})
         except Exception as e:
             if "NOT_FOUND" not in str(e): raise e
             logger.warning(f"⚠️ [MCP] Route NOT_FOUND for '{origin}' -> '{destination}'. Retrying with context...")

         # Attempt 2: If destination is the city, and origin is generic (or vice-versa), 
         # Google often fails. We try to help it.
         # This is heuristic but powerful for "Préfecture" or "Gare"
         if len(origin) < 15 and "," not in origin:
             alt_origin = f"{origin} near {destination}"
             try:
                 directions = gmaps.directions(origin=alt_origin, destination=destination, mode=mode, language="fr")
                 if directions:
                    #  logger.info(f"✅ [MCP] Route found (Attempt 2: {alt_origin}).")
                     return sanitize_for_json({"type": "directions", "data": directions})
             except:
                 pass

         return {"error": "Aucun itinéraire trouvé. Essayez d'être plus précis (ex: 'Préfecture de Nîmes')."}

    except Exception as e:
         logger.error(f"❌ [MCP] compute_routes failed: {e}")
         return {"error": str(e)}

@mcp.tool()
def compute_routes(origin: str, destination: str, mode: str = "transit") -> Dict[str, Any]:
    """
    Calcule des itinéraires et temps de trajet entre deux points.
    Si un lieu est vague (ex: 'Préfecture'), précise la ville si possible.
    """
    try:
        if not origin or not destination:
             return {"error": "Both 'origin' and 'destination' must be provided."}
        return _compute_routes_logic(origin, destination, mode)
    except Exception as e:
        logger.exception(f"❌ [MCP] compute_routes failed: {e}")
        return {"error": str(e)}

if __name__ == "__main__":
    mcp.run()
