import os
import requests
import time
import pandas as pd
from dotenv import load_dotenv
from typing import List, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Load environment variables
load_dotenv("app/.env")

CLIENT_ID = os.getenv("FRANCE_TRAVAIL_CLIENT_ID")
CLIENT_SECRET = os.getenv("FRANCE_TRAVAIL_CLIENT_SECRET")

AUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"

# List of grand domaines based on API documentation
GRAND_DOMAINES = [
    "A",
    "B",
    "C",
    "C15",
    "D",
    "E",
    "F",
    "G",
    "H",
    "I",
    "J",
    "K",
    "L",
    "L14",
    "M",
    "M13",
    "M14",
    "M15",
    "M16",
    "M17",
    "M18",
    "N",
]

# Type Contrat for Level 3 splitting
TYPES_CONTRAT = ["CDI", "CDD", "MIS", "CCE", "CTI", "LIB", "DIN", "FRA"]

# Scope: Metropolitan France (01 to 95)
DEPARTEMENTS = [str(i).zfill(2) for i in range(1, 96)] + ["2A", "2B"]
DEPARTEMENTS = sorted(list(set(DEPARTEMENTS)))
if "20" in DEPARTEMENTS:
    DEPARTEMENTS.remove("20")

# Rate Limiter Configuration
MAX_CALLS_PER_SECOND = 9  # Safe margin
LOCK_TOKEN = threading.Lock()
LOCK_METRICS = threading.Lock()
LOCK_DATA = threading.Lock()


class RateLimiter:
    def __init__(self, calls_per_sec):
        self.delay = 1.0 / calls_per_sec
        self.next_call = time.time()
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            now = time.time()
            if now < self.next_call:
                time.sleep(self.next_call - now)
            self.next_call = time.time() + self.delay


RATE_LIMITER = RateLimiter(MAX_CALLS_PER_SECOND)

# Metrics Tracker
METRICS = {
    "total_calls": 0,
    "rate_limit_errors": 0,
    "start_time": 0,
}

# Session management
SESSION = {"token": None, "http": None}


def get_http_session():
    if SESSION["http"] is None:
        s = requests.Session()
        # Connection pooling: 20 connections max for our 10 threads
        adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        SESSION["http"] = s
    return SESSION["http"]


def increment_metrics(key):
    with LOCK_METRICS:
        METRICS[key] += 1


