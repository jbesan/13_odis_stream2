# OD&IS - Prototype d'Aide à la Localisation (Recherche Inversée)

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
- **Pondération Avancée :** Ajustez l'importance de chaque grande catégorie ou utilisez des profils prédéfinis (Famille, Santé, Emploi). Affinez la priorité de critères spécifiques (ex: "Logement Social" prioritaire).
- **Vue par Bassin de Vie ([DEPRECATED]) :** Permet d'agréger les résultats à l'échelle des "bassins de vie" de l'INSEE. Cette fonctionnalité est maintenue pour compatibilité mais le projet privilégie désormais la vue par commune.
- **Scoring Intelligent :** Chaque commune de France est évaluée sur sa compatibilité avec le profil. La taille de la population n'est plus un filtre mais un critère de score.
- **Système de "Binômes" ([DEPRECATED]) :** L'algorithme associait des communes voisines pour proposer des solutions conjointes. Cette logique est en cours de remplacement par l'enrichissement automatique via le bassin de vie.
- **Carte Interactive :** Visualisez les localités les mieux notées, leur score, et superposez des couches d'informations additionnelles (écoles, établissements de santé, services d'inclusion).
- **Résultats Détaillés & Export PDF :** Explorez les 5 meilleurs résultats avec une analyse de leurs points forts et exportez un rapport PDF complet pour la famille accompagnée.
- **Assistant IA (Multi-Agent ODIS) :** Interagissez en langage naturel avec un système multi-agent capable de conduire l'entretien, de calculer les scores, et de décorer les résultats avec des infos terrain et web. Voir la [documentation détaillée de l'architecture](app/agents/README.md).
- **Grounding Google Search :** Grâce à l'agent spécialisé WEB, accédez aux dernières actualités locales et au contexte social des communes visées.
- **Expertise Emploi (France Travail Live) :** Intégration en temps réel des offres d'emploi via l'API France Travail (Search API V2). Le moteur utilise exclusivement les codes ROME pour un scoring "Live" ultra-précis tenant compte de la tension de recrutement et du volume réel d'offres par commune. [DEPRECATED] L'usage des profils FAP et du BMO annuel a été retiré.
- **Référentiel des Associations Réfugiés ([F-26]):** Accédez à une base de données qualifiée d'associations spécialisées dans l'accueil des nouveaux arrivants, accessible via l'interface et l'assistant IA.
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

## ⚙️ Fonctionnement : Le Moteur de Scoring

Le cœur de l'application est un pipeline de scoring qui évalue les communes (ou les bassins de vie) en fonction du profil utilisateur.

1.  **Filtrage Géographique :** Le moteur délimite la zone de recherche selon le périmètre choisi (Département, Région, France Métropolitaine) ou une zone spécifique (Custom). Il n'utilise plus de rayon en km mais des limites administratives réelles.
2.  **Calcul des Critères :** Il calcule des dizaines de scores individuels pour chaque commune (Emploi, Logement, Santé, etc.).
3.  **Enrichissement par le Bassin de Vie :** Pour certains critères (ex: Éducation, Santé), le score d'une commune est **enrichi** par les services présents dans son bassin de vie. Si une commune n'a pas de lycée mais qu'il y en a un dans son bassin de vie, elle reçoit un score partiel (via le `bdv_factor` défini dans [scores_config.yaml](./app/scores_config.yaml)). Cela permet de valoriser les communes qui bénéficient des services de leur territoire proche.
4.  **Logique de Binôme ([DEPRECATED]) :** Historiquement, le moteur créait des paires de communes. Cette approche est devenue secondaire face à l'enrichissement par le bassin de vie décrit ci-dessus.
5.  **Agrégation par Catégorie :** Les scores des critères individuels sont moyennés pour former des scores de catégories. Pour un exemple concret du calcul, consultez le [SCORE_EXAMPLE.md](./SCORE_EXAMPLE.md).
6.  **Agrégation par Bassin de Vie (Optionnel) :** Si l'utilisateur choisit cette vue, **tous les scores** des communes (y compris Éducation, Santé, Inclusion) sont agrégés au niveau du bassin de vie par une **moyenne pondérée par la population**. Cela permet d'obtenir une vue d'ensemble cohérente où les services des grandes communes pèsent plus lourd.
7.  **Score Pondéré Final :** Enfin, un `weighted_score` global est calculé pour chaque entité (commune, binôme ou bassin de vie) en appliquant les poids définis par l'utilisateur. Les résultats sont ensuite classés selon ce score final.

