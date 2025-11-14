# PRD (Product Requirements Document) - OD&IS "Stream 2"
**Version :** 1.3
**Projet :** Prototype de Recherche Inversée (Aide à la Localisation)
**Auteur :** D4G: OD&IS (revu le 08/11/2025)

---

## 1. Contexte et Objectif

**Objectif :** Fournir un prototype d'outil d'aide à la décision pour les **travailleurs sociaux** accompagnant des personnes/familles en parcours d'intégration (statut régularisé, post-CADA).

**Principe (Recherche Inversée) :** L'outil ne part pas d'un lieu, mais du **"projet de vie"** de la personne pour identifier les territoires (communes ou paires de communes) les plus pertinents.

**Fondations Techniques :**
* **Données :** Exclusivement Open Data.
* **Moteur :** Scoring de pertinence (`scoring.py`) basé sur un profil utilisateur.
* **Stack :** Streamlit (UI), Pandas/GeoPandas (Data), Folium (Carto).

---

## 2. Persona Cible

* **Utilisateur :** Le **travailleur social**.
* **Rôle :** Utilise l'outil *pendant* l'entretien comme support de médiation et d'exploration.
* **Besoin Clé :** **Explicabilité**. L'outil doit justifier *pourquoi* un territoire est recommandé (points forts, radar de scores).

---

## 3. Parcours Fonctionnel (Version Actuelle 1.1)

### Étape 1 : Accueil (`1_Accueil.py`)
* Présentation du projet, logos, et information RGPD.
* L'utilisateur peut commencer le parcours pour remplir le formulaire ou aller directement à la page des résultats.
* Le parcours peut être personnalisé avec le nom de la personne accompagnée.
* Des scénarios de démonstration peuvent être chargés via des paramètres dans l'URL.

### Étape 2 : Formulaire "Projet de Vie" (`2_Formulaire.py`)
Collecte des besoins via un formulaire multi-pages (basé sur `st.session_state['form_page']`).

* **Localisation :** `ui_departement`, `ui_commune` (point de départ).
* **Famille :** `ui_nb_adultes`, `ui_nb_enfants`.
* **Éducation :** `ui_classe_enfant_{i}` (déclenche le scoring Éducation si `nb_enfants > 0`).
* **Projet Pro :**
    * `ui_metiers_adult_{i}` (codes FAP, alimente `met_match_adult_scaled`).
    * `ui_formations_adult_{i}` (codes formations, alimente `form_match_adult_scaled`).
* **Logement :**
    * `ui_hebergement` (ex: "Chez l'habitant") : **Clarification (v1.1)** - Il s'agit de la solution *temporaire* à l'arrivée. **Cette donnée n'est pas utilisée dans le scoring actuel** ; elle est contextuelle pour le travailleur social.
    * `ui_logement` (ex: "Logement Social") : **Critère de scoring**. Active les scores pertinents (`log_soc_inoc_scaled`, `log_vac_scaled`, etc.).
