# PRD (Product Requirements Document) - OD&IS "Stream 2"
**Version :** 1.1
**Projet :** Prototype de Recherche Inversée (Aide à la Localisation)
**Auteur :** D4G: OD&IS (revu le 06/11/2025)

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
* **[Feature]** `st.button("Commencer le parcours")` -> `pages/2_Formulaire.py`
* **[Feature]** `st.button("Aller directement à la page résultats")` -> `pages/3_Resultats.py`
* **[Feature]** Scénarios de démo via query params (`?demo=...`) qui pré-remplissent `st.session_state` via `config.py`. Passer un scénario de démo dans l'URL amène sur la page d'accueil et le formulaire du projet de vie sera pre-rempli.
* **[Feature]** Personnalisation du parcours avec le nom de la personne accompagnée

**Description :**
Sur la page d'accueil (`1_Accueil.py`), un champ de saisie optionnel sera ajouté pour le "Nom de la personne accompagnée". Si un nom est fourni, il sera stocké dans la session Streamlit (`st.session_state['person_name']`) et utilisé pour personnaliser les titres, sous-titres et labels sur les pages du formulaire (`2_Formulaire.py`) et des résultats (`3_Resultats.py`). Par exemple, "Localisation actuelle de la personne accompagnée" deviendra "Localisation actuelle de la personne accompagnée (Nom Prénom)". Si aucun nom n'est saisi, l'application continuera d'utiliser la formulation générique.

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

* **[Feature] Barre latérale (`ui.py`) :**
    * Permet d'ajuster les **poids** (sliders) des catégories : `poids_emploi`, `poids_logement`, `poids_education`, etc.
    * Permet d'ajuster les **filtres** : `pop_min`, `binome_penalty`.
* **[Feature] Onglets d'Inputs :** Reprise des champs du formulaire (Étape 2) pour modification rapide et itération.
* **[Feature] Bouton "Mettre à jour" :** Déclenche `run_search()` qui appelle le pipeline de scoring.

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

* **[Feature] Colonne de Gauche (Liste) :**
    * `ui.display_results_list()` : Affiche le Top 5 des résultats (commune ou binôme).
    * (Impliqué) Affichage détaillé avec points forts et radar des sous-scores par catégorie.
* **[Feature] Colonne de Droite (Carte) :**
    * `st_folium` affichant la carte de base.
    * **Couche de base :** `maps.build_scores_layer()` - Choroplèthe de *toutes* les communes de la zone, colorées par `weighted_score`.
    * **Couches d'info (Toggles) :**
        * Affichage des Top 5 résultats.
        * `maps.build_ecoles_layer()` (si `nb_enfants > 0`).
        * `maps.build_sante_layer()` (si `besoin_sante != "Aucun"`).
        * `maps.build_services_layer()` (si `besoins_autres` non vide).

---