import os
import requests
import time
import pandas as pd
from dotenv import load_dotenv
from typing import List, Dict

# Load environment variables
load_dotenv("app/.env")

CLIENT_ID = os.getenv("FRANCE_TRAVAIL_CLIENT_ID")
CLIENT_SECRET = os.getenv("FRANCE_TRAVAIL_CLIENT_SECRET")

AUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"

# List of grand domaines based on API documentation
GRAND_DOMAINES = [
    "A", "B", "C", "C15", "D", "E", "F", "G", "H", "I", "J", "K", "L", "L14",
    "M", "M13", "M14", "M15", "M16", "M17", "M18", "N"
]

# Scope: Metropolitan France (01 to 95)
DEPARTEMENTS = [str(i).zfill(2) for i in range(1, 96)]
# Skip specific 2A/2B for purely numeric range or add them explicitly
if "2A" not in DEPARTEMENTS:
    DEPARTEMENTS.extend(["2A", "2B"])
DEPARTEMENTS.sort()

# Metrics Tracker
METRICS = {
    "total_calls": 0,
    "rate_limit_errors": 0,
    "start_time": 0,
}

# Session management for token persistence and refresh
SESSION = {
    "token": None
}

def get_token():
    METRICS["total_calls"] += 1
    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "o2dsoffre api_offresdemploiv2"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(AUTH_URL, data=payload, headers=headers)
    response.raise_for_status()
    token = response.json()["access_token"]
    SESSION["token"] = token
    return token

def fetch_chunk(params) -> List[Dict]:
    offers = []
    
    def get_headers():
        return {
            "Authorization": f"Bearer {SESSION['token']}",
            "Accept": "application/json"
        }
    
    # Call to get total count
    METRICS["total_calls"] += 1
    params["range"] = "0-0"
    resp = requests.get(SEARCH_URL, params=params, headers=get_headers())
    
    if resp.status_code == 401:
        print("\n  [401] Token expired. Refreshing...")
        get_token()
        return fetch_chunk(params) # Recurse with new token

    if resp.status_code == 429:
        METRICS["rate_limit_errors"] += 1
        time.sleep(2)
        return fetch_chunk(params) # Retry once for the total check
        
    if resp.status_code == 204:
        return []
    
    content_range = resp.headers.get("Content-Range", "")
    total = 0
    if "/" in content_range:
        total = int(content_range.split("/")[-1])
    
    if total == 0:
        return []

    # If total > 3150 and we haven't split by domain yet, return None to trigger split
    if total > 3150 and "grandDomaine" not in params:
        return None
    
    # Fetch all pages (capped at 3150 per API limits)
    limit = min(total, 3150)
    for start in range(0, limit, 150):
        end = min(start + 149, limit - 1)
        params["range"] = f"{start}-{end}"
        
        # Retry logic for rate limiting
        for attempt in range(5):
            METRICS["total_calls"] += 1
            r = requests.get(SEARCH_URL, params=params, headers=get_headers())
            
            if r.status_code == 200 or r.status_code == 206:
                batch = r.json().get("resultats", [])
                offers.extend(batch)
                break
            elif r.status_code == 401:
                print("\n  [401] Token expired during batch. Refreshing...")
                get_token()
                # Headers will be updated on next retry of this batch
            elif r.status_code == 429:
                METRICS["rate_limit_errors"] += 1
                wait = (2 ** attempt) + 0.5 
                print(f"  [429] Rate limit hit. Waiting {wait}s...")
                time.sleep(wait)
            else:
                print(f"  Error {r.status_code} on {params}")
                break
        
        # Core rate limiting: max 10 calls/sec -> 0.1s minimum delay
        # We use 0.12s to be safe and account for network jitter
        time.sleep(0.12)
        
    return offers

def run_etl():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: Missing France Travail credentials.")
        return

    METRICS["start_time"] = time.time()
    get_token() # Initial token
    all_data = []
    
    print(f"=== Starting France Travail Live ETL ===")
    print(f"Target: Metropolitan France ({len(DEPARTEMENTS)} departments)")
    print(f"Strategy: Dept -> Grand Domaine (if > 3150 results)")
    
    try:
        for dept in DEPARTEMENTS:
            print(f"Processing Dept {dept}...", end=" ", flush=True)
            offers = fetch_chunk({"departement": dept})
            
            if offers is None: # Too many results, split by domain
                print("\n  > 3150 results. Splitting by Domain:")
                for domain in GRAND_DOMAINES:
                    domain_offers = fetch_chunk({"departement": dept, "grandDomaine": domain})
                    if isinstance(domain_offers, list):
                        all_data.extend(domain_offers)
                        print(f"    - Domain {domain}: {len(domain_offers)} offers")
                        
                        # Check if we likely missed data
                        # fetch_chunk returns up to 3150. If we hit exactly 3150, 
                        # it's possible there were more.
                        if len(domain_offers) >= 3150:
                            print(f"      /!\\ WARNING: Domain {domain} in Dept {dept} reached pagination limit (3150). Data might be truncated.")
                    elif domain_offers is None:
                        # This shouldn't happen with the current logic unless we add LEVEL 3
                        print(f"    - Domain {domain}: Still too many results (> 3150). Truncated to 3150.")
                        # Fallback fetch the first 3150 anyway
                        truncated_fetch = fetch_chunk({"departement": dept, "grandDomaine": domain, "range": "0-3149"})
                        if truncated_fetch:
                            all_data.extend(truncated_fetch)
            elif offers:
                all_data.extend(offers)
                print(f"Fetched {len(offers)} offers.")
            else:
                print("No offers.")

    except KeyboardInterrupt:
        print("\nProcess interrupted by user. Saving partial data...")
    except Exception as e:
        print(f"\nCritical Error during extraction: {e}")

    # Transformation
    if all_data:
        # Transform
        print("Transforming and aggregating data...")
        df = pd.DataFrame(all_data)
        
        # Deduplicate by Offer ID to ensure perfectly clean data
        initial_count = len(df)
        df = df.drop_duplicates(subset=["id"])
        dropped = initial_count - len(df)
        if dropped > 0:
            print(f"  Note: {dropped} duplicate IDs dropped.")

        # Extract needed fields
        df["commune"] = df["lieuTravail"].apply(lambda x: x.get("commune") if isinstance(x, dict) else None)
        df["domaine_3"] = df["romeCode"].str[:3]
        
        # Handle nombrePostes
        df["nombrePostes"] = pd.to_numeric(df["nombrePostes"], errors="coerce").fillna(1)
        
        # Final Aggregation
        agg = df.groupby(["commune", "romeCode", "domaine_3", "romeLibelle"]).agg({
            "id": "count",
            "nombrePostes": "sum"
        }).rename(columns={"id": "nb_offres", "nombrePostes": "total_postes"}).reset_index()
        
        # Save
        output_path = "data/odis_live_jobs_agg.parquet"
        agg.to_parquet(output_path, index=False)
        
        duration = time.time() - METRICS["start_time"]
        print(f"\n=== ETL SUMMARY ===")
        print(f"Duration: {duration:.2f} seconds ({duration/60:.2f} minutes)")
        print(f"Total API Calls: {METRICS['total_calls']}")
        print(f"Rate Limit Errors (429): {METRICS['rate_limit_errors']}")
        print(f"Total Raw Offers Fetched: {len(all_data)}")
        print(f"Total Final Rows: {len(agg)}")
        print(f"Total Market Opportunities: {agg['total_postes'].sum()}")
        print(f"Results saved to: {output_path}")
    else:
        print("No data fetched to process.")

if __name__ == "__main__":
    run_etl()