* **Santé & Inclusion :**
    * `ui_besoin_sante` (ex: "Maternité").
    * `ui_besoins_autres` (services d'inclusion).
    * **Clarification (v1.1) :** Ces deux champs **ne sont pas utilisés dans le calcul du `weighted_score`**. Leur unique fonction actuelle est de déclencher l'affichage de couches d'information (overlays) sur la carte des résultats (`maps.py`) pour aider à la projection. L'inclusion dans le scoring est une *feature future* potentielle.
* **Mobilité :** `ui_loc_distance_km` (rayon de recherche max, filtre le `GeoDataFrame`).

### Étape 3 : Interface de Résultats (`3_Resultats.py`)
Page principale interactive (layout `col_results`, `col_map`).

* Une barre latérale permet d'ajuster les poids des catégories et les filtres de recherche.
* Des onglets permettent de modifier rapidement les critères du "projet de vie".
* Un bouton "Mettre à jour" relance la recherche avec les nouveaux paramètres.

### Étape 4 : Moteur de Scoring (`scoring.py`)
Logique métier principale, orchestrée par `compute_odis_score()`.

1.  **Pré-filtrage :** `df[df.population > config.pop_min]`.
2.  **Distance :** Calcul `dist_current_loc` (via `sjoin_nearest`) depuis `config.commune_actuelle`.
3.  **Filtrage Geo :** `filter_by_distance(df, max_distance_km=config.loc_distance_km)`.
4.  **Scores Critères :** `compute_criteria_scores()` calcule les scores normalisés (QuantileTransformer) pour les critères activés (ex: `met_match_adult1_scaled`, `log_soc_inoc_scaled`).
5.  **Logique "Binôme" :**
    * `add_neighbor_scores()` "explode" le dataframe pour créer des paires (commune-A, commune-A) [monôme] et (commune-A, commune-B) [binôme].
    * `compute_category_scores()` calcule le score de catégorie.
    * **Point clé :** Le score d'un critère pour un binôme est le `max(score_A, score_B * (1 - binome_penalty))`.
6.  **Score Pondéré :** `compute_weighted_score()` applique les poids (sliders) de l'UI (`config.poids_...`) aux scores de catégorie (`..._cat_score`).
7.  **Sélection Finale :** `select_best_score_per_commune()` ne garde que la meilleure ligne (monôme OU binôme) pour chaque `codgeo`.

### Étape 5 : Visualisation des Résultats (`3_Resultats.py`)

* **Colonne de Gauche (Liste) :** Affiche le Top 5 des résultats (commune ou binôme) avec leurs points forts et un radar de scores.
* **Colonne de Droite (Carte) :** Affiche une carte choroplèthe avec le score de toutes les communes. Des couches d'information additionnelles (écoles, santé, services) peuvent être superposées.

---
## 4. Features

## 🚀 Feature [F-01]: Navigation Principale

### 📝 User Story
En tant que travailleur social, je veux des points de départ clairs pour commencer un nouveau profil ou accéder directement aux résultats afin de naviguer efficacement dans l'outil.

### 🔑 Key Features
* Un bouton "Commencer le parcours" pour démarrer le formulaire.
* Un bouton "Aller directement à la page résultats" pour contourner le formulaire.

### 📊 Status
- Completed

## 🚀 Feature [F-02]: Scénarios de Démonstration

### 📝 User Story
En tant que travailleur social, je veux pouvoir charger des scénarios de démonstration pré-remplis pour comprendre rapidement les capacités de l'outil sans avoir à remplir manuellement le formulaire.

### 🔑 Key Features
* Charger un profil de démo à l'aide d'un paramètre de requête d'URL (par ex., `?demo=...`).
* Le formulaire est pré-rempli avec les données de la démo.

### 📊 Status
- Completed

## 🚀 Feature [F-03]: Personnalisation du Parcours

### 📝 User Story
En tant que travailleur social, je veux saisir le nom de la personne que j'accompagne pour personnaliser l'interface, rendant le processus plus centré sur l'humain.

### 🔑 Key Features
* Un champ de saisie pour le nom de la personne sur la page d'accueil.
* Le nom est utilisé pour personnaliser les titres et les libellés dans toute l'application.

### 📊 Status
- Completed

## 🚀 Feature [F-04]: Contrôles Interactifs des Résultats

### 📝 User Story
En tant que travailleur social, je veux ajuster les poids de notation et les filtres en temps réel pour explorer collaborativement différents scénarios avec la personne que j'accompagne.

### 🔑 Key Features
* Une barre latérale avec des curseurs pour ajuster les poids des différentes catégories de notation.
* Une barre latérale avec des champs pour filtrer les résultats (par ex., population minimale).
* Un bouton "Mettre à jour" pour relancer la recherche avec les nouvelles valeurs.

### 📊 Status
- Completed

## 🚀 Feature [F-05]: Édition Rapide des Entrées

### 📝 User Story
En tant que travailleur social, je veux pouvoir modifier rapidement les entrées initiales du "projet de vie" depuis la page des résultats pour itérer sur la recherche sans retourner au formulaire principal.

### 🔑 Key Features
* Des onglets sur la page des résultats qui reflètent les champs du formulaire.
* La possibilité de changer n'importe quelle valeur d'entrée.
* Un bouton "Mettre à jour" pour relancer la recherche avec les nouvelles valeurs.

### 📊 Status
- Completed

## 🚀 Feature [F-06]: Affichage des 5 Meilleurs Résultats

### 📝 User Story
En tant que travailleur social, je veux voir une liste claire et classée des meilleurs lieux recommandés pour discuter facilement des options les plus prometteuses.

### 🔑 Key Features
* Affiche les 5 meilleurs résultats (commune seule ou en binôme).
* Montre les points forts de chaque résultat.
* Inclut un graphique radar pour visualiser la répartition des scores par catégorie.

### 📊 Status
- Completed

## 🚀 Feature [F-07]: Visualisation sur Carte Interactive

### 📝 User Story
En tant que travailleur social, je veux voir les lieux recommandés sur une carte interactive pour mieux comprendre leur contexte géographique et explorer les services environnants.

### 🔑 Key Features
* Une carte choroplèthe montrant les scores de toutes les communes dans la zone de recherche.
* Des marqueurs pour les 5 meilleurs résultats.
* Des couches cartographiques activables pour les écoles, les services de santé et d'autres points d'intérêt.

### 📊 Status
- Completed

## 🚀 Feature [F-08]: Ajout de l'indicateur loyer moyen

### 📝 User Stories
- En tant que travailleur social, je veux intégrer le loyer moyen dans le score pour mieux évaluer l'accessibilité financière d'une localité pour la famille que j'accompagne.

### 🔑 Key Features
* Intégrer une nouvelle source de données (API) fournissant le loyer moyen par commune.
* Créer un nouveau critère de score "loyer" dans la catégorie "Logement", où un loyer plus bas résulte en un meilleur score.
* Afficher le loyer comme un point fort (ex: "Loyer modéré") dans la liste des résultats lorsqu'il est significatif pour une localité.

### 📊 Status
- In Progress

## 🚀 Feature [F-09]: Résultats par bassin de vie

### 📝 User Stories
- En tant que travailleur social, je veux pouvoir agréger et visualiser les résultats par "bassin de vie" pour obtenir une perspective régionale plus pertinente et intuitive sur les territoires bien connectés.

### 🔑 Key Features
*   Ajouter un bouton de bascule (toggle) dans la barre latérale des résultats pour choisir entre la vue "par commune" et la vue "par bassin de vie".
*   Agréger les indicateurs de score existants au niveau du "bassin de vie" pour calculer un nouveau score global.
*   Mettre à jour la carte des résultats pour afficher des polygones colorés représentant les "bassins de vie" et leur score.

### 📊 Status
- Completed

## 🚀 Feature [F-10]: Refonte du Filtrage et Score de Population

### 📝 User Stories
- En tant que travailleur social, je veux que la pertinence d'une localité ne soit plus limitée par un seuil de population fixe, mais que la population soit un facteur parmi d'autres dans le score pour des résultats plus nuancés.
- En tant que travailleur social, je souhaite que la recherche par zone géographique (département, région) soit plus précise et retourne toutes les communes réellement présentes dans cette zone.

### 🔑 Key Features
*   Suppression du filtre de population minimal : la population est désormais un critère de score dans la catégorie "Inclusion", valorisant les pôles urbains sans exclure les zones rurales.
*   Refonte de la logique de filtrage géographique pour utiliser des calculs géospatiaux (centroïdes et intersections) au lieu de simples distances, s'appliquant de manière cohérente aux communes et aux bassins de vie.

### 📊 Status
- Completed
