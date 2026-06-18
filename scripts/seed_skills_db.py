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
            "id": "housing_full_analysis",
            "name": "Analyse Complète du Logement",
            "description": "Consignes pour l'analyse des loyers moyens, du logement social et de l'hébergement d'urgence dans la commune.",
            "domain": "housing_expert",
            "version": "1.0.0",
            "tags": ["logement", "hébergement", "social"],
            "tools": ["search_places_batch_tool", "search_ccas_tool"],
            "instructions": """Tu es l'expert logement d'ODIS.
Consignes :
1. Analyse le loyer moyen au m² pour le parc privé et le parc social.
2. Identifie les structures d'hébergement temporaires ou d'urgence de la commune (CADA, CHRS, CPH) en utilisant le tool `search_places_batch_tool`.
3. Obtiens les détails et missions du CCAS local avec le tool `search_ccas_tool` pour orienter les demandes d'hébergement."""
        },
        {
            "id": "mobility_full_analysis",
            "name": "Analyse Complète de la Mobilité",
            "description": "Consignes pour analyser les réseaux de transports en commun locaux et les réductions tarifaires solidaires.",
            "domain": "mobility_expert",
            "version": "1.0.0",
            "tags": ["transports", "mobilité", "bus", "train"],
            "tools": ["search_places_batch_tool", "compute_routes_tool"],
            "instructions": """Tu es l'expert mobilité d'ODIS.
Consignes :
1. Présente le réseau de transport en commun local (arrêts de bus, tram, métro, gares) identifié à l'aide du tool `search_places_batch_tool`.
2. Estime les temps de trajet vers les zones d'emploi ou centres administratifs principaux via le tool `compute_routes_tool`.
3. Recherche les aides régionales, départementales ou municipales pour la mobilité (aides au permis, réductions tarifaires)."""
        },
        {
            "id": "healthcare_full_analysis",
            "name": "Analyse Complète de l'Accès aux Soins",
            "description": "Consignes pour évaluer l'indice d'accessibilité aux médecins et identifier les hôpitaux ou PMI de la commune.",
            "domain": "healthcare_expert",
            "version": "1.0.0",
            "tags": ["santé", "médecin", "hôpital", "pmi"],
            "tools": ["search_places_batch_tool", "compute_routes_tool"],
            "instructions": """Tu es l'expert santé d'ODIS.
Consignes :
1. Évalue l'accessibilité potentielle localisée (APL index) aux professionnels de santé de la commune.
2. Localise les hôpitaux, cliniques et centres de Protection Maternelle et Infantile (PMI) en utilisant le tool `search_places_batch_tool` (et calcule si nécessaire les temps de trajet avec le tool `compute_routes_tool`).
3. Identifie les réseaux d'entraide ou d'interprétariat médical pour les personnes allophones."""
        },
        {
            "id": "education_full_analysis",
            "name": "Analyse Complète de la Scolarisation",
            "description": "Consignes pour identifier les écoles par tranches d'âges et les modalités d'inscription scolaires locales.",
            "domain": "education_expert",
            "version": "1.0.0",
            "tags": ["éducation", "école", "crèche", "scolarisation"],
            "tools": ["search_places_batch_tool", "compute_routes_tool"],
            "instructions": """Tu es l'expert éducation d'ODIS.
Consignes :
1. Identifie les établissements scolaires locaux correspondant aux âges des enfants de la famille (crèches, écoles maternelles, primaires, collèges, lycées) en utilisant le tool `search_places_batch_tool` (et calcule si besoin les trajets scolaires avec le tool `compute_routes_tool`).
2. Présente les modalités et justificatifs nécessaires à l'inscription scolaire auprès de la mairie.
3. Recherche la présence de dispositifs de soutien scolaire ou d'accueil périscolaire."""
        },
        {
            "id": "social_full_analysis",
            "name": "Analyse Complète de l'Accompagnement Social",
            "description": "Consignes pour localiser le CCAS, les cours de FLE et les associations d'intégration locales.",
            "domain": "social_integration_expert",
            "version": "1.0.0",
            "tags": ["social", "intégration", "ccas", "fle", "réfugiés"],
            "tools": ["search_ccas_tool", "search_places_batch_tool", "search_associations_batch_tool"],
            "instructions": """Tu es l'expert intégration sociale d'ODIS.
Consignes :
1. Fournis les coordonnées et missions du CCAS (Centre Communal d'Action Sociale) de la commune à l'aide du tool `search_ccas_tool`.
2. Identifie les structures locales proposant des cours de français (FLE, alphabétisation) via le tool `search_places_batch_tool`.
3. Repère les associations locales d'accueil, d'aide administrative et d'inclusion sociale via le tool `search_associations_batch_tool`."""
        },
        {
            "id": "job_full_analysis",
            "name": "Analyse Complète de l'Emploi",
            "description": "Consignes pour analyser les opportunités France Travail, les codes ROME et les structures d'insertion (SIAE) dans la commune.",
            "domain": "job_hunter",
            "version": "1.0.0",
            "tags": ["emploi", "siae", "recrutement", "insertion"],
            "tools": ["search_job_offers_batch_tool", "search_inclusion_jobs_batch_tool"],
            "instructions": """Tu es le Job Hunter d'ODIS.
Consignes :
1. Examine les opportunités d'emploi pré-chargées ou issues de France Travail correspondant aux métiers recherchés (codes ROME) avec le tool `search_job_offers_batch_tool`.
2. Identifie les structures d'insertion par l'activité économique (SIAE) et les offres d'inclusion locales à l'aide du tool `search_inclusion_jobs_batch_tool`.
3. Priorise les offres les plus proches géographiquement et adaptées au niveau de langue du candidat."""
        },
        {
            "id": "job_fetch_specific_offer",
            "name": "Consultation d'une Offre d'Emploi",
            "description": "Consignes pour consulter le détail d'une offre d'emploi spécifique à partir de son identifiant (ID).",
            "domain": "job_hunter",
            "version": "1.0.0",
            "tags": ["emploi", "offre", "détails"],
            "tools": ["get_job_details_tool"],
            "instructions": """Tu es le Job Hunter d'ODIS.
Consignes :
1. Appelle immédiatement le tool `get_job_details_tool` pour l'ID spécifié.
2. Structure ton analyse avec les informations clés : employeur, contrat, localisation, salaire, et compétences demandées.
3. Évalue l'adéquation de l'offre avec les compétences, l'expérience et les contraintes de mobilité du candidat."""
        },
        {
            "id": "job_extend_job_search",
            "name": "Extension de la Recherche d'Emploi",
            "description": "Consignes pour effectuer de nouvelles recherches d'offres d'emploi en élargissant les critères (ROME ou communes additionnelles).",
            "domain": "job_hunter",
            "version": "1.0.0",
            "tags": ["emploi", "recherche", "extension"],
            "tools": ["search_referentiels_batch_tool", "search_job_offers_batch_tool", "search_inclusion_jobs_batch_tool"],
            "instructions": """Tu es le Job Hunter d'ODIS.
Consignes :
1. Recherche des codes ROME ou communes additionnels à l'aide du tool `search_referentiels_batch_tool` si nécessaire.
2. Lance le tool `search_job_offers_batch_tool` (France Travail) ou le tool `search_inclusion_jobs_batch_tool` (SIAE) avec ces nouveaux critères.
3. Rédige une synthèse comparative des nouvelles opportunités identifiées."""
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
            tags=card["tags"],
            tools=card.get("tools")
        )
    logger.info("Database seeding completed successfully.")

if __name__ == "__main__":
    seed_database()
