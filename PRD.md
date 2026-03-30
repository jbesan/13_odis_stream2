# PRD (Product Requirements Document) - OD&IS "Stream 2"

**Version :** 1.5
**Projet :** Prototype de Recherche Inversée (Aide à la Localisation)
**Auteur :** D4G: OD&IS (revu le 26/02/2026)

---

## 1. Contexte et Objectif

**Objectif :** Fournir un prototype d'outil d'aide à la décision pour les **travailleurs sociaux** accompagnant des personnes/familles en parcours d'intégration (statut régularisé, post-CADA).

**Principe (Recherche Inversée) :** L'outil ne part pas d'un lieu, mais du **"projet de vie"** de la personne pour identifier les territoires (communes ou paires de communes) les plus pertinents.

**Fondations Techniques :**

- **Données :** Exclusivement Open Data.
- **Moteur :** Scoring de pertinence (`scoring.py`) basé sur un profil utilisateur.
- **Stack :** Streamlit (UI), Pandas/GeoPandas (Data), Folium (Carto).

---

## 2. Persona Cible

- **Utilisateur :** Le **travailleur social**.
- **Rôle :** Utilise l'outil _pendant_ l'entretien comme support de médiation et d'exploration.
- **Besoin Clé :** **Explicabilité**. L'outil doit justifier _pourquoi_ un territoire est recommandé (points forts, radar de scores).

---

## 3. Parcours Fonctionnel (Version Actuelle 1.1)

_(Omitted for brevity in this section, see original)_

---

## 4. Idées d'améliorations

Liste en vrac d'idées d'amélioration

- [x] Ajouter un call-to-action dans les fiches du top 5 (e.g. appeler TS local)
- [x] Génerer un prompt pour une Deep Research sur un des territoires recommandés
- [x] Normaliser les scores de catégorie pour éviter une surévaluation de certains critères
- [x] Ajouter des profils de pondération (famille vs célibataire)
- [x] Ajouter la base J'accueille d'accueil chez l'habitant
- [x] Ajouter la base des transports publics ?
- [ ] Critere 'Niveau de vie' de l'INSEE ?
- [ ] Enlever la couleur politique
- [ ] Ajouter le salaire moyen + comparaison avec loyer moyen
- [ ] Regarder pourquoi les grandes villes ressortent plus
- [ ] Ajouter le critère insse d'acces aux instratuctures (mobilité): https://www.insee.fr/fr/statistiques/1908098
- [ ] Ajouter le critère insse d'affordabilité
- [ ] Temps d'accès à un médecin / Déserts médicaux
- [x] Regarder les Entreprises de l'Insertion (et leur soffres d'emploi)
  - champ 'est_siae' https://recherche-entreprises.api.gouv.fr/docs/
  - Il y a aussi des tables structures + offres sur Odace
- [ ] Remplacer par y-a-t'il un CTAI/PTAI (signe que la commune s'implique dans l'intégration) + Est-ce que la commune est adhérante ANVITA ? Ajouter un label ?
- [x] Rechercher au niveau national ou viser une région/ département en particulier?
- [x] Ajouter un 'en savoir plus' pour comprendre le scoring
- [ ] Gare --> a conditionner avec une question mobilité (besoin de revenir regulierement)
- [x] FLE doit être un critere à part entiere et il faut trouver une base spécifique
- [x] Les multiselects sont frustrants car il faut trouver la terminologie exacte
- [x] Créer et exploiter un dataset des associations dédiés aux réfugiés / demandeurs d'asile
- [x] [F-43] Upgrade to Gemini 3.1 Flash-Lite for all agents.
- [x] [F-44] Standardize all agents to return Pydantic structured outputs instead of raw strings.
- [x] AI Bot: ajouter le contact CCAS (passer dans get_city_details? )

## 5. Recent / active Feature Developements

_(Most features omitted for brevity. Appending F-42)_

## 🚀 Feature [F-42]: Refinements Hébergement (Court Terme)

### 📝 User Story

- En tant que travailleur social, je veux des options d'hébergement court-terme plus réalistes et alignées sur les dispositifs existants (IML/Solibail, pensions de famille, foyers spécifiques, hébergement citoyen).
- Je veux pouvoir sélectionner plusieurs options d'hébergement simultanément pour élargir le champ des recherches.

