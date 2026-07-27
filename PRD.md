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
- [x] Mieux tracker la version des sources de données (Data Manifest JSON, modal "À propos des sources" et colonne BigQuery manifest_version dans search_events)

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

---

## 🚀 Feature [F-64]: Employment Post-Scoring Hydration

### 📝 User Stories
- En tant que travailleur social, je veux pouvoir consulter des offres d'emploi réelles et actualisées correspondant aux codes ROME recherchés directement dans les détails de chaque commune recommandée ("En savoir plus"), afin de faciliter la discussion concrète sur l'insertion professionnelle du bénéficiaire.
- En tant qu'architecte, je veux que ces offres soient récupérées de manière asynchrone après le scoring (post-scoring) et stockées de manière structurée dans le modèle de données afin de ne pas ralentir le calcul initial et de permettre leur réutilisation par les agents experts.

### 🔑 Key Features
* **Background Post-Scoring Ingestion**: Lancement d'un thread asynchrone après le scoring pour requêter l'API de France Travail pour chaque commune recommandée (Top 5 + ville pressentie).
* **Smart ROME-based Querying**: Pour chaque commune, recherche des offres d'emploi correspondant aux codes ROME spécifiés dans le profil de recherche (`codes_metiers`), avec une limite maximale de 3 offres par code ROME et de 10 offres totales par commune pour garantir la diversité des propositions.
* **Structured Model Serialization**: Création d'un modèle Pydantic `JobOfferDetail` et ajout d'un champ `matching_job_offers` dans le modèle `EmploymentMetrics` afin de sérialiser proprement les offres au sein de `CommuneResult`.
* **Graceful Fallback & Logging**: Gestion robuste des cas de credentials manquants ou d'erreurs d'API France Travail en affichant un message de repli clair dans l'UI sans bloquer ni faire planter l'application.
* **Premium UI Display**: Intégration d'un nouvel expandeur "Offres d'emploi disponibles" au sein de l'onglet "💼 Emploi & Formation" de la modale "En savoir plus", affichant de manière lisible le titre de l'offre, l'entreprise, le type de contrat (CDD/CDI), le lieu et un lien/bouton direct pour postuler.

### 📊 Status
- **June 2026**: Complete. Fully integrated async fetching, model schemas, caching, fallback logic, test suite, and premium Streamlit UI results presentation.

---

## 🚀 Feature [F-65]: Odace D4G API Ingestion & Fallback Integration

### 📝 User Stories
- En tant que développeur, je veux intégrer le pipeline d'ingestion de données avec la nouvelle plateforme d'API Odace Silver (`https://odace.services.d4g.fr`), afin de charger directement des données qualifiées plutôt que d'exécuter du scraping lourd et fragile de fichiers Open Data bruts.
- En tant qu'opérateur système, je veux qu'en cas d'indisponibilité de l'API Odace ou de restriction des droits d'accès (ex. erreur `501 Not Implemented` sur les requêtes SQL complexes avec une clé de développement standard), le pipeline bascule de manière transparente sur les fichiers open-data ou caches locaux sans interrompre l'ETL globale.

### 🔑 Key Features
- **Config-Driven Ingestion**: Paramétrage dynamique de `use_odace` et `odace_table` dans `sources.yaml`.
- **API silver Client Integration**: Ingestion des tables d'intérêt (`dim_maternite`, `fact_couverture_petite_enfance`, etc.) via `OdaceClient`.
- **Transparent Dual-Path Fallback**: Interception gracieuse des erreurs de réseau ou API avec bascule automatique vers les fichiers open data locaux correspondants.
- **Backward-Compatible Ingestion Boundary**: Isolement complet de la structure des parquets et formats intermédiaires en sortie du cleaner (ex: ré-écriture de `maternites_drees.json` en local pour `build.py`) pour préserver les moteurs de scoring et l'UI sans changement aval.

### 📊 Status
- **June 2026**: Complete. Fully migrated all target datasets (including CAF, maternities, housing delay, insecurity, mobility, APL, RNA, loyers, and BPE). Resolved the PLM population hierarchy and BPE capacity metadata challenges. E2E pipeline runs successfully, and all 130 tests pass green.

---

## 🚀 Feature [F-66]: Streamlining Expert Agents & PM-Driven Swarm Orchestration

