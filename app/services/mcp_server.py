
from fastmcp import FastMCP
from typing import Dict, Any, List, Optional, Union
import json
import pandas as pd
import geopandas as gpd
import numpy as np
from utils.data_loader import load_all_data_raw
from core.scoring import ScoringEngine
from core.models import SearchCriterias, CriteriaItem
from services.rna_rag import RNARagService
import config as cfg
import logging
from utils.common import normalize_text, calculate_fuzzy_match_score, sanitize_for_json
import os
import requests
from services.mcp_inclusion import _search_inclusion_jobs_logic, _get_inclusion_job_details_logic

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")

# Initialize FastMCP Server
mcp = FastMCP("ODIS-Core")

# Initialize RNA RAG Service
try:
    rna_rag_service = RNARagService()
except Exception as e:
    logger.error(f"Failed to initialize RNARagService in MCP: {e}")
    rna_rag_service = None

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
        live_jobs_data=DATA_CONTEXT.get('live_jobs_data', pd.DataFrame()),
        siae_jobs_data=DATA_CONTEXT.get('siae_jobs_data', pd.DataFrame())
    )

def _search_referentiels_logic(query: str, domain: str) -> List[Dict[str, Any]]:
    """Recherche des codes officiels (Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels."""

    ensure_data_context()
    # print(f"Search Referentiels for Query: {query}, Domain: {domain}")

    if 'referentiels_raw' not in DATA_CONTEXT:
        logger.warning("   ⚠️ Referentiels data not available.")
        return []

    df = DATA_CONTEXT['referentiels_raw']
    if df.empty:
        return []

    # 1. Filter by Domain
    if domain == 'housing_types':
        # Synthetic domain for housing choice refinement
        df = pd.DataFrame([
            {"code": "appt_all", "label": "Appartement (Tous types)", "key": "housing_types"},
            {"code": "appt_t1_t2", "label": "Appartement (T1 & T2)", "key": "housing_types"},
            {"code": "appt_t3_p", "label": "Appartement (T3+)", "key": "housing_types"},
            {"code": "house_all", "label": "Maison", "key": "housing_types"},
        ])
    elif domain:
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

    # 1. Calculate relevance score - ensure scalar float
    def _safe_score(row):
        try:
            val = calculate_relevance(row)
            return float(val) if not isinstance(val, (pd.Series, pd.DataFrame)) else 0.0
        except:
            return 0.0

    work_df['score'] = work_df.apply(_safe_score, axis=1)
    
    # 2. Sort by relevance score descending
    results_df = work_df[work_df['score'] > 0].sort_values(by='score', ascending=False)
    
    # 3. Format Output
    results_df = results_df.head(5) # Returns the top 5
    results = []
    for _, row in results_df.iterrows():
        results.append({
            "code": row['code'],
            "label": row['label']
        })
        
    if results:
         top_summary = [f"{r['label']}" for r in results[:3]]
        #  logger.info(f"   Top matches: {top_summary}")
    
    # print(f"✅ [MCP] Response: Found {len(results)} matches.")
    # print(results)

    return results


