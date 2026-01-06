import pandas as pd
from fastmcp import FastMCP
import requests
import os
import time
import logging
import json
from typing import List, Dict, Any, Optional

# Late import to avoid circular dependency
def _resolve_insee(city_name: str) -> Optional[str]:
    try:
        from mcp_server import _search_commune_logic
        results = _search_commune_logic(city_name)
        if results:
             return results[0].get('codgeo') # In ODIS results, codgeo is the INSEE
    except Exception as e:
        logger.error(f"Error in _resolve_insee: {e}")
    return None

# Standardize Logging with the working stream
logger = logging.getLogger("agent_tools")

# Configuration
AUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
BASE_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2"

# Robust Path Calculation
import config as cfg
REFERENTIELS_PATH = os.path.join(cfg.get_data_path(), cfg.REFERENTIELS_FILE)
logger.info(f"📍 [FranceTravail] Module loaded. Referentiels: {REFERENTIELS_PATH}")

# Initialize FastMCP Server
mcp = FastMCP("France-Travail")

# Token Cache
TOKEN_CACHE = {
    "access_token": None,
    "expires_at": 0
}

def _get_access_token() -> str:
    """Retrieves or refreshes the OAuth2 access token using Client Credentials flow."""
    now = time.time()
    if TOKEN_CACHE["access_token"] and now < TOKEN_CACHE["expires_at"] - 60:
        return TOKEN_CACHE["access_token"]

    client_id = os.environ.get("FRANCE_TRAVAIL_CLIENT_ID")
    client_secret = os.environ.get("FRANCE_TRAVAIL_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError("Missing FRANCE_TRAVAIL_CLIENT_ID or FRANCE_TRAVAIL_CLIENT_SECRET in environment.")

    logger.info("🔑 [FranceTravail] Refreshing access token...")
    
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "o2dsoffre api_offresdemploiv2"
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = requests.post(AUTH_URL, data=payload, headers=headers, timeout=10)
    if response.status_code != 200:
        logger.error(f"❌ [FranceTravail] Auth Failed: {response.status_code} - {response.text}")
    response.raise_for_status()
    
    data = response.json()
    logger.info(f"✅ [FranceTravail] Token Refreshed (expires in {data.get('expires_in')}s)")
    TOKEN_CACHE["access_token"] = data["access_token"]
    TOKEN_CACHE["expires_at"] = now + int(data["expires_in"])
    
    return TOKEN_CACHE["access_token"]

def _resolve_fap_label(fap_code: str) -> Optional[str]:
    """Resolves a FAP code to its label using the ODIS referentials."""
    try:
        if not os.path.exists(REFERENTIELS_PATH):
            logger.warning(f"Referentiels file not found at {REFERENTIELS_PATH}")
            return None
        
        df = pd.read_parquet(REFERENTIELS_PATH)
        fap_df = df[(df['key'] == 'fap_codes') & (df['code'] == fap_code)]
        if not fap_df.empty:
            return fap_df.iloc[0]['label']
    except Exception as e:
        logger.error(f"Error resolving FAP label: {e}")
    return None

def search_job_offers_logic(
    query: Optional[str] = None,
    location: Optional[str] = None,
    fap_code: Optional[str] = None,
    distance: int = 10,
    sort: int = 1,
    range_start: int = 0,
    range_end: int = 49
) -> Dict[str, Any]:
    """Publicly exported logic for searching job offers."""
    logger.info(f"👉 [FranceTravail] ENTERING search_job_offers_logic (loc={location}, fap={fap_code})")
    token = _get_access_token()
    
    # 1. Resolve Location if it's a Name
    if location and not (location.isdigit() and len(location) == 5):
        logger.info(f"🔍 [FranceTravail] Resolving location name: '{location}'")
        resolved = _resolve_insee(location)
        if resolved:
            logger.info(f"✅ [FranceTravail] Resolved '{location}' -> {resolved}")
            location = resolved
        else:
            logger.warning(f"⚠️ [FranceTravail] Could not resolve '{location}' to an INSEE code.")

    # 2. Resolve FAP label
    fap_label = None
    if fap_code:
        fap_label = _resolve_fap_label(fap_code)
        if fap_label:
            logger.info(f"✅ [FranceTravail] FAP {fap_code} -> '{fap_label}'")
        else:
            logger.warning(f"Could not resolve FAP {fap_code}")

    # Combine query and FAP label for motsCles
    keywords = []
    if fap_label:
        keywords.append(fap_label)
    if query:
        keywords.append(query)
    
    mots_cles = ",".join(keywords) if keywords else None

    params = {
        "range": f"{range_start}-{range_end}",
        "sort": sort
    }
    if mots_cles:
        params["motsCles"] = mots_cles
    if location:
        params["commune"] = location
        params["distance"] = distance

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    logger.info(f"👉 [FranceTravail] API Call: {BASE_URL}/offres/search | Params: {params}")
    response = requests.get(f"{BASE_URL}/offres/search", params=params, headers=headers, timeout=10)
    
    if response.status_code == 204:
        logger.info("⚠️ [FranceTravail] No results (204 No Content)")
        return {"offres": [], "total": 0}
    
    if response.status_code != 200:
        logger.error(f"❌ [FranceTravail] Search Error: {response.status_code} - {response.text}")

    response.raise_for_status()
    data = response.json()
    logger.info(f"🎁 [FranceTravail] API DUMP (Total: {data.get('total', 0)}): {data.get('resultats', [])[:2]}")
    
    # Extract total from Content-Range header if present
    total = 0
    content_range = response.headers.get("Content-Range", "")
    if "/" in content_range:
        try:
            total = int(content_range.split("/")[-1])
        except:
            pass
            
    return {
        "offres": data.get("resultats", []),
        "total": total
    }

@mcp.tool()
def search_job_offers(
    query: Optional[str] = None,
    location: Optional[str] = None,
    fap_code: Optional[str] = None,
    distance: int = 10,
    sort: int = 1,
    range_start: int = 0,
    range_end: int = 49
) -> Dict[str, Any]:
    """
    Rechercher des offres d'emploi sur France Travail.
    
    Args:
        query: Mots clés supplémentaires (ex: 'Alternance').
        location: Code INSEE de la commune (ex: '33063').
        fap_code: Code FAP (Famille Professionnelle) de métier.
        distance: Rayon de recherche en km autour de la commune.
        sort: Tri (0: Pertinence, 1: Date décr., 2: Distance).
        range_start: Index de début (pagination).
        range_end: Index de fin (pagination).
    """
    try:
        return search_job_offers_logic(
            query=query, 
            location=location, 
            fap_code=fap_code, 
            distance=distance,
            sort=sort, 
            range_start=range_start, 
            range_end=range_end
        )
    except Exception as e:
        logger.exception(f"❌ [FranceTravail] Critical error in search_job_offers wrapper: {e}")
        return {"offres": [], "total": 0, "error": str(e)}

def _get_job_details_logic(job_id: str) -> Dict[str, Any]:
    """Internal logic for getting job details."""
    token = _get_access_token()
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    logger.info(f"👉 [FranceTravail] Getting job details: {job_id}")
    response = requests.get(f"{BASE_URL}/offres/{job_id}", headers=headers)
    
    if response.status_code == 204:
        return {"error": "Offre non trouvée."}
        
    response.raise_for_status()
    data = response.json()
    logger.info(f"🎁 [FranceTravail] Raw Details Response: {data}")
    return data

@mcp.tool()
def get_job_details(job_id: str) -> Dict[str, Any]:
    """
    Récupère les détails complets d'une offre d'emploi.
    
    Args:
        job_id: L'identifiant unique de l'offre (ex: '123ABCD').
    """
    return _get_job_details_logic(job_id)

if __name__ == "__main__":
    mcp.run()