### 📝 User Stories
- En tant que travailleur social, je veux que l'outil réponde instantanément si mon dossier contient déjà toutes les réponses, sans forcer un cycle d'analyse long et inutile.
- En tant que travailleur social, je veux que la recherche thématique avancée soit ciblée sur les besoins spécifiques de la personne accompagnée (ex: ne pas mobiliser d'expert scolaire s'il n'y a pas d'enfants).
- En tant qu'architecte, je veux remplacer l'ancien routeur par un agent chef de projet (PM) capable d'orchestrer un swarm de 6 experts thématiques isolés en un seul tour de table parallélisé.

### 🔑 Key Features
- **Project Manager Triage & Direct Answer Bypass**: Lancement de l'agent `ts_agent` comme premier nœud de graphe. Si le dossier contient déjà la réponse, il retourne directement le résultat finale en court-circuitant le swarm.
- **Dynamic Swarm Planning & Decoupled Skill Cards**: Analyse de la requête et des critères par le PM pour allouer des missions spécifiques et des Skill Cards (chargées depuis des fichiers Markdown pour simplifier l'édition).
- **6 Specialized Expert Agents**: Division de la recherche sur Marseille en 6 agents thématiques isolés : logement (`housing_expert`), transports (`mobility_expert`), santé (`healthcare_expert`), éducation (`education_expert`), social (`social_integration_expert`) et emploi (`job_hunter`).
- **Cumulative Token and Cost Merging**: Consolidation en temps réel des statistiques d'usage de tous les agents pour une journalisation intègre dans BigQuery.

### 📊 Status
- **June 2026**: Complete. Fully implemented the 6 experts, PM planning routing, Markdown skill card decoupling (migrated from SQLite), Direct Answer bypass logic, and cumulative telemetry usage tracking. Upgraded `pydantic-ai` to 1.107.0 to natively support combining custom python functions and native Gemini search tools on expert agents. Verified with green test suites (E2E graph execution and file-based stores).

---

## 🚀 Feature [F-67]: Swarm Prompt Optimization, BQ Native RAG Search, and ACL Visibility Hardening

### 📝 User Stories
- En tant que travailleur social, je veux que les rapports générés par les agents soient clairs, succincts et n'exposent pas d'acronymes de conception interne (ex. "ODIS").
- En tant que développeur, je veux que la construction des prompts du swarm d'agents suive le principe DRY (Don't Repeat Yourself) via un configurateur centralisé de boilerplate.
- En tant que travailleur social, je veux voir les détails complets des associations (missions, coordonnées, etc.) dans l'onglet Intégration Sociale, sans qu'ils soient masqués ou vides.
- En tant qu'architecte, je veux que la recherche sémantique d'associations (RAG) soit performante, consistante avec l'ingestion, et déléguée nativement à BigQuery sans dilution de score géographique.

### 🔑 Key Features
* **Swarm Boilerplate Builder (DRY)**: Centralisation des instructions de collaboration au sein de `get_swarm_boilerplate` dans `agent_config.py` pour unifier le contexte (agent coordinateur, experts thématiques, travailleur social humain comme utilisateur final) et éradiquer les acronymes internes.
* **ACL Visibility Hardening**: Ajout de la clé `"agent_social_integration_expert"` dans le schéma d'exposition `odis_visibility` des champs de `AssociationDetail` dans `app/core/models.py`, débloquant l'accès aux données textuelles pour le social integration expert.
* **Native BQ Vector Search (`ML.DISTANCE`)**: Migration complète du calcul de similarité cosinus de la mémoire locale (NumPy/Pandas) vers BigQuery à l'aide de la fonction native `ML.DISTANCE` sur l'index d'embedding 128 dimensions.
* **L2-Normalized Query Embeddings**: Normalisation explicite du vecteur d'embedding de la requête dans `rna_rag.py` avant de l'envoyer à BigQuery pour assurer des scores de similarité fidèles.
* **Geographical Search Query Optimization**: Ajustement des schémas d'outils RAG des experts pour instruire explicitement les LLM de ne pas injecter le nom de la ville dans les termes de recherche, le filtrage géographique étant déjà géré nativement par les codes INSEE (`codgeo`).

### 📊 Status
- **June 2026**: Complete. Swarm prompts refactored, ACL exposures updated, BigQuery vector search optimized, and RAG search query instructions integrated. Checked with all 135 passing unit and E2E tests.

---

## 🚀 Feature [F-68]: Health Needs Breakdown & Form Checkboxes Alignment