@mcp.tool()
def search_referentiels(query: str, domain: Optional[str] = None) -> Dict[str, Any]:
    """
    Recherche des codes officiels (Communes, Formations, ROME, Services d'inclusion, WALDEC, etc.) dans les référentiels.
    
    Args:
        query (str): Mot clé de recherche.
        domain (str): Domaine de recherche possibles:['formation_codes', 'inclusion_services', 'waldec_codes', 'rome_codes', 'regions', 'departements', 'communes'].
    
    Returns:
        List[Dict[str, Any]]: Liste des codes + labels officiels correspondants.
    """

    try:
        if not query:
            return {"error": "Missing 'query' parameter."}
        
        valid_domains = ['rome_codes', 'formation_codes', 'inclusion_services', 'waldec_codes', 'regions', 'departements', 'communes', 'housing_types']
        if domain and domain not in valid_domains:
            return {"error": f"Invalid domain: {domain}. Must be one of {valid_domains}"}

        results = _search_referentiels_logic(query, domain)
        return {"results": results}
    except Exception as e:
        logger.exception(f"❌ [MCP] search_referentiels failed: {e}")
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
    
    # Robustness: Extract codes if they are enriched objects (dict with 'code')
    def get_code(v):
        if isinstance(v, dict) and 'code' in v: return v['code']
        if isinstance(v, list): return [get_code(i) for i in v]
        return v
    
    # Normalize filters for the engine
    for k in ['commune_actuelle', 'codes_metiers', 'codes_formations', 
              'inc_services_add_selection', 'inc_asso_add_selection', 'type_logement']:
        if k in filters:
            filters[k] = get_code(filters[k])

    from datetime import datetime
    start_logic = datetime.now()
    logger.debug(f"⚙️  [MCP] Entering _compute_top_cities_logic at {start_logic.strftime('%H:%M:%S.%f')[:-3]}")

    engine = get_scoring_engine()
    
    # 1. Resolve Commune
    commune_input = filters.get('commune_actuelle')
    # Use 'Paris' (75056) as the absolute fallback if input is missing or None
    resolved_commune = '75056' 
    
    if engine and isinstance(commune_input, (str, dict, CriteriaItem)):
         c_code = get_code(commune_input)
         if c_code and c_code in engine.df_all_communes.index:
             resolved_commune = c_code
         elif isinstance(commune_input, str) and commune_input.strip():
             matches = engine.df_all_communes[engine.df_all_communes['libgeo'].str.lower() == commune_input.lower()]
             if not matches.empty:
                 resolved_commune = str(matches.index[0])
             else:
                 logger.warning(f"   ⚠️ City '{commune_input}' not found. Falling back to Paris (75056).")

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

    config = SearchCriterias(
        poids_emploi=get_weight('emploi'),
        poids_logement=get_weight('logement'),
        poids_education=get_weight('education'),
        poids_inclusion=get_weight('inclusion'),
        poids_mobilite=get_weight('mobilité'),
        poids_sante=get_weight('sante'),
        criteria_weights=criteria_obj.criteria_weights,
        weight_profile=profile_name,
        commune_actuelle=resolved_commune,
        loc_search_area=loc_search_area,
        loc_search_code=loc_search_code,
        nb_adultes=nb_adultes,
        nb_enfants=int(filters.get('nb_enfants', 0)),
        hebergement_cible=filters.get('hebergement_cible', []),
        logement=filters.get('logement', 'Location'),
        codes_metiers=c_metiers,
        codes_formations=c_formations,
        classe_enfants=filters.get('classe_enfants', []),
        besoin_sante=filters.get('besoin_sante', 'Aucun'),
        type_logement=filters.get('type_logement') or "appt_all",
        inc_services_add_selection=specific_needs,
        inc_services_core_selection=socle_sel,
        inc_asso_add_selection=filters.get('inc_asso_add_selection', [])
    )
    
    # 3. Run Engine
    try:
        start_engine = datetime.now()
        processed_gdf = engine.run(config, log_prefix="chatbot")
        end_engine = datetime.now()
        logger.debug(f"⏱️  [ENGINE] run() took {(end_engine - start_engine).total_seconds():.3f}s")
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
        details_obj: CommuneResult = engine.format_city_details(row, config=config)
        details_dict = details_obj.model_dump()
        
        # AI Pruning: Streamline details but keep data not yet handled by other tools
        # Education and Formations stay here for now. Associations are handled by SCOUT.
        details_streamlined = {
            "identity": {
                "name": details_obj.name,
                "population": details_obj.population,
                "bassin_de_vie": details_obj.name_bdv
            },
            "scores": details_dict.get("scores", {}),
            "education": details_dict.get("education", {}),
            "emploi": {
                "top_metiers": details_dict.get("emploi", {}).get("top_metiers", []),
                "formations": details_dict.get("emploi", {}).get("formations", [])
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
        
    end_logic = datetime.now()
    logger.debug(f"🏁 [MCP] Exiting _compute_top_cities_logic at {end_logic.strftime('%H:%M:%S.%f')[:-3]} - Full duration: {(end_logic - start_logic).total_seconds():.3f}s")
    
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


def _call_google_v1(endpoint: str, body: Dict[str, Any], field_mask: str) -> Dict[str, Any]:
    """Helper for Google Maps Platform V1 REST calls."""
    gmaps_key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not gmaps_key:
        return {"error": "Clé Maps manquante."}
    
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": gmaps_key,
        "X-Goog-FieldMask": field_mask
    }
    try:
        response = requests.post(endpoint, json=body, headers=headers)
        if response.status_code != 200:
            logger.error(f"❌ [REST] Google V1 call failed ({response.status_code}): {response.text}")
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"❌ [REST] Google V1 call failed: {e}")
        return {"error": str(e)}


