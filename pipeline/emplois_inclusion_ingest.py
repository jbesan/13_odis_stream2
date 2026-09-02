import os
import requests
import time
import pandas as pd
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from dotenv import load_dotenv
from pipeline.employment_coverage import METROPOLITAN_DEPARTMENTS
from pipeline.common import CONFIG_FILE, load_config

# Load environment variables
load_dotenv("app/.env")

# Constants
OUTPUT_PATH = Path("pipeline/cache/output/odis_inclusion_jobs.parquet")
STRUCTURES_PATH = Path("pipeline/cache/output/odis_inclusion_structures.parquet")
COVERAGE_OUTPUT_PATH = Path("pipeline/cache/output/odis_inclusion_jobs_coverage.parquet")
SIAE_LOOKUP_PATH = Path("pipeline/cache/raw/structures_inclusion.parquet")
API_URL = "https://emplois.inclusion.beta.gouv.fr/api/v1/siaes/"

def _ttl_days() -> int:
    """Read the inclusion-jobs cache policy from the source catalog."""
    return load_config(CONFIG_FILE)["local_files"]["inclusion_jobs"]["ttl_days"]

# Relevant SIAE types (ACI, AI, EI, ETTI, EITI)
SIAE_TYPES_RELEVANT = {"ACI", "AI", "EI", "ETTI", "EITI"}

DEPARTEMENTS = list(METROPOLITAN_DEPARTMENTS)


@dataclass
class DepartmentJobsFetchResult:
    department: str
    jobs: List[Dict[str, Any]]
    pages_expected: int
    pages_retrieved: int
    status: str
    error: str | None = None


def get_inclusion_jobs_status() -> Dict[str, Any]:
    """Returns the status and age of the inclusion jobs data.

    Returns:
        Dict[str, Any]: A dictionary with data availability status, age in days, and TTL metrics.
    """
    ttl_days = _ttl_days()
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


def extract_rome_code(rome_str: str) -> Optional[str]:
    """Extracts only the ROME code (e.g., A1203) from a string like 'Label (A1203)'."""
    if not rome_str:
        return None
    match = re.search(r"\(([A-Z]\d{4})\)", rome_str)
    if match:
        return match.group(1)
    return rome_str  # Fallback if already a code or format differs


def fetch_department_jobs_with_coverage(dept: str) -> DepartmentJobsFetchResult:
    """Fetches hiring structures and their job openings for a specific department.

    Args:
        dept: The 2-3 digit department code.

    Returns:
        List[Dict[str, Any]]: List of structures that contain job openings.
    """
    headers = {"Accept": "application/json"}
    params = {"postes_dans_le_departement": dept, "page_size": 100}

    all_results = []
    url = API_URL
    backoff = 5.0
    pages_retrieved = 0
    pages_expected: int | None = None

    while url:
        while True:
            try:
                response = requests.get(
                    url,
                    headers=headers,
                    params=params if url == API_URL else None,
                    timeout=30,
                )
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    wait_time = (
                        float(retry_after)
                        if retry_after and retry_after.isdigit()
                        else backoff
                    )
                    logging.warning(
                        f"    [Rate Limit] HTTP 429. Waiting {wait_time}s for dept {dept}..."
                    )
                    time.sleep(wait_time)
                    backoff = min(backoff * 2.0, 60.0)
                    continue
                response.raise_for_status()
                data = response.json()
                break
            except Exception as e:
                logging.error(f"    [Error] Failed to fetch dept {dept}: {e}")
                return DepartmentJobsFetchResult(
                    dept,
                    [],
                    pages_expected or 0,
                    pages_retrieved,
                    "failed",
                    str(e),
                )

        results = data.get("results", [])
        pages_retrieved += 1
        # Filter for structures with at least one job opening
        hiring = [s for s in results if len(s.get("postes", [])) > 0]
        all_results.extend(hiring)

        url = data.get("next")
        if pages_expected is None:
            total_pages = data.get("total_pages")
            pages_expected = int(total_pages) if total_pages is not None else None
        if url:
            time.sleep(0.5)  # Gentle iteration

    return DepartmentJobsFetchResult(
        dept,
        all_results,
        pages_expected or pages_retrieved,
        pages_retrieved,
        "success",
    )


def fetch_department_jobs(dept: str) -> List[Dict[str, Any]]:
    """Compatibility wrapper returning data only after a complete collection."""
    result = fetch_department_jobs_with_coverage(dept)
    if result.status != "success":
        raise RuntimeError(f"Inclusion jobs fetch failed for {dept}: {result.error}")
    return result.jobs


