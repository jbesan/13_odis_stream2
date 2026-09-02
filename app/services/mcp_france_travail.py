from fastmcp import FastMCP
import requests
import os
import time
import logging
from typing import Dict, Any, Optional
import re


# Late import to avoid circular dependency
def _resolve_insee(city_name: str) -> Optional[str]:
    try:
        from services.mcp_server import _search_referentiels_logic

        results = _search_referentiels_logic(city_name, domain="communes")
        if results:
            return results[0].get("code")  # Standardized ODIS field
    except Exception as e:
        logger.error(f"Error in _resolve_insee: {e}")
    return None


# Standardize Logging with the working stream
logger = logging.getLogger("agent_tools")

# Configuration
AUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
BASE_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2"

# Initialize FastMCP Server
mcp = FastMCP("France-Travail")

# Token Cache
TOKEN_CACHE = {"access_token": None, "expires_at": 0}


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
        raise ValueError(
            "Missing FRANCE_TRAVAIL_CLIENT_ID or FRANCE_TRAVAIL_CLIENT_SECRET in environment."
        )

    logger.debug("🔑 [FranceTravail] Refreshing access token...")

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "o2dsoffre api_offresdemploiv2",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    response = requests.post(AUTH_URL, data=payload, headers=headers, timeout=10)
    if response.status_code != 200:
        logger.error(
            f"❌ [FranceTravail] Auth Failed: {response.status_code} - {response.text}"
        )
    response.raise_for_status()

    data = response.json()
    logger.debug(
        f"✅ [FranceTravail] Token Refreshed (expires in {data.get('expires_in')}s)"
    )
    TOKEN_CACHE["access_token"] = data["access_token"]
    TOKEN_CACHE["expires_at"] = int(now + int(data["expires_in"]))

    return str(TOKEN_CACHE["access_token"])


def _prune_job_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    """Prunes a job offer payload to keep only the essentials for the agent context."""
    return {
        "id": offer.get("id"),
        "intitule": offer.get("intitule"),
        "typeContrat": offer.get("typeContrat"),
        "typeContratLibelle": offer.get("typeContratLibelle"),
        "description_sh": (offer.get("description", "")[:500] + "...")
        if offer.get("description")
        else None,
        "dateCreation": offer.get("dateCreation"),
        "lieuTravail": {
            "libelle": offer.get("lieuTravail", {}).get("libelle"),
            "codeINSEE": offer.get("lieuTravail", {}).get("codeINSEE"),
        },
        "entreprise": {"nom": offer.get("entreprise", {}).get("nom")},
        "salaire": {"libelle": offer.get("salaire", {}).get("libelle")},
        "dureeTravailLibelle": offer.get("dureeTravailLibelle"),
        "experienceLibelle": offer.get("experienceLibelle"),
        "origineOffre": {"urlOrigine": offer.get("origineOffre", {}).get("urlOrigine")},
    }


