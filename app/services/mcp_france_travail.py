import pandas as pd
from fastmcp import FastMCP
import requests
import os
import time
import logging
import json
from typing import List, Dict, Any, Optional
from utils.common import normalize_text

# Late import to avoid circular dependency
def _resolve_insee(city_name: str) -> Optional[str]:
    try:
        from services.mcp_server import _search_commune_logic
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
# logger.info(f"📍 [FranceTravail] Module loaded. Referentiels: {REFERENTIELS_PATH}")

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
    access_token = TOKEN_CACHE["access_token"]
    expires_at = TOKEN_CACHE["expires_at"]
    if access_token and isinstance(expires_at, (int, float)) and now < expires_at - 60:
        return str(access_token)

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
    TOKEN_CACHE["expires_at"] = int(now + int(data["expires_in"]))
    
    return str(TOKEN_CACHE["access_token"])


def _get_all_rome_codes() -> List[Dict[str, str]]:
    """Retrieves all standard ROME categories from referentiels."""
    try:
        import pandas as pd
        df = pd.read_parquet(os.path.join(cfg.get_data_path(), cfg.REFERENTIELS_FILE))
        subset = df[df['key'] == 'rome_codes']
        return [{"code": str(row['code']), "libelle": str(row['label'])} for _, row in subset.iterrows()]
    except Exception as e:
        logger.error(f"❌ [FranceTravail] Failed to load ROME referencial: {e}")
        return []

def _search_rome_appellations_logic(query: str) -> List[Dict[str, str]]:
    """Internal logic for searching ROME categories."""
    all_codes = _get_all_rome_codes()
    if not all_codes:
        logger.warning("⚠️ [FranceTravail] ROME categories list is empty.")
        return []
    
    # 1. Normalize and clean query
    q_norm = normalize_text(query)
    stop_words = {"et", "des", "les", "chez", "pour", "dans", "par", "avec", "assimiles", "assimile", "personnels", "personnel"}
    
    raw_terms = q_norm.replace(',', ' ').replace('(', ' ').replace(')', ' ').replace('/', ' ').split()
    terms = [t.rstrip('s') for t in raw_terms if len(t) > 2 and t not in stop_words]
    
    if not terms:
        terms = [q_norm]
        
    logger.debug(f"🔍 [FranceTravail] Searching ROME for '{query}' -> Terms: {terms}")
        
    matches: List[Dict[str, Any]] = []
    for item in all_codes:
        libelle_raw = item['libelle']
        lib_norm = normalize_text(libelle_raw)
        
        # Scoring based on how many terms match
        score = sum(1 for term in terms if term in lib_norm)
        
        if score > 0:
            lib_tokens = set(lib_norm.replace('-', ' ').split())
            for term in terms:
                if term in lib_tokens:
                    score += 2
                    
            if terms and terms[0] in lib_tokens:
                score += 5

            matches.append({"code": str(item['code']), "libelle": libelle_raw, "_score": score})
            
    # Sort by score (desc) and then alphabetically
    matches.sort(key=lambda x: (-int(x['_score']), str(x['libelle'])))
    
    if matches:
        logger.debug(f"✅ [FranceTravail] Found {len(matches)} ROME matches. Top: {matches[0]['libelle']} ({matches[0]['code']})")
    
    # Return top 20 matches
    return [{"code": str(m['code']), "libelle": str(m['libelle'])} for m in matches[:20]]

