import os
import requests
import time
import pandas as pd
import re
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv("app/.env")

# Constants
OUTPUT_PATH = Path("pipeline/cache/output/odis_inclusion_jobs.parquet")
STRUCTURES_PATH = Path("pipeline/cache/output/odis_inclusion_structures.parquet")
SIAE_LOOKUP_PATH = Path("pipeline/cache/raw/structures-inclusion-2026-02-16.parquet")
API_URL = "https://emplois.inclusion.beta.gouv.fr/api/v1/siaes/"
TTL_DAYS = 7

# Relevant SIAE types (ACI, AI, EI, ETTI, EITI)
SIAE_TYPES_RELEVANT = {"ACI", "AI", "EI", "ETTI", "EITI"}

# metropolitan departments + overseas
DEPARTEMENTS = [str(i).zfill(2) for i in range(1, 96)] + ["2A", "2B", "971", "972", "973", "974", "976"]
if "20" in DEPARTEMENTS:
    DEPARTEMENTS.remove("20")
DEPARTEMENTS = sorted(DEPARTEMENTS)

def get_inclusion_jobs_status() -> Dict[str, Any]:
    """Returns the status and age of the inclusion jobs data.

    Returns:
        Dict[str, Any]: A dictionary with data availability status, age in days, and TTL metrics.
    """
    if not OUTPUT_PATH.exists():
        return {"exists": False, "within_ttl": False, "age_days": None, "ttl_days": TTL_DAYS}
    
    mtime = datetime.fromtimestamp(OUTPUT_PATH.stat().st_mtime)
    age_days = (datetime.now() - mtime).days
    return {
        "exists": True,
        "within_ttl": age_days < TTL_DAYS,
        "age_days": age_days,
        "ttl_days": TTL_DAYS,
        "path": str(OUTPUT_PATH)
    }

def extract_rome_code(rome_str: str) -> Optional[str]:
    """Extracts only the ROME code (e.g., A1203) from a string like 'Label (A1203)'."""
    if not rome_str:
        return None
    match = re.search(r"\(([A-Z]\d{4})\)", rome_str)
    if match:
        return match.group(1)
    return rome_str # Fallback if already a code or format differs

def fetch_department_jobs(dept: str) -> List[Dict[str, Any]]:
    """Fetches hiring structures and their job openings for a specific department.

    Args:
        dept: The 2-3 digit department code.

    Returns:
        List[Dict[str, Any]]: List of structures that contain job openings.
    """
    headers = {
        "Accept": "application/json"
    }
    params = {
        "postes_dans_le_departement": dept,
        "page_size": 100
    }
    
    all_results = []
    url = API_URL
    backoff = 5.0
    
    while url:
        while True:
            try:
                response = requests.get(url, headers=headers, params=params if url == API_URL else None, timeout=30)
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_time = float(retry_after) if retry_after and retry_after.isdigit() else backoff
                    print(f"    [Rate Limit] HTTP 429. Waiting {wait_time}s for dept {dept}...")
                    time.sleep(wait_time)
                    backoff = min(backoff * 2.0, 60.0)
                    continue
                response.raise_for_status()
                data = response.json()
                break
            except Exception as e:
                print(f"    [Error] Failed to fetch dept {dept}: {e}")
                return all_results
                
        results = data.get('results', [])
        # Filter for structures with at least one job opening
        hiring = [s for s in results if len(s.get('postes', [])) > 0]
        all_results.extend(hiring)
        
        url = data.get('next')
        if url:
            time.sleep(0.5) # Gentle iteration
            
    return all_results

