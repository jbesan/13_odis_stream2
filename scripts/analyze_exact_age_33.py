import os
import requests
import time
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

load_dotenv("app/.env")

CLIENT_ID = os.getenv("FRANCE_TRAVAIL_CLIENT_ID")
CLIENT_SECRET = os.getenv("FRANCE_TRAVAIL_CLIENT_SECRET")
AUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
SEARCH_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"

GRAND_DOMAINES = ["A", "B", "C", "C15", "D", "E", "F", "G", "H", "I", "J", "K", "L", "L14", "M", "M13", "M14", "M15", "M16", "M17", "M18", "N"]

class RateLimiter:
    def __init__(self, calls_per_sec):
        self.delay = 1.0 / calls_per_sec
        self.next_call = time.time()
        self.lock = threading.Lock()
    def wait(self):
        with self.lock:
            now = time.time()
            if now < self.next_call: time.sleep(self.next_call - now)
            self.next_call = time.time() + self.delay

RATE_LIMITER = RateLimiter(9)
SESSION = {"token": None, "http": requests.Session()}

def get_token():
    payload = {"grant_type": "client_credentials", "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "scope": "o2dsoffre api_offresdemploiv2"}
    resp = requests.post(AUTH_URL, data=payload)
    SESSION["token"] = resp.json()["access_token"]
    return SESSION["token"]

def api_call(params):
    RATE_LIMITER.wait()
    for attempt in range(5):
        try:
            headers = {"Authorization": f"Bearer {SESSION['token']}", "Accept": "application/json"}
            resp = SESSION["http"].get(SEARCH_URL, params=params, headers=headers, timeout=30)
            if resp.status_code in [200, 206, 204]: return resp
            if resp.status_code == 401: get_token(); continue
            if resp.status_code == 429: time.sleep(2**attempt); continue
            return resp
        except: time.sleep(1); continue
    return None

def fetch_segment(params):
    resp = api_call({**params, "range": "0-149"})
    if not resp or resp.status_code == 204: return []
    content_range = resp.headers.get("Content-Range", "")
    total = int(content_range.split("/")[-1]) if "/" in content_range else 0
    if total > 3150 and "grandDomaine" not in params: return None
    
    offers = resp.json().get("resultats", [])
    limit = min(total, 3150)
    for start in range(150, limit, 150):
        end = min(start + 149, limit - 1)
        r = api_call({**params, "range": f"{start}-{end}"})
        if r and r.status_code in [200, 206]: offers.extend(r.json().get("resultats", []))
    return offers

def analyze():
    get_token()
    dept = "33"
    print(f"Extraction des dates pour la Gironde...")
    all_dates = []
    
    # Basic check
    initial = fetch_segment({"departement": dept})
    if initial is None: # Too big, use domains
        with ThreadPoolExecutor(max_workers=5) as exec:
            futures = [exec.submit(fetch_segment, {"departement": dept, "grandDomaine": g}) for g in GRAND_DOMAINES]
            for f in as_completed(futures):
                res = f.result()
                if res: all_dates.extend([o.get("dateCreation") for o in res])
    else:
        all_dates = [o.get("dateCreation") for o in initial]

    if not all_dates:
        print("Erreur: aucune donnée récupérée.")
        return

    df = pd.DataFrame({"date": pd.to_datetime(all_dates)})
    now = pd.Timestamp.now(tz='UTC')
    df["age_days"] = (now - df["date"]).dt.total_seconds() / (24 * 3600)
    
    total = len(df)
    print(f"\nStats sur {total} offres (Dédoublonné):")
    
    bins = [0, 7, 30, 90, 180, 365, 9999]
    labels = ["< 1 semaine", "1 sem - 1 mois", "1 - 3 mois", "3 - 6 mois", "6 mois - 1 an", "> 1 an"]
    df["cat"] = pd.cut(df["age_days"], bins=bins, labels=labels)
    
    summary = df["cat"].value_counts().reindex(labels)
    for label, count in summary.items():
        print(f"- {label:15}: {count:5} ({count/total*100:4.1f}%)")

if __name__ == "__main__":
    analyze()
