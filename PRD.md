# PRD (Product Requirements Document) - OD&IS "Stream 2"

**Version :** 1.4
**Projet :** Prototype de Recherche Inversée (Aide à la Localisation)
**Auteur :** D4G: OD&IS (revu le 08/12/2025)

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

### Étape 1 : Accueil (`1_Accueil.py`)

- Présentation du projet, logos, et information RGPD.
- L'utilisateur peut commencer le parcours pour remplir le formulaire ou aller directement à la page des résultats.
- Le parcours peut être personnalisé avec le nom de la personne accompagnée.
- Des scénarios de démonstration peuvent être chargés via des paramètres dans l'URL.

### Étape 2 : Formulaire "Projet de Vie" (`2_Formulaire.py`)

Collecte des besoins via un formulaire multi-pages (basé sur `st.session_state['form_page']`).

- **Localisation :** `ui_departement`, `ui_commune` (point de départ).
- **Famille : :** `ui_nb_adultes`, `ui_nb_enfants`.
- **Éducation :** `ui_classe_enfant_{i}` (déclenche le scoring Éducation si `nb_enfants > 0`).
- **Projet Pro :**
  - `ui_metiers_adult_{i}` (codes FAP, alimente `met_match_adult_scaled`).
  - `ui_formations_adult_{i}` (codes formations, alimente `form_match_adult_scaled`).
- **Logement :**
  - `ui_hebergement` (ex: "Chez l'habitant") : **Clarification (v1.1)** - Il s'agit de la solution _temporaire_ à l'arrivée. **Cette donnée n'est pas utilisée dans le scoring actuel** ; elle est contextuelle pour le travailleur social.
  - `ui_logement` (ex: "Logement Social") : **Critère de scoring**. Active les scores pertinents (`log_soc_inoc_scaled`, `log_vac_scaled`, etc.).