### 📝 User Stories
- En tant que travailleur social, je veux pouvoir sélectionner plusieurs besoins en santé indépendamment, afin d'évaluer fidèlement la situation médicale complexe d'un bénéficiaire.
- En tant que travailleur social, je veux que la sélection des critères de santé soit présentée sous forme de cases à cocher individuelles pour être cohérente avec le parcours du logement.
- En tant qu'architecte, je veux que les coefficients et le Boost Bassin de Vie de chaque besoin en santé soient déclaratifs et configurés individuellement sans logique de max() dynamique en dur.

### 🔑 Key Features
* **Individual Checkboxes Selection UI**: Refactoring du sélecteur de santé en cases à cocher individuelles (`ui_sante_cb_...`) avec un callback de synchronisation bidirectionnelle dans le gestionnaire de profil (`data_loader.py`).
* **Precomputed Multi-Score Evaluation**: Démantèlement de l'indicateur consolidé `sante_structures_scaled` au profit de 7 indicateurs précalculés autonomes.
* **Config-driven weights & BdV Boosts**: Alignement déclaratif de chaque structure avec sa propre règle de boost bassin de vie (ex: 0.8 pour un hôpital régional, 0.25 pour une maternité, et 0.0 pour les autres structures locales) et son propre poids d'importance (2.0 par défaut).

### 📊 Status
- **July 2026**: Complete. Fully integrated, tested with 100% green pytest baseline, visual forms aligned, and validation scenarios updated.

---

## 🚀 Feature [F-69]: J'Accueille Operational Geographic Filtering

### 📝 User Stories
- En tant que travailleur social de J'Accueille, je veux pouvoir restreindre la recherche de communes uniquement aux zones opérationnelles de mon organisation (présence de coordinateurs locaux et d'accueillants/prospects), afin de ne pas proposer des territoires où J'Accueille n'a pas les ressources nécessaires pour accompagner et installer le bénéficiaire.
- En tant qu'architecte, je veux que ce filtrage s'applique en amont du scoring pour optimiser les performances de calcul en limitant la liste des communes évaluées.

### 🔑 Key Features
* **Operational Areas Double Filtering**: Restriction des communes à celles situées dans des bassins de vie opérationnels éligibles. Un bassin de vie est qualifié s'il possède au moins un contact accueillant (base contact) OU un prospect inscrit (base prospect) et si son département est inclus dans la sélection de départements stratégiques de J'Accueille (présence d'un coordinateur).
* **Prospects Dataset Integration**: Ajout et traitement automatique d'un nouveau fichier de prospects (`OD&ISbis - Inscrits`) au sein du pipeline ETL local et de BigQuery (`jaccueille_prospects_bdv`).
* **UI Controls**: Ajout d'une case à cocher ("Restreindre la recherche uniquement aux zones opérationnelles J'Accueille") visible exclusivement pour les utilisateurs de J'Accueille dans le panneau de configuration de l'organisation.

### 📊 Status
- **July 2026**: Complete. ETL pipeline integrated, BigQuery tables populated, UI components registered, and scoring filters tested with green pytest suites.

---

## 🚀 Feature [F-70]: Data Inclusion API Integration

### 📝 User Stories
- En tant que travailleur social, je veux voir les services d'inclusion détaillés (nom de structure, description, thématiques et lien direct Soliguide) plutôt que des thématiques génériques non explicatives, afin de mieux orienter le bénéficiaire.
- En tant qu'architecte, je veux que la récupération des données s'effectue de manière asynchrone (post-scoring pipeline) et soit filtrée par les thématiques de recherche de l'utilisateur pour préserver les performances et la pertinence.

### 🔑 Key Features
* **Single /search/services API Call**: Utilisation de l'API de recherche Data Inclusion qui retourne nativement la structure imbriquée (plus besoin d'appels secondaires à `/structures`), optimisant la latence et la consommation réseau.
* **Thematique Search Filtering**: Filtrage automatique des résultats via le paramètre `thematiques` de l'API et au moment du groupage pour n'afficher que les catégories thématiques sélectionnées par l'utilisateur.
* **Global Structure Deduplication**: Chaque structure d'accueil n'apparaît qu'une seule fois (dans le premier expander thématique), avec la liste complète de ses services associés affichée en sous-titre pour un rendu UX compact et clair.
* **Robust Fallback & Test Coverage**: Gestion gracieuse des erreurs de réseau ou clés API absentes, avec une suite de tests unitaires et E2E garantissant la robustesse en production et en local.

### 📊 Status
- **July 2026**: Complete. Fully integrated, documented in postscoring architecture catalog, covered by unit and E2E test suites, and optimized to 1 API request/commune.

---

## 🚀 Feature [F-71]: BigQuery Telemetry & In-App Admin Analytics Dashboard

