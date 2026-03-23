# OD&IS - Prototype d'Aide à la Mobilité (Recherche Inversée)

[![Python Version](https://img.shields.io/badge/python-3.10-blue.svg)](https://www.python.org/downloads/release/python-3100/)
[![Framework](https://img.shields.io/badge/Framework-Streamlit-red.svg)](https://streamlit.io)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](../../LICENSE)

## 🎯 Contexte et Objectifs du Projet

Ce projet, surnommé **"Stream 2"**, est un prototype fonctionnel explorant une approche de **"recherche inversée"** pour l'aide à la relocalisation des personnes et familles accompagnées par des structures d'insertion comme le programme [J'accueille](https://www.jaccueille.fr/) de [SINGA](https://www.singafrance.com/).

Il s'inscrit en complément du projet principal [13_odis](https://github.com/dataforgoodfr/13_odis) (ou "Stream 1"), qui se concentre sur l'exploration et la comparaison d'indicateurs pour une commune déjà sélectionnée.

L'innovation de ce prototype est de renverser la logique : au lieu de partir d'un lieu, **on part des besoins et du projet de vie de la personne**. Le persona principal est le travailleur social qui, à travers cet outil, peut identifier les territoires les plus prometteurs pour la réussite d'un projet d'intégration.

![Comparaison Stream 1 vs Stream 2](./images/Screenshot-3.png)

Ce prototype a un triple objectif :

1.  **Valider la pertinence de l'approche** auprès des futurs utilisateurs (travailleurs sociaux, accompagnants).
2.  **Démontrer la faisabilité technique** de construire un score de pertinence en utilisant exclusivement des données ouvertes (Open Data).
3.  **Promouvoir l'intérêt de cette démarche** auprès de potentiels partenaires, décideurs et financeurs.

## ✨ Fonctionnalités Principales

- **Profil Personnalisé :** Définissez un "projet de vie" détaillé incluant la composition du foyer, le niveau scolaire des enfants, les métiers visés, les besoins en formation, etc.
- **Pondération Avancée :** Choisissez un profil prédéfinis (Équilibré, Famille, Santé, Emploi) ou activez le **Mode Expert** pour un réglage fin des poids de chaque catégorie.
- **Scoring Intelligent :** Chaque commune de France est évaluée sur sa compatibilité avec le profil via un modèle de données typé. La taille de la population est traitée via un **score Gaussien** (favorisant les villes moyennes autour de 50 000 habitants) plutôt qu'un simple filtre.
- **Optimisation Mémoire :** Le moteur de recherche est optimisé pour traiter des milliers de communes en limitant le chargement des colonnes redondantes et en purgeant les indicateurs non pertinents après le calcul des scores (Deny-list).
- **Carte Interactive :** Visualisez les localités les mieux notées, leur score, et superposez des couches d'informations additionnelles (écoles, établissements de santé, services d'inclusion).
- **Résultats Détaillés & Export PDF :** Explorez les 5 meilleurs résultats avec une analyse comparative générée automatiquement par l'IA et exportez un rapport PDF complet incluant ces analyses.
- **Assistant IA (Multi-Agent ODIS) :** Système multi-agent (LangGraph) capable de conduire l'entretien via l'agent **Interviewer**, de calculer les scores avec l'agent **Scorer**, et d'enrichir les résultats avec des infos terrain (**Scout**) et web (**Web**). voir la [documentation détaillée de l'architecture](app/agents/README.md).
- **Grounding Google Search :** Grâce à l'agent spécialisé WEB, accédez aux dernières actualités locales et au contexte social des communes visées.
- **Référentiel des Associations Réfugiés :** Accédez à une base de données qualifiée d'associations spécialisées dans l'accueil des nouveaux arrivants.
- **Moteur de Recherche RAG (RNA) :** Recherche sémantique et thématique sur l'ensemble du Répertoire National des Associations (RNA) via BigQuery et Vertex AI, permettant de classer les associations par catégories d'inclusion (FLE, Logement, Emploi, etc.) avec une précision inégalée.
- **Accueils Citoyens (J'Accueille) :** Intégration de la base de données de l'association J'Accueille pour valoriser les bassins de vie disposant déjà d'un réseau d'hébergement citoyen actif. (Données Mars 2026).
- **Scénarios de Démonstration :** Chargez rapidement des profils pré-configurés pour découvrir le potentiel de l'outil.

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

## ⚙️ Fonctionnement : Le Moteur de Scoring

Le cœur de l'application est un pipeline de scoring qui évalue les communes (ou les bassins de vie) en fonction du profil utilisateur.

1.  **Filtrage Géographique :** Le moteur délimite la zone de recherche selon le périmètre choisi (Département, Région, France Métropolitaine) ou une zone spécifique (Custom). Il n'utilise plus de rayon en km mais des limites administratives réelles.
2.  **Calcul des Critères :** Il calcule des dizaines de scores individuels pour chaque commune (Emploi, Logement, Santé, etc.).
3.  **Enrichissement par le Bassin de Vie :** Pour certains critères (ex: Éducation, Santé), le score d'une commune est bonifié par les opportunités du Bassin de Vie via une logique de Boost non-pénalisante. Si une commune n'a pas de lycée mais qu'il y en a un dans son Bassin de Vie, elle reçoit un bonus (via le `bdv_factor` défini dans [scores_config.yaml](./app/scores_config.yaml)). Cela permet de valoriser les communes qui bénéficient des services de leur territoire proche.
4.  **Agrégation par Catégorie :** Les scores des critères individuels sont moyennés pour former des scores de catégories. Pour un exemple concret du calcul et une explication détaillée de la logique de boost, consultez la [Documentation du Scoring](./SCORING.md).
5.  **Score Pondéré Final :** Enfin, un `weighted_score` global est calculé pour chaque commune en appliquant les poids définis. Le moteur s'appuie sur le modèle `SearchCriterias` pour garantir la cohérence entre l'interface formulaire, le chatbot et l'export PDF. Les résultats sont ensuite classés selon ce score final.

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
- **Taille de la Population** : Utilisé via une fonction Gaussienne ($\mu$=50k, $\sigma$=40k) pour favoriser les communes de taille intermédiaire.

**Catégorie : Mobilité**

- **Appartenance à la même agglomération (EPCI)** : Vérifie si la commune proposée est dans le même Établissement Public de Coopération Intercommunale (EPCI) que la commune de départ.
- **Présence d'une Gare et Transports** : Bonifie les communes disposant d'une gare ferroviaire et d'une bonne densité d'arrêts de transports en commun.

## 🛠️ Stack Technique

- **Framework Applicatif :** [Streamlit](https://streamlit.io/)
- **Analyse de Données :** [Pandas](https://pandas.pydata.org/), [GeoPandas](https://geopandas.org/), [NumPy](https://numpy.org/)
- **Scoring & Normalisation :** [Scikit-learn](https://scikit-learn.org/)
- **Cartographie Interactive :** [Folium](https://python-visualization.github.io/folium/) & [streamlit-folium](https://github.com/randyzwitch/streamlit-folium)
- **Graphiques :** [Plotly Express](https://plotly.com/python/plotly-express/)
- **Infrastructures Cloud :** [Google BigQuery](https://cloud.google.com/bigquery) (Stockage & Vector Search) et [Vertex AI](https://cloud.google.com/vertex-ai) (Embeddings Multimodal) pour le moteur de recherche d'associations (RAG).

## 📂 Structure du Projet

Le code de l'application Streamlit est organisé de manière modulaire au sein du répertoire app/ pour séparer les différentes logiques :

```
app/
├── main.py                 # Point d'entrée Streamlit
├── config.py               # Configuration globale
├── scores_config.yaml      # Paramétrage des poids du scoring
├── core/                   # Logique métier transverse
│   ├── scoring.py          # Moteur de scoring ODIS
│   ├── models.py           # Modèles de données (Pydantic)
│   ├── maps.py             # Fonctions cartographiques Folium
│   └── pdf_generator.py    # Génération des rapports PDF
├── ui/                     # Composants et Graphiques
│   ├── components.py       # Composants réutilisables Streamlit
│   └── charts.py           # Visualisations Plotly
├── utils/                  # Utilitaires techniques
│   ├── data_loader.py      # Chargement et cache des données
│   └── logger.py           # Gestion des logs applicatifs
├── agents/                 # Intelligence Artificielle (Multi-Agent)
│   ├── graph.py            # Orchestration LangGraph
│   ├── state.py            # Définition de l'état partagé
│   └── tools.py            # Outils experts (Scoring, FT, Maps)
└── pages/                  # Pages de l'application
    ├── 1_Accueil.py
    ├── 2_Formulaire.py
    ├── 3_Resultats.py
    └── 4_AI_Chatbot.py
```

- **core/** : Contient le cœur algorithmique du projet, notamment le moteur de scoring et les modèles de données.
- **ui/** : Regroupe les composants visuels et les fonctions de rendu graphique.
- **utils/** : Services techniques pour le chargement optimisé des données Parquet et le logging.
- **app/agents/** : Architecture multi-agent orchestrée par LangGraph pour l'assistance interactive.
  - **graph.py** : Graphe d'orchestration.
  - **interviewer.py**, **scorer.py**, **scout.py**, **web.py**, **job_hunter.py**, **synthesizer.py**, **refiner.py** : Agents spécialisés.

## 🤖 Interface AI Agent (Assistant ODIS 2.0)

L'Assistant ODIS 2.0 est une interface de conversation en langage naturel conçue pour simplifier le travail de diagnostic social. Il repose sur une architecture multi-agent innovante :

### Architecture Multi-Agent (LangGraph)

Contrairement à un chatbot classique, l'Assistant ODIS est orchestré par un graphe d'états (LangGraph) qui coordonne plusieurs experts spécialisés (PydanticAI) :

1.  **Le Routeur :** Analyse la demande et décide de l'action à entreprendre.
2.  **L'Interviewer :** Conduit l'entretien et affine le diagnostic.
3.  **Le Scorer :** Calcule les scores ODIS et explique les résultats.
4.  **Parallélisation des Experts :** Pour toute analyse de ville, le système lance simultanément :
    - **Scout** : Analyse le terrain (Google Maps).
    - **WEB** : Recherche l'actualité et le contexte social (Google Search).
    - **Job Hunter** : Trouve les offres d'emploi réelles (France Travail).
5.  **Synthétiseur** : Fusionne toutes les données en une réponse unique.

### Points Forts de l'Agent

- **Raisonnement Métier :** Il comprend les codes ROME, les types de logement et les besoins spécifiques (santé, inclusion).
- **Transparence :** Chaque affirmation est sourcée, que ce soit via les données ODIS, Google Maps ou des recherches Web citées.
- **Proactivité :** L'agent cherche à compléter votre dossier sans que vous ayez besoin de remplir des dizaines de champs manuellement.

## 🔮 Feuille de Route et Améliorations Futures

Ce prototype est une base solide qui peut être grandement améliorée :

- **⭐ Fonctionnalités :**
  - **Comptes Utilisateurs :** Permettre de sauvegarder, nommer et gérer plusieurs scénarios de "projets de vie".
  - **Filtres Avancés :** Ajouter des filtres plus fins (ex: exclure certaines régions, filtrer par couleur politique).
  - **Comparaison des Résultats :** Ajouter une fonction pour comparer 2 ou 3 des meilleurs résultats côte à côte.

- **📊 Données & Scoring :**
  - **Intégrer le Loyer Moyen :** Ajouter le loyer moyen comme critère de score pour mieux évaluer l'accessibilité financière.
  - **Étendre les Sources de Données :** Intégrer plus de jeux de données (transports en commun, services de santé spécifiques, activités culturelles).
  - **Fraîcheur des Données :** Mettre en place un pipeline pour mettre à jour automatiquement les données sous-jacentes.
  - **Affiner les Critères :** Travailler avec des travailleurs sociaux pour affiner la liste des critères et leur pertinence.

- **💻 Technique & UX :**
  - **Refactoring du Scoring :** La logique de scoring a été refactorisée et optimisée, mais peut encore être améliorée pour plus de modularité.
  - **Tests :** Ajouter des tests unitaires et d'intégration pour fiabiliser le pipeline de scoring et l'interface.
  - **Performance :** Optimiser le chargement des données et les calculs de score pour une meilleure fluidité.
  - **Design UI/UX :** Améliorer le design visuel, la mise en page et l'ergonomie sur mobile.

## ⚖️ Licence

Ce projet est sous licence MIT. Consultez le fichier [LICENSE](../../LICENSE) à la racine du projet pour plus de détails.