![Explication de la logique de scoring](./images/Screenshot-4.png)

### Critères de Scoring

Le score est calculé à partir d'une multitude de critères, regroupés en grandes catégories. Chaque critère est normalisé pour permettre une comparaison équitable. Voici la liste des critères utilisés :

**Catégorie : Emploi**

- **Taux Besoin Emploi** : Mesure le nombre d'emplois non pourvus pour 1000 habitants, indiquant la demande de main-d'œuvre locale.
- **Taux Besoin Emploi en Tension** : Identifie spécifiquement les métiers "en tension" (difficiles à recruter) pour 1000 habitants, signalant les zones où l'insertion professionnelle est potentiellement plus rapide.
- **Adéquation Compétences/Emploi** : Évalue la correspondance entre les métiers recherchés par les adultes du foyer et les familles de métiers les plus demandées dans la zone.
- **Adéquation Besoins/Formations** : Mesure la présence de centres de formation proposant les cursus recherchés par les adultes du foyer.
- **Déclin Démographique Actif** : Valorise les communes perdant leur population active (25-54 ans), signalant un besoin de main-d'œuvre.
- **Opportunités Emploi (Live)** : Mesure le nombre réel d'offres d'emploi disponibles dans la commune pour les métiers recherchés (Source: API France Travail).
- **Tension de recrutement (Live)** : Identifie les offres signalées comme difficiles à pourvoir, signalant un fort besoin de main-d'œuvre immédiat.

**Catégorie : Logement**

- **Taux de Logements Vacants** : Calcule le pourcentage de logements vacants, un indicateur de la disponibilité sur le marché locatif privé.
- **Taux de Logements Sociaux Inoccupés** : Mesure la part des logements sociaux vacants ou vides, indiquant une disponibilité potentielle dans le parc social.
- **Taux de Grandes Résidences Principales** : Pour l'hébergement "chez l'habitant", ce critère évalue la proportion de résidences principales de 5 pièces et plus.

**Catégorie : Éducation**

- **Taux de Classes à Risque de Fermeture** : Identifie les écoles où des classes risquent de fermer faute d'élèves, ce qui peut être une opportunité pour de nouvelles familles.
- **Taux de Couverture Petite Enfance** : Évalue la disponibilité des modes de garde (crèches, assistantes maternelles) pour les jeunes enfants (< 3 ans), basé sur les données de la CAF.
- **Déclin Démographique Jeune** : Valorise les communes perdant leur population jeune (-15 ans), indicateur d'un besoin de repeuplement scolaire.
- **Education**: Annuaire de l'éducation (Data.gouv), Effectifs (Data.gouv), Taux de couverture Petite Enfance (CAF), Crèches (BPE/INSEE), Formations (Data.gouv).

**Catégorie : Inclusion & Vie Locale**

- **Taux de Services d'Inclusion** : Mesure la densité de services dédiés à l'inclusion (apprentissage du français, aide juridique, etc.) pour 1000 habitants.
- **Accompagnement Réfugiés** : Évalue la présence d'associations spécialisées dans l'accueil des personnes réfugiées (Source: RNA filtré).
- **Présence de Soutien Spécifique** : Vérifie la présence de services répondant aux besoins spécifiques exprimés dans le formulaire (santé, handicap, etc.).
- **Taille de la Population** : La population de la commune est utilisée comme un critère, favorisant les communes de taille intermédiaire pour un meilleur équilibre accueil/intégration.
- **Couleur Politique** : Prend en compte l'affiliation politique de la municipalité, en valorisant celles jugées plus favorables à l'accueil.

**Catégorie : Mobilité**

