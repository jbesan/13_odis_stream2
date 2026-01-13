import os
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

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

def get_count(token, params):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    params["range"] = "0-0"
    resp = requests.get(SEARCH_URL, params=params, headers=headers)
    if resp.status_code == 204:
        return 0
    content_range = resp.headers.get("Content-Range", "")
    if "/" in content_range:
        return int(content_range.split("/")[-1])
    return 0

def analyze():
    token = get_token()
    dept = "33"
    
    print(f"=== Analyse de l'ancienneté des offres en Gironde ({dept}) ===")
    
    # 1. Total Stock
    total = get_count(token, {"departement": dept})
    print(f"Total des offres en ligne : {total}")
    
    # 2. Breakdown using minCreationDate
    now = datetime.now()
    intervals = {
        "7j": 7,
        "30j": 30,
        "90j": 90,
        "180j": 180,
        "365j": 365
    }
    
    print("\nBreakdown (Stock cumulé par date de création) :")
    for label, days in intervals.items():
        min_date = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
        count = get_count(token, {"departement": dept, "minCreationDate": min_date})
        print(f"- Créées depuis {label} : {count} ({count/total*100:.1f}%)")
    
    one_year_ago = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    older_than_year = get_count(token, {"departement": dept, "maxCreationDate": one_year_ago})
    print(f"- Plus de 1 an         : {older_than_year} ({older_than_year/total*100:.1f}%)")

if __name__ == "__main__":
    analyze()
