import requests
import os
import sys
from dotenv import load_dotenv

# ROME V3 Domains Mapping (3 characters) - Verified for France Travail API v2
# Based on your previous output, confirming V3 definitions are the correct ones.
ROME_V3_LABELS = {
    # FAMILY A: AGRICULTURE
    "A11": "Engins agricoles et forestiers",
    "A12": "Espaces naturels et verts",
    "A13": "Études et assistance technique",
    "A14": "Production agricole (Elevage/Culture)",
    "A15": "Soins aux animaux",
    # FAMILY B: ARTS & ARTISANAT
    "B11": "Arts plastiques",
    "B12": "Céramique",
    "B13": "Décoration",
    "B14": "Fibres et papier",
    "B16": "Métal, verre, bijoux",
    # FAMILY C: BANQUE / ASSURANCE
    "C11": "Assurance",
    "C12": "Banque",
    "C15": "Immobilier",
    # FAMILY D: COMMERCE / VENTE
    "D11": "Vente en alimentation",
    "D12": "Vente spécialisée",
    "D14": "Relation commerciale",
    "D15": "Management de magasin",
    # FAMILY F: BTP / CONSTRUCTION
    "F11": "Conception et études BTP",
    "F13": "Second oeuvre",
    "F16": "Chauffage et plomberie",
    "F17": "Menuiserie",
    # FAMILY G: HÔTELLERIE / RESTAURATION
    "G12": "Hébergement",
    "G13": "Cuisine",
    "G14": "Service en restauration",
    "G15": "Restauration rapide",
    "G16": "Production culinaire (Manager/Pers.)",
    "G18": "Animation",
    # FAMILY H: INDUSTRIE
    "H12": "Etudes et méthodes",
    "H15": "Qualité et sécurité",
    # FAMILY I: MAINTENANCE
    "I11": "Maintenance machines",
    "I13": "Maintenance équipement",
    "I16": "Mécanique véhicule / Engins",
    # FAMILY J: SANTÉ
    "J15": "Soins paramédicaux / Infirmiers",
    "J13": "Rééducation et appareillage",
    # FAMILY K: SERVICES À LA PERSONNE / COLLECTIVITÉ
    "K11": "Accompagnement social",
    "K12": "Développement local",
    "K13": "Aide à la vie quotidienne / Social",
    "K14": "Education et formation",
    "K18": "Sécurité et secours",
    "K22": "Nettoyage et propreté",
    # FAMILY M: SUPPORT ENTREPRISE
    "M12": "Conseil et gestion / Etudes",
    "M14": "Ressources humaines",
    "M16": "Secrétariat et assistanat",
    "M17": "Stratégie commerciale / Marketing",
    "M18": "Logistique",
    # FAMILY N: TRANSPORT / LOGISTIQUE
    "N11": "Conduite de véhicule",
    "N41": "Manutention et stockage",
}

# 1. Load Credentials from app/.env
dotenv_path = os.path.join(os.getcwd(), 'app', '.env')
if not os.path.exists(dotenv_path):
    print(f"❌ Error: .env file not found at {dotenv_path}")
    sys.exit(1)

load_dotenv(dotenv_path)

CLIENT_ID = os.getenv("FRANCE_TRAVAIL_CLIENT_ID")
CLIENT_SECRET = os.getenv("FRANCE_TRAVAIL_CLIENT_SECRET")
AUTH_URL = "https://entreprise.francetravail.fr/connexion/oauth2/access_token?realm=/partenaire"
BASE_URL = "https://api.francetravail.io/partenaire/offresdemploi/v2/offres/search"

def get_token():
    payload = {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "api_offresdemploiv2 o2dsoffre"
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    res = requests.post(AUTH_URL, data=payload, headers=headers)
    if res.status_code != 200:
        print(f"❌ Auth Failed: {res.status_code} - {res.text}")
        sys.exit(1)
    return res.json()["access_token"]

def fetch_stats(insee_code, distance):
    token = get_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    params = {
        "commune": insee_code,
        "distance": distance,
        "range": "0-149",
        "sort": 1
    }
    
    print(f"📡 Querying France Travail for INSEE: {insee_code}, Radius: {distance}km...")
    res = requests.get(BASE_URL, params=params, headers=headers)
    
    if res.status_code == 204:
        print("⚠️ No job offers found for these parameters.")
        return

    res.raise_for_status()
    data = res.json()
    offres = data.get("resultats", [])
    
    content_range = res.headers.get("Content-Range", "")
    total_api = content_range.split("/")[-1] if "/" in content_range else "unknown"
    
    print(f"✅ Samples retrieved: {len(offres)} (Total matching: {total_api})")
    
    cluster_stats = {}
    appellation_stats = {}
    
    for o in offres:
        # 1. ROME Cluster Analysis
        rome = o.get("romeCode", "???")
        cluster = rome[:3]
        cluster_stats[cluster] = cluster_stats.get(cluster, 0) + 1
        
        # 2. Appellation Analysis (Granular Job titles)
        app_label = o.get("appellationlibelle")
        if not app_label:
             # Clean common (H/F) suffix from title for cleaner stats
             app_label = o.get("intitule", "").split(" (H/F)")[0].split(" H/F")[0]
        
        app_code = o.get("appellationCode", "????")
        # Store with code for unambiguous mapping
        key = f"{app_label}"
        appellation_stats[key] = appellation_stats.get(key, 0) + 1
        
    # --- TABLE 1: ROME V3 CLUSTERS ---
    print("\n" + "="*75)
    print(f"{'CLUSTER':<10} | {'DESCRIPTION (ROME V3)':<40} | {'COUNT'}")
    print("-" * 75)
    sorted_clusters = sorted(cluster_stats.items(), key=lambda x: x[1], reverse=True)
    for code, count in sorted_clusters:
        label = ROME_V3_LABELS.get(code, "Famille macro ou code rare")
        print(f"{code:<10} | {label:<40} | {count}")
    
    # --- TABLE 2: TOP APPELLATIONS ---
    print("\n" + "="*75)
    print(f"{'TOP 15 MÉTIERS PRÉCIS (APPELLATIONS)':<65} | {'COUNT'}")
    print("-" * 75)
    sorted_apps = sorted(appellation_stats.items(), key=lambda x: x[1], reverse=True)[:15]
    for app, count in sorted_apps:
        print(f"{app[:65]:<65} | {count}")
        
    print("="*75 + "\n")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 ft_stats.py <INSEE_CODE> <DISTANCE_KM>")
    else:
        fetch_stats(sys.argv[1], sys.argv[2])