def _search_places_logic(queries: List[str], location: str) -> Dict[str, Any]:
    ensure_data_context()
    try:
        results = []
        # Google Places V1 - Text Search (New)
        endpoint = "https://places.googleapis.com/v1/places:searchText"
        # Only request necessary fields (removing ratings as requested)
        field_mask = "places.displayName,places.types,places.editorialSummary,places.formattedAddress,places.id"
        
        for q in queries[:30]:
            body = {
                "textQuery": f"{q} near {location}, France",
                "languageCode": "fr",
                "maxResultCount": 5 # Limit to top 5 results per query for tokens
            }
            
            res = _call_google_v1(endpoint, body, field_mask)
            if "error" in res:
                return res
            
            places = res.get('places', [])
            for p in places:
                # V1 structure: displayName is an object with 'text'
                name = p.get("displayName", {}).get("text")
                summary = p.get("editorialSummary", {}).get("text")
                
                place_data = {
                    "name": name,
                    "description": summary,
                    "types": p.get("types"),
                    "address": p.get("formattedAddress"),
                    "place_id": p.get("id")
                }
                results.append(place_data)
            
        logger.info(f"✅ [MCP] search_places (V1) finished. Total results: {len(results)}")
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




def _compute_routes_logic(origin: str, destination: str, mode: str = "transit") -> Dict[str, Any]:
    ensure_data_context()
    try:
         # Routes V1 mapping
         mode_map = {
             "transit": "TRANSIT",
             "walking": "WALK",
             "driving": "DRIVE",
             "bicycling": "BICYCLE"
         }
         v1_mode = mode_map.get(mode.lower(), "TRANSIT")
         
         endpoint = "https://routes.googleapis.com/directions/v2:computeRoutes"
         # Field mask: geocodingResults for addresses, routes for the rest.
         field_mask = "routes.distanceMeters,routes.duration,routes.legs.steps.distanceMeters,routes.legs.steps.staticDuration,routes.legs.steps.transitDetails,routes.legs.steps.navigationInstruction,geocodingResults"

         def prune_v1_routes(data):
             if not data.get('routes'): return None
             route = data['routes'][0]
             leg = route['legs'][0]
             
             transit_summary = []
             steps_summary = []
             
             # Resolve addresses from geocodingResults if available
             geo = data.get('geocodingResults', {})
             origin_addr = geo.get('origin', {}).get('formattedAddress')
             dest_addr = geo.get('destination', {}).get('formattedAddress')
             
             for s in leg.get('steps', []):
                 # V1 durations are strings like "120s"
                 def parse_dur(d): return f"{int(d.replace('s', '')) // 60} min" if d else "0 min"
                 
                 step_info = {
                     "distance": f"{s.get('distanceMeters', 0)} m",
                     "duration": parse_dur(s.get('staticDuration')),
                     "instruction": s.get('navigationInstruction', {}).get('instructions')
                 }
                 
                 td = s.get('transitDetails')
                 if td:
                     step_info["mode"] = "TRANSIT"
                     line_info = td.get('transitLine', {})
                     line_name = line_info.get('nameShort') or line_info.get('name')
                     vehicle_name = line_info.get('vehicle', {}).get('name', {}).get('text', 'Transit')
                     step_info["details"] = f"{vehicle_name} {line_name}"
                     if line_name: transit_summary.append(line_name)
                 else:
                     step_info["mode"] = "WALK"
                 
                 steps_summary.append(step_info)
             
             # Main duration parsing
             total_dur_s = int(route.get('duration', '0s').replace('s', ''))
             total_dur_min = total_dur_s // 60
             
             return {
                 "origin": origin_addr,
                 "destination": dest_addr,
                 "distance": f"{route.get('distanceMeters', 0) / 1000:.1f} km",
                 "duration": f"{total_dur_min} min",
                 "transit_summary": ", ".join(transit_summary) if transit_summary else None,
                 "steps": steps_summary
             }

         body = {
             "origin": {"address": origin},
             "destination": {"address": destination},
             "travelMode": v1_mode,
             "computeAlternativeRoutes": False,
             "languageCode": "fr",
             "units": "METRIC"
         }

         # Attempt 1
         res = _call_google_v1(endpoint, body, field_mask)
         if "routes" in res:
             return sanitize_for_json({"type": "directions", "data": prune_v1_routes(res)})
         
         # Attempt 2: Heuristic fallback for generic origins
         if len(origin) < 15 and "," not in origin:
             body["origin"]["address"] = f"{origin} near {destination}"
             res = _call_google_v1(endpoint, body, field_mask)
             if "routes" in res:
                 return sanitize_for_json({"type": "directions", "data": prune_v1_routes(res)})

         return {"error": "Aucun itinéraire trouvé (V1)."}

    except Exception as e:
         logger.error(f"❌ [MCP] compute_routes (V1) failed: {e}")
         return {"error": str(e)}