def run_ingestion(departments: List[str] = None) -> None:
    """Main ingestion loop using the public unauthenticated API.

    Args:
        departments: Optional list of specific department codes to ingest. If None,
            all DEPARTEMENTS are processed.
    """
    # Load SIAE lookup for code_insee fallback
    if SIAE_LOOKUP_PATH.exists():
        logging.info(f"  [Lookup] Loading SIAE lookup table from {SIAE_LOOKUP_PATH}...")
        str_inc = pd.read_parquet(SIAE_LOOKUP_PATH)
        # Ensure siret is string for matching
        str_inc["siret"] = str_inc["siret"].astype(str)
    else:
        logging.warning(
            f"  [Warning] SIAE lookup file not found at {SIAE_LOOKUP_PATH}. Fallback to local code_insee will be disabled."
        )
        str_inc = pd.DataFrame(columns=["siret", "code_insee"])

    depts_to_process = departments or DEPARTEMENTS
    logging.info(
        f"=== Starting Ingestion (Public Mode): Les emplois de l'inclusion ({len(depts_to_process)} depts) ==="
    )

    all_rows = []
    all_structures = []

    coverage_results: list[DepartmentJobsFetchResult] = []
    for dept in depts_to_process:
        logging.info(f"  Processing {dept}...")
        coverage_result = fetch_department_jobs_with_coverage(dept)
        coverage_results.append(coverage_result)
        if coverage_result.status != "success":
            raise RuntimeError(
                f"Inclusion jobs coverage failed for {dept}: {coverage_result.error}"
            )
        structures = coverage_result.jobs

        for siae in structures:
            siret = siae.get("siret")
            siae_type = siae.get("type")
            siae_name = siae.get("enseigne") or siae.get("raison_sociale")

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
                all_structures.append(
                    {
                        "codgeo": str(siae_code_insee),
                        "siae_siret": siret,
                        "siae_type": siae_type,
                        "siae_name": siae_name,
                    }
                )

            postes = siae.get("postes", [])
            for p in postes:
                lieu = p.get("lieu")
                code_insee = None
                if isinstance(lieu, dict):
                    code_insee = lieu.get("code_insee")

                # Fallback to SIAE info from lookup if lieu is missing or doesn't have code_insee
                if not code_insee and siret:
                    code_insee = siae_code_insee  # Reuse the one from SIAE

                if code_insee:
                    all_rows.append(
                        {
                            "job_id": p.get("id"),  # New: keep job ID
                            "codgeo": str(code_insee),
                            "siae_siret": siret,
                            "siae_type": siae_type,
                            "siae_name": siae_name,
                            "rome": extract_rome_code(p.get("rome")),
                            "postes": p.get("nombre_postes_ouverts", 1),
                        }
                    )

        time.sleep(1)  # Gentle delay between departments

    coverage = pd.DataFrame(
        [
            {
                "department": result.department,
                "status": result.status,
                "offers_count": len(result.jobs),
                "pages_expected": result.pages_expected,
                "pages_retrieved": result.pages_retrieved,
                "error": result.error,
            }
            for result in coverage_results
        ]
    )
    expected_departments = set(depts_to_process)
    completed_departments = set(
        coverage.loc[coverage["status"] == "success", "department"]
    )
    missing_departments = sorted(expected_departments - completed_departments)
    if missing_departments:
        raise RuntimeError(
            "Inclusion jobs coverage is incomplete; refusing to publish: "
            + ", ".join(missing_departments)
        )

    if all_rows:
        df = pd.DataFrame(all_rows)
        # Ensure types
        df["postes"] = (
            pd.to_numeric(df["postes"], errors="coerce").fillna(1).astype(int)
        )

        # Save granular data as requested (no aggregation)
        os.makedirs(OUTPUT_PATH.parent, exist_ok=True)
        df.to_parquet(OUTPUT_PATH, index=False)
        logging.info(f"Successfully saved {len(df)} job opening records to {OUTPUT_PATH}")
    else:
        logging.warning("No job data collected.")
        os.makedirs(OUTPUT_PATH.parent, exist_ok=True)
        pd.DataFrame(
            columns=[
                "job_id",
                "codgeo",
                "siae_siret",
                "siae_type",
                "siae_name",
                "rome",
                "postes",
            ]
        ).to_parquet(OUTPUT_PATH, index=False)

    if all_structures:
        df_struct = pd.DataFrame(all_structures)
        # Unique structures per commune
        df_struct = df_struct.drop_duplicates(subset=["codgeo", "siae_siret"])
        os.makedirs(STRUCTURES_PATH.parent, exist_ok=True)
        df_struct.to_parquet(STRUCTURES_PATH, index=False)
        logging.info(
            f"Successfully saved {len(df_struct)} unique SIAE structure records to {STRUCTURES_PATH}"
        )
    else:
        logging.warning("No structure data collected.")

    os.makedirs(COVERAGE_OUTPUT_PATH.parent, exist_ok=True)
    coverage.to_parquet(COVERAGE_OUTPUT_PATH, index=False)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s"
    )
    run_ingestion()