def get_token():
    increment_metrics("total_calls")
    with LOCK_TOKEN:
        payload = {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "scope": "o2dsoffre api_offresdemploiv2",
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        response = requests.post(AUTH_URL, data=payload, headers=headers)
        response.raise_for_status()
        token = response.json()["access_token"]
        SESSION["token"] = token
        return token


def api_call(params):
    RATE_LIMITER.wait()
    increment_metrics("total_calls")

    for attempt in range(5):
        try:
            http = get_http_session()
            headers = {
                "Authorization": f"Bearer {SESSION['token']}",
                "Accept": "application/json",
            }
            resp = http.get(SEARCH_URL, params=params, headers=headers, timeout=30)

            if resp.status_code == 200 or resp.status_code == 206:
                return resp
            elif resp.status_code == 204:
                return resp
            elif resp.status_code == 401:
                print("\n  [401] Refreshing token...")
                get_token()
                continue
            elif resp.status_code == 429:
                increment_metrics("rate_limit_errors")
                time.sleep(2**attempt)
                continue
            else:
                return resp
        except (requests.exceptions.RequestException, OSError) as e:
            print(f"\n  [Network Error] {e}. Retrying ({attempt + 1}/5)...")
            time.sleep(1 + attempt)
            continue
    return None


def prune_offer(offer: Dict[str, Any]) -> Dict[str, Any]:
    """Keeps only essential fields to reduce memory usage during ETL."""
    # Note: Extracting commune here avoids using .apply() later
    lt = offer.get("lieuTravail")
    commune = None
    if isinstance(lt, dict):
        commune = lt.get("commune")

    return {
        "id": offer.get("id"),
        "commune": commune,
        "romeCode": offer.get("romeCode"),
        "romeLibelle": offer.get("romeLibelle"),
        "nombrePostes": offer.get("nombrePostes"),
        "offresManqueCandidats": offer.get("offresManqueCandidats", False),
    }


def fetch_all_pages(params):
    all_offers = []

    # Range for the first call
    current_params = {**params, "range": "0-149"}
    resp = api_call(current_params)

    if resp is None:
        print(f"  [Fatal] Failed to fetch first page for {params} after 5 attempts.")
        return []

    if resp.status_code == 204:
        return []

    if not (resp.status_code == 200 or resp.status_code == 206):
        return []

    # Parse total from Content-Range
    content_range = resp.headers.get("Content-Range", "")
    total = 0
    if "/" in content_range:
        total = int(content_range.split("/")[-1])

    if total == 0:
        return []

    # If too big and not yet fully split, signal need for deeper split
    if total > 3150:
        if "grandDomaine" not in params:
            return None  # Trigger Level 2
        if "typeContrat" not in params:
            return None  # Trigger Level 3

    # Store first batch (pruned)
    results = resp.json().get("resultats", [])
    all_offers.extend([prune_offer(o) for o in results])

    # Fetch remaining pages
    limit = min(total, 3150)
    # Start from 150 since we already have the first page
    for start in range(150, limit, 150):
        end = min(start + 149, limit - 1)
        page_resp = api_call({**params, "range": f"{start}-{end}"})
        if page_resp and page_resp.status_code in [200, 206]:
            results = page_resp.json().get("resultats", [])
            all_offers.extend([prune_offer(o) for o in results])
        elif page_resp is None:
            print(f"      [Error] Could not fetch batch {start}-{end} for {params}.")

    if total > 3150:
        print(f"      /!\\ WARNING: Truncated to 3150 for params: {params}")

    return all_offers


def run_etl():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: Missing credentials.")
        return

    METRICS["start_time"] = time.time()
    get_token()

    print(f"=== Starting Optimized France Travail Live ETL ===")
    print(f"Target: Metropolitan France ({len(DEPARTEMENTS)} depts)")
    print(f"Strategy: Dept -> Domain -> TypeContrat (if needed)")

    all_raw_data = []

    def process_segment(params):
        results = fetch_all_pages(params)

        # Level 1 Split (by Domain)
        if results is None and "grandDomaine" not in params:
            # print(f"\n  Splitting {params['departement']} by Domain...")
            sub_results = []
            for domain in GRAND_DOMAINES:
                res = process_segment({**params, "grandDomaine": domain})
                if res and isinstance(res, list):
                    sub_results.extend(res)
            return sub_results

        # Level 2 Split (by Type Contrat)
        if results is None and "grandDomaine" in params:
            print(
                f"    - Domain {params['grandDomaine']} in {params['departement']} > 3150. Splitting by TypeContrat..."
            )
            sub_results = []
            for t_contrat in TYPES_CONTRAT:
                res = fetch_all_pages({**params, "typeContrat": t_contrat})
                if res and isinstance(res, list):
                    sub_results.extend(res)
            # Final check for "everything else" is not easy, but we cover 99% with TYPES_CONTRAT
            return sub_results

        return results

    # We use 10 threads to overlap network latency
    with ThreadPoolExecutor(max_workers=10) as executor:
        future_to_dept = {
            executor.submit(process_segment, {"departement": d}): d
            for d in DEPARTEMENTS
        }

        for future in as_completed(future_to_dept):
            dept = future_to_dept[future]
            try:
                data = future.result()
                if data:
                    with LOCK_DATA:
                        all_raw_data.extend(data)
                    print(f"Dept {dept}: Fetched {len(data)} offers.")
                else:
                    print(f"Dept {dept}: No offers.")
            except Exception as e:
                print(f"Dept {dept} generated an exception: {e}")

    # Transformation
    if all_raw_data:
        print("\n--- Processing Data ---")
        df = pd.DataFrame(all_raw_data)
        initial_count = len(df)
        df = df.drop_duplicates(subset=["id"])
        dropped = initial_count - len(df)
        if dropped > 0:
            print(f"  Note: {dropped} duplicate IDs dropped.")

        # Fields are already pruned. Just ensure types.
        df["offresManqueCandidats"] = (
            df["offresManqueCandidats"].fillna(False).astype(bool)
        )
        df["nombrePostes"] = (
            pd.to_numeric(df["nombrePostes"], errors="coerce").fillna(1).astype(int)
        )

        agg = (
            df.groupby(["commune", "romeCode", "romeLibelle"])
            .agg(
                {
                    "id": "count",
                    "nombrePostes": "sum",
                    "offresManqueCandidats": "sum",  # Count how many offers in this group are in tension
                }
            )
            .rename(
                columns={
                    "id": "nb_offres",
                    "nombrePostes": "total_postes",
                    "offresManqueCandidats": "nb_offres_tension",
                }
            )
            .reset_index()
        )

        output_path = "pipeline/cache/output/odis_ft_jobs_agg.parquet"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        agg.to_parquet(output_path, index=False, engine="fastparquet")

        # Validation: Check if all expected departments are present
        df["dept"] = df["commune"].str[:2]
        found_depts = df["dept"].unique()
        missing_depts = [d for d in DEPARTEMENTS if d not in found_depts]

        duration = (time.time() - METRICS["start_time"]) / 60
        print(f"\n=== ETL SUMMARY ===")
        print(f"Duration: {duration:.2f} minutes")
        print(f"Total API Calls: {METRICS['total_calls']}")
        print(f"Rate Limit Errors (429): {METRICS['rate_limit_errors']}")
        print(f"Raw Offers Fetched: {initial_count}")
        print(f"Final Aggregated Rows: {len(agg)}")
        print(f"Total Postes (Market Opportunity): {int(agg['total_postes'].sum())}")

        if missing_depts:
            print(f"/!\\ WARNING: Missing data for departments: {missing_depts}")
        else:
            print("✅ All departments successfully mapped in output.")

        print(f"Saved to: {output_path}")
        return output_path
    else:
        print("No data fetched.")
        return None


if __name__ == "__main__":
    run_etl()