def _search_job_offers_logic(
    query: Optional[str] = None,
    location: Optional[str] = None,
    rome: Optional[str] = None,
    distance: int = 10,
    sort: int = 1,
    range_start: int = 0,
    range_end: int = 19,
    rome_code: Optional[str] = None,  # Alias 1
    rome_codes: Optional[str] = None,  # Alias 2
    rome_label: Optional[str] = None,  # ROME label fallback
) -> Dict[str, Any]:
    """Publicly exported logic for searching job offers."""

    # 0. Robustness: Handle parameter aliases
    if not rome:
        rome = rome_code or rome_codes

    # Validation
    if rome:
        if not isinstance(rome, str):
            logger.warning(
                f"⚠️ [FranceTravail] Invalid ROME type: {type(rome)}. Returning empty."
            )
            return {"offres": [], "total": 0}

        # Strict ROME pattern: One letter A-N followed by 4 digits
        if not re.match(r"^[A-N][0-9]{4}$", rome):
            logger.warning(
                f"⚠️ [FranceTravail] Invalid ROME format: '{rome}'. (Possible confusing with INSEE/Postcode). Returning empty."
            )
            return {"offres": [], "total": 0}

    # logger.info(f"👉 [FranceTravail] ENTERING search_job_offers_logic (loc={location}, rome={rome})")
    token = _get_access_token()

    # 1. Resolve Location if it's a Name or Malformed
    loc_str = str(location or "")
    insee_match = re.search(r"\b(\d{5})\b", loc_str)

    if insee_match:
        location = insee_match.group(1)
    elif location and not (location.isdigit() and len(location) == 5):
        # logger.info(f"🔍 [FranceTravail] Resolving location name: '{location}'")
        resolved = _resolve_insee(location)
        if resolved:
            # logger.info(f"✅ [FranceTravail] Resolved '{location}' -> {resolved}")
            location = resolved
        else:
            logger.warning(
                f"⚠️ [FranceTravail] Could not resolve '{location}' to an INSEE code."
            )

    # 3. Prepare API parameters
    params: Dict[str, Any] = {"range": f"{range_start}-{range_end}", "sort": sort}

    # 4. Handle ROME code
    if rome:
        # If we have a 5-char ROME code, it goes to codeROME
        params["codeROME"] = rome

    # Use user query as motsCles
    if query:
        params["motsCles"] = query

    if location:
        params["commune"] = location
        params["distance"] = (
            distance if (distance is not None and distance != "") else 10
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Range": f"offres={range_start}-{range_end}",
    }

    logger.debug(
        f"👉 [FranceTravail] API Call: {BASE_URL}/offres/search | Params: {params}"
    )
    response = requests.get(
        f"{BASE_URL}/offres/search", params=params, headers=headers, timeout=10
    )

    # Simple & Direct Fallback: If codeROME returns HTTP 500 from FT server, retry with motsCles (rome_label)
    if response.status_code == 500 and "codeROME" in params:
        label_to_use = rome_label or query
        if label_to_use:
            logger.info(
                f"🔄 [FranceTravail] FT 500 on codeROME '{rome}'. Retrying with motsCles='{label_to_use}'."
            )
            fallback_params = dict(params)
            fallback_params.pop("codeROME", None)
            fallback_params["motsCles"] = label_to_use
            response = requests.get(
                f"{BASE_URL}/offres/search",
                params=fallback_params,
                headers=headers,
                timeout=10,
            )

    if response.status_code == 204:
        return {"status": "success_empty", "offres": [], "total": 0}

    if response.status_code not in [200, 206]:
        logger.warning(
            f"⚠️ [FranceTravail] Search Error: {response.status_code} - {response.text[:200]}"
        )
        return {
            "status": "error",
            "offres": [],
            "total": 0,
            "error_code": f"http_{response.status_code}",
            "retryable": response.status_code in {429, 500, 502, 503, 504},
        }

    response.raise_for_status()
    # data = response.json()
    data = response.json()
    logger.debug(
        f"🎁 [FranceTravail] API response received with {data.get('total', 0)} results."
    )

    # Extract total from Content-Range header if present
    total = 0
    content_range = response.headers.get("Content-Range", "")
    if "/" in content_range:
        try:
            total = int(content_range.split("/")[-1])
        except (ValueError, IndexError) as exc:
            logger.debug(
                "Failed to parse total from Content-Range header '%s': %s",
                content_range,
                exc,
            )

    pruned_offres = [_prune_job_offer(o) for o in data.get("resultats", [])]

    return {
        "status": "success_nonempty" if pruned_offres else "success_empty",
        "offres": pruned_offres,
        "total": total,
    }


@mcp.tool()
def search_job_offers(
    query: Optional[str] = None,
    location: Optional[str] = None,
    rome: Optional[str] = None,
    distance: int = 10,
    sort: int = 1,
    range_start: int = 0,
    range_end: int = 19,
    rome_code: Optional[str] = None,
    rome_codes: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Rechercher des offres d'emploi sur France Travail.

    Args:
        query: Mots clés supplémentaires (ex: 'Alternance').
        location: Code INSEE de la commune (ex: '33063').
        rome: Code ROME (ex: 'M1805').
        distance: Rayon de recherche en km autour de la commune.
        sort: Tri (0: Pertinence, 1: Date décr., 2: Distance).
        range_start: Index de début (pagination).
        range_end: Index de fin (pagination).
    """
    try:
        return _search_job_offers_logic(
            query=query,
            location=location,
            rome=rome,
            distance=distance,
            sort=sort,
            range_start=range_start,
            range_end=range_end,
            rome_code=rome_code,
            rome_codes=rome_codes,
        )
    except Exception as e:
        logger.exception(
            f"❌ [FranceTravail] Critical error in search_job_offers wrapper: {e}"
        )
        return {"offres": [], "total": 0, "error": str(e)}


def _get_job_details_logic(job_id: str) -> Dict[str, Any]:
    """Internal logic for getting job details with PII filtering and pruning."""

    token = _get_access_token()

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    response = requests.get(f"{BASE_URL}/offres/{job_id}", headers=headers, timeout=10)

    if response.status_code == 204:
        return {"error": "Offre non trouvée."}

    response.raise_for_status()
    data = response.json()

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
        desc = re.sub(
            r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", "[EMAIL]", desc
        )
        # Simple phone mask (French pattern)
        desc = re.sub(
            r"(?:(?:\+|00)33|0)\s*[1-9](?:[\s.-]*\d{2}){4}", "[TELEPHONE]", desc
        )
        pruned["description"] = desc

    # 3. Rich metadata
    pruned["competences"] = [
        c.get("libelle") for c in data.get("competences", []) if c.get("libelle")
    ]
    pruned["qualites"] = [
        q.get("libelle")
        for q in data.get("qualitesProfessionnelles", [])
        if q.get("libelle")
    ]

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
    try:
        if not job_id:
            return {"error": "Missing 'job_id' parameter."}
        return _get_job_details_logic(job_id)
    except Exception as e:
        logger.exception(f"❌ [FranceTravail] get_job_details failed: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    mcp.run()
