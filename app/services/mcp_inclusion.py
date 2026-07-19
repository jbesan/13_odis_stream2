import requests
import os
import logging
import re
from typing import Dict, Any, Optional
import pandas as pd
import config as cfg

logger = logging.getLogger("mcp_inclusion")

# Configuration
API_URL = "https://emplois.inclusion.beta.gouv.fr/api/v1/siaes/"


def _prune_inclusion_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    """Standardizes SIAE offer format for the agent.

    Args:
        offer: The raw SIAE structure dict from the API.

    Returns:
        Dict[str, Any]: The standardized/pruned structure dict containing key attributes.
    """
    # The API returns SIAE structures with a list of 'postes'
    # We need to flatten this or return the SIAE with job details
    return {
        "id": offer.get("id"),
        "name": offer.get("enseigne") or offer.get("raison_sociale"),
        "type": offer.get("type"),
        "siret": offer.get("siret"),
        "description": offer.get("description"),
        "postes": offer.get("postes", []),
    }


def _search_inclusion_jobs_logic(
    location: Optional[str] = None,
    rome: Optional[str] = None,
    query: Optional[str] = None,
) -> Dict[str, Any]:
    """Search for SIAE job offers using the public Les emplois de l'inclusion API (no token).

    Args:
        location: The search location (INSEE code, department code, or name).
        rome: Optional ROME job category code filter (e.g. "A1203").
        query: Optional search query text.

    Returns:
        Dict[str, Any]: A dictionary containing the list of pruned offers and total count.
    """
    headers = {"Accept": "application/json"}
    params: Dict[str, Any] = {"page_size": 20}

    if location:
        # Robust search for INSEE (5 digits) or Dept (2-3 digits)
        # LLMs sometimes pass "communes:87085" or "87085,rome:"
        loc_str = str(location)
        insee_match = re.search(r"\b(\d{5})\b", loc_str)
        dept_match = re.search(r"\b(\d{2,3})\b", loc_str)

        if insee_match:
            params["code_insee"] = insee_match.group(1)
            params["distance_max_km"] = 20  # 20km radius
            logger.debug(f"🔍 [Inclusion] Searching near INSEE {params['code_insee']}")
        elif dept_match:
            params["postes_dans_le_departement"] = dept_match.group(1)
            logger.debug(
                f"🔍 [Inclusion] Searching in Dept {params['postes_dans_le_departement']}"
            )
        else:
            # Fallback to original but it will likely 400 if garbage
            params["postes_dans_le_departement"] = location

    logger.debug(f"🔍 [Inclusion] Searching for jobs near {location} (20km radius)...")

    try:
        response = requests.get(API_URL, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        results = data.get("results", [])

        # Filter by ROME if provided (Offline filtering since main list is small per dept)
        filtered = []
        for siae in results:
            postes = siae.get("postes", [])
            if not postes:
                continue

            match_postes = []
            for p in postes:
                p_rome = p.get("rome")  # Format "Label (CODE)"
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
                siae_copy["postes"] = match_postes
                filtered.append(_prune_inclusion_offer(siae_copy))

        return {"offres": filtered, "total": len(filtered)}
    except Exception as e:
        logger.error(f"❌ [Inclusion] Search failed: {e}")
        return {"error": str(e), "offres": []}


def _get_inclusion_job_details_logic(siae_id: str) -> Dict[str, Any]:
    """Fetch details for a specific SIAE structure using public API department lookup fallback.

    Args:
        siae_id: The SIAE structure SIRET (14 digits), UUID, or job ID.

    Returns:
        Dict[str, Any]: A dictionary containing details of the SIAE structure and its posts.
    """
    dept = None
    siae_id_str = str(siae_id)

    # Load local parquet cache once if it exists
    df_cache = None
    parquet_path = os.path.join(cfg.get_data_path(), cfg.SIAE_JOBS_FILE)
    if os.path.exists(parquet_path):
        try:
            df_cache = pd.read_parquet(parquet_path)
        except Exception as e:
            logger.warning(f"[Inclusion] Failed to load parquet cache: {e}")

    # 1. If siae_id is a SIRET (14 digits)
    if len(siae_id_str) == 14 and siae_id_str.isdigit():
        if df_cache is not None:
            match = df_cache[df_cache["siae_siret"] == siae_id_str]
            if not match.empty:
                codgeo = str(match.iloc[0]["codgeo"])
                dept = codgeo[:2]
        if not dept:
            dept = siae_id_str[:2]

    # 2. If siae_id is a numeric job ID
    elif siae_id_str.isdigit():
        if df_cache is not None:
            try:
                match = df_cache[df_cache["job_id"] == int(siae_id_str)]
                if not match.empty:
                    codgeo = str(match.iloc[0]["codgeo"])
                    dept = codgeo[:2]
            except Exception as e:
                logger.warning(f"[Inclusion] Parquet job ID lookup failed: {e}")

    # 3. Fallback to Streamlit session state
    if not dept:
        try:
            import streamlit as st
            dept_state = st.session_state.get("ui_departement")
            if dept_state:
                dept = str(dept_state)
            else:
                demo_data = st.session_state.get("demo_data", {})
                if isinstance(demo_data, dict) and "departement_actuel" in demo_data:
                    dept = str(demo_data["departement_actuel"])
        except Exception:
            pass

    # If we couldn't resolve the department, fallback to default '33'
    if not dept:
        logger.warning(
            f"[Inclusion] Could not resolve department for {siae_id_str}. Defaulting to '33'."
        )
        dept = "33"

    headers = {"Accept": "application/json"}
    params = {"postes_dans_le_departement": dept, "page_size": 100}

    try:
        response = requests.get(API_URL, headers=headers, params=params, timeout=15)
        response.raise_for_status()
        results = response.json().get("results", [])

        # Search for a match in results (either by siret, id [UUID], or job id inside postes)
        for siae in results:
            if siae.get("siret") == siae_id_str or siae.get("id") == siae_id_str:
                return _prune_inclusion_offer(siae)

            # Check if any job ID matches
            postes = siae.get("postes", [])
            for p in postes:
                if str(p.get("id")) == siae_id_str:
                    return _prune_inclusion_offer(siae)

        # If not found in the list, construct a basic fallback stub using the already loaded parquet cache
        logger.warning(
            f"[Inclusion] Structure/job {siae_id_str} not found in live results for dept {dept}."
        )

        if df_cache is not None:
            try:
                match = pd.DataFrame()
                if len(siae_id_str) == 14 and siae_id_str.isdigit():
                    match = df_cache[df_cache["siae_siret"] == siae_id_str]
                elif siae_id_str.isdigit():
                    match = df_cache[df_cache["job_id"] == int(siae_id_str)]

                if not match.empty:
                    row = match.iloc[0]
                    return {
                        "id": siae_id_str,
                        "name": row.get("siae_name", "Structure d'Insertion"),
                        "type": row.get("siae_type", "SIAE"),
                        "siret": row.get("siae_siret", siae_id_str),
                        "description": "Détails indisponibles en direct (recherche publique uniquement).",
                        "postes": [
                            {
                                "id": row.get("job_id"),
                                "rome": row.get("rome"),
                                "nombre_postes_ouverts": row.get("postes", 1),
                            }
                        ],
                    }
            except Exception as e:
                logger.warning(f"[Inclusion] Cache stub generation failed: {e}")

        # Ultimate fallback stub so agent doesn't crash
        return {
            "id": siae_id_str,
            "name": f"Structure {siae_id_str}",
            "type": "SIAE",
            "siret": siae_id_str if len(siae_id_str) == 14 else "",
            "description": "Détails indisponibles (recherche publique hors-département).",
            "postes": [],
        }
    except Exception as e:
        logger.error(
            f"❌ [Inclusion] Public detail lookup failed for {siae_id_str}: {e}"
        )
        return {"error": str(e)}
