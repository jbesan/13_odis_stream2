import os
import time
import json
import logging
import re
import jwt
import requests
import pandas as pd
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv
from pipeline.common import CONFIG_FILE, load_config

# Load environment variables from pipeline/.env
load_dotenv(Path(__file__).parent / ".env")

# Constants
RAW_OUTPUT_PATH = Path("pipeline/cache/raw/salesforce_jaccueille_raw.parquet")
OUTPUT_PATH = Path("pipeline/cache/output/salesforce_jaccueille.parquet")
OUTPUT_BDV_PATH = Path("pipeline/cache/output/salesforce_jaccueille_bdv.parquet")

CODES_POSTAUX_PATH = Path("pipeline/cache/clean/codes_postaux.parquet")
COMMUNES_PATH = Path("pipeline/cache/output/odis_communes.parquet")


def _ttl_days() -> int:
    """Read the Salesforce cache policy from the source catalog."""
    return load_config(CONFIG_FILE)["local_files"]["salesforce_jaccueille"]["ttl_days"]


def get_salesforce_status() -> Dict[str, Any]:
    """Returns the status and age of the Salesforce J'accueille cached dataset.

    Returns:
        Dict[str, Any]: Status info containing exists, within_ttl, age_days, and ttl_days.
    """
    ttl_days = _ttl_days()
    # The postal-code aggregate is the cached Salesforce source artefact.  The
    # BDV aggregate is deliberately rebuilt for every candidate because its
    # commune and postal-code mappings belong to that candidate run.
    if not OUTPUT_PATH.exists():
        return {
            "exists": False,
            "within_ttl": False,
            "age_days": None,
            "ttl_days": ttl_days,
        }

    mtime = datetime.fromtimestamp(OUTPUT_PATH.stat().st_mtime)
    age_days = (datetime.now() - mtime).days
    return {
        "exists": True,
        "within_ttl": age_days < ttl_days,
        "age_days": age_days,
        "ttl_days": ttl_days,
        "path": str(OUTPUT_PATH),
    }


def clean_postal_code(pc: Any) -> Optional[str]:
    """Cleans and normalizes French 5-digit postal codes.

    Args:
        pc: Raw postal code input (string, int, float, or None).

    Returns:
        Optional[str]: Cleaned 5-digit postal code string or None if invalid.
    """
    if pd.isna(pc) or pc is None:
        return None

    pc_str = str(pc).strip().split(".")[0]
    # Keep only alphanumeric characters
    pc_str = re.sub(r"[^\w]", "", pc_str)

    if not pc_str:
        return None

    # Standardize to 5 digits for numeric postal codes
    if pc_str.isdigit():
        pc_str = pc_str.zfill(5)
        # Valid French postal code range check (01000 - 98999, excluding 00xxx)
        if (
            len(pc_str) == 5
            and (1000 <= int(pc_str) <= 98999)
            and not pc_str.startswith("00")
        ):
            return pc_str

    # Corsica 2A/2B handling
    if pc_str.upper().startswith(("2A", "2B")) and len(pc_str) == 5:
        return pc_str.upper()

    return None