### 🔑 Key Features

- **Transformation UI :** Passage de "radio" (choix unique) à "checkboxes" (choix multiples) pour les différentes options d'hébergement.
- **Location avec Intermédiation :** Remplace l'option "Location" (dans Hébergement). Recherche via RNA des associations proposant des services IML/Solibail ("intermédiation locative", "solibail"). Active le score de vacance et de loyer moyen (`log_loyer_moyen_appt_all_scaled`).
- **Centres d'Hébergement :** Remplace l'option "Foyers". Intègre les sources BPE pour D703 (CHRS) et D704 (CPH) et évalue les scores basé sur `sum(CAPACITE)` d'accueil par habitant de la commune.
- **Foyers & Pensions de Famille :** Utilise la classification BPE D710 et le nom ("fjt", "pension", "migrant") pour le décompte de places dans les Foyers de Jeunes Travailleurs, Pensions de famille et Foyers de Travailleurs Migrants.
- **Chez l'habitant :** Recherche enrichie via RNAG pour "hébergement citoyen". Intégration de la base "J'Accueille" pour identifier les bassins de vie disposant d'accueillants actifs, valorisé via le score binaire `heb_jaccueille_score`. **Note :** Les données d'accueillants sont considérées comme non-publiques et sont stockées sur BigQuery (`jaccueille_accueillants_bdv`), puis récupérées dynamiquement par l'application pour plus de sécurité.

### 📊 Status

- **March 2026**: CI/CD pipeline stabilized. Tests and snapshots updated for Cloud Run deployment.
- **March 2026 (Fix)**: Resolved "Ecoles Elémentaires" visibility issue through enhanced data ingestion (expanded matching logic) and updated POI categorization.
- **March 2026 (Pipeline)**: Robustness improvements to `pipeline/etl.py` and `pipeline/ingest.py` (CLI arguments fix and non-interactive support).
- Planned (Feb 2026) - Initial refinements planned.

## 🚀 Feature [F-45]: Télémétrie, Feedback et Authentification Globale

### 📝 User Story

- En tant que chef de produit, je veux collecter des données quantitatives (télémétrie) et qualitatives (feedback utilisateur, état de l'agent) lors des tests réels, sans impacter les performances de l'application.
- En tant qu'administrateur, je veux m'assurer que l'application entière est protégée par une authentification avant accès.

### 🔑 Key Features

- **Authentification Globale :** `main.py` bloque l'accès à toutes les pages et affiche un avertissement "Feedback Mode". Utilisation du système d'authentification existant (`utils.auth`).
- **Télémétrie via Cloud Logging :** Des événements structurés (`RUN_SEARCH`, `SEARCH_RESULTS_RETURNED`) sont enregistrés de façon asynchrone (via Cloud Logging) et synchronisés dans BigQuery grâce à un Sink. Chaque événement possède un `interaction_id`.
- **Suivi de l'État Agent :** L'état asynchrone du LLM (ODISGraphState) est structuré et inséré directement dans une table BigQuery dédiée lors des interactions avec le Chatbot.
- **Formulaire de Feedback Intégré :** Un bouton dans la barre latérale permet d'ouvrir un modal de feedback ('Bug', 'Question', 'Suggestion'). Les envois sont liés à la session via le `interaction_id` et sauvegardés sur BigQuery.

### 📊 Status

- **March 2026**: Done

## 🚀 Feature [F-46]: Associations Directory Refinements

### 📝 User Story

- En tant que travailleur social, je veux que l'annuaire des associations soit plus lisible et informatif pour mieux orienter les usagers.
- Je veux voir une description courte des associations pour comprendre leur champ d'action sans quitter l'application.

### 🔑 Key Features

- **Formatage des noms :** Transformation des noms d'associations (souvent en MAJUSCULES dans la base RNA) en Title Case (majuscule en début de chaque mot).
- **Affichage des descriptions :** Intégration du champ `description` de la table `rna_rag`. Affichage des ~250 premiers caractères.
- **Lien externe :** Ajout d'un lien "Lire la suite" pointant vers la fiche détaillée sur `assoce.fr` pour chaque association.

### 📊 Status

## 🚀 Feature [F-47]: Amélioration de la Sélection des Métiers (ROME)

### 📝 User Story

- En tant que travailleur social, je veux que la liste des métiers proposés soit plus courte et mette en avant les métiers les plus prometteurs pour faciliter la saisie du projet de vie.
- Je veux que la liste soit triée par volume d'offres d'emploi réelles pour orienter l'usager vers les secteurs qui recrutent.

### 🔑 Key Features

- **Tri par pertinence :** La liste des codes ROME dans le formulaire est triée par le nombre total d'offres d'emploi (`total_postes`) enregistrées dans `odis_ft_jobs_agg.parquet`.
- **Troncature (Top 200) :** Limiter la liste initiale aux 200 métiers les plus demandés (couvrant ~75% du marché) pour éviter de perdre l'utilisateur dans une liste de 1500+ codes.
- **Support des codes existants :** Assurer que les codes ROME saisis manuellement ou via des scénarios démo qui ne seraient pas dans le Top 200 restent fonctionnels et affichables.

### 📊 Status

- **March 2026**: Definition and implementation started.

## 🚀 Feature [F-48]: Refonte de la Section Inclusion (Autres Besoins)

### 📝 User Story

- En tant que travailleur social, je veux que les besoins d'inclusion les plus courants (FLE, accompagnement administratif, recherche d'emploi) soient mis en avant via des cases à cocher simples et pré-remplies.
- Je veux une interface simplifiée où les critères "socle" et "additionnels" sont fusionnés pour plus de clarté dans le scoring.

