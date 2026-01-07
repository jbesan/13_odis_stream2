import pandas as pd
from fastmcp import FastMCP
import requests
import os
import time
import logging
import json
from typing import List, Dict, Any, Optional
from utils import normalize_text

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

    logger.debug("🔑 [FranceTravail] Refreshing access token...")
    
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
    logger.debug(f"✅ [FranceTravail] Token Refreshed (expires in {data.get('expires_in')}s)")
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

# ROME Cache Configuration
ROME_CACHE_PATH = os.path.join(cfg.get_data_path(), "rome_appellations.json")

def _get_all_appellations() -> List[Dict[str, str]]:
    """Retrieves all ROME appellations, from cache or API."""
    if os.path.exists(ROME_CACHE_PATH):
        # Cache for 7 days
        if time.time() - os.path.getmtime(ROME_CACHE_PATH) < 7 * 24 * 3600:
            try:
                with open(ROME_CACHE_PATH, 'r') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"Error reading ROME cache: {e}")

    token = _get_access_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    url = f"{BASE_URL}/referentiel/appellations"
    logger.debug(f"📡 [FranceTravail] Fetching ROME Appellations from API...")
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        # Save to cache
        os.makedirs(os.path.dirname(ROME_CACHE_PATH), exist_ok=True)
        with open(ROME_CACHE_PATH, 'w') as f:
            json.dump(data, f)
        
        logger.info(f"✅ [FranceTravail] ROME Appellations cached ({len(data)} items)")
        return data
    except Exception as e:
        logger.error(f"❌ [FranceTravail] Failed to fetch ROME referential: {e}")
        return []

@mcp.tool()
def search_rome_appellations(query: str) -> List[Dict[str, str]]:
    """
    Recherche des intitulés de métiers précis (appellations ROME) à partir d'un mot-clé.
    Utile pour traduire un code FAP ou un métier générique en codes précis pour l'API.
    
    Args:
        query: Mot-clé à rechercher (ex: 'Boulanger', 'Social').
    """
    all_apps = _get_all_appellations()
    if not all_apps:
        logger.warning("⚠️ [FranceTravail] ROME appellations list is empty.")
        return []
    
    # 1. Normalize and clean query
    q_norm = normalize_text(query)
    # Common noise words in FAP labels that don't help keyword matching
    stop_words = {"et", "des", "les", "chez", "pour", "dans", "par", "avec", "assimiles", "assimile", "personnels", "personnel"}
    
    # Split, clean punctuation, and singularize (rough)
    raw_terms = q_norm.replace(',', ' ').replace('(', ' ').replace(')', ' ').replace('/', ' ').split()
    terms = [t.rstrip('s') for t in raw_terms if len(t) > 2 and t not in stop_words]
    
    if not terms:
        # Fallback to literal query if everything was filtered
        terms = [q_norm]
        
    logger.debug(f"🔍 [FranceTravail] Searching ROME for '{query}' -> Terms: {terms}")
        
    matches = []
    for app in all_apps:
        libelle_raw = app['libelle']
        lib_norm = normalize_text(libelle_raw)
        
        # Scoring based on how many terms match
        score = sum(1 for term in terms if term in lib_norm)
        
        if score > 0:
            # Bonus if terms found as whole words
            lib_tokens = set(lib_norm.replace('-', ' ').split())
            for term in terms:
                if term in lib_tokens:
                    score += 2
                    
            # Bonus if the first term matches exactly a part of the libelle
            if terms and terms[0] in lib_tokens:
                score += 5

            matches.append({"code": app['code'], "libelle": libelle_raw, "_score": score})
            
    # Sort by score (desc) and then alphabetically
    matches.sort(key=lambda x: (-x['_score'], x['libelle']))
    
    # Trace top result for debugging
    if matches:
        logger.debug(f"✅ [FranceTravail] Found {len(matches)} matches. Top: {matches[0]['libelle']} ({matches[0]['code']})")
    else:
        logger.warning(f"⚠️ [FranceTravail] No ROME matches found for query: '{query}'")
    # Return top 20 matches
    return [{"code": m['code'], "libelle": m['libelle']} for m in matches[:20]]