def _search_ccas_logic(codgeo: str) -> List[Dict[str, Any]]:
    """
    Internal logic for looking up CCAS information.
    Accepts INSEE code, falls back to Bassin de Vie if no local CCAS.
    """
    ensure_data_context()
    if 'structures_ccas' not in DATA_CONTEXT or DATA_CONTEXT['structures_ccas'].empty:
        logger.warning(f"⚠️ [MCP] structures_ccas not available or empty in DATA_CONTEXT.")
        return []
    
    df = DATA_CONTEXT['structures_ccas']
    odis = DATA_CONTEXT['odis']
    
    # 1. Direct match on Commune
    mask = (df['codgeo'].astype(str) == str(codgeo))
    results = df[mask].copy()
    
    # 2. Fallback to Bassin de Vie (Requirement F-26)
    if results.empty and codgeo in odis.index:
        bv = odis.loc[codgeo, 'bassin_de_vie']
        if pd.notna(bv):
             bv_str = str(bv).replace('.0', '')
             # Get all communes in the same BV
             bv_communes = odis[odis['bassin_de_vie'] == bv].index
             mask = (df['codgeo'].isin(bv_communes))
             results = df[mask].copy()
    
    return results.to_dict(orient='records')

@mcp.tool()
def search_ccas(codgeo: str) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Recherche les informations du CCAS (Centre Communal d'Action Sociale) pour une commune.
    Si aucun CCAS n'est trouvé dans la commune, l'outil retourne les CCAS du Bassin de Vie.
    
    Args:
        codgeo: Code INSEE de la commune (ex: '33063').
    """
    try:
        if not codgeo or not (isinstance(codgeo, str) and len(codgeo) == 5):
            return {"error": f"Invalid INSEE code (codgeo): {codgeo}. Must be 5 characters."}
        return _search_ccas_logic(codgeo)
    except Exception as e:
        logger.exception(f"❌ [MCP] search_ccas failed: {e}")
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

def _search_rna_rag_logic(query: str, codgeo: str, top_k: int = 10) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Internal logic for looking up associations via RAG.
    """
    if not rna_rag_service:
        return {"error": "RNARagService not initialized. Check BigQuery authentication."}
    
    try:
        return rna_rag_service.get_associations_semantic(query, codgeos=[codgeo], top_k=top_k)
    except Exception as e:
        logger.exception(f"❌ [MCP] _search_rna_rag_logic failed: {e}")
        return {"error": str(e)}

@mcp.tool()
def search_rna_rag(query: str, codgeo: str, top_k: int = 10) -> Union[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Recherche sémantique d'associations dans une commune spécifique (RAG).
    Retourne les associations les plus pertinentes (score > 0.8) triées par pertinence.
    
    Args:
        query: Terme de recherche (ex: 'football', 'hébergement d'urgence').
        codgeo: Code INSEE de la commune (5 chiffres).
        top_k: Nombre maximum de résultats à retourner.
    """
    return _search_rna_rag_logic(query, codgeo, top_k=top_k)

@mcp.tool()
def search_inclusion_jobs_batch(queries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Recherche d'offres SIAE (Insertion par l'Activité Économique) en mode Batch.
    
    Args:
        queries: Liste de dictionnaires {'location': '...', 'rome': '...', 'query': '...'}
    """
    results = {}
    for q in queries:
        loc = q.get('location')
        rome = q.get('rome')
        query_text = q.get('query')
        key = f"{rome or ''}|{loc or ''}|{query_text or ''}"
        try:
            results[key] = _search_inclusion_jobs_logic(location=loc, rome=rome, query=query_text)
        except Exception as e:
            results[key] = {"error": str(e), "offres": [], "total": 0}
    return results

@mcp.tool()
def get_inclusion_job_details(siae_id: str) -> Dict[str, Any]:
    """
    Récupère les détails d'une structure SIAE et ses offres.
    
    Args:
        siae_id: L'identifiant (SIRET ou ID interne) de la structure.
    """
    return _get_inclusion_job_details_logic(siae_id)

if __name__ == "__main__":
    mcp.run()
