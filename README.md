# OD&IS - Prototype d'Aide à la Mobilité (Recherche Inversée)

[![Python Version](https://img.shields.io/badge/python-3.10-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![Framework](https://img.shields.io/badge/Framework-Streamlit-red.svg)](https://streamlit.io)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](../../LICENSE)

## 🎯 Contexte et Objectifs du Projet

Ce projet, surnommé **"Stream 2"**, est un prototype fonctionnel explorant une approche de **"recherche inversée"** pour l'aide à la relocalisation des personnes et familles accompagnées par des structures d'insertion comme le programme [J'accueille](https://www.jaccueille.fr/) de [SINGA](https://www.singafrance.com/).

Il s'inscrit en complément du projet principal [13_odis](https://github.com/dataforgoodfr/13_odis) (ou "Stream 1"), qui se concentre sur l'exploration et la comparaison d'indicateurs pour une commune déjà sélectionnée.

L'innovation de ce prototyp### 🛠️ Key Features

- **Reverse Search Algorithm**: Multi-criteria scoring engine with dynamic weights.
- **AI Synthesis (Graph-based)**: Agentic workflow powered by pydantic-graph and Gemini for deep site analysis.
- **Observability**: Hierarchical tracing and token usage monitoring via Pydantic Logfire.
- **Background Tasks**: Non-blocking AI execution for Cloud Run stability (Daemon threads + Fragment polling).
- **Interactive Map**: Folium/Leaflet integration for spatial mediation.
- **PDF Reports**: Automated generation of argued territorial summaries.
 les plus prometteurs pour la réussite d'un projet d'intégration.

![Comparaison Stream 1 vs Stream 2](./images/Screenshot-3.png)

Ce prototype a un triple objectif :

1.  **Valider la pertinence de l'approche** auprès des futurs utilisateurs (travailleurs sociaux, accompagnants).
2.  **Démontrer la faisabilité technique** de construire un score de pertinence en utilisant exclusivement des données ouvertes (Open Data).
3.  **Promouvoir l'intérêt de cette démarche** auprès de potentiels partenaires, décideurs et financeurs.

## ✨ Fonctionnalités Principales

- **Profil Personnalisé :** Définissez un "projet de vie" détaillé incluant la composition du foyer, le niveau scolaire des enfants, les métiers visés, les besoins en formation, etc.
- **Pondération Avancée :** Choisissez un profil prédéfinis (Équilibré, Famille, Santé, Emploi) ou activez le **Mode Expert** pour un réglage fin des poids de chaque catégorie.
- **Scoring Intelligent :** Chaque commune de France est évaluée selon sa compatibilité avec le projet de vie via un modèle de données typé. Pour garantir une comparaison équitable, le moteur utilise un **Scaling par Quantiles (p1/p99)** qui neutralise l'impact des valeurs aberrantes (outliers) sur la distribution des scores.
- **Carte Interactive :** Visualisez les localités les mieux notées, leur score, et superposez des couches d'informations additionnelles (écoles, établissements de santé, services d'inclusion).
- **Résultats Détaillés & Export PDF :** Explorez les 5 meilleurs résultats avec une analyse comparative générée automatiquement par l'IA et exportez un rapport PDF complet incluant ces analyses.
- **Assistant IA (Multi-Agent ODIS) :** Système multi-agent (pydantic-graph) orchestré par un agent chef de projet (Project Manager / `ts_agent`) qui pilote un swarm de 6 experts thématiques (logement, transport, santé, éducation, insertion et emploi) pour réaliser une analyse de terrain et web parallélisée. voir la [documentation détaillée de l'architecture](ARCHITECTURE.md).
- **Grounding Google Search :** Recherche en ligne temps réel par les agents experts pour extraire des actualités locales, des aides régionales ou des démarches administratives.
- **Moteur de Recherche RAG (RNA) :** Recherche sémantique et thématique sur l'ensemble du Répertoire National des Associations (RNA) via BigQuery et Vertex AI, permettant de classer les associations par catégories d'inclusion (FLE, Logement, Emploi, etc.) ou des recherches spécifiques (intégration des personnes réfugiées).
- **Accueils Citoyens (J'Accueille) :** Intégration de la base de données de l'association J'Accueille pour valoriser les bassins de vie disposant déjà d'un réseau d'hébergement citoyen actif. (Données Mars 2026).
- **Profils Partenaires & Zones Stratégiques :** Support de contextes d'organisations spécifiques (via `?org=`) permettant de pré-configurer l'outil (pondérations, zones prioritaires) tout en laissant le contrôle final à l'utilisateur.

## 📸 Aperçu de l'Application

|                         Page d'accueil                         |                      Vue détaillée d'un résultat                       |
| :------------------------------------------------------------: | :--------------------------------------------------------------------: |
| ![./images/Screenshot Page Accueil](./images/Screenshot-1.png) | ![./images/Screenshot détail d'un résultat](./images/Screenshot-2.png) |

## 🚀 Installation et Lancement

### Prérequis

- [Python 3.10+](https://www.python.org/)
- Un environnement virtuel (recommandé).

### Instructions

1.  **Clonez le dépôt :**

    ```bash
    git clone https://github.com/jbesan/13_odis_stream2.git
    cd 13_odis
    ```

2.  **Créez et activez un environnement virtuel :**

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```

3.  **Installez les dépendances :**

    ```bash
    pip install -r app/requirements.txt
    ```

4.  **Lancez l'application Streamlit :**

    ```bash
    streamlit run app/1_Accueil.py
    ```

    L'application devrait s'ouvrir dans votre navigateur web.

5.  **Exécuter les tests (Avant déploiement) :**
    ```bash
    source .venv/bin/activate
    python3 -m pytest tests
    ```

6.  **Mettre à jour/Exécuter le pipeline d'ingestion (Optionnel) :**
    Pour recharger et mettre à jour les données (notamment depuis l'API Odace), consultez la documentation détaillée dans le [README du pipeline](file:///Users/jacques/dev/13_odis_stream2/pipeline/README.md).


## ⚙️ Fonctionnement : Le Moteur de Scoring

Le cœur de l'application est un pipeline de scoring multi-critères qui évalue la compatibilité de chaque commune avec le projet de vie de l'utilisateur.

Le calcul s'effectue en deux phases principales :
1. **Pre-scoring (Offline) :** Calcul et normalisation (scaling par quantiles) des indicateurs territoriaux statiques (logement, démographie, équipements).
2. **Live-scoring (Online) :** Calcul dynamique en fonction du profil saisi (opportunités d'emploi directes, centres de formation, structures de santé spécifiques, proximité et bonus EPCI).

Pour plus de détails sur la logique d'enrichissement par bassin de vie (Boost opportunité), les baselines obligatoires, la normalisation par centiles ou la liste complète des 45 indicateurs, consultez la [Documentation du Scoring](./SCORING.md).

## 🛡️ Qualité et Robustesse (Spec-Driven Development)

Le projet suit une approche rigoureuse de développement piloté par les spécifications (SDD) et le typage statique :

- **Typage Stricte (Mypy)** : Le codebase est 100% conforme à `mypy` en mode strict. Toutes les fonctions sont annotées et les modèles Pydantic assurent la validation des données à l'exécution.
- **Tests Automatisés (Pytest)** : Une suite de plus de 100 tests unitaires et d'intégration couvre le moteur de scoring, les agents pydantic-graph et les composants UI.
- **Documentation Narrative** : Chaque changement majeur est documenté dans le `walkthrough.md` et le [guide d'architecture](./ARCHITECTURE.md).

## 🛠️ Stack Technique

- **Framework Applicatif :** [Streamlit](https://streamlit.io/)
- **Analyse de Données :** [Pandas](https://pandas.pydata.org/), [GeoPandas](https://geopandas.org/), [NumPy](https://numpy.org/)
- **Observabilité :** [Pydantic Logfire](https://logfire.pydantic.dev/) (Tracing, Télémétrie, Monitoring LLM)
- **Scoring & Normalisation :** [Scikit-learn](https://scikit-learn.org/)
- **Cartographie Interactive :** [Folium](https://python-visualization.github.io/folium/) & [streamlit-folium](https://github.com/randyzwitch/streamlit-folium)
- **Graphiques :** [Plotly Express](https://plotly.com/python/plotly-express/)
- **Infrastructures Cloud :** [Google BigQuery](https://cloud.google.com/bigquery) (Stockage & Vector Search) et [Vertex AI](https://cloud.google.com/vertex-ai) (Embeddings Multimodal) pour le moteur de recherche d'associations (RAG).

## 📂 Structure du Projet

```text
app/
├── 1_Accueil.py            # Point d'entrée principal Streamlit
├── config.py               # Configuration et constantes
├── scores_config.yaml      # Paramétrage des critères de scoring
├── core/                   # Logique métier ODIS
│   ├── scoring.py          # Moteur de calcul (Normalisation, Pondération)
│   ├── models.py           # Modèles de données Pydantic (SearchCriterias, etc.)
│   ├── maps.py             # Rendu cartographique Folium
│   └── pdf_generator.py    # Génération de rapports PDF ReportLab
├── ui/                     # Interface et Composants
│   ├── components.py       # Fragments UI et formulaires
│   ├── charts.py           # Graphiques Plotly
│   └── feedback.py         # Module de collecte de retours
├── utils/                  # Services transverses
│   ├── data_loader.py      # Chargement et cache des données (Parquet/BigQuery)
│   ├── auth.py             # Authentification simple
│   └── common.py           # Fonctions utilitaires
├── agents/                 # Écosystème Multi-Agent (pydantic-graph)
│   ├── graph.py            # Graphe d'orchestration (MapReduce)
│   ├── interviewer.py      # Agent d'extraction de profil (One-shot)
│   ├── scorer.py           # Agent d'analyse de scores
│   ├── scout.py            # Agent de recherche locale (Maps)
│   └── ...                 # Autres agents experts (Web, JobHunter, etc.)
└── pages/                  # Pages Streamlit secondaires
    ├── 2_Formulaire.py     # Saisie manuelle du projet
    └── 3_Resultats.py      # Recherche et visualisation
```

## 🤖 Interface AI Agent (Assistant ODIS 2.0)

L'Assistant ODIS 2.0 est une interface de conversation en langage naturel conçue pour simplifier le travail de diagnostic social. Il repose sur une architecture multi-agent innovante (v6.0) :

### Architecture Multi-Agent (pydantic-graph)

Orchestré par `pydantic-graph`, le système suit un pattern **MapReduce (Swarm) piloté par un chef de projet (PM)** :

1. **Le Chef de Projet (Triage / `ts_agent`) :** Reçoit la question et le contexte de l'utilisateur. Il évalue le dossier, sélectionne les instructions des experts (Skill Cards) et planifie des missions sur-mesure pour chaque domaine.
2. **Le Swarm Parallèle (6 Experts Métiers) :** Le système lance en parallèle uniquement les experts pertinents requis pour la mission :
   - **Job Hunter** : Offres ROME et insertion (France Travail / SIAE).
   - **Housing Expert** : Loyers au m², délais d'attente HLM, CCAS.
   - **Mobility Expert** : Réseau de transport en commun et tarification solidaire.
   - **Healthcare Expert** : Indicateur APL, hôpitaux et maternités.
   - **Education Expert** : Écoles, crèches et démarches d'inscription.
   - **Social Integration Expert** : Associations de réfugiés, RNA et accompagnement.
3. **Le Join & Synthétiseur :** Fusionne les analyses des experts actifs et génère le rapport ou la réponse finale argumentée.

### Routage & Modes d'Exécution du Swarm

Le chef de projet (`ts_agent`) oriente dynamiquement la requête selon 3 modes :
* **Full Analysis (`full_analysis`)** : Lancé au début ou à la demande pour réaliser l'analyse complète de la commune (exécute les 6 experts).
* **Specific Ask (`specific_ask`)** : Lancé pour des questions de suivi nécessitant de nouvelles requêtes externes (exécute uniquement les experts concernés).
* **Direct Answer Bypass (`direct_answer`)** : Si la réponse est déjà présente dans le contexte ou les rapports d'experts déjà générés, le PM génère la réponse directement et court-circuite complètement le swarm d'experts et le synthétiseur pour une réponse instantanée.

Pour plus de détails techniques, consultez la [documentation technique de l'architecture des agents](./app/agents/GRAPH_ARCHITECTURE.md).

### Points Forts de l'Agent

- **Raisonnement Métier & Outils Hybrides :** Il comprend les codes ROME, les structures d'insertion et d'accueil. Il combine des outils Python locaux et la recherche en ligne en temps réel (Gemini Google Search Grounding).
- **Transparence :** Chaque affirmation est rigoureusement sourcée (données ODIS locales, APIs ou liens web réels).
- **Proactivité :** L'agent cherche à compléter le dossier social à partir du dialogue pour éviter les saisies de formulaires fastidieuses.

## 🔮 Feuille de Route et Améliorations Futures

Ce prototype est une base solide qui peut être grandement améliorée :

- **⭐ Fonctionnalités :**
  - **Comptes Utilisateurs :** Permettre de sauvegarder, nommer et gérer plusieurs scénarios de "projets de vie".
  - **Comparaison des Résultats :** Ajouter une fonction pour comparer 2 ou 3 des meilleurs résultats côte à côte.

- **📊 Données & Scoring :**
  - **Étendre les Sources de Données :** Intégrer plus de jeux de données (transports en commun, services de santé spécifiques, activités culturelles).
  - **Fraîcheur des Données :** Mettre en place un pipeline pour mettre à jour automatiquement les données sous-jacentes.
  - **Affiner les Critères :** Travailler avec des travailleurs sociaux pour affiner la liste des critères et leur pertinence.

- **💻 Technique & UX :**
- **Performance :** Optimisation du rendu cartographique via un cutoff automatique à 1 000 polygones (Top 1000) pour garantir la fluidité sur tous les terminaux.
- **Design UI/UX :** Améliorer le design visuel, la mise en page et l'ergonomie sur mobile.

## ⚖️ Licence

Ce projet est sous licence MIT. Consultez le fichier [LICENSE](../../LICENSE) à la racine du projet pour plus de détails.