- **Santé & Inclusion :**
  - `ui_besoin_sante` (ex: "Maternité").
  - `ui_besoins_autres` (services d'inclusion).
  - **Clarification (v1.1) :** Ces deux champs **ne sont pas utilisés dans le calcul du `weighted_score`**. Leur unique fonction actuelle est de déclencher l'affichage de couches d'information (overlays) sur la carte des résultats (`maps.py`) pour aider à la projection. L'inclusion dans le scoring est une _feature future_ potentielle.
- **Mobilité :** `ui_loc_distance_km` (rayon de recherche max, filtre le `GeoDataFrame`).

### Étape 3 : Interface de Résultats (`3_Resultats.py`)

Page principale interactive (layout `col_results`, `col_map`).

- Une barre latérale permet d'ajuster les poids des catégories et les filtres de recherche.
- Des onglets permettent de modifier rapidement les critères du "projet de vie".
- Un bouton "Mettre à jour" relance la recherche avec les nouveaux paramètres.

### Étape 4 : Moteur de Scoring (`scoring.py`)

Logique métier principale, orchestrée par `compute_odis_score()`.

1.  **Pré-filtrage :** `df[df.population > config.pop_min]`.
2.  **Distance :** Calcul `dist_current_loc` (via `sjoin_nearest`) depuis `config.commune_actuelle`.
3.  **Filtrage Geo :** `filter_by_distance(df, max_distance_km=config.loc_distance_km)`.
4.  **Scores Critères :** `compute_criteria_scores()` calcule les scores normalisés (QuantileTransformer) pour les critères activés (ex: `met_match_adult1_scaled`, `log_soc_inoc_scaled`).
5.  **Logique "Binôme" :**
    - `add_neighbor_scores()` "explode" le dataframe pour créer des paires (commune-A, commune-A) [monôme] et (commune-A, commune-B) [binôme].
    - `compute_category_scores()` calcule le score de catégorie.
    - **Point clé :** Le score d'un critère pour un binôme est le `max(score_A, score_B * (1 - binome_penalty))`.
6.  **Score Pondéré :** `compute_weighted_score()` applique les poids (sliders) de l'UI (`config.poids_...`) aux scores de catégorie (`..._cat_score`).
7.  **Sélection Finale :** `select_best_score_per_commune()` ne garde que la meilleure ligne (monôme OU binôme) pour chaque `codgeo`.

### Étape 5 : Visualisation des Résultats (`3_Resultats.py`)

- **Colonne de Gauche (Liste) :** Affiche le Top 5 des résultats (commune ou binôme) avec leurs points forts et un radar de scores.
- **Colonne de Droite (Carte) :** Affiche une carte choroplèthe avec le score de toutes les communes. Des couches d'information additionnelles (écoles, santé, services) peuvent être superposées.

---

## 4. Idées d'améliorations

Liste en vrac d'idées d'amélioration

- Ajouter un call-to-action dans les fiches du top 5 (e.g. appeler TS local)
  - Annuaire TS local
- Génerer un prompt pour une Deep Research sur un des territoires recommandés
- Normaliser les scores de catégorie pour éviter une surévaluation de certains critères
- Ajouter des profils de pondération (famille vs célibataire)
- Ajouter la base J'accueille d'accueil chez l'habitant
- Ajouter la base des transports publics ?
- Critere 'Niveau de vie' de l'INSEE ?
- Enlever la couleur politique --> remplacer par y-a-t'il un CTAI (signe que la commune s'implique dans l'intégration) + Est-ce que la commune est adhérante ANVTA ? Ajouter un label ?
- Rechercher au niveau national ou viser une région/ département en particulier?
- Ajouter un 'en savoir plus' pour comprendre le scoring
- Gare --> a conditionner avec une question mobilité (besoin de revenir regulierement)
- FLE doit être un critere à part entiere et il faut trouver une base spécifique
- Les multiselects sont frustrants car il faut trouver la terminologie exacte

## 5. Features

## 🚀 Feature [F-01]: Navigation Principale

### 📝 User Story

En tant que travailleur social, je veux des points de départ clairs pour commencer un nouveau profil ou accéder directement aux résultats afin de naviguer efficacement dans l'outil.

### 🔑 Key Features

- Un bouton "Commencer le parcours" pour démarrer le formulaire.
- Un bouton "Aller directement à la page résultats" pour contourner le formulaire.

### 📊 Status

- Completed

## 🚀 Feature [F-02]: Scénarios de Démonstration

### 📝 User Story

En tant que travailleur social, je veux pouvoir charger des scénarios de démonstration pré-remplis pour comprendre rapidement les capacités de l'outil sans avoir à remplir manuellement le formulaire.

### 🔑 Key Features

- Charger un profil de démo à l'aide d'un paramètre de requête d'URL (par ex., `?demo=...`).
- Le formulaire est pré-rempli avec les données de la démo.

### 📊 Status

- Completed

## 🚀 Feature [F-03]: Personnalisation du Parcours

### 📝 User Story

En tant que travailleur social, je veux saisir le nom de la personne que j'accompagne pour personnaliser l'interface, rendant le processus plus centré sur l'humain.

### 🔑 Key Features

- Un champ de saisie pour le nom de la personne sur la page d'accueil.
- Le nom est utilisé pour personnaliser les titres et les libellés dans toute l'application.

### 📊 Status

- Completed

## 🚀 Feature [F-04]: Contrôles Interactifs des Résultats

### 📝 User Story

En tant que travailleur social, je veux ajuster les poids de notation et les filtres en temps réel pour explorer collaborativement différents scénarios avec la personne que j'accompagne.

### 🔑 Key Features

- Une barre latérale avec des curseurs pour ajuster les poids des différentes catégories de notation.
- Une barre latérale avec des champs pour filtrer les résultats (par ex., population minimale).
- Un bouton "Mettre à jour" pour relancer la recherche avec les nouvelles valeurs.

### 📊 Status

- Completed

## 🚀 Feature [F-05]: Édition Rapide des Entrées

### 📝 User Story

En tant que travailleur social, je veux pouvoir modifier rapidement les entrées initiales du "projet de vie" depuis la page des résultats pour itérer sur la recherche sans retourner au formulaire principal.

### 🔑 Key Features

- Des onglets sur la page des résultats qui reflètent les champs du formulaire.
- La possibilité de changer n'importe quelle valeur d'entrée.
- Un bouton "Mettre à jour" pour relancer la recherche avec les nouvelles valeurs.

### 📊 Status

- Completed

## 🚀 Feature [F-06]: Affichage des 5 Meilleurs Résultats

### 📝 User Story

En tant que travailleur social, je veux voir une liste claire et classée des meilleurs lieux recommandés pour discuter facilement des options les plus prometteuses.

### 🔑 Key Features

- Affiche les 5 meilleurs résultats (commune seule ou en binôme).
- Montre les points forts de chaque résultat.
- Inclut un graphique radar pour visualiser la répartition des scores par catégorie.

### 📊 Status

- Completed

## 🚀 Feature [F-07]: Visualisation sur Carte Interactive

### 📝 User Story

En tant que travailleur social, je veux voir les lieux recommandés sur une carte interactive pour mieux comprendre leur contexte géographique et explorer les services environnants.

### 🔑 Key Features

- Une carte choroplèthe montrant les scores de toutes les communes dans la zone de recherche.
- Des marqueurs pour les 5 meilleurs résultats.
- L'option "Afficher le Top 5" affiche le rang (1-5) sur la carte au centroïde de chaque résultat du top 5.
- Des couches cartographiques activables pour les écoles, les services de santé et d'autres points d'intérêt.

### 📊 Status

- Completed

## 🚀 Feature [F-08]: Ajout de l'indicateur loyer moyen

### 📝 User Stories

- En tant que travailleur social, je veux intégrer le loyer moyen dans le score pour mieux évaluer l'accessibilité financière d'une localité pour la famille que j'accompagne.

### 🔑 Key Features

- Intégrer une nouvelle source de données (API) fournissant le loyer moyen par commune.
- Créer un nouveau critère de score "loyer" dans la catégorie "Logement", où un loyer plus bas résulte en un meilleur score.
- Afficher le loyer comme un point fort (ex: "Loyer modéré") dans la liste des résultats lorsqu'il est significatif pour une localité.

### 📊 Status

- Completed

### 🔑 Key Features

- **Intégration Données :** Source "Carte des Loyers" (2023) du Ministère de la Transition Écologique (Data Gouv).
- **Indicateur Loyer :** `loyer_app_m2` (Loyer d'annonce moyen prédictif pour les appartements).
- **Scoring :** `loyer_abordable_scaled` (Logique "Lower is Better" : plus le loyer est bas, meilleur est le score).
- **Intégration UI :** Affichage dans la catégorie "Logement" si l'utilisateur choisit "Location".

## 🚀 Feature [F-09]: Résultats par bassin de vie

### 📝 User Stories

- En tant que travailleur social, je veux pouvoir agréger et visualiser les résultats par "bassin de vie" pour obtenir une perspective régionale plus pertinente et intuitive sur les territoires bien connectés.

### 🔑 Key Features

- Ajouter un bouton de bascule (toggle) dans la barre latérale des résultats pour choisir entre la vue "par commune" et la vue "par bassin de vie".
- Agréger les indicateurs de score existants au niveau du "bassin de vie" pour calculer un nouveau score global.
- Mettre à jour la carte des résultats pour afficher des polygones colorés représentant les "bassins de vie" et leur score.

### 📊 Status

- Completed

## 🚀 Feature [F-10]: Refonte du Filtrage et Score de Population

### 📝 User Stories

- En tant que travailleur social, je veux que la pertinence d'une localité ne soit plus limitée par un seuil de population fixe, mais que la population soit un facteur parmi d'autres dans le score pour des résultats plus nuancés.
- En tant que travailleur social, je souhaite que la recherche par zone géographique (département, région) soit plus précise et retourne toutes les communes réellement présentes dans cette zone.

### 🔑 Key Features

- Suppression du filtre de population minimal : la population est désormais un critère de score dans la catégorie "Inclusion", valorisant les pôles urbains sans exclure les zones rurales.
- Refonte de la logique de filtrage géographique pour utiliser des calculs géospatiaux (centroïdes et intersections) au lieu de simples distances, s'appliquant de manière cohérente aux communes et aux bassins de vie.

### 📊 Status

- Completed

## 🚀 Feature [F-11]: Export des résultats en PDF

### 📝 User Stories

- En tant que travailleur social, je veux exporter les résultats de la recherche en PDF pour les partager facilement avec la famille accompagnée, afin de leur fournir un résumé clair et professionnel des recommandations.

### 🔑 Key Features

- Un bouton "Exporter en PDF" sur la page des résultats.
- La première page rappelle les critères de recherche, et la carte de tous les résultats avec le calque 'Top 5' actif.
- Le PDF inclut les 5 meilleurs résultats (bassins de vie ou communes), une page par résultat.
- Pour chaque recommandation, le PDF affiche les points forts et le graphique radar des scores.
- Le PDF intègre le logo "J'accueille".

### 📊 Status

- Completed

## 🚀 Feature [F-12]: Ajout de critères santé et éducation au scoring

### 📝 User Stories

- En tant que travailleur social, je veux que le score d'éducation reflète le **nombre** de niveaux scolaires pertinents présents dans une localité, pour une évaluation plus fine.
- En tant que travailleur social, je veux que le besoin de proximité d'un service de santé soit un critère de score pour que les recommandations tiennent compte de l'accès aux soins.

### 🔑 Key Features

- **Score d'Éducation Basé sur le Nombre :** Un nouveau score est ajouté à la catégorie "Éducation". Il **compte combien** de niveaux scolaires demandés (ex: "Maternelle", "Collège") sont présents dans la commune. Une commune avec 2 types d'écoles sur 2 demandés aura un score plus élevé qu'une commune avec 1 sur 2.
- **Score de Santé Basé sur la Présence :** Un nouveau score est ajouté à la catégorie "Inclusion". Il vaut 100% si une commune possède le service de santé exact sélectionné ("Hôpital", "Maternité", etc.), et 0% sinon.
- **Agrégation par Bassin de Vie :**
  - Pour le score d'**éducation**, le calcul pour le bassin de vie comptera le nombre de types d'écoles uniques présents dans l'ensemble des communes qui le composent.
  - Pour le score de **santé**, le bassin de vie obtiendra 100% si au moins une de ses communes possède le service requis.
- **Affichage dans les Résultats :** La présence de ces services est mentionnée dans les points forts du résultat.

### 📊 Status

- Completed

## 🚀 Feature [F-13]: Nouveau score d'inclusion

### 📝 User Stories

- En tant que travailleur social, je veux évaluer précisément la présence de services institutionnels essentiels pour garantir un soutien de base à la famille accompagnée.
- En tant que travailleur social, je veux mesurer le dynamisme social local via le nombre d'associations pour identifier les communautés où la famille accompagnée pourra facilement s'intégrer.
- En tant que travailleur social, je veux pouvoir faire correspondre les intérêts spécifiques de la famille accompagnée (sport, culture, nature) avec les associations locales disponibles afin de favoriser son épanouissement personnel et son intégration.

### 🔑 Key Features

- Refonte du calcul du score "Inclusion" intégrant cinq composantes : "Socle Administratif" (`inc_services_core_scaled`), "Services Spécifiques" (`inc_services_add_scaled`), "Lien Social" (`inc_asso_core_scaled`), "Affinité" (`inc_asso_add_scaled`) et "Population".
- Le "Socle Administratif" (`inc_services_core_scaled`) est basé sur la présence et la densité des services institutionnels clés issus de `annuaire_inclusion_index.csv`.
  Note: Les catégories services à cocher par défaut sont:

* logement-hebergement: etre-accompagne-pour-se-loger
* acces-aux-droits-et-citoyennete: accompagnement-juridique
* acces-aux-droits-et-citoyennete: demandeurs-dasile-et-naturalisation
* preparer-sa-candidature: realiser-un-cv-et-ou-une-lettre-de-motivation

- Le "Lien Social" (`inc_asso_core_scaled`) mesure la densité d'associations (via le RNA et les codes WALDEC) correspondant à des catégories fixes définies dans `.agent/rna_config.py`. Les données associations sont dans `csv/rna_waldec_20250901_mini_odis.csv`, l'index dans `csv/rna-associations-nomenclature-waldec.json`.
- L'"Affinité" (`inc_asso_add_scaled`) mesure la densité d'associations (via le RNA et les codes WALDEC) correspondant aux centres d'intérêt spécifiques sélectionnés par l'utilisateur.
- "Services Spécifiques" (`inc_services_add_scaled`) mesure la présence d'autres services d'inclusion sélectionnés.
- La "Population" (`inc_population_scaled`) est utilisée comme un critère direct, favorisant les communes de taille importante.
- Une nouvelle interface utilisateur pour l'étape "Inclusion" (étape 7/8 du formulaire) proposera deux champs multiselect : un pour les services institutionnels (avec des pré-sélections déselectionnables) et un pour les activités associatives.
- La métrique utilisée pour les associations est le nombre total d'associations correspondantes pour 1000 habitants, calculé au niveau communal et agrégé en moyenne pondérée par la population pour les Bassins de Vie.
- Les quatre composantes du score d'inclusion sont pondérées de manière égale au sein de la catégorie Inclusion.
- **Note sur l'agrégation** : Pour simplifier la compréhension, tous les scores (y compris Éducation, Santé et Inclusion) sont agrégés au niveau Bassin de Vie par une moyenne pondérée par la population des communes. Cela signifie qu'un service présent dans une petite commune aura moins d'impact sur le score global du bassin qu'un service présent dans une grande commune.

### 📊 Status

- Completed

## 🚀 Feature [F-14]: Support Accueil Petite Enfance

### 📝 User Stories

- En tant que parent de jeunes enfants (< 3 ans), je veux inclure la disponibilité des modes de garde (crèches, assistantes maternelles) dans le score pour identifier les communes où je pourrai faire garder mes enfants.

### 🔑 Key Features

- **Nouveau Critère "Petite Enfance" :** Ajout d'un critère de score spécifique pour l'accueil des jeunes enfants, distinct des structures scolaires classiques.
- **Source de Données :** Utilisation des données de la CAF (Taux de couverture ou nombre de places) pour évaluer l'offre.
- **Intégration UI :** Ajout d'une option "Crèche / Assistante Maternelle" dans le menu déroulant "Education" existant.
- **Scoring :**
  - Le score est calculé sur la base du taux de couverture (places pour 100 enfants) ou de la densité de places.
  - Il est intégré à la catégorie "Education" mais calculé indépendamment des écoles.
- **Agrégation :** Agrégation au niveau Bassin de Vie par moyenne pondérée par la population (cohérent avec les autres scores).

### 📊 Status

- Completed

## 🚀 Feature [F-15]: Profils de Pondération et Affinage des Critères

### 📝 User Stories

- En tant que travailleur social, je veux appliquer en un clic une configuration de poids standard (ex: "Priorité Famille") pour gagner du temps lors de l'entretien.
- En tant que travailleur social, je veux pouvoir indiquer qu'un critère spécifique (ex: "Présence d'un Lycée") est **prioritaire** au sein de sa catégorie, sans avoir à gérer des dizaines de curseurs complexes.

### 🔑 Key Features

- **Sélecteur de Profils (Presets) :**

  - Ajout d'un menu déroulant "Profil de Priorité" dans la barre latérale (au-dessus des sliders).
  - **Options proposées :**
    - _Équilibré_ (Défaut) : Tous les poids à 100.
    - _Famille_ (Stabilité & Education) : Logement (300), Education (300), Reste (100).
    - _Santé_ (Soins & Stabilité) : Santé (300), Logement (200), Reste (100).
    - _Economique_ (Emploi & Formation) : Emploi (300), Mobilité (200), Reste (100).
  - La sélection d'un profil met à jour automatiquement les sliders de catégorie existants.

- **Pondération Intra-Catégorie (Niveau Critère) :**
  - Refonte du moteur de scoring (`compute_category_scores`) pour passer d'une moyenne simple à une **moyenne pondérée** des critères.
  - **UI d'Affinage :** Dans les formulaires (onglets), ajout d'un contrôle "Importance" (ex: Toggle ou Sélecteur "Normal / Important") à côté des critères clés (Niveaux scolaires, Types de santé, Thématiques associatives).
  - **Impact Mathématique :** Un critère marqué "Important" reçoit un coefficient multiplicateur (ex: x3) dans le calcul du score de sa catégorie.

### 📊 Status

- Completed

## 🚀 Feature [F-16]: Optimisation et Refactoring ETL

### 📝 User Stories

- En tant que développeur, je veux que le pipeline ETL soit robuste, rapide et produise des fichiers optimisés pour l'application afin de réduire le temps de chargement et d'améliorer la réactivité.

### 🔑 Key Features

- **Pipeline Unifié :** Intégration de l'étape de déploiement (`deploy_data.py`) directement dans le CLI `etl.py`.
- **Pré-calcul des Scores :** Déplacement de la logique de normalisation (Min-Max Scaling) du temps d'exécution (`scoring.py`) vers le temps de construction (`build.py`) pour les indicateurs statiques.
- **Optimisation des Fichiers :** Suppression des colonnes inutilisées dans `odis_communes.parquet` et utilisation de formats verticaux (`bmo_vertical.parquet`) pour les données volumineuses (métiers).
- **Gestion des Erreurs Topologiques :** Correction robuste des géométries invalides lors de l'agrégation des Bassins de Vie.

### 📊 Status

- Completed

## 🚀 Feature [F-17]: Intégration Données Odace (Gares)

### 📝 User Stories

- En tant que travailleur social, je veux savoir si une commune dispose d'une gare ferroviaire pour évaluer la mobilité pendulaire et l'accès aux grands pôles urbains pour la famille accompagnée.

### 🔑 Key Features

- **Intégration API Odace :** Connexion à l'API Odace pour récupérer les données référentielles (`dim_commune` et `dim_gare`).
- **Indicateur Gare :** Création d'un indicateur binaire "Présence d'une gare" (`mob_gare_scaled`) pour chaque commune.
- **Scoring Mobilité :** Intégration de ce nouvel indicateur dans la catégorie "Mobilité", permettant de valoriser les communes desservies par le train.
- **Affichage :** Mention de la présence d'une gare dans les points forts de la localité.

### 📊 Status

- Completed

## 🚀 Feature [F-18]: Indicateurs de Déclin de Population

### 📝 User Stories

- En tant que travailleur social, je veux identifier les communes qui perdent des habitants, en particulier des jeunes, car elles pourraient être plus incitées à accueillir de nouvelles familles pour maintenir leurs écoles et services.

### 🔑 Key Features

- **Nouvelle Source de Données :** "Population Détails" (Insee) avec ventilation par âge (2011, 2016, 2022).
- **Indicateur Déclin Jeune :** `youth_decline_scaled`. Score élevé si la population des moins de 15 ans diminue. Intégré à la catégorie "Éducation".
- **Indicateur Déclin Actifs :** `workclass_decline_scaled`. Score élevé si la population des 25-54 ans diminue. Intégré à la catégorie "Emploi".
- **Scoring :** Plus le déclin est fort, plus le score est élevé (opportunité d'accueil).
- **Affichage :** Point fort "Besoin de familles" ou "Besoin d'actifs".

### 📊 Status

- Not Started

## 🚀 Feature [F-19]: Odis AI Agent (Assistant Virtuel)

### 📝 User Stories

- En tant que travailleur social, je veux interagir en langage naturel avec l'outil pour décrire la situation de la famille (récit de vie) et obtenir des recommandations sans avoir à remplir manuellement chaque champ du formulaire.
- En tant que travailleur social, je veux que l'assistant comprenne le jargon métier (codes ROME, besoins spécifiques) et trouve automatiquement les correspondances dans les référentiels.

### 🔑 Key Features

- **Moteur LLM :** Intégration de **Gemini 2.5-flash-lite** pour une compréhension contextuelle avancée et une capacité de "Tool Use" robuste.
- **Architecture MCP (Model Context Protocol) :**
  - Serveur MCP (`app/mcp_server.py`) exposant les données ODIS (Référentiels, Communes, Scoring) comme des outils standardisés.
  - Client Gemini (`app/gemini_client.py`) consommant ces outils.
- **Outils Intelligents :**
  - `search_commune(query)`: Trouve le code INSEE exact d'une ville.
  - `search_referentiels(query, domain)`: Recherche sémantique robuste (tolérance aux fautes, mots vides) dans les référentiels Métiers (FAP), Formations et Associations (WALDEC).
  - `compute_top_cities(weight_profile, criterias)`: Lance le moteur de scoring ODIS avec des critères structurés.
- **Robustesse & Typage :** Utilisation de modèles **Pydantic** (`app/models.py`) pour garantir que l'IA génère des paramètres de recherche valides (schéma strict).
- **Prompt Engineering :** "System Instruction" externalisée (`AGENT_PROMPT.md`) définissant un persona "Assistant Expert" avec un protocole d'entretien strict en 4 phases (Ancrage, Famille, Besoins, Validation).

### 📊 Status

- Completed

## 🚀 Feature [F-20]: Détails Territoire (Learn More)

### 📝 User Stories

- En tant qu'utilisateur (via UI classique ou Agent IA), je veux accéder à une fiche détaillée agrégeant toutes les informations disponibles sur un territoire (emploi, éducation, santé, inclusion, vie associative) pour approfondir ma compréhension au-delà du simple score.

### 🔑 Key Features

- **Nouveau Outil MCP :** `get_city_details(codgeo)` qui agrège les données de toutes les sources disponibles (ODIS, Annuaire Education/Santé/Inclusion, Associations, BMO).
- **Structure des Données :** Retourne un objet JSON structuré avec :
  - **Identité :** Nom, Code, Population, Bassin de Vie.
  - **Scores :** Détail des scores bruts et normalisés.
  - **Emploi :** Top secteurs recruteurs (BMO).
  - **Education :** Nombre d'établissements par niveau.
  - **Santé :** Dénombrement des services clés.
  - **Inclusion :** Liste des services disponibles.
  - **Associations :** Thématiques principales et volumétrie.
- **Intégration UI :** Bouton "En savoir plus" dans la liste des résultats ouvrant une vue détaillée.
- **Intégration Agent :** Le chatbot peut appeler cet outil pour répondre à des questions spécifiques comme "Quelles sont les associations sportives à X ?" ou "Y a-t-il un hôpital à Y ?".

### 📊 Status

- Not Started
