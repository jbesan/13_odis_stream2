# PRD (Product Requirements Document) - OD&IS "Stream 2"

**Version :** 1.6
**Projet :** Prototype de Recherche Inversée (Aide à la Localisation)
**Auteur :** D4G: OD&IS (revu le 08/05/2026)

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
- [x] [F-43] Upgrade to Gemini 3.1 Flash-Lite for all agents.
- [x] [F-44] Standardize all agents to return Pydantic structured outputs instead of raw strings.
- [x] [F-55] Pydantic-Graph Migration: Move from LangGraph to native pydantic-graph MapReduce.
- [x] [F-57] Metadata-Driven Context Architecture (ACL)
- [x] AI Bot: ajouter le contact CCAS (passer dans get_city_details? )

---

## 🚀 Feature [F-58]: Unified Profiling & Refinement Flow

### 📝 User Story
- En tant que travailleur social, je veux que l'IA me propose une synthèse narrative de la situation ("Dossier") après chaque recherche, afin de valider qu'elle a bien compris mon projet de vie.
- En tant qu'architecte, je veux séparer l'extraction technique (Interviewer) de la synthèse narrative (Refiner), et garantir que tous les agents experts utilisent ce briefing consolidé comme source de vérité.

### 🔑 Key Features
- **Centralized Briefing** : Déplacement du `odis_brief` dans `SearchCriterias` (Source of Truth).
- **Refiner Agent** : Transformation de l'agent Scorer en un agent de synthèse global (Dossier + Pitches).
- **Cascading Context** : Le briefing généré par le Refiner est injecté dans le graphe MapReduce pour un grounding de haute qualité.

### 📊 Status
- **May 2026**: Complete.

---

## 🚀 Feature [F-59]: PLM Arrondissement Consolidation & Filtering

### 📝 User Story
- En tant que travailleur social, je veux que les résultats pour Paris, Lyon et Marseille soient présentés au niveau global de la commune et non par arrondissement, afin d'éviter la pollution visuelle sur la carte et de garantir que les scores reflètent les services consolidés du territoire entier.
- En tant qu'administrateur de données, je veux que toutes les fiches détaillées (CCAS, POIs, associations, formations) soient agrégées au niveau de la commune parente globale pour assurer la cohérence et l'exhaustivité des informations.

### 🔑 Key Features
- **ETL Aggregation**: Somme et moyenne pondérée par la population de toutes les variables et ratios d'arrondissements PLM dans le script de build.
- **Reference Table Cascade**: Regroupement des tables CCAS, POIs, RNA, Formations sur les codes INSEE parents (`75056`, `69123`, `13055`) et filtrage complet des arrondissements.
- **Pure Configuration-Driven Weights**: Retrait de tous les multiplicateurs hardcodés dans l'interface formulaire pour s'assurer que les calculs de scoring reposent uniquement sur les poids définis dans `scores_config.yaml`.

### 📊 Status
- **May 2026**: Complete. Fully integrated, tested (100% green pytest suite), and E2E snapshots updated.

---

## 🚀 Feature [F-61]: Comparaison avec une Ville Pressentie

### 📝 User Story
- En tant que travailleur social, je veux pouvoir indiquer une commune spécifique ("ville pressentie") en amont de ma recherche afin de pouvoir la comparer aux 5 communes recommandées ainsi qu'à la commune actuelle de résidence, indépendamment de son score brut.
- En tant qu'utilisateur, je veux voir cette ville pressentie affichée en tête de ma liste de résultats avec une coloration distinctive (jaune "J'accueille") afin de faciliter la discussion comparative avec le bénéficiaire.

### 🔑 Key Features
- **Form Selection UI**: Une case à cocher "Une idée de ville en tête ?" (décochée par défaut) dans l'onglet **Localisation / Zone de recherche**, qui dévoile un sélecteur unique des 100 villes les plus peuplées de France.
- **Scoring Integration**: La ville pressentie est injectée dans `SearchCriterias.commune_pressentie` et est explicitement forcée dans la liste des communes évaluées par le `ScoringEngine`, lui appliquant les mêmes pondérations pour une comparaison radar intègre.
- **Distinctive UI Presentation**:
  - Affichée tout en haut de la liste de résultats (avant le Top 5) avec un style spécifique (fond jaune `#F5D819`, texte sombre `#1B4429`).
  - Si elle fait partie du Top 5 naturel, elle reste à son rang de classement naturel dans le Top 5 mais adopte cette même charte graphique distinctive jaune/noire.
