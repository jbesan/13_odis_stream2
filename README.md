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
- **Assistant IA (Multi-Agent ODIS) :** Système multi-agent (pydantic-graph) capable de conduire l'entretien via l'agent **Interviewer** (en un coup), de calculer les scores avec l'agent **Scorer**, et d'enrichir les résultats avec des infos terrain (**Scout**) et web (**Web**). voir la [documentation détaillée de l'architecture](ARCHITECTURE.md).
- **Grounding Google Search :** Grâce à l'agent spécialisé WEB, accédez aux dernières actualités locales et au contexte social des communes visées.
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

Le cœur de l'application est un pipeline de scoring qui évalue les communes (ou les bassins de vie) en fonction du profil utilisateur.

1.  **Filtrage Géographique :** Le moteur délimite la zone de recherche selon le périmètre choisi (Département, Région, France Métropolitaine) ou une zone spécifique (Custom). Il n'utilise plus de rayon en km mais des limites administratives réelles.
2.  **Calcul des Critères :** Il calcule des dizaines de scores individuels pour chaque commune (Emploi, Logement, Santé, etc.).
3.  **Enrichissement par le Bassin de Vie :** Pour certains critères (ex: Éducation, Santé), le score d'une commune est bonifié par les opportunités du Bassin de Vie via une logique de Boost non-pénalisante. Si une commune n'a pas de lycée mais qu'il y en a un dans son Bassin de Vie, elle reçoit un bonus (via le `bdv_factor` défini dans [scores_config.yaml](./app/scores_config.yaml)). Cela permet de valoriser les communes qui bénéficient des services de leur territoire proche.
4.  **Agrégation par Catégorie :** Les scores des critères individuels sont moyennés pour former des scores de catégories. Pour un exemple concret du calcul et une explication détaillée de la logique de boost, consultez la [Documentation du Scoring](./SCORING.md).
5.  **Score Pondéré Final :** Enfin, un `weighted_score` global est calculé pour chaque commune en appliquant les poids définis. Le moteur s'appuie sur le modèle `SearchCriterias` pour garantir la cohérence entre l'interface formulaire, le chatbot et l'export PDF. Les résultats sont ensuite classés selon ce score final.

## 🛡️ Qualité et Robustesse (Spec-Driven Development)

Le projet suit une approche rigoureuse de développement piloté par les spécifications (SDD) et le typage statique :

- **Typage Stricte (Mypy)** : Le codebase est 100% conforme à `mypy` en mode strict. Toutes les fonctions sont annotées et les modèles Pydantic assurent la validation des données à l'exécution.
- **Tests Automatisés (Pytest)** : Une suite de plus de 100 tests unitaires et d'intégration couvre le moteur de scoring, les agents pydantic-graph et les composants UI.
- **Documentation Narrative** : Chaque changement majeur est documenté dans le `walkthrough.md` et le [guide d'architecture](./ARCHITECTURE.md).

![Explication de la logique de scoring](./images/Screenshot-4.png)

### Critères de Scoring

Le score est calculé à partir d'une multitude de critères, regroupés en grandes catégories. Chaque critère est normalisé pour permettre une comparaison équitable. Voici la liste des critères utilisés :

**Catégorie : Emploi**

- **Opportunités Emploi (Match direct)** : Mesure le nombre réel d'offres d'emploi disponibles dans la commune pour les métiers recherchés (Source: API France Travail).
- **Tension de recrutement** : Identifie les offres signalées comme difficiles à pourvoir, signalant un fort besoin de main-d'œuvre immédiat.
- **Offres SIAE** : Identification des offres d'insertion par l'activité économique correspondant au profil (Source: Les emplois de l'inclusion).
- **Centres de Formation** : Mesure la présence de centres de formation proposant les cursus recherchés par les adultes du foyer.
- **Déclin Démographique Actif** : Valorise les communes perdant leur population active (25-54 ans), signalant un besoin de main-d'œuvre.

**Catégorie : Logement**

- **Taux de Logements Vacants** : Calcule le pourcentage de logements vacants structurels (> 2 ans), un indicateur de la disponibilité sur le marché locatif privé.
- **Taux de Logements Sociaux Inoccupés** : Mesure la part des logements sociaux vacants ou vides, indiquant une disponibilité potentielle dans le parc social.
- **Sous-occupation** : Évalue le potentiel de cohabitation et d'accueil chez l'habitant via le taux de sous-occupation des résidences principales.
- **Loyer Moyen** : Intégration des loyers moyens (Appartements et Maisons) pour évaluer l'accessibilité financière (Source: ODACE 2024).

**Catégorie : Éducation**

- **Taux de Classes à Risque de Fermeture** : Identifie les écoles où des classes risquent de fermer faute d'élèves, ce qui peut être une opportunité pour de nouvelles familles.
- **Taux de Couverture Petite Enfance** : Évalue la disponibilité des modes de garde (crèches, assistantes maternelles) pour les jeunes enfants (< 3 ans), basé sur les données de la CAF.
- **Proximité Scolaire** : Vérifie la présence de structures d'enseignement (Maternelle, Elémentaire, Collège, Lycée) dans la commune ou à proximité immédiate.
- **Déclin Démographique Jeune** : Valorise les communes perdant leur population jeune (-15 ans), indicateur d'un besoin de repeuplement scolaire.

**Catégorie : Inclusion & Vie Locale**

- **Accueils J'Accueille** : Valorise les bassins de vie disposant d'un réseau actif d'hébergement citoyen.
- **Accompagnement Réfugiés** : Évalue la présence d'associations spécialisées dans l'accueil des personnes réfugiées (Source: RNA).
- **Lien Social & Associations** : Mesure la densité associative globale et thématique (Loisirs, Sport, Culture) pour favoriser l'intégration.
- **Services d'Inclusion** : Mesure la présence de services dédiés (Français Langue Étrangère, aide administrative, etc.).
- **Taille de la Population** : Utilisé via une fonction Gaussienne dynamique (cible réglable entre 5k et 200k habitants) pour favoriser les communes correspondant au projet de vie.

**Catégorie : Mobilité**

- **Appartenance à la même agglomération (EPCI)** : Vérifie si la commune proposée est dans le même Établissement Public de Coopération Intercommunale (EPCI) que la commune de départ.
- **Présence d'une Gare et Transports** : Bonifie les communes disposant d'une gare ferroviaire et d'une bonne densité d'arrêts de transports en commun.

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

L'Assistant ODIS 2.0 est une interface de conversation en langage naturel conçue pour simplifier le travail de diagnostic social. Il repose sur une architecture multi-agent innovante :

### Architecture Multi-Agent (pydantic-graph)

Contrairement à un chatbot classique, l'Assistant ODIS est orchestré par un pipeline de données (pydantic-graph) qui coordonne plusieurs experts spécialisés (PydanticAI) :

1.  **L'Auto-Détection (Interviewer) :** Un agent one-shot qui extrait les critères depuis votre texte initial.
2.  **Le Triage :** Analyse la demande et planifie l'exécution parallèle des experts.
3.  **Parallélisation des Experts (MapReduce) :** Pour toute analyse de ville, le système lance simultanément :
    - **Scout** : Analyse le terrain (Google Maps).
    - **WEB** : Recherche l'actualité et le contexte social (Google Search).
    - **Job Hunter** : Trouve les offres d'emploi réelles (France Travail).
4.  **Synthétiseur (Join) :** Fusionne toutes les données en une réponse unique et cohérente.

### Points Forts de l'Agent

- **Raisonnement Métier :** Il comprend les codes ROME, les types de logement et les besoins spécifiques (santé, inclusion).
- **Transparence :** Chaque affirmation est sourcée, que ce soit via les données ODIS, Google Maps ou des recherches Web citées.
- **Proactivité :** L'agent cherche à compléter votre dossier sans que vous ayez besoin de remplir des dizaines de champs manuellement.

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