def get_salesforce_jwt_token() -> Tuple[str, str]:
    """Authenticates to Salesforce via OAuth 2.0 JWT Bearer flow.

    Returns:
        Tuple[str, str]: (access_token, instance_url)

    Raises:
        ValueError: If required environment variables are missing.
        RuntimeError: If authentication request fails.
    """
    client_id = os.getenv("SF_CLIENT_ID")
    username = os.getenv("SF_USERNAME")
    login_url = os.getenv("SF_LOGIN_URL", "https://login.salesforce.com").rstrip("/")
    key_path = os.getenv("SF_PRIVATE_KEY_PATH")
    key_content = os.getenv("SF_PRIVATE_KEY_CONTENT")

    if not client_id or not username:
        raise ValueError(
            "Missing Salesforce credentials (SF_CLIENT_ID, SF_USERNAME) in environment."
        )

    private_key = None
    if key_path and os.path.exists(key_path):
        with open(key_path, "r", encoding="utf-8") as f:
            private_key = f.read()
    elif key_content:
        private_key = key_content

    if not private_key:
        raise ValueError(
            f"Salesforce private key not found at path '{key_path}' and SF_PRIVATE_KEY_CONTENT not set."
        )

    claim = {
        "iss": client_id,
        "sub": username,
        "aud": login_url,
        "exp": int(time.time()) + 300,
    }

    try:
        assertion = jwt.encode(claim, private_key, algorithm="RS256")
    except Exception as e:
        raise RuntimeError(f"Failed to encode JWT assertion: {e}")

    token_url = f"{login_url}/services/oauth2/token"
    payload = {
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion,
    }

    resp = requests.post(token_url, data=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(
            f"Salesforce OAuth token request failed ({resp.status_code}): {resp.text}"
        )

    data = resp.json()
    access_token = data.get("access_token")
    instance_url = data.get("instance_url")

    if not access_token or not instance_url:
        raise RuntimeError(
            f"Salesforce OAuth response missing token or instance_url: {data}"
        )

    return access_token, instance_url


def fetch_soql_records(
    instance_url: str, access_token: str, soql_query: str
) -> List[Dict[str, Any]]:
    """Executes a SOQL query against Salesforce REST API handling pagination."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }

    query_url = f"{instance_url}/services/data/v60.0/query/?q={soql_query}"
    all_records = []

    while query_url:
        resp = requests.get(query_url, headers=headers, timeout=60)
        if resp.status_code != 200:
            logging.error(f"SOQL Query failed ({resp.status_code}): {resp.text}")
            resp.raise_for_status()

        data = resp.json()
        records = data.get("records", [])
        all_records.extend(records)

        next_url = data.get("nextRecordsUrl")
        if next_url:
            query_url = f"{instance_url}{next_url}"
        else:
            query_url = None

    return all_records


def aggregate_salesforce_data(
    leads: List[Dict[str, Any]], contacts: List[Dict[str, Any]]
) -> pd.DataFrame:
    """Processes Lead and Contact records into a postal code aggregated DataFrame."""
    # 1. Process Leads
    lead_map: Dict[str, int] = {}
    for l in leads:
        pc = clean_postal_code(l.get("PostalCode"))
        if pc and l.get("Id"):
            lead_map[pc] = lead_map.get(pc, 0) + 1

    # 2. Process Contacts
    contact_map: Dict[str, int] = {}
    for c in contacts:
        pc = clean_postal_code(c.get("MailingPostalCode"))
        if pc and c.get("Id"):
            contact_map[pc] = contact_map.get(pc, 0) + 1

    # 3. Merge all unique postal codes
    all_postal_codes = sorted(set(lead_map.keys()) | set(contact_map.keys()))

    rows = []
    for pc in all_postal_codes:
        l_cnt = lead_map.get(pc, 0)
        c_cnt = contact_map.get(pc, 0)
        tot_cnt = l_cnt + c_cnt

        rows.append(
            {
                "code_postal": pc,
                "lead_count": l_cnt,
                "contact_count": c_cnt,
                "total_jaccueille_count": tot_cnt,
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(
            columns=[
                "code_postal",
                "lead_count",
                "contact_count",
                "total_jaccueille_count",
            ]
        )

    return df


def aggregate_by_bassin_de_vie(
    df_cp_sf: pd.DataFrame,
    *,
    postal_codes_path: Path = CODES_POSTAUX_PATH,
    communes_path: Path = COMMUNES_PATH,
) -> pd.DataFrame:
    """Map postal-code counts to a candidate run's bassin de vie reference."""
    if df_cp_sf.empty or not postal_codes_path.exists() or not communes_path.exists():
        logging.warning(
            "[Salesforce] Missing reference mappings for Bassin de Vie aggregation."
        )
        return pd.DataFrame(
            columns=[
                "bassin_de_vie",
                "lead_count",
                "contact_count",
                "total_jaccueille_count",
                "codes_postaux",
            ]
        )

    df_cp = pd.read_parquet(postal_codes_path).drop_duplicates(
        subset=["code_postal"], keep="first"
    )
    df_comm = pd.read_parquet(communes_path)[
        ["codgeo", "bassin_de_vie"]
    ].drop_duplicates(subset=["codgeo"], keep="first")

    merged = df_cp_sf.merge(df_cp, on="code_postal", how="inner")
    merged_bdv = merged.merge(df_comm, on="codgeo", how="inner")

    bdv_rows = []
    for bdv, group in merged_bdv.groupby("bassin_de_vie"):
        lead_count = group["lead_count"].sum()
        contact_count = group["contact_count"].sum()
        total_count = lead_count + contact_count

        all_postal_codes = [str(cp) for cp in group["code_postal"].dropna().unique()]
        bdv_rows.append(
            {
                "bassin_de_vie": str(bdv),
                "lead_count": int(lead_count),
                "contact_count": int(contact_count),
                "total_jaccueille_count": int(total_count),
                "codes_postaux": json.dumps(sorted(list(set(all_postal_codes)))),
            }
        )

    df_bdv = pd.DataFrame(bdv_rows)
    if df_bdv.empty:
        df_bdv = pd.DataFrame(
            columns=[
                "bassin_de_vie",
                "lead_count",
                "contact_count",
                "total_jaccueille_count",
                "codes_postaux",
            ]
        )

    return df_bdv


def run_salesforce_ingest(
    force: bool = False,
    *,
    output_bdv_path: Path | None = None,
    postal_codes_path: Path = CODES_POSTAUX_PATH,
    communes_path: Path = COMMUNES_PATH,
) -> Path:
    """Refresh Salesforce source data and build a BDV aggregate for one run.

    ``OUTPUT_PATH`` remains the short-lived shared source cache.  The output
    consumed by the application is rebuilt against the caller's run-scoped
    geographic references and is never read from a previous release.
    """
    output_bdv_path = output_bdv_path or OUTPUT_BDV_PATH
    status = get_salesforce_status()
    if not force and status["within_ttl"]:
        logging.info(
            "[Salesforce] Local source cache is valid (%s days old < TTL %s days).",
            status["age_days"],
            status["ttl_days"],
        )
        clean_cp_df = pd.read_parquet(OUTPUT_PATH)
    else:
        logging.info(
            "[Salesforce] Starting live Salesforce J'accueille data extraction..."
        )
        access_token, instance_url = get_salesforce_jwt_token()

        # 1. Fetch Leads (Prospects) with status filters requested by team
        target_statuses = (
            "'En attente de nouvelles après RI', "
            "'Inscription non-finalisée', "
            "'Inscrit à une date de RI', "
            "'En attente de conversion', "
            "'Confirmation non finalisée'"
        )
        lead_soql = (
            "SELECT Id, PostalCode, CreatedDate, Status, Blackliste__c "
            "FROM Lead "
            "WHERE IsDeleted = FALSE AND Blackliste__c = FALSE "
            f"AND Status IN ({target_statuses})"
        )

        logging.info("[Salesforce] Fetching Leads...")
        leads = fetch_soql_records(instance_url, access_token, lead_soql)
        logging.info(f"[Salesforce] Extracted {len(leads)} active Leads.")

        # 2. Fetch Contacts with filters
        contact_soql = (
            "SELECT Id, MailingPostalCode, CreatedDate, Blackliste__c "
            "FROM Contact "
            "WHERE IsDeleted = FALSE AND Blackliste__c = FALSE"
        )
        logging.info("[Salesforce] Fetching Contacts...")
        contacts = fetch_soql_records(instance_url, access_token, contact_soql)
        logging.info(f"[Salesforce] Extracted {len(contacts)} active Contacts.")

        # 3. Save raw payload
        RAW_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        raw_df = pd.DataFrame(
            {
                "entity_type": ["lead"] * len(leads) + ["contact"] * len(contacts),
                "record_id": [l.get("Id") for l in leads]
                + [c.get("Id") for c in contacts],
                "postal_code": [l.get("PostalCode") for l in leads]
                + [c.get("MailingPostalCode") for c in contacts],
                "created_date": [l.get("CreatedDate") for l in leads]
                + [c.get("CreatedDate") for c in contacts],
            }
        )
        raw_df.to_parquet(RAW_OUTPUT_PATH, index=False)
        logging.info(f"[Salesforce] Raw data saved to {RAW_OUTPUT_PATH}")

        # 4. Process and aggregate by Postal Code
        clean_cp_df = aggregate_salesforce_data(leads, contacts)
        OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        clean_cp_df.to_parquet(OUTPUT_PATH, index=False)
        logging.info(
            "[Salesforce] Postal Code aggregate saved to %s (%s postal codes).",
            OUTPUT_PATH,
            len(clean_cp_df),
        )

    # 5. Candidate-specific aggregation Code Postal -> BDV
    clean_bdv_df = aggregate_by_bassin_de_vie(
        clean_cp_df,
        postal_codes_path=postal_codes_path,
        communes_path=communes_path,
    )
    output_bdv_path.parent.mkdir(parents=True, exist_ok=True)
    clean_bdv_df.to_parquet(output_bdv_path, index=False)
    logging.info(
        "[Salesforce] Candidate BDV aggregate saved to %s (%s BDVs).",
        output_bdv_path,
        len(clean_bdv_df),
    )

    return output_bdv_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_salesforce_ingest(force=True)