### 📝 User Stories
- En tant qu'administrateur, je veux pouvoir accéder à un tableau de bord d'analytics sécurisé au sein de l'application Streamlit afin de visualiser l'activité globale de la plateforme, le volume de recherches et les recommandations métiers sans impacter les performances utilisateurs.
- En tant qu'architecte, je veux que la navigation et les événements d'usage clés (ex: consultation des détails du score, déclenchement d'une analyse IA) soient tracés dans BigQuery de manière dédupliquée et associés aux sessions utilisateurs.

### 🔑 Key Features
* **In-App Admin Dashboard (`pages/4_Analytics.py`)**: Page dédiée d'analyse BI comportant 3 onglets ("Activité Globale", "Résultats & Recommandations", "Profil de Recherches") avec filtres par période de date et organisation, accessible uniquement par les administrateurs whitelistés via `auth.is_admin()`.
* **Centralized Sidebar Redirection**: Bouton d'accès `"📊 Dashboard Analytics"` intégré dans la barre latérale sous condition de rôle administrateur (`ui_comp.render_admin_sidebar_link()`).
* **Usage Event Telemetry (`odis_logs.usage_events`)**: Enregistrement générique des événements applicatifs avec payload JSON flexible (`page_view`, `view_commune_details`, `run_ia_analysis`, `export_pdf`).
* **Page View & Action Deduplication**: Suivi de la navigation de page avec déduplication d'état Streamlit, origin tracking, et comptage des exports PDF.
* **Search Events Schema Enhancement (`odis_logs.search_events`)**: Enrichissement de la table de recherches avec `search_hash` (`config.compute_hash()`) et `org_id`.
* **Advanced Analytics Visualizations**: Top 5 utilisateurs actifs, Top 15 communes consultées ("En Savoir Plus"), Radar chart Plotly des scores thématiques moyens, et analyse des profils de recherche (aires géographiques, enfants, métiers ROME, besoins de santé, profils de poids).
* **Resilient BQ Execution**: Query engine configuré avec `create_bqstorage_client=False` pour contourner les limitations gRPC Storage API locales et assurer une lecture 100% fiable de l'historique de recherches.

### 📊 Status
- **July 2026**: Complete. Fully implemented with 3 tabs, tested with 100% green pytest baseline (218 passing tests), BQ schemas updated, and documentation synchronized.

---

## 🚀 Feature [F-63]: Search Results Permalinks & Sharing

### 📝 User Stories
- En tant que travailleur social, je veux pouvoir partager facilement les résultats d'une recherche avec mon responsable ou un collègue situé dans la ville ciblée via un lien URL unique (`?search=<share_id>`), afin de leur permettre de consulter l'ensemble des scores et des analyses IA sans perte de contexte.
- En tant que destinataire d'un lien de partage, je veux pouvoir charger directement la page de résultats complète et cliquer sur "Modifier les critères" pour affiner la recherche et générer ma propre déclinaison de la recherche sans altérer le lien d'origine.

### 🔑 Key Features
* **Storage Architecture (GCS + BigQuery telemetry)**: Sauvegarde intégrale du payload JSON (`SearchCriterias` + `SearchResultsData`) sous forme compressée sur Google Cloud Storage (`gs://odis-stream2-eu/searches/<share_id>.json`), sans fallback fichier local, et enregistrement de la télémétrie sur BigQuery `odis_logs.usage_events` (`event_name="search_shared"`).
* **On-Demand Snapshotting**: Génération du snapshot lors du clic sur "Partager la recherche" pour capturer l'état réel et complet des résultats (scores, graphiques, synthèses et analyses IA approfondies des communes).
* **URL Parameter Routing (`st.query_params`)**: Interception du paramètre `?search=<share_id>` à l'entrée sur `1_Accueil.py`, restauration automatique des objets Pydantic dans `st.session_state` et redirection fluide vers `pages/3_Resultats.py` via `st.switch_page`.
* **Sharing UI Dialog (`@st.dialog`)**: Fenêtre modale de partage proposant l'URL permalien avec zone de saisie, ainsi que des raccourcis directs de partage Slack et Email (`mailto:`).
* **Fine-Tuning Fork Logic**: Toute modification de critères et nouveau lancement de recherche depuis un permalien partagé réinitialise `active_share_id`, créant un nouveau snapshot de recherche indépendant.

### 📊 Status
- **July 2026**: Complete. Integrated, covered by unit test suite (`test_share_service.py`), verified with green test execution, and fully documented.


