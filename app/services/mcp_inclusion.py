
import pandas as pd
import requests
import os
import time
import logging
import re
from typing import List, Dict, Any, Optional
from utils.common import normalize_text

logger = logging.getLogger("mcp_inclusion")

# Configuration
API_URL = "https://emplois.inclusion.beta.gouv.fr/api/v1/siaes/"
AUTH_URL = "https://emplois.inclusion.beta.gouv.fr/api/v1/token-auth/"

# Token Cache
TOKEN_CACHE = {
    "token": None,
    "last_refresh": 0
}

def _get_access_token() -> Optional[str]:
    """Retrieves or refreshes the API token."""
    now = time.time()
    # Refresh every 24h (token is usually long-lived but let's be safe)
    if TOKEN_CACHE["token"] and now - TOKEN_CACHE["last_refresh"] < 86400:
        return TOKEN_CACHE["token"]

    login = os.environ.get("EMPLOIS_INCLUSION_LOGIN")
    password = os.environ.get("EMPLOIS_INCLUSION_PWD")

    if not login or not password:
        logger.error("❌ [Inclusion] Missing EMPLOIS_INCLUSION_LOGIN or EMPLOIS_INCLUSION_PWD.")
        return None

    try:
        payload = {"username": login, "password": password}
        response = requests.post(AUTH_URL, json=payload, timeout=10)
        if response.status_code == 200:
            token = response.json().get("token")
            TOKEN_CACHE["token"] = token
            TOKEN_CACHE["last_refresh"] = now
            return token
        else:
            logger.error(f"❌ [Inclusion] Auth Failed: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"❌ [Inclusion] Auth Error: {e}")
        return None

def _prune_inclusion_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    """Standardizes SIAE offer for the agent."""
    # The API returns SIAE structures with a list of 'postes'
    # We need to flatten this or return the SIAE with job details
    return {
        "id": offer.get("id"),
        "name": offer.get("enseigne") or offer.get("raison_sociale"),
        "type": offer.get("type"),
        "siret": offer.get("siret"),
        "description": offer.get("description"),
        "postes": offer.get("postes", [])
    }

def _search_inclusion_jobs_logic(
    location: Optional[str] = None,
    rome: Optional[str] = None,
    query: Optional[str] = None
) -> Dict[str, Any]:
    """
    Search for SIAE job offers using Les emplois de l'inclusion API.
    """
    token = _get_access_token()
    if not token:
        return {"error": "Authentication failed", "offres": []}

    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json"
    }
    
    params = {
        "page_size": 20
    }
    
    if location:
        # Robust search for INSEE (5 digits) or Dept (2-3 digits)
        # LLMs sometimes pass "communes:87085" or "87085,rome:"
        loc_str = str(location)
        insee_match = re.search(r'\b(\d{5})\b', loc_str)
        dept_match = re.search(r'\b(\d{2,3})\b', loc_str)
        
        if insee_match:
            params["code_insee"] = insee_match.group(1)
            params["distance_max_km"] = 20 # 20km radius
            logger.info(f"🔍 [Inclusion] Searching near INSEE {params['code_insee']}")
        elif dept_match:
            params["postes_dans_le_departement"] = dept_match.group(1)
            logger.info(f"🔍 [Inclusion] Searching in Dept {params['postes_dans_le_departement']}")
        else:
            # Fallback to original but it will likely 400 if garbage
            params["postes_dans_le_departement"] = location

    # The API doesn't have a direct 'rome' filter in the main SIAE list?
    # Actually, let's check the API documentation or previous script.
    # In emplois_inclusion_ingest.py, it fetches EVERYTHING for a dept and filters.
    
    logger.info(f"🔍 [Inclusion] Searching for jobs near {location} (20km radius)...")
    
    try:
        response = requests.get(API_URL, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        results = data.get('results', [])
        
        # Filter by ROME if provided (Offline filtering since main list is small per dept)
        filtered = []
        for siae in results:
            postes = siae.get('postes', [])
            if not postes:
                continue
                
            match_postes = []
            for p in postes:
                p_rome = p.get('rome') # Format "Label (CODE)"
                # Extract code
                m = re.search(r"\(([A-Z]\d{4})\)", p_rome or "")
                code = m.group(1) if m else p_rome
                
                if rome:
                    # Loosen matching: if rome is 3 digits, match prefix
                    if len(rome) == 3 and code and code.startswith(rome):
                        match_postes.append(p)
                    elif code == rome:
                        match_postes.append(p)
                else:
                    match_postes.append(p)
            
            if match_postes:
                siae_copy = siae.copy()
                siae_copy['postes'] = match_postes
                filtered.append(_prune_inclusion_offer(siae_copy))
                
        return {
            "offres": filtered,
            "total": len(filtered)
        }
    except Exception as e:
        logger.error(f"❌ [Inclusion] Search failed: {e}")
        return {"error": str(e), "offres": []}

def _get_inclusion_job_details_logic(siae_id: str) -> Dict[str, Any]:
    """Fetch details for a specific SIAE structure."""
    token = _get_access_token()
    if not token:
        return {"error": "Authentication failed"}

    headers = {
        "Authorization": f"Token {token}",
        "Accept": "application/json"
    }

    try:
        url = f"{API_URL}{siae_id}/"
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        return _prune_inclusion_offer(response.json())
    except Exception as e:
        logger.error(f"❌ [Inclusion] Get details failed for {siae_id}: {e}")
        return {"error": str(e)}