def _prune_job_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    """Prunes a job offer payload to keep only the essentials for the agent context."""
    return {
        "id": offer.get("id"),
        "intitule": offer.get("intitule"),
        "typeContrat": offer.get("typeContrat"),
        "typeContratLibelle": offer.get("typeContratLibelle"),
        "description_sh": (offer.get("description", "")[:300] + "...") if offer.get("description") else None,
        "dateCreation": offer.get("dateCreation"),
        "lieuTravail": {"libelle": offer.get("lieuTravail", {}).get("libelle")},
        "entreprise": {"nom": offer.get("entreprise", {}).get("nom")},
        "salaire": {"libelle": offer.get("salaire", {}).get("libelle")},
        "dureeTravailLibelle": offer.get("dureeTravailLibelle"),
        "experienceLibelle": offer.get("experienceLibelle"),
        "origineOffre": {"urlOrigine": offer.get("origineOffre", {}).get("urlOrigine")}
    }

def search_job_offers_logic(
    query: Optional[str] = None,
    location: Optional[str] = None,
    fap_code: Optional[str] = None,
    appellation_codes: Optional[List[str]] = None,
    distance: int = 10,
    sort: int = 1,
    range_start: int = 0,
    range_end: int = 19
) -> Dict[str, Any]:
    """Publicly exported logic for searching job offers."""
    logger.info(f"👉 [FranceTravail] ENTERING search_job_offers_logic (loc={location}, fap={fap_code}, apps={appellation_codes})")
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
    
    # 3. Add ROME codes if provided
    if appellation_codes:
        # The France Travail API expects 'codeRome' (not 'appellation' for multiple codes in V2)
        # and it should be a comma-separated string.
        params["codeRome"] = ",".join(appellation_codes)

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }

    logger.debug(f"👉 [FranceTravail] API Call: {BASE_URL}/offres/search | Params: {params}")
    response = requests.get(f"{BASE_URL}/offres/search", params=params, headers=headers, timeout=10)
    
    if response.status_code == 204:
        logger.info("⚠️ [FranceTravail] No results (204 No Content)")
        return {"offres": [], "total": 0}
    
    if response.status_code not in [200, 206]:
        logger.error(f"❌ [FranceTravail] Search Error: {response.status_code} - {response.text}")

    response.raise_for_status()
    # data = response.json()
    data = response.json()
    logger.debug(f"🎁 [FranceTravail] API response received with {data.get('total', 0)} results.")
    
    # Extract total from Content-Range header if present
    total = 0
    content_range = response.headers.get("Content-Range", "")
    if "/" in content_range:
        try:
            total = int(content_range.split("/")[-1])
        except:
            pass
            
    pruned_offres = [_prune_job_offer(o) for o in data.get("resultats", [])]
            
    return {
        "offres": pruned_offres,
        "total": total
    }

@mcp.tool()
def search_job_offers(
    query: Optional[str] = None,
    location: Optional[str] = None,
    fap_code: Optional[str] = None,
    appellation_codes: Optional[List[str]] = None,
    distance: int = 10,
    sort: int = 1,
    range_start: int = 0,
    range_end: int = 19
) -> Dict[str, Any]:
    """
    Rechercher des offres d'emploi sur France Travail.
    
    Args:
        query: Mots clés supplémentaires (ex: 'Alternance').
        location: Code INSEE de la commune (ex: '33063').
        fap_code: Code FAP (Famille Professionnelle) de métier.
        appellation_codes: Liste de codes métiers précis (ROME Appellations, ex: ['11573']).
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
            appellation_codes=appellation_codes,
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
    logger.debug(f"🎁 [FranceTravail] Raw Details Response received (id={job_id})")
    # We prune even the details but with a bit more info
    pruned = _prune_job_offer(data)
    # Add a bit more for details
    if data.get("description"):
        pruned["description_full"] = data.get("description")
    pruned["competences"] = data.get("competences", [])
    pruned["qualitesProfessionnelles"] = data.get("qualitesProfessionnelles", [])
    
    return pruned

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