- **AI & Context Grounding**:
  - `SearchResultsData.commune_pressentie` stocke le résultat de scoring complet de la ville pressentie.
  - Le `Refiner` reçoit les métriques complètes de la ville pressentie pour générer un pitch comparatif ciblé s'il n'est pas déjà dans le Top 5.
  - La ville pressentie bénéficie de l'activation des agents experts d'analyse avancée et de l'export PDF.

### 📊 Status
- **May 2026**: Complete. Fully integrated, tested with 100% green test pass, premium visual outlines, custom SVG Material pushpins, and validated live Refiner agent structured output.

---

## 🚀 Feature [F-62]: Pipeline v2 - Dynamic API Ingestion, Caching, and Resilient Validation

### 📝 User Stories
- En tant que développeur et mainteneur d'ODIS, je veux que le pipeline d'ETL soit déclaratif et centralisé en termes de politiques de cache (TTL), afin de simplifier la maintenance et l'ajout de nouvelles sources de données.
- En tant qu'opérateur du pipeline, je veux que les téléchargements depuis data.gouv.fr effectuent une vérification de version en amont via l'API de métadonnées, afin d'économiser la bande passante et le temps d'exécution.
- En tant qu'utilisateur final d'ODIS, je veux que le pipeline d'ingestion soit ultra-résilient et utilise une stratégie de staging "Blue-Green" pour rejeter automatiquement les données corrompues et préserver les caches existants sans casser l'application.

### 🔑 Key Features
- **Config-Driven TTL & Caching Policies**: Déclaration de `ttl_days` directement dans `sources.yaml` pour tous les jeux de données (fichiers statiques et appels d'API complexes comme France Travail ou Odace).
- **Lightweight Update Check (data.gouv.fr API)**: Extraction automatique du Resource ID pour les URLs `data.gouv.fr` et interrogation de l'API de métadonnées (`/api/1/datasets/r/{id}/`) pour comparer la date de dernière modification sans télécharger le fichier complet.
- **Shadow Staging Ingestion (Option A Fallback)**: Téléchargement sous forme de fichier `.staging` et exécution isolée du cleaning. En cas d'anomalie ou d'échec de validation, le pipeline émet une alerte console et conserve la dernière version stable en cache.
- **Declarative Schema Validation**: Validation automatique basée sur la configuration (présence obligatoire des colonnes de `used_columns` et contrôles d'intégrité de base) avant le remplacement effectif des fichiers en cache.

### 📊 Status
- **May 2026**: Complete. Fully integrated, tested with a comprehensive green test suite (Blue-Green staging-and-restore, schema contracts, lightweight data.gouv update checks, and complex API TTL limits), and validated in the ETL CLI.

---

## 🚀 Feature [F-63]: Pipeline v2 Codebase Hardening, DRY Refactoring, and Consolidated Logging

### 📝 User Story
- En tant que développeur et mainteneur d'ODIS, je veux que le codebase du pipeline d'ingestion soit propre, sans code mort ni dupliqué, afin d'en faciliter la maintenance et les évolutions.
- En tant qu'opérateur du pipeline, je veux des logs de console homogènes, clairs et moins verbeux, avec un format de journalisation standard pour toutes les phases (Ingest, Build, Prescoring, Deploy), afin de diagnostiquer rapidement les erreurs de production.

### 🔑 Key Features
- **Dead Code Eradication**: Retrait de la définition tronquée et inutile de `clean_bpe` dans `ingest.py`.
- **Centralized Orchestration Logging (DRY)**: Centralisation des appels de début (`STARTED`), succès (`COMPLETED`), et d'erreur (`ERROR`) de chaque cleaner au niveau du gestionnaire de cycle de vie `run_clean_step_safely`, éliminant plus de 50 lignes de code redondant.
- **Unified Global Logging Settings**: Centralisation de la configuration `logging.basicConfig` dans `common.py` pour un format homogène, avec mise en sourdine des bibliothèques externes verbeuses (comme `requests` ou `fastparquet`).
- **Clean PLM Arrondissement Reference (DRY)**: Déclaration de la liste des arrondissements PLM sous forme de constante réutilisable unique `PLM_ARRONDISSEMENTS` au lieu de listes dupliquées.
- **Standardized Exception Capturing**: Remplacement de tous les formats de trace d'exception manuels et des `print()` par `logging.exception()` ou `exc_info=True`.

### 📊 Status
- **May 2026**: Complete. Codebase fully audited, dead code eradicated, clean orchestration logging centralized via a muted step cleaner wrapper, PLM lists consolidated, and exceptions unified under standard logging formats. Tested and verified.