### 🔑 Key Features

- **Simplification UI :** Remplacement du multiselect "Socle Administratif" caché par une série de 10-12 checkboxes explicites pour les besoins prioritaires.
- **Valeurs par Défaut :** Les 3 services du "Socle Administratif" actuel sont cochés par défaut.
- **Fusion du Scoring :** Suppression du critère `inc_services_core_scaled`. Tous les services sélectionnés (via checkboxes ou multiselect complémentaire) sont désormais agrégés dans un score unique `inc_services_add_scaled`.
- **Filtre Dynamique :** Le multiselect "Autres services" ne propose plus les services déjà présents sous forme de checkboxes pour éviter les doublons.

### 📊 Status

- **March 2026**: Definition and implementation started.

## 🚀 Feature [F-49]: Refonte Pydantic Data Models (Search Results)

### 📝 User Story

- En tant que développeur, je veux que la structure des résultats de recherche (Top 5, Current Geo) soit typée et unifiée afin d'éviter les accès par dictionnaire non sécurisés et de simplifier l'intégration dans toutes les fonctionnalités (Chatbot, Pitch, PDF, Télémétrie).

### 🔑 Key Features

- **Nouveau Modèle `CityResult`** : Encapsule l'identité d'une commune, son score global et ses détails par catégorie de manière formelle.
- **Nouveau Modèle `SearchResultsData`** : Encapsule les métadonnées de la recherche (Hash), la liste structurée des n meilleurs résultats (`top_cities`), et la commune actuelle de référence (`current_geo`).
- **Refactorisation Transversale** : Remplacement des dictionnaires natifs dans l'interface, le Scoring Engine, PDF Generator et les interactions avec les agents AI.

### 📊 Status

- **March 2026**: Definition started.

## 🚀 Feature [F-50]: Ciblage Dynamique de la Population

### 📝 User Story

- En tant que travailleur social, je veux pouvoir ajuster la taille cible de la commune (population) pour mieux correspondre aux préférences de l'usager (ex: petite ville vs grande métropole).
- Je veux que l'outil recalcule automatiquement la pertinence en fonction de cette taille cible, avec une marge de tolérance (sigma) adaptée.

### 🔑 Key Features

- **Slider de Population :** Ajout d'un curseur dans la section "Zone de recherche" permettant de choisir une cible entre 2 000 et 200 000 habitants (échelle logarithmique).
- **Calcul de Sigma Dynamique :** Ajustement automatique de l'écart-type (sigma) de la fonction gaussienne en fonction de la cible `mu` pour maintenir une dispersion cohérente.
- **Scoring Temps Réel :** Re-calcul dynamique du score `inc_population_scaled` lors de la recherche, outre-passant la valeur pré-calculée si une cible est définie par l'utilisateur.

### 📊 Status

- **March 2026**: Conception et implémentation en cours.