@mcp.tool()
def search_rome_appellations(query: str) -> List[Dict[str, str]]:
    """
    Recherche des catégories de métiers (codes ROME) à partir d'un mot-clé.
    Utile pour identifier les codes métiers officiels pour la recherche d'offres.
    
    Args:
        query: Mot-clé à rechercher (ex: 'Boulanger', 'Informatique').
    """
    return _search_rome_appellations_logic(query)


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
    rome_code: Optional[str] = None,
    appellation_codes: Optional[List[str]] = None,
    distance: int = 10,
    sort: int = 1,
    range_start: int = 0,
    range_end: int = 19
) -> Dict[str, Any]:
    """Publicly exported logic for searching job offers."""
    # logger.info(f"👉 [FranceTravail] ENTERING search_job_offers_logic (loc={location}, rome={rome_code}, apps={appellation_codes})")
    token = _get_access_token()
    
    # 1. Resolve Location if it's a Name
    if location and not (location.isdigit() and len(location) == 5):
        # logger.info(f"🔍 [FranceTravail] Resolving location name: '{location}'")
        resolved = _resolve_insee(location)
        if resolved:
            # logger.info(f"✅ [FranceTravail] Resolved '{location}' -> {resolved}")
            location = resolved
        else:
            logger.warning(f"⚠️ [FranceTravail] Could not resolve '{location}' to an INSEE code.")

    # 3. Handle ROME code
    if rome_code:
        # If we have a 5-char ROME code, it goes to codeRome
        if appellation_codes is None:
            appellation_codes = []
        if rome_code not in appellation_codes:
            appellation_codes.append(rome_code)

    # 4. Prepare API parameters
    params: Dict[str, Any] = {
        "range": f"{range_start}-{range_end}",
        "sort": sort
    }
    
    # Use user query as motsCles
    if query:
        params["motsCles"] = query
        
    if location:
        params["commune"] = location
        params["distance"] = distance
    
    # Remove domaine logic as we now use ROME everywhere
    
    # Still allow explicit appellation codes if provided (but API might ignore if domaine is set? 
    # Usually codeRome is separate. Let's see.)
    if appellation_codes:
        params["codeRome"] = ",".join(sorted(appellation_codes))

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
    rome_code: Optional[str] = None,
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
        rome_code: Code ROME (ex: 'M1805').
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
            rome_code=rome_code, 
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
    """Internal logic for getting job details with PII filtering and pruning."""

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
    logger.info(f"🎁 [FranceTravail] Raw Details Response received (id={job_id})")
    
    # 1. Base Pruning
    pruned = _prune_job_offer(data)
    # Remove the short description as we'll provide the full one
    if "description_sh" in pruned:
        del pruned["description_sh"]
    
    # 2. Description with PII filtering (Emails and Phones)
    desc = data.get("description", "")
    if desc:
        import re
        # Simple email mask
        desc = re.sub(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+', '[EMAIL]', desc)
        # Simple phone mask (French pattern)
        desc = re.sub(r'(?:(?:\+|00)33|0)\s*[1-9](?:[\s.-]*\d{2}){4}', '[TELEPHONE]', desc)
        pruned["description"] = desc

    # 3. Rich metadata
    pruned["competences"] = [c.get("libelle") for c in data.get("competences", []) if c.get("libelle")]
    pruned["qualites"] = [q.get("libelle") for q in data.get("qualitesProfessionnelles", []) if q.get("libelle")]
    
    # Application link if available
    contact = data.get("contact", {})
    if contact.get("urlPostulation"):
        pruned["url_postulation"] = contact.get("urlPostulation")
    elif data.get("origineOffre", {}).get("urlOrigine"):
        pruned["url_postulation"] = data.get("origineOffre", {}).get("urlOrigine")

    # logger.info(f"🔍 [FranceTravail] Final Tool Output for {job_id}: {json.dumps(pruned, ensure_ascii=False)}")
    return pruned

@mcp.tool()
def get_job_details(job_id: str) -> Dict[str, Any]:
    """
    Récupère les détails complets d'une offre d'emploi.
    
    Args:
        job_id: L'identifiant unique de l'offre (ex: '123ABCD').
    """
    logger.info(f"🚀 [TOOL_CALL] get_job_details invoked with ID: {job_id}")
    return _get_job_details_logic(job_id)

if __name__ == "__main__":
    mcp.run()
