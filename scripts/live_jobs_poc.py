import os
import requests
import time
import pandas as pd
from dotenv import load_dotenv

# Load environment variables from app/.env
load_dotenv("app/.env")

CLIENT_ID = os.getenv("FRANCE_TRAVAIL_CLIENT_ID")
CLIENT_SECRET = os.getenv("FRANCE_TRAVAIL_CLIENT_SECRET")

AUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"

def get_token():
    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "o2dsoffre api_offresdemploiv2"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    response = requests.post(AUTH_URL, data=payload, headers=headers)
    response.raise_for_status()
    return response.json()["access_token"]

def fetch_offers(token, departement="33"):
    offers = []
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    # France Travail API limits pagination to 3150 results
    # Each request can fetch up to 150 results
    chunk_size = 150
    for start in range(0, 3150, chunk_size):
        end = min(start + chunk_size - 1, 3149)
        params = {
            "departement": departement,
            "range": f"{start}-{end}",
            "sort": 1 # Sort by date
        }
        
        print(f"Fetching range {start}-{end}...")
        response = requests.get(SEARCH_URL, params=params, headers=headers)
        
        if response.status_code == 204:
            print("No more results.")
            break
        
        if response.status_code == 200 or response.status_code == 206:
            data = response.json()
            if "filtresPossibles" in data:
                print("\nAvailable Aggregations in response:")
                for f in data["filtresPossibles"]:
                    print(f"- Filtre: {f.get('filtre')}, Unique values: {len(f.get('agregation', []))}")
            
            batch = data.get("resultats", [])
            offers.extend(batch)
            print(f"Received {len(batch)} offers. Total: {len(offers)}")
            
            # Check if we reached the end via Content-Range header
            content_range = response.headers.get("Content-Range", "")
            if "/" in content_range:
                total_str = content_range.split("/")[-1]
                total_available = int(total_str)
                if len(offers) >= total_available:
                    print("Reached total available results.")
                    break
        else:
            print(f"Error {response.status_code}: {response.text}")
            break
            
        # Small sleep to be nice to the API
        time.sleep(0.5)
        
    return offers

def consolidate_offers(offers):
    if not offers:
        return pd.DataFrame()
    
    # Flattening interested fields
    data = []
    for o in offers:
        data.append({
            "romeCode": o.get("romeCode"),
            "romeLibelle": o.get("romeLibelle"),
            "commune": o.get("lieuTravail", {}).get("commune")
        })
    
    df = pd.DataFrame(data)
    
    # Aggregation
    agg = df.groupby(["romeCode", "romeLibelle", "commune"]).size().reset_index(name="nb_offres")
    
    # Sort by number of offers
    agg = agg.sort_values("nb_offres", ascending=False)
    
    return agg

if __name__ == "__main__":
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Error: Missing France Travail credentials in app/.env")
    else:
        try:
            print("Authenticating...")
            token = get_token()
            
            print("Starting data collection for department 33...")
            all_offers = fetch_offers(token, departement="33")
            
            print(f"Consolidating {len(all_offers)} offers...")
            consolidated = consolidate_offers(all_offers)
            
            if not consolidated.empty:
                output_file = "data/live_jobs_33_consolidation.csv"
                consolidated.to_csv(output_file, index=False)
                print(f"Done! Results saved to {output_file}")
                
                print("\nTop 10 ROME/Commune combinations:")
                print(consolidated.head(10).to_string(index=False))
                
                print(f"\nTotal unique combinations: {len(consolidated)}")
                print(f"Total offres accounted for: {consolidated['nb_offres'].sum()}")
            else:
                print("No data collected.")
                
        except Exception as e:
            print(f"An error occurred: {e}")
