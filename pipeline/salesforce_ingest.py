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
OUTPUT_PATH = Path("pipeline/cache/clean/salesforce_jaccueille.parquet")
OUTPUT_BDV_PATH = Path("pipeline/cache/output/salesforce_jaccueille_bdv.parquet")

CODES_POSTAUX_PATH = Path("pipeline/cache/clean/codes_postaux.parquet")
COMMUNES_PATH = Path("pipeline/cache/output/odis_communes.parquet")


def _ttl_days() -> int:
    """Read the Salesforce cache policy from the source catalog."""
    cfg = load_config(CONFIG_FILE)
    local_files = cfg.get("local_files", {})
    if "salesforce_jaccueille" in local_files:
        return local_files["salesforce_jaccueille"].get("ttl_days", 7)
    return cfg.get("sources", {}).get("salesforce_jaccueille", {}).get("ttl_days", 7)


def get_salesforce_status(cache_path: Optional[Path] = None) -> Dict[str, Any]:
    """Returns the status and age of the Salesforce J'accueille cached dataset.

    Args:
        cache_path: Optional path to the cached postal code parquet file. Defaults to OUTPUT_PATH.

    Returns:
        Dict[str, Any]: Status info containing exists, within_ttl, age_days, and ttl_days.
    """
    ttl_days = _ttl_days()
    target_path = Path(cache_path) if cache_path is not None else OUTPUT_PATH
    if not target_path.exists():
        legacy_path = Path("pipeline/cache/output/salesforce_jaccueille.parquet")
        if (
            cache_path is None
            and target_path == Path("pipeline/cache/clean/salesforce_jaccueille.parquet")
            and legacy_path.exists()
        ):
            target_path = legacy_path
        else:
            return {
                "exists": False,
                "within_ttl": False,
                "age_days": None,
                "ttl_days": ttl_days,
            }

    mtime = datetime.fromtimestamp(target_path.stat().st_mtime)
    age_days = (datetime.now() - mtime).days
    return {
        "exists": True,
        "within_ttl": age_days < ttl_days,
        "age_days": age_days,
        "ttl_days": ttl_days,
        "path": str(target_path),
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
        private_key = key_content.replace("\\n", "\n")

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
    df_postal_codes: Optional[pd.DataFrame] = None,
    df_communes: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """Map postal-code counts to a candidate run's bassin de vie reference.

    Args:
        df_cp_sf: Postal-code aggregated Salesforce DataFrame.
        postal_codes_path: Path to clean postal codes parquet.
        communes_path: Path to communes parquet containing codgeo and bassin_de_vie.
        df_postal_codes: Optional in-memory DataFrame for postal codes.
        df_communes: Optional in-memory DataFrame for communes.

    Returns:
        pd.DataFrame: Aggregated DataFrame by bassin_de_vie with columns:
            [bassin_de_vie, lead_count, contact_count, total_jaccueille_count, codes_postaux].
    """
    empty_bdv_df = pd.DataFrame(
        columns=[
            "bassin_de_vie",
            "lead_count",
            "contact_count",
            "total_jaccueille_count",
            "codes_postaux",
        ]
    )

    if df_cp_sf.empty or "code_postal" not in df_cp_sf.columns:
        return empty_bdv_df

    if df_postal_codes is not None:
        df_cp = df_postal_codes.copy()
    elif postal_codes_path.exists():
        df_cp = pd.read_parquet(postal_codes_path)
    else:
        logging.warning(
            "[Salesforce] Missing postal codes reference mapping for Bassin de Vie aggregation."
        )
        return empty_bdv_df

    if "code_postal" not in df_cp.columns or "codgeo" not in df_cp.columns:
        logging.warning(
            "[Salesforce] Postal codes reference mapping missing required columns 'code_postal' or 'codgeo'."
        )
        return empty_bdv_df

    df_cp = df_cp.drop_duplicates(subset=["code_postal"], keep="first")

    if df_communes is not None:
        df_comm = df_communes.copy()
    elif communes_path.exists():
        df_comm = pd.read_parquet(communes_path)
    else:
        logging.warning(
            "[Salesforce] Missing communes reference mapping for Bassin de Vie aggregation."
        )
        return empty_bdv_df

    if "codgeo" not in df_comm.columns or "bassin_de_vie" not in df_comm.columns:
        logging.warning(
            "[Salesforce] Communes reference mapping missing required columns 'codgeo' or 'bassin_de_vie'."
        )
        return empty_bdv_df

    df_comm = df_comm[["codgeo", "bassin_de_vie"]].drop_duplicates(
        subset=["codgeo"], keep="first"
    )

    df_sf = df_cp_sf.copy()
    df_sf["code_postal"] = df_sf["code_postal"].astype(str).str.strip().str.zfill(5)
    for col in ["lead_count", "contact_count", "total_jaccueille_count"]:
        if col not in df_sf.columns:
            df_sf[col] = 0

    df_cp["code_postal"] = df_cp["code_postal"].astype(str).str.strip().str.zfill(5)
    df_cp["codgeo"] = df_cp["codgeo"].astype(str).str.strip()
    df_comm["codgeo"] = df_comm["codgeo"].astype(str).str.strip()
    df_comm["bassin_de_vie"] = df_comm["bassin_de_vie"].astype(str).str.strip()

    merged = df_sf.merge(df_cp[["code_postal", "codgeo"]], on="code_postal", how="inner")
    merged_bdv = merged.merge(df_comm[["codgeo", "bassin_de_vie"]], on="codgeo", how="inner")

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
        return empty_bdv_df

    return df_bdv


def fetch_and_aggregate_salesforce(
    force: bool = False,
    *,
    output_path: Optional[Path] = None,
    raw_output_path: Optional[Path] = None,
) -> pd.DataFrame:
    """Fetch Salesforce leads/contacts or reuse local source cache if valid.

    Args:
        force: If True, bypass cache and fetch live from Salesforce.
        output_path: Destination path for postal code aggregate parquet. Defaults to OUTPUT_PATH.
        raw_output_path: Destination path for raw records parquet. Defaults to RAW_OUTPUT_PATH.

    Returns:
        pd.DataFrame: Postal code aggregated DataFrame with columns:
            [code_postal, lead_count, contact_count, total_jaccueille_count].
    """
    target_output_path = output_path or OUTPUT_PATH
    target_raw_path = raw_output_path or RAW_OUTPUT_PATH
    status = get_salesforce_status(target_output_path)
    if not force and not status["within_ttl"] and target_output_path.resolve() != OUTPUT_PATH.resolve():
        status = get_salesforce_status(OUTPUT_PATH)

    if not force and status["within_ttl"]:
        logging.info(
            "[Salesforce] Local source cache is valid (%s days old < TTL %s days).",
            status.get("age_days"),
            status.get("ttl_days"),
        )
        source_file = Path(status["path"])
        df = pd.read_parquet(source_file)
        if target_output_path.resolve() != source_file.resolve():
            target_output_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(target_output_path, index=False)
        return df

    logging.info("[Salesforce] Starting live Salesforce J'accueille data extraction...")
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
    target_raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_df = pd.DataFrame(
        {
            "entity_type": ["lead"] * len(leads) + ["contact"] * len(contacts),
            "record_id": [l.get("Id") for l in leads] + [c.get("Id") for c in contacts],
            "postal_code": [l.get("PostalCode") for l in leads]
            + [c.get("MailingPostalCode") for c in contacts],
            "created_date": [l.get("CreatedDate") for l in leads]
            + [c.get("CreatedDate") for c in contacts],
        }
    )
    raw_df.to_parquet(target_raw_path, index=False)
    logging.info(f"[Salesforce] Raw data saved to {target_raw_path}")

    # 4. Process and aggregate by Postal Code
    clean_cp_df = aggregate_salesforce_data(leads, contacts)
    target_output_path.parent.mkdir(parents=True, exist_ok=True)
    clean_cp_df.to_parquet(target_output_path, index=False)
    logging.info(
        "[Salesforce] Postal Code aggregate saved to %s (%s postal codes).",
        target_output_path,
        len(clean_cp_df),
    )
    return clean_cp_df


get_or_fetch_salesforce_data = fetch_and_aggregate_salesforce


def run_salesforce_ingest(
    force: bool = False,
    *,
    output_bdv_path: Path | None = None,
    output_clean_path: Path | None = None,
    postal_codes_path: Path = CODES_POSTAUX_PATH,
    communes_path: Path = COMMUNES_PATH,
) -> Path:
    """Refresh Salesforce source data and build a BDV aggregate for one run.

    Decoupled workflow:
    1. Extracts and aggregates by postal code (reusing cached postal-code data if valid).
    2. Aggregates postal codes to Bassin de Vie using candidate references.
    """
    output_bdv_path = output_bdv_path or OUTPUT_BDV_PATH
    clean_cp_df = fetch_and_aggregate_salesforce(
        force=force,
        output_path=output_clean_path or OUTPUT_PATH,
    )

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