def run_ingestion(departments: List[str] = None) -> None:
    """Main ingestion loop using the public unauthenticated API.

    Args:
        departments: Optional list of specific department codes to ingest. If None,
            all DEPARTEMENTS are processed.
    """
    # Load SIAE lookup for code_insee fallback
    if SIAE_LOOKUP_PATH.exists():
        print(f"  [Lookup] Loading SIAE lookup table from {SIAE_LOOKUP_PATH}...")
        str_inc = pd.read_parquet(SIAE_LOOKUP_PATH, engine='fastparquet')
        # Ensure siret is string for matching
        str_inc['siret'] = str_inc['siret'].astype(str)
    else:
        print(f"  [Warning] SIAE lookup file not found at {SIAE_LOOKUP_PATH}. Fallback to local code_insee will be disabled.")
        str_inc = pd.DataFrame(columns=['siret', 'code_insee'])

    depts_to_process = departments or DEPARTEMENTS
    print(f"=== Starting Ingestion (Public Mode): Les emplois de l'inclusion ({len(depts_to_process)} depts) ===")
    
    all_rows = []
    all_structures = []
    
    for dept in depts_to_process:
        print(f"  Processing {dept}...")
        structures = fetch_department_jobs(dept)
        
        for siae in structures:
            siret = siae.get('siret')
            siae_type = siae.get('type')
            siae_name = siae.get('enseigne') or siae.get('raison_sociale')
            
            # Filter structures by type
            if siae_type not in SIAE_TYPES_RELEVANT:
                continue

            # Identify structure location for structures table
            # Try to get code_insee from lookup if available
            siae_code_insee = None
            if siret:
                match = str_inc[str_inc.siret == str(siret)]
                if not match.empty:
                    siae_code_insee = match.code_insee.iloc[0]
            
            # If not in lookup, we can try to find it from one of the job locations (if any)
            # but for structures many might not have jobs.
            # However, the SIAE object itself might have a 'ville' or similar?
            # Let's check what SIAE object contains. The API usually returns address info.
            # For now, if we don't have code_insee, we might skip or record it.
            if siae_code_insee:
                all_structures.append({
                    "codgeo": str(siae_code_insee),
                    "siae_siret": siret,
                    "siae_type": siae_type,
                    "siae_name": siae_name
                })
            
            postes = siae.get('postes', [])
            for p in postes:
                lieu = p.get('lieu')
                code_insee = None
                if isinstance(lieu, dict):
                    code_insee = lieu.get('code_insee')
                
                # Fallback to SIAE info from lookup if lieu is missing or doesn't have code_insee
                if not code_insee and siret:
                    code_insee = siae_code_insee # Reuse the one from SIAE
                
                if code_insee:
                    all_rows.append({
                        "job_id": p.get('id'), # New: keep job ID
                        "codgeo": str(code_insee),
                        "siae_siret": siret,
                        "siae_type": siae_type,
                        "siae_name": siae_name,
                        "rome": extract_rome_code(p.get('rome')),
                        "postes": p.get('nombre_postes_ouverts', 1)
                    })
        
        time.sleep(1) # Gentle delay between departments
        
    if all_rows:
        df = pd.DataFrame(all_rows)
        # Ensure types
        df['postes'] = pd.to_numeric(df['postes'], errors='coerce').fillna(1).astype(int)
        
        # Save granular data as requested (no aggregation)
        os.makedirs(OUTPUT_PATH.parent, exist_ok=True)
        df.to_parquet(OUTPUT_PATH, index=False, engine='fastparquet')
        print(f"\n✅ Successfully saved {len(df)} job opening records to {OUTPUT_PATH}")
    else:
        print("\n⚠️ No job data collected.")

    if all_structures:
        df_struct = pd.DataFrame(all_structures)
        # Unique structures per commune
        df_struct = df_struct.drop_duplicates(subset=['codgeo', 'siae_siret'])
        os.makedirs(STRUCTURES_PATH.parent, exist_ok=True)
        df_struct.to_parquet(STRUCTURES_PATH, index=False, engine='fastparquet')
        print(f"✅ Successfully saved {len(df_struct)} unique SIAE structure records to {STRUCTURES_PATH}")
    else:
        print("\n⚠️ No structure data collected.")

if __name__ == "__main__":
    run_ingestion()
