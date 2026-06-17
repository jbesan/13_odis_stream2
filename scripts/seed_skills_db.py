import os
import sys
import logging

# Add 'app' directory to python path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'app'))

from services.knowledge_store import KnowledgeStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_skills_db")

def seed_database():
    logger.info("Initializing KnowledgeStore and database...")
    store = KnowledgeStore()
    
    seed_cards = [
        {
            "id": "basic_housing",
            "name": "Priorisation et Règles de Logement",
            "description": "Consignes pour l'analyse des loyers moyens, du logement social et de l'hébergement d'urgence.",
            "domain": "housing_expert",
            "version": "1.0.0",
            "tags": ["logement", "hébergement", "social"],
            "instructions": """Tu es l'expert logement d'ODIS.
Consignes :
1. Analyse le loyer moyen au m² et le délai d'attente pour un logement social.
2. Identifie les structures d'hébergement d'urgence locales (CHRS, CPH, CADA).
3. Si le candidat recherche un Logement Social ou un hébergement temporaire, explique clairement les démarches locales prioritaires."""
        },
        {
            "id": "basic_mobility",
            "name": "Transports et Tarification Solidaire",
            "description": "Consignes pour analyser les réseaux de transports en commun locaux et les réductions tarifaires.",
            "domain": "mobility_expert",
            "version": "1.0.0",
            "tags": ["transports", "mobilité", "bus", "train"],
            "instructions": """Tu es l'expert mobilité d'ODIS.
Consignes :
1. Présente le réseau de transport en commun local (arrêts de bus, tram, métro, gares).
2. Calcule les temps de trajet vers les points d'intérêt principaux (ex: préfecture).
3. Recherche s'il existe une tarification solidaire ou gratuite des transports locaux pour les bénéficiaires de la protection internationale ou personnes à faibles ressources."""
        },
        {
            "id": "basic_healthcare",
            "name": "Accès aux Soins et Structures de Santé",
            "description": "Consignes pour évaluer l'indice d'accessibilité aux médecins et identifier les hôpitaux ou PMI.",
            "domain": "healthcare_expert",
            "version": "1.0.0",
            "tags": ["santé", "médecin", "hôpital", "pmi"],
            "instructions": """Tu es l'expert santé d'ODIS.
Consignes :
1. Évalue l'accessibilité potentielle localisée (APL index) aux professionnels de santé.
2. Identifie les hôpitaux de proximité, les PMI (Protection Maternelle et Infantile) et les centres de santé.
3. Si le candidat a un besoin de santé spécifique, indique les démarches locales d'accès aux soins et de prise en charge."""
        },
        {
            "id": "basic_education",
            "name": "Scolarisation et Modes de Garde",
            "description": "Consignes pour identifier les écoles par tranches d'âges et les modalités d'inscription locales.",
            "domain": "education_expert",
            "version": "1.0.0",
            "tags": ["éducation", "école", "crèche", "scolarisation"],
            "instructions": """Tu es l'expert éducation d'ODIS.
Consignes :
1. Identifie les établissements scolaires locaux correspondants aux âges des enfants de la famille (crèches, écoles maternelles, primaires, collèges, lycées).
2. Explique brièvement les modalités d'inscription scolaire en mairie ou auprès du rectorat.
3. Indique s'il existe des dispositifs locaux d'accompagnement (aide aux devoirs, cantine solidaire)."""
        },
        {
            "id": "basic_social",
            "name": "Accompagnement Social et Intégration",
            "description": "Consignes pour localiser le CCAS, les cours de FLE et les associations d'intégration.",
            "domain": "social_integration_expert",
            "version": "1.0.0",
            "tags": ["social", "intégration", "ccas", "fle", "réfugiés"],
            "instructions": """Tu es l'expert intégration sociale d'ODIS.
Consignes :
1. Fournis les coordonnées et missions du CCAS (Centre Communal d'Action Sociale) de la commune.
2. Liste les associations locales spécialisées dans l'accueil et l'accompagnement des réfugiés (RNA).
3. Indique les démarches locales pour l'insertion (CAF, guichets uniques, cours de français/FLE)."""
        },
        {
            "id": "basic_jobs",
            "name": "Emploi et Insertion Professionnelle",
            "description": "Consignes pour analyser les opportunités France Travail, les codes ROME et les structures d'insertion (SIAE).",
            "domain": "job_hunter",
            "version": "1.0.0",
            "tags": ["emploi", "siae", "recrutement", "insertion"],
            "instructions": """Tu es le Job Hunter d'ODIS.
Consignes :
1. Examine les opportunités d'emploi pré-chargées ou issues de France Travail correspondant aux métiers recherchés (codes ROME).
2. Identifie les structures d'insertion par l'activité économique (SIAE) et les offres d'inclusion locales.
3. Priorise les offres les plus proches géographiquement et adaptées au niveau de langue du candidat."""
        }
    ]

    logger.info(f"Seeding {len(seed_cards)} default Skill Cards...")
    for card in seed_cards:
        store.insert_or_update_skill(
            skill_id=card["id"],
            description=card["description"],
            instructions=card["instructions"],
            domain=card["domain"],
            name=card["name"],
            version=card["version"],
            tags=card["tags"]
        )
    logger.info("Database seeding completed successfully.")

if __name__ == "__main__":
    seed_database()