- **Appartenance à la même agglomération (EPCI)** (`mob_epci_scaled`) : Vérifie si la commune proposée est dans le même Établissement Public de Coopération Intercommunale (EPCI) que la commune de départ.
- **Présence d'une Gare (`mob_gare_scaled`)** : Bonifie les communes disposant d'une gare ferroviaire (Source: Odace).

## 🛠️ Stack Technique

- **Framework Applicatif :** [Streamlit](https://streamlit.io/)
- **Analyse de Données :** [Pandas](https://pandas.pydata.org/), [GeoPandas](https://geopandas.org/), [NumPy](https://numpy.org/)
- **Scoring & Normalisation :** [Scikit-learn](https://scikit-learn.org/)
- **Cartographie Interactive :** [Folium](https://python-visualization.github.io/folium/) & [streamlit-folium](https://github.com/randyzwitch/streamlit-folium)
- **Graphiques :** [Plotly Express](https://plotly.com/python/plotly-express/)
- **Sources de Données :** Les données sont agrégées depuis de nombreuses sources ouvertes, notamment l'INSEE, Data.gouv.fr, France Travail (Pôle Emploi), Odace (Gares), etc.

## 📂 Structure du Projet

Le code de l'application Streamlit est organisé de manière modulaire au sein du répertoire app/ pour séparer les différentes logiques :

```
app/
├── 1_Accueil.py
├── config.py
├── data_loader.py
├── maps.py
├── scoring.py
├── ui.py
├── pages/
│ ├── 2_Formulaire.py
│ └── 3_Resultats.py
└── requirements.txt
```

- 1_Accueil.py : C'est le point d'entrée principal de l'application multipage. Il affiche la page d'accueil.
- pages/2_Formulaire.py : La deuxième page de l'application, qui contient le formulaire du projet de vie.
- pages/3_Resultats.py : La troisième page qui affiche les résultats du scoring.
- ui.py : Ce fichier est responsable de la création de tous les composants de l'interface utilisateur avec Streamlit.
- scoring.py : Le cœur logique du prototype. Il contient l'ensemble du pipeline de traitement et de notation, optimisé pour la performance.
- maps.py : Regroupe toutes les fonctions liées à la génération des cartes interactives avec Folium.
- config.py : Un fichier central pour la configuration.
- data_loader.py : Contient la logique pour charger les données.
- app/agents/ : Répertoire contenant l'intelligence artificielle du projet.
  - orchestrator.py : Cerveau central gérant le routing et la collaboration entre experts.
  - interviewer.py : Agent spécialisé dans la collecte et le diagnostic des besoins.
  - scorer.py : Expert en calcul de pertinence basé sur le moteur ODIS.
  - scout.py : Expert terrain utilisant l'API Google Maps (POIs, itinéraires).
  - web.py : Expert news utilisant Google Search pour le contexte social et l'actualité.
  - job_hunter.py : Expert emploi connecté en temps réel aux API France Travail.

## 🤖 Interface AI Agent (Assistant ODIS 2.0)

L'Assistant ODIS 2.0 est une interface de conversation en langage naturel conçue pour simplifier le travail de diagnostic social. Il repose sur une architecture multi-agent innovante :

### Architecture Multi-Agent (LangGraph)

Contrairement à un chatbot classique, l'Assistant ODIS est orchestré par un graphe d'états (LangGraph) qui coordonne plusieurs experts spécialisés (PydanticAI) :

1.  **Le Routeur :** Analyse votre demande et décide de l'action à entreprendre (Interview, Scoring, ou Décoration).
2.  **L'Interviewer :** Conduit l'entretien. Il détecte vos besoins et met à jour le diagnostic partagé.
3.  **Le Scorer :** Calcule les scores ODIS sur des milliers de communes et explique les résultats.
4.  **La Cascade de Décoration :** Lorsque vous explorez une ville, une chaîne d'experts se déclenche :
    - **Scout** : Analyse le terrain (Google Maps).
    - **WEB** : Recherche l'actualité et le contexte social (Google Search).
    - **Job Hunter** : Trouve les offres d'emploi réelles (France Travail).
    - **Synthétiseur** : Fusionne toutes ces informations en une réponse unique et cohérente.

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
